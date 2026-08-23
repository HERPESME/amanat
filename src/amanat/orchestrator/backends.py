"""Swappable LLM backends.

The model holds no authority in this system — it can only propose, and every
proposal is judged by `PolicyEngine` before it reaches a rail. That is what makes
the provider a genuinely free choice rather than an architectural commitment, so
the seam is worth making explicit.

Both backends bind the same four governed actions on the same `AgentSession`.
The tool *declarations* are written twice because the two providers use
different schema shapes, and a clever abstraction over that would be harder to
read than the duplication. The *handlers* are shared: they are session methods,
and there is no path from either backend to a rail that skips the policy engine.

Selection is by whichever credential is present — see `resolve_backend`.
"""
from __future__ import annotations

import json
import os
from typing import Protocol, runtime_checkable

from amanat.orchestrator.session import AgentSession

MAX_TOOL_ITERATIONS = 12

SYSTEM = """\
You are a payments agent operating under a bounded spending envelope.

How this works:
- You may only spend by calling the provided tools. You cannot move money any
  other way, and every call is checked by a deterministic policy engine before
  it reaches the payment rail.
- A REFUSED result is final for that request. Do not retry the identical call.
  Read the reason, and either adjust within your envelope or explain to the user
  why the request cannot be met.
- Amounts are in integer paise (100 paise = 1 rupee).

The settlement model you are operating:
1. RESERVE a ceiling before the final amount is known. This blocks funds in the
   customer's own account; it does not spend them.
2. DEBIT the actual amount once it is known. It must be at or below the ceiling.
3. RELEASE the difference back to the customer, promptly.

Choosing the ceiling is the judgement call. Too low and the debit is rejected
and the purchase fails. Too high and you have stranded the customer's money for
no reason. State your reasoning for the ceiling you pick.
"""

# One place to describe what the agent may do. Each backend renders these into
# its own provider's schema; the handler names map to AgentSession methods.
TOOLS = [
    {
        "name": "reserve_funds",
        "description": ("Block a spending ceiling against the payment rail. This "
                        "does not spend the money; it reserves it in the "
                        "customer's own account."),
        "parameters": {
            "type": "object",
            "properties": {
                "amount_paise": {"type": "integer",
                                 "description": "The ceiling to block, in integer paise."},
                "payee": {"type": "string",
                          "description": "The merchant to block the funds against."},
                "reason": {"type": "string",
                           "description": "Why this ceiling — state the reasoning for the amount."},
            },
            "required": ["amount_paise", "payee", "reason"],
        },
    },
    {
        "name": "debit_actual",
        "description": "Debit the actual amount, at or below the reserved ceiling.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount_paise": {"type": "integer",
                                 "description": "The realised amount to debit, in integer paise."},
                "reason": {"type": "string", "description": "What this debit is for."},
            },
            "required": ["amount_paise", "reason"],
        },
    },
    {
        "name": "release_remainder",
        "description": "Return the unspent remainder of the block to the customer.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "description": "Why the remainder is being released now."},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "get_status",
        "description": "Report current blocked, debited, released and stranded amounts.",
        "parameters": {"type": "object", "properties": {}},
    },
]


class _BadArgument(Exception):
    """A tool argument the model sent that cannot be used."""


def _paise(args: dict, key: str) -> int:
    """Coerce a money argument, or refuse. Never raises past dispatch.

    Tool arguments are attacker-controlled input — a compromised or
    prompt-injected model chooses them, so they get the same treatment as a form
    field. Floats are rejected outright rather than rounded: silently turning
    10.5 paise into 10 or 11 is a rounding decision nobody authorised, and money
    in this codebase is integer paise everywhere.
    """
    if key not in args:
        raise _BadArgument(f"missing required argument {key!r}")
    value = args[key]
    if isinstance(value, bool) or value is None:
        raise _BadArgument(f"{key!r} must be an integer number of paise")
    if isinstance(value, float):
        raise _BadArgument(f"{key!r} must be integer paise, not a fractional amount")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise _BadArgument(f"{key!r} is not an integer number of paise") from None


def _text(args: dict, key: str, required: bool = True) -> str:
    value = args.get(key)
    if value is None:
        if required:
            raise _BadArgument(f"missing required argument {key!r}")
        return ""
    if not isinstance(value, str):
        raise _BadArgument(f"{key!r} must be text")
    return value


def dispatch(session: AgentSession, name: str, args: dict) -> str:
    """Route a model's tool call to the governed session. The only entry point.

    Returns a refusal string for anything it cannot honour, and never raises. An
    exception escaping here would abort the turn *and skip the evidence entry* —
    letting a malformed call go unrecorded, which is the one outcome this system
    must not have.
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "reserve_funds":
            return session.reserve(_paise(args, "amount_paise"),
                                   _text(args, "payee"),
                                   _text(args, "reason", required=False)
                                   ).as_tool_result()
        if name == "debit_actual":
            return session.debit(_paise(args, "amount_paise"),
                                 _text(args, "reason", required=False)
                                 ).as_tool_result()
        if name == "release_remainder":
            return session.release(None, _text(args, "reason", required=False)
                                   ).as_tool_result()
        if name == "get_status":
            return session.status().as_tool_result()
    except _BadArgument as exc:
        session.record_malformed_call(name, args, str(exc))
        return f"REFUSED: {exc}"

    session.record_malformed_call(name, args, "unknown tool")
    return f"REFUSED: unknown tool {name!r}"


@runtime_checkable
class LLMBackend(Protocol):
    name: str
    model: str

    def run(self, session: AgentSession, user_input: str) -> str: ...


class AnthropicBackend:
    """Claude via the SDK's tool runner.

    Per the SDK's guidance the approval gate lives inside the tool functions
    rather than in a hand-written loop — the runner drives the conversation and
    each tool consults the policy engine before touching the rail.
    """

    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5") -> None:
        self.model = model

    def run(self, session: AgentSession, user_input: str,
            max_tokens: int = 16000) -> str:
        import anthropic
        from anthropic import beta_tool

        @beta_tool
        def reserve_funds(amount_paise: int, payee: str, reason: str) -> str:
            """Block a spending ceiling against the payment rail.

            Args:
                amount_paise: The ceiling to block, in integer paise.
                payee: The merchant to block the funds against.
                reason: Why this ceiling — state the reasoning for the amount.
            """
            return dispatch(session, "reserve_funds", locals())

        @beta_tool
        def debit_actual(amount_paise: int, reason: str) -> str:
            """Debit the actual amount, at or below the reserved ceiling.

            Args:
                amount_paise: The realised amount to debit, in integer paise.
                reason: What this debit is for.
            """
            return dispatch(session, "debit_actual", locals())

        @beta_tool
        def release_remainder(reason: str) -> str:
            """Return the unspent remainder of the block to the customer.

            Args:
                reason: Why the remainder is being released now.
            """
            return dispatch(session, "release_remainder", locals())

        @beta_tool
        def get_status() -> str:
            """Report blocked, debited, released and stranded amounts."""
            return dispatch(session, "get_status", {})

        runner = anthropic.Anthropic().beta.messages.tool_runner(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=f"{SYSTEM}\n\n{session.briefing()}",
            tools=[reserve_funds, debit_actual, release_remainder, get_status],
            messages=[{"role": "user", "content": user_input}],
        )
        final = None
        for message in runner:
            final = message
        if final is None:
            return ""
        return "".join(b.text for b in final.content if b.type == "text")


class GeminiBackend:
    """Gemini via the interactions API, with an explicit tool loop.

    The system prompt is prepended to the first turn rather than passed as a
    separate field: the instruction must reach the model, and prepending works
    regardless of which system-instruction shape this API version accepts.
    """

    name = "gemini"

    def __init__(self, model: str = "gemini-3.7-flash",
                 api_key: str | None = None) -> None:
        self.model = model
        # An explicit key lets a caller (e.g. the hosted demo) run on a visitor's
        # own credential instead of the process environment. Never logged.
        self.api_key = api_key

    def run(self, session: AgentSession, user_input: str) -> str:
        from google import genai

        client = genai.Client(api_key=self.api_key or os.environ["GEMINI_API_KEY"])
        tools = [{"type": "function", **t} for t in TOOLS]

        interaction = client.interactions.create(
            model=self.model,
            input=f"{SYSTEM}\n\n{session.briefing()}\n\n---\n\n{user_input}",
            tools=tools,
        )

        for _ in range(MAX_TOOL_ITERATIONS):
            calls = [s for s in getattr(interaction, "steps", [])
                     if getattr(s, "type", None) == "function_call"]
            if not calls:
                break

            results = []
            for call in calls:
                args = call.arguments
                if isinstance(args, str):          # some versions return JSON text
                    args = json.loads(args or "{}")
                results.append({
                    "type": "function_result",
                    "name": call.name,
                    "call_id": call.id,
                    "result": [{"type": "text",
                                "text": dispatch(session, call.name, args or {})}],
                })

            interaction = client.interactions.create(
                model=self.model,
                input=results,
                tools=tools,
                previous_interaction_id=interaction.id,
            )
        else:
            return (interaction.output_text or "") + (
                f"\n\n[stopped after {MAX_TOOL_ITERATIONS} tool iterations]")

        return interaction.output_text or ""


def resolve_backend() -> LLMBackend | None:
    """Pick a backend from whatever credential is present. Gemini wins if both.

    Returns None when nothing is configured, so callers can print a useful
    message instead of raising. The governed core needs no credential at all.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiBackend()
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return AnthropicBackend()
    return None
