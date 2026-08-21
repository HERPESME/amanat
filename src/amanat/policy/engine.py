"""Deterministic policy enforcement.

    The LLM proposes. The policy engine disposes. The rail enforces.

Three layers of authority, each stricter than the last, the outermost being a
bank. This module is the middle layer and contains no model call, no sampling,
and no network I/O — it is a pure function of (proposal, envelope, state) plus
the rail capability table.

Every decision, allowed or refused, is written to the evidence chain. Refusals
are recorded as carefully as debits: a chain of happy paths proves nothing about
governance, and the refusals are what the demo is built around.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.policy.envelope import Envelope, LedgerState
from amanat.rails.semantics import RAILS


class Action(Enum):
    RESERVE = "reserve"   # place a ceiling against the rail
    DEBIT = "debit"       # move the actual amount
    RELEASE = "release"   # return the unused difference
    REVOKE = "revoke"     # tear the block down


@dataclass(frozen=True)
class Proposal:
    """What the agent wants to do. Never trusted; always evaluated."""

    action: Action
    amount: int           # paise
    payee: str
    rail_id: str
    memo: str = ""


@dataclass
class Verdict:
    allowed: bool
    reason: str
    citation: str = ""
    url: str = ""
    quote: str = ""

    def __str__(self) -> str:
        head = f"{'ALLOW' if self.allowed else 'REFUSE'}: {self.reason}"
        return f"{head}\n    {self.citation}: “{self.quote}”" if self.quote else head


# Which rail capability each action depends on. A debit for less than the block
# is a *partial* debit, and several rails forbid exactly that.
_FULL_DEBIT = object()


class PolicyEngine:
    """Evaluates proposals against the envelope, then against the rail."""

    def __init__(self, chain: EvidenceChain) -> None:
        self.chain = chain

    def evaluate(self, proposal: Proposal, envelope: Envelope,
                 state: LedgerState, now: datetime | None = None) -> Verdict:
        verdict = self._decide(proposal, envelope, state, now)
        self._record(proposal, verdict)
        return verdict

    # -- layers ------------------------------------------------------------

    def _decide(self, p: Proposal, env: Envelope, state: LedgerState,
                now: datetime | None) -> Verdict:
        # Layer 0 — the envelope is a grant with a lifetime.
        if env.is_expired(now):
            return Verdict(False, "envelope has expired")

        # Layer 1 — is the human's grant wide enough?
        envelope_verdict = self._check_envelope(p, env, state)
        if not envelope_verdict.allowed:
            return envelope_verdict

        # Layer 2 — is this transition legal on this rail at all?
        return self._check_rail(p, state)

    def _check_envelope(self, p: Proposal, env: Envelope,
                        state: LedgerState) -> Verdict:
        if p.amount <= 0:
            return Verdict(False, "amount must be positive")

        if not env.permits_payee(p.payee):
            return Verdict(
                False,
                f"payee {p.payee!r} is not in the envelope's allowed payees")

        if p.action is Action.RESERVE:
            # Budget before per-transaction cap: the budget is the harder
            # boundary, so it should be the reason the caller is given.
            if state.blocked + p.amount > env.max_total:
                return Verdict(
                    False,
                    f"reserving {p.amount} would exceed the envelope budget "
                    f"{env.max_total} (already blocked {state.blocked})")
            if p.amount > env.max_per_txn:
                return Verdict(
                    False,
                    f"amount {p.amount} exceeds per-transaction cap {env.max_per_txn}")

        if p.action is Action.DEBIT:
            if p.amount > state.blocked - state.debited:
                return Verdict(
                    False,
                    f"debit {p.amount} exceeds the remaining block "
                    f"{state.blocked - state.debited}")

        if p.action is Action.RELEASE and p.amount > state.available:
            return Verdict(
                False,
                f"cannot release {p.amount}; only {state.available} is unspent")

        return Verdict(True, "within envelope")

    def _check_rail(self, p: Proposal, state: LedgerState) -> Verdict:
        rail = RAILS.get(p.rail_id)
        if rail is None:
            return Verdict(False, f"unknown rail {p.rail_id!r}")

        # Numeric bounds the rail itself enforces. Checking them here turns a
        # downstream rejection into an explainable refusal that carries the
        # circular deciding it — and stops the engine approving a reserve the
        # rail was always going to decline.
        if p.action is Action.RESERVE:
            breach = rail.exceeds("max_block_amount", state.blocked + p.amount)
            if breach is not None:
                return Verdict(False, breach.reason, breach.citation,
                               breach.url, breach.quote)

        # A debit for less than the standing block is a partial debit. Rails
        # differ on this, and the difference is load-bearing for the whole
        # amount-contingent design — so it is checked explicitly, with the
        # rail's own words attached to the refusal.
        if p.action is Action.DEBIT and p.amount < state.blocked - state.debited:
            decision = rail.explain("partial_debit")
            if not decision.allowed:
                return Verdict(
                    False,
                    f"{rail.display_name} does not permit partial debit "
                    f"({decision.reason})",
                    decision.citation, decision.url, decision.quote)

        return Verdict(True, f"permitted on {rail.display_name}")

    # -- evidence ----------------------------------------------------------

    def _record(self, p: Proposal, v: Verdict) -> None:
        payload = {
            "action": p.action.value,
            "proposed_amount": p.amount,
            "payee": p.payee,
            "rail": p.rail_id,
            "allowed": v.allowed,
            "reason": v.reason,
            "citation": v.citation,
            "quote": v.quote,
        }
        self.chain.append(
            Actor.POLICY,
            EventType.POLICY_DECISION if v.allowed else EventType.REFUSAL,
            payload,
        )
