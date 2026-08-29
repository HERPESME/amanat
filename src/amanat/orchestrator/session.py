"""The governed execution core — everything the agent can do, and nothing more.

Deliberately contains no LLM call. Every action an agent could take is a method
here, and every method routes through the same sequence:

    propose  ->  policy engine  ->  rail  ->  evidence chain

That ordering is the architecture. The LLM sits *above* this module and can only
reach the rail by asking; it cannot construct a rail call directly, and there is
no code path that skips the policy check.

Keeping the core LLM-free is also what makes the governance testable: the
containment tests exercise real refusals without an API key, a network call, or
a sampled token. If a test needs a model to prove the agent is bounded, the
agent is not bounded.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.policy.engine import Action, PolicyEngine, Proposal, Verdict
from amanat.policy.envelope import Envelope, LedgerState
from amanat.rails.base import BlockRef, RailError
from amanat.rails.simulator import SimulatedRail


def _consent_bytes(body: dict) -> bytes:
    """Canonical bytes of a consent record minus its own signature field.

    Same discipline as the AP2 mandate signature (`ensure_ascii=False`, sorted
    keys, tight separators) so a widening signed here verifies the same way a
    mandate does, in the browser and in the adjudicator.
    """
    return _json.dumps({k: v for k, v in body.items() if k != "signature"},
                       sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str).encode("utf-8")


@dataclass
class ActionResult:
    """What came back from an attempted action, in a shape an LLM can read."""

    ok: bool
    detail: str
    citation: str = ""
    state: dict = field(default_factory=dict)

    def as_tool_result(self) -> str:
        head = "OK" if self.ok else "REFUSED"
        parts = [f"{head}: {self.detail}"]
        if self.citation:
            parts.append(f"(authority: {self.citation})")
        if self.state:
            parts.append(f"state: {self.state}")
        return " ".join(parts)


class AgentSession:
    """One bounded spending session: an envelope, a rail, and an audit trail."""

    def __init__(self, envelope: Envelope, rail: SimulatedRail,
                 chain: EvidenceChain | None = None) -> None:
        self.envelope = envelope
        self.rail = rail
        self.chain = chain or EvidenceChain.new(envelope.subject)
        self.engine = PolicyEngine(chain=self.chain)
        self.state = LedgerState()
        self.block: BlockRef | None = None

        self.chain.append(Actor.HUMAN, EventType.ENVELOPE, envelope.to_payload())

    # -- what the agent may ask for ---------------------------------------

    def reserve(self, amount: int, payee: str, reason: str = "") -> ActionResult:
        """Place a spending ceiling. The agent's authority becomes bank-held."""
        return self._attempt(Action.RESERVE, amount, payee, reason)

    def debit(self, amount: int, reason: str = "") -> ActionResult:
        """Move the actual amount, once it is known."""
        payee = self.envelope.allowed_payees[0]
        return self._attempt(Action.DEBIT, amount, payee, reason)

    def release(self, amount: int | None = None, reason: str = "") -> ActionResult:
        """Hand the unspent difference back. The other half of amount-contingency."""
        amount = self.state.available if amount is None else amount
        payee = self.envelope.allowed_payees[0]
        return self._attempt(Action.RELEASE, amount, payee, reason)

    def status(self) -> ActionResult:
        return ActionResult(True, "current position", state=self._snapshot())

    # -- re-approval: the cap is the human's, and only the human can raise it ----

    def propose_raise(self, new_max_total: int, reason: str = "") -> ActionResult:
        """The agent ASKS the human to widen the cap. It cannot grant this itself.

        When a fare comes in above the envelope's budget, the agent's only move is
        to surface it and request more room. That request is recorded as a proposal
        (actor=agent), like any other — it moves no money and grants no authority.
        The refusal that prompted it is already in the chain; this is what an agent
        does *instead of* overspending.
        """
        self.chain.append(Actor.AGENT, EventType.PROPOSAL, {
            "action": "raise_ceiling",
            "current_max_total": self.envelope.max_total,
            "requested_max_total": new_max_total,
            "reason": reason,
        })
        return ActionResult(
            False,
            f"raising the cap from {self.envelope.max_total} to {new_max_total} "
            "needs the human's approval — the agent cannot widen its own grant",
            state=self._snapshot())

    def approve_raise(self, new_max_total: int, user_key, *,
                      new_max_per_txn: int | None = None,
                      reason: str = "") -> ActionResult:
        """The HUMAN widens the cap and signs the new grant. Not an agent action.

        `user_key` is the human's Ed25519 private key — the same party that signs
        the AP2 mandate, never the orchestrator's. The widened envelope is recorded
        as a fresh human-signed ENVELOPE entry (from/to caps, the reason, the cnf
        key and a signature over it), so a later auditor can see the grant was
        raised, by how much, and that the key that raised it is the one the mandate
        names. The session then evaluates against the new cap — nothing before this
        entry is retroactively permitted.
        """
        widened = self.envelope.widened(
            max_total=new_max_total,
            max_per_txn=new_max_per_txn if new_max_per_txn is not None else new_max_total,
            reason=reason)
        body = {
            "event": "envelope_widened",
            "subject": self.envelope.subject,
            "from": {"max_total": self.envelope.max_total,
                     "max_per_txn": self.envelope.max_per_txn},
            "to": {"max_total": widened.max_total,
                   "max_per_txn": widened.max_per_txn},
            "reason": reason,
            "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519",
                            "x": user_key.public_key().public_bytes_raw().hex()}},
        }
        body["signature"] = user_key.sign(_consent_bytes(body)).hex()
        self.chain.append(Actor.HUMAN, EventType.ENVELOPE, body)
        self.envelope = widened
        return ActionResult(
            True,
            f"cap raised to {widened.max_total}, signed by the human key "
            f"{body['cnf']['jwk']['x'][:12]}…",
            state=self._snapshot())

    def record_malformed_call(self, tool: str, args: dict, why: str) -> None:
        """Log a tool call that never became a proposal.

        A call rejected at the argument boundary never reaches the policy
        engine, so nothing else would record it — and an unrecorded call from an
        untrusted model is exactly the gap the evidence chain exists to close.
        Garbled attempts are evidence too.
        """
        self.chain.append(Actor.POLICY, EventType.REFUSAL, {
            "rule": "malformed_tool_call",
            "tool": tool,
            "reason": why,
            "arguments": {k: repr(v)[:120] for k, v in (args or {}).items()},
        })

    def briefing(self) -> str:
        """The envelope, rendered for the model.

        An agent that cannot see its own constraints will flail against them.
        The first live run proved it: the model invented three payees, was
        refused three times, and reported to the user that the envelope had no
        allocation — none of which it could have known was wrong.

        Telling it the boundary is not the same as trusting it to respect the
        boundary. The policy engine still checks every call; this only removes
        the guessing.
        """
        e = self.envelope
        rail = self.rail.profile
        cap = rail.limit("max_block_amount")
        lines = [
            "YOUR ENVELOPE — the policy engine enforces all of this regardless "
            "of what you do:",
            f"  total budget      {e.max_total} paise (₹{e.max_total / 100:,.2f})",
            f"  per transaction   {e.max_per_txn} paise (₹{e.max_per_txn / 100:,.2f})",
            f"  allowed payees    {', '.join(e.allowed_payees)}"
            "   ← no other payee will be accepted",
            f"  expires           {e.expires_at.isoformat()}",
            f"  rail              {rail.display_name}",
        ]
        if cap is not None:
            lines.append(f"  rail block cap    {cap.value} paise ({cap.render()}) "
                         f"— {cap.citation}")
        if not rail.permits("remainder_release_without_teardown"):
            lines.append("  NOTE: this rail cannot return part of a block. Releasing "
                         "the remainder tears the whole block down, and only one "
                         "block per merchant may stand at a time.")
        return "\n".join(lines)

    # -- the one path to the rail -----------------------------------------

    def _attempt(self, action: Action, amount: int, payee: str,
                 reason: str) -> ActionResult:
        proposal = Proposal(action, amount, payee, self.rail.rail_id, memo=reason)

        # The LLM's ask is recorded before it is judged, so the audit trail
        # shows what was wanted as well as what was permitted.
        self.chain.append(Actor.AGENT, EventType.PROPOSAL, {
            "action": action.value, "amount": amount,
            "payee": payee, "reason": reason,
        })

        verdict: Verdict = self.engine.evaluate(proposal, self.envelope, self.state)
        if not verdict.allowed:
            return ActionResult(False, verdict.reason, verdict.citation,
                                self._snapshot())

        try:
            self._apply(action, amount, payee)
        except RailError as exc:
            # Policy permitted it; the rail did not. Distinct failure, recorded
            # distinctly — this is where an under-set ceiling surfaces.
            self.chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
                "action": action.value, "amount": amount,
                "outcome": "rail_rejected", "error": str(exc),
            })
            return ActionResult(False, f"rail rejected: {exc}", state=self._snapshot())

        self.chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "action": action.value, "amount": amount, "outcome": "applied",
            "block_id": self.block.block_id if self.block else None,
        })
        return ActionResult(True, f"{action.value} of {amount} applied",
                            verdict.citation, self._snapshot())

    def _apply(self, action: Action, amount: int, payee: str) -> None:
        if action is Action.RESERVE:
            self.block = self.rail.reserve(amount, payee)
            self.state.blocked += amount
        elif action is Action.DEBIT:
            assert self.block is not None, "cannot debit before reserving"
            self.rail.debit(self.block, amount)
            self.state.debited += amount
        elif action is Action.RELEASE:
            assert self.block is not None, "cannot release before reserving"
            self.rail.release(self.block, amount)
            self.state.released += amount
        else:
            raise RailError(f"{action.value} is not applicable in this session")

    def _snapshot(self) -> dict:
        return {
            "blocked": self.state.blocked,
            "debited": self.state.debited,
            "released": self.state.released,
            "available": self.state.available,
            "stranded": self.state.stranded,
        }

    # -- audit -------------------------------------------------------------

    def evidence_packet(self) -> dict:
        return self.chain.export_packet()

    def summary(self) -> str:
        return (f"{len(self.chain.entries)} evidence entries · "
                f"{len(self.chain.rail_transitions())} rail transitions · "
                f"{len(self.chain.refusals())} refusals")
