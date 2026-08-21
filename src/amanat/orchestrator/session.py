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

from dataclasses import dataclass, field

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.policy.engine import Action, PolicyEngine, Proposal, Verdict
from amanat.policy.envelope import Envelope, LedgerState
from amanat.rails.base import BlockRef, RailError
from amanat.rails.simulator import SimulatedRail


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
