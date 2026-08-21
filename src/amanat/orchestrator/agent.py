"""The LLM layer — thin by design.

The agent reaches money only through `AgentSession`, whose every method routes
through the policy engine. Refusals come back to the model as ordinary tool
results, so it can adapt (ask the human, lower the ceiling, stop) instead of
retrying blindly.

Following the SDK's guidance, the approval gate lives *inside* the tool
functions rather than in a hand-written loop: `client.beta.messages.tool_runner`
drives the conversation, and each tool consults the policy engine before it
touches the rail. There is no code path from the model to `SimulatedRail` that
skips `AgentSession._attempt`.

Requires ANTHROPIC_API_KEY (or an `ant auth login` profile). The governed core in
`session.py` needs neither, which is where the containment tests live.
"""
from __future__ import annotations

from amanat.orchestrator.session import AgentSession

MODEL = "claude-opus-5"

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


def build_tools(session: AgentSession):
    """Bind the session's governed actions as tools the model can call."""
    from anthropic import beta_tool

    @beta_tool
    def reserve_funds(amount_paise: int, payee: str, reason: str) -> str:
        """Block a spending ceiling against the payment rail.

        Args:
            amount_paise: The ceiling to block, in integer paise.
            payee: The merchant to block the funds against.
            reason: Why this ceiling — state the reasoning for the amount.
        """
        return session.reserve(amount_paise, payee, reason).as_tool_result()

    @beta_tool
    def debit_actual(amount_paise: int, reason: str) -> str:
        """Debit the actual amount, at or below the reserved ceiling.

        Args:
            amount_paise: The realised amount to debit, in integer paise.
            reason: What this debit is for.
        """
        return session.debit(amount_paise, reason).as_tool_result()

    @beta_tool
    def release_remainder(reason: str) -> str:
        """Return the unspent remainder of the block to the customer.

        Args:
            reason: Why the remainder is being released now.
        """
        return session.release(None, reason).as_tool_result()

    @beta_tool
    def get_status() -> str:
        """Report the current blocked, debited, released and stranded amounts."""
        return session.status().as_tool_result()

    return [reserve_funds, debit_actual, release_remainder, get_status]


def run(session: AgentSession, user_input: str, max_tokens: int = 16000) -> str:
    """Run one agent turn against a governed session. Returns the final text."""
    import anthropic

    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        tools=build_tools(session),
        messages=[{"role": "user", "content": user_input}],
    )

    final = None
    for message in runner:
        final = message

    if final is None:
        return ""
    return "".join(b.text for b in final.content if b.type == "text")
