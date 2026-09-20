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
from datetime import datetime, timedelta, timezone

from amanat.evidence.canonical import MAX_SAFE_INT, sanitize_text
from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.interop.ap2 import from_open_payment_mandate, verify_mandate
from amanat.policy.consent import (
    build_widening, consent_bytes as _consent_bytes, sign_consent, verify_consent,
)
from amanat.policy.engine import Action, PolicyEngine, Proposal, Verdict
from amanat.policy.envelope import Envelope, LedgerState
from amanat.policy.obligations import Obligation, ObligationPolicy, obligations as _obligations, overdue
from amanat.rails.base import BlockRef, RailError
from amanat.rails.simulator import SimulatedRail


# Text a model supplies is recorded in the chain, so it is bounded and cleaned
# at the boundary: a payee, a reason, a tool or argument name. The chain refuses
# what it cannot canonicalise, which is only safe if hostile input never reaches it.
MAX_REASON = 500
MAX_PAYEE = 128
MAX_NAME = 64


def _clip(value, limit: int) -> str:
    """Untrusted input as bounded, well-formed text that is safe to record."""
    return sanitize_text(value if isinstance(value, str) else str(value))[:limit]


def _is_paise(value) -> bool:
    """A plain integer the chain can hold exactly (and a browser can reproduce)."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and -MAX_SAFE_INT <= value <= MAX_SAFE_INT)


@dataclass(frozen=True)
class InDoubt:
    """A rail call whose outcome is unknown: it may or may not have happened."""

    action: Action
    amount: int
    payee: str
    key: str                # the idempotency key it was sent under
    proposal_seq: int       # the chain entry that asked for it


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
                 chain: EvidenceChain | None = None, *, resume: bool = False,
                 mandate: dict | None = None,
                 release_remainder_within: timedelta | None = None) -> None:
        self.envelope = envelope
        self.release_remainder_within = release_remainder_within   # the human's deadline, if any
        self.rail = rail
        self.chain = chain or EvidenceChain.new(envelope.subject)
        self.engine = PolicyEngine(chain=self.chain)
        self.state = LedgerState()
        self.block: BlockRef | None = None
        self.block_payee: str | None = None      # who the standing block was placed for
        self.in_doubt: InDoubt | None = None     # a rail call whose outcome is unknown
        self.grant_key: str | None = None        # the human key that signed the grant, if any

        if resume:
            if not self.chain.entries:
                raise ValueError("resume needs a chain that already holds entries")
            self._replay()
        else:
            grant: dict = {"signed": False}
            if mandate is not None:
                grant = self._accept_mandate(envelope, mandate)
            self.chain.append(Actor.HUMAN, EventType.ENVELOPE,
                              {**envelope.to_payload(), "grant": grant})

    def _accept_mandate(self, envelope: Envelope, mandate: dict) -> dict:
        """Bind the session to a mandate the human signed, or refuse to start."""
        if verify_mandate(mandate) is not True:
            raise ValueError("the mandate is not validly signed; refusing to start a "
                             "session under a grant no human key vouches for")
        granted = from_open_payment_mandate(mandate)
        for name in ("max_total", "max_per_txn", "allowed_payees", "expires_at"):
            if getattr(granted, name) != getattr(envelope, name):
                raise ValueError(f"the envelope does not match the mandate it claims to "
                                 f"come from ({name} differs)")
        self.grant_key = mandate["cnf"]["jwk"]["x"]
        return {"signed": True, "kind": "ap2.open_payment_mandate",
                "public_key": self.grant_key, "mandate_signature": mandate["signature"]}

    # -- what the agent may ask for ---------------------------------------

    def reserve(self, amount: int, payee: str, reason: str = "") -> ActionResult:
        """Place a spending ceiling. The agent's authority becomes bank-held."""
        return self._attempt(Action.RESERVE, amount, payee, reason)

    def _standing_payee(self) -> str:
        """The payee the standing block was placed for (the grant's first, before any)."""
        return self.block_payee or self.envelope.allowed_payees[0]

    def debit(self, amount: int, reason: str = "") -> ActionResult:
        """Move the actual amount, once it is known."""
        return self._attempt(Action.DEBIT, amount, self._standing_payee(), reason)

    def release(self, amount: int | None = None, reason: str = "") -> ActionResult:
        """Hand the unspent difference back. The other half of amount-contingency."""
        amount = self.state.available if amount is None else amount
        return self._attempt(Action.RELEASE, amount, self._standing_payee(), reason)

    def status(self) -> ActionResult:
        return ActionResult(True, "current position", state=self._snapshot())

    # -- clocks: a hold that outlives what it was for --------------------------

    def obligations(self, now: datetime | None = None) -> list[Obligation]:
        """The deadlines running on this session's holds: the rail's own (where the registry
        cites one), the human's release deadline, and the end of the envelope. Reads the chain;
        moves no money."""
        policy = ObligationPolicy(release_remainder_within=self.release_remainder_within,
                                  resolve_by=self.envelope.expires_at)
        return _obligations(self.chain.entries, rail_id=self.rail.rail_id,
                            now=now or datetime.now(timezone.utc), policy=policy)

    def sweep(self, now: datetime | None = None) -> list[Obligation]:
        """Write each overdue obligation into the chain, once, and return the ones just written.

        Noticing an orphan is evidence too. Nothing is released and the rail is not asked: what
        to do about a forgotten remainder is a decision for a person or a later, separate step.
        """
        now = now or datetime.now(timezone.utc)
        noted = {(e.payload.get("kind"), e.payload.get("block_id"))
                 for e in self.chain.entries if e.event_type is EventType.OBLIGATION}
        fresh = []
        for o in overdue(self.obligations(now)):
            if (o.kind, o.block_id) in noted:
                continue
            self.chain.append(Actor.POLICY, EventType.OBLIGATION, o.to_payload(now))
            noted.add((o.kind, o.block_id))
            fresh.append(o)
        return fresh

    # -- re-approval: the cap is the human's, and only the human can raise it ----

    def propose_raise(self, new_max_total: int, reason: str = "") -> ActionResult:
        """The agent ASKS the human to widen the cap. It cannot grant this itself.

        When a fare comes in above the envelope's budget, the agent's only move is
        to surface it and request more room. That request is recorded as a proposal
        (actor=agent), like any other — it moves no money and grants no authority.
        The refusal that prompted it is already in the chain; this is what an agent
        does *instead of* overspending.
        """
        reason = _clip(reason, MAX_REASON)
        if not _is_paise(new_max_total):
            return self._refuse_unrepresentable("raise_ceiling", new_max_total, "", reason)
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

    def approve_raise_signed(self, consent: dict) -> ActionResult:
        """Apply a widening the HUMAN signed elsewhere. The session never sees a key.

        The consent must verify against the key it names, be for this subject,
        start from exactly the caps now in force (so an old one cannot be
        replayed), only widen, and — when the session began under a signed
        mandate — come from the key that signed it. Anything else is refused and
        the refusal is recorded.
        """
        problem = self._consent_problem(consent)
        if problem:
            self.chain.append(Actor.POLICY, EventType.REFUSAL, {
                "rule": "consent_rejected", "reason": _clip(problem, 200)})
            return ActionResult(False, f"consent rejected: {problem}",
                                state=self._snapshot())
        to = consent["to"]
        widened = self.envelope.widened(
            max_total=to["max_total"], max_per_txn=to["max_per_txn"],
            reason=_clip(consent.get("reason", ""), MAX_REASON))
        self.chain.append(Actor.HUMAN, EventType.ENVELOPE, consent)
        self.envelope = widened
        return ActionResult(
            True, f"cap raised to {widened.max_total}, signed by the human key "
                  f"{consent['cnf']['jwk']['x'][:12]}…", state=self._snapshot())

    def _consent_problem(self, consent: object) -> str:
        if not isinstance(consent, dict) or consent.get("event") != "envelope_widened":
            return "not an envelope_widened consent"
        if not verify_consent(consent):
            return "the signature does not verify against the key it names"
        if consent.get("subject") != self.envelope.subject:
            return "the consent is for a different subject"
        current = {"max_total": self.envelope.max_total,
                   "max_per_txn": self.envelope.max_per_txn}
        if consent.get("from") != current:
            return "the consent does not start from the caps now in force"
        to = consent.get("to")
        if not (isinstance(to, dict) and _is_paise(to.get("max_total"))
                and _is_paise(to.get("max_per_txn"))):
            return "the new caps must be whole numbers of paise"
        if to["max_total"] < current["max_total"] or to["max_per_txn"] < current["max_per_txn"]:
            return "a consent may only widen a cap, never lower it"
        if self.grant_key and consent["cnf"]["jwk"]["x"].lower() != self.grant_key.lower():
            return "the consent was not signed by the key that signed the grant"
        return ""

    def approve_raise(self, new_max_total: int, user_key, *,
                      new_max_per_txn: int | None = None,
                      reason: str = "") -> ActionResult:
        """Sign a widening with a key held HERE — for tests and local tools only.

        A hosted service must call `approve_raise_signed` with a signature the
        human's device made; a session that holds the human's private key can
        sign consent no human gave.
        """
        body = build_widening(
            self.envelope, new_max_total=new_max_total,
            new_max_per_txn=(new_max_total if new_max_per_txn is None else new_max_per_txn),
            reason=reason,
            public_key_hex=user_key.public_key().public_bytes_raw().hex())
        return self.approve_raise_signed(sign_consent(body, user_key))

    def record_malformed_call(self, tool: str, args: dict, why: str) -> None:
        """Log a tool call that never became a proposal.

        A call rejected at the argument boundary never reaches the policy
        engine, so nothing else would record it — and an unrecorded call from an
        untrusted model is exactly the gap the evidence chain exists to close.
        Garbled attempts are evidence too.
        """
        self.chain.append(Actor.POLICY, EventType.REFUSAL, {
            "rule": "malformed_tool_call",
            "tool": _clip(tool, MAX_NAME),
            "reason": _clip(why, 200),
            "arguments": {_clip(k, MAX_NAME): _clip(repr(v), 120)
                          for k, v in (args or {}).items()},
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

    def _refuse_unrepresentable(self, action: str, amount, payee: str,
                                reason: str) -> ActionResult:
        """An amount the chain cannot hold is refused — as evidence, not as a crash."""
        shown = _clip(repr(amount), 40)
        self.chain.append(Actor.AGENT, EventType.PROPOSAL, {
            "action": action, "amount": None, "amount_repr": shown,
            "payee": payee, "reason": reason})
        self.chain.append(Actor.POLICY, EventType.REFUSAL, {
            "rule": "unrepresentable_amount", "action": action, "amount_repr": shown,
            "reason": f"amount must be an integer number of paise, at most {MAX_SAFE_INT}"})
        return ActionResult(
            False, f"amount must be an integer number of paise, at most {MAX_SAFE_INT}",
            state=self._snapshot())

    def _attempt(self, action: Action, amount: int, payee: str,
                 reason: str) -> ActionResult:
        payee, reason = _clip(payee, MAX_PAYEE), _clip(reason, MAX_REASON)
        if not _is_paise(amount):
            return self._refuse_unrepresentable(action.value, amount, payee, reason)
        proposal = Proposal(action, amount, payee, self.rail.rail_id, memo=reason)

        # The LLM's ask is recorded before it is judged, so the audit trail
        # shows what was wanted as well as what was permitted.
        asked = self.chain.append(Actor.AGENT, EventType.PROPOSAL, {
            "action": action.value, "amount": amount,
            "payee": payee, "reason": reason,
        })

        if self.in_doubt is not None:
            return self._refuse_in_doubt()

        verdict: Verdict = self.engine.evaluate(proposal, self.envelope, self.state)
        if not verdict.allowed:
            return ActionResult(False, verdict.reason, verdict.citation,
                                self._snapshot())

        # Write-ahead: the intent is durable BEFORE the rail is asked, and the rail
        # is given a key derived from it. If the process dies from here on, the
        # chain says exactly what was in flight and the same key makes re-asking safe.
        key = f"{self.chain.subject}:{asked.seq}"
        self.chain.append(Actor.POLICY, EventType.RAIL_INTENT, {
            "action": action.value, "amount": amount, "payee": payee,
            "idempotency_key": key, "proposal_seq": asked.seq})
        return self._execute(action, amount, payee, key, asked.seq, verdict.citation)

    def _execute(self, action: Action, amount: int, payee: str, key: str,
                 proposal_seq: int, citation: str = "", *,
                 recovered: bool = False) -> ActionResult:
        """Ask the rail and record what came back — success, refusal, or silence."""
        common = {"action": action.value, "amount": amount, "payee": payee,
                  "idempotency_key": key, "proposal_seq": proposal_seq}
        try:
            block_id = self._apply(action, amount, payee, key)
        except RailError as exc:
            # Policy permitted it; the rail did not. Distinct failure, recorded
            # distinctly — this is where an under-set ceiling surfaces. A definite
            # refusal means nothing happened, so nothing is left in doubt.
            self.in_doubt = None
            self.chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
                **common, "outcome": "rail_rejected", "error": _clip(str(exc), 300)})
            return ActionResult(False, f"rail rejected: {exc}", state=self._snapshot())
        except Exception as exc:  # noqa: BLE001 — a timeout, a reset, a bug: unknown
            # The rail may or may not have acted. Say so, and stop moving money.
            self.in_doubt = InDoubt(action, amount, payee, key, proposal_seq)
            self.chain.append(Actor.POLICY, EventType.RAIL_IN_DOUBT, {
                **common, "error": _clip(f"{type(exc).__name__}: {exc}", 300)})
            return ActionResult(
                False, "the rail call did not complete and its outcome is unknown; "
                       "no further money actions until it is resolved",
                state=self._snapshot())

        self.in_doubt = None
        self.chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            **common, "outcome": "applied", "block_id": block_id,
            **({"recovered": True} if recovered else {})})
        return ActionResult(True, f"{action.value} of {amount} applied",
                            citation, self._snapshot())

    def _refuse_in_doubt(self) -> ActionResult:
        reason = ("an earlier rail call is in doubt: its outcome is unknown, so no "
                  "further money action is allowed until it is resolved")
        self.chain.append(Actor.POLICY, EventType.REFUSAL, {
            "rule": "in_doubt_unresolved", "reason": reason,
            "in_doubt_key": self.in_doubt.key if self.in_doubt else ""})
        return ActionResult(False, reason, state=self._snapshot())

    def resolve_in_doubt(self) -> ActionResult:
        """Re-issue the call whose outcome is unknown, under the same key.

        On a rail that honours idempotency keys the call acts once whichever side
        of the failure the first attempt landed on. A rail that cannot make that
        guarantee must be reconciled against its own state instead.
        """
        pending = self.in_doubt
        if pending is None:
            return ActionResult(True, "nothing in doubt", state=self._snapshot())
        if not getattr(self.rail, "supports_idempotency", False):
            return ActionResult(
                False, f"{self.rail.rail_id} cannot deduplicate a re-issued call; "
                       "reconcile against the rail's own state before resuming",
                state=self._snapshot())
        return self._execute(pending.action, pending.amount, pending.payee,
                             pending.key, pending.proposal_seq, recovered=True)

    def _apply(self, action: Action, amount: int, payee: str, key: str) -> str | None:
        """Move money on the rail and keep the ledger in step. Returns the block id."""
        kw = ({"idempotency_key": key}
              if getattr(self.rail, "supports_idempotency", False) else {})
        if action is Action.RESERVE:
            self.block = self.rail.reserve(amount, payee, **kw)
            self.block_payee = payee
            self.state.blocked += amount
            return self.block.block_id
        if action not in (Action.DEBIT, Action.RELEASE):
            raise RailError(f"{action.value} is not applicable in this session")
        if self.block is None:
            raise RailError(f"cannot {action.value} before reserving")
        block_id = self.block.block_id
        if action is Action.DEBIT:
            self.rail.debit(self.block, amount, **kw)
            self.state.debited += amount
        else:
            self.rail.release(self.block, amount, **kw)
            self.state.released += amount
        return block_id

    def _replay(self) -> None:
        """Rebuild the ledger and any call in doubt from a chain that already exists."""
        reached_outcome: set[int] = set()
        intents: dict[int, dict] = {}
        for e in self.chain.entries:
            p = e.payload
            if e.event_type is EventType.ENVELOPE and (p.get("grant") or {}).get("signed"):
                self.grant_key = p["grant"]["public_key"]
            if e.event_type is EventType.ENVELOPE and p.get("event") == "envelope_widened":
                self.envelope = self.envelope.widened(
                    max_total=p["to"]["max_total"], max_per_txn=p["to"]["max_per_txn"],
                    reason=p.get("reason", ""))
            elif e.event_type is EventType.RAIL_INTENT:
                intents[p["proposal_seq"]] = p
            elif e.event_type is EventType.RAIL_TRANSITION and "proposal_seq" in p:
                reached_outcome.add(p["proposal_seq"])
                if p.get("outcome") != "applied":
                    continue
                if p["action"] == Action.RESERVE.value:
                    self.block = self.rail.get_block(p["block_id"])
                    self.block_payee = p["payee"]
                    self.state.blocked += p["amount"]
                elif p["action"] == Action.DEBIT.value:
                    self.state.debited += p["amount"]
                elif p["action"] == Action.RELEASE.value:
                    self.state.released += p["amount"]
        dangling = [seq for seq in intents if seq not in reached_outcome]
        if dangling:
            p = intents[max(dangling)]
            self.in_doubt = InDoubt(Action(p["action"]), p["amount"], p["payee"],
                                    p["idempotency_key"], p["proposal_seq"])

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
