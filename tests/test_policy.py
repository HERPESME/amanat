"""Adversarial containment suite.

The policy engine is the only thing standing between a hallucinating LLM and a
payment rail. Every test here is an attack it must refuse.

Two independent layers must both pass before money moves:
  1. the human's envelope  (are you allowed to spend this?)
  2. the rail's semantics  (is this transition even legal on this rail?)

Layer 2 is the differentiated half — a refusal that cites the circular.
"""
from datetime import datetime, timedelta, timezone

import pytest

from amanat.evidence.chain import EvidenceChain, EventType
from amanat.policy.envelope import Envelope
from amanat.policy.engine import PolicyEngine, Proposal, Action, LedgerState


def _envelope(**kw):
    defaults = dict(
        subject="order-1",
        max_total=200_00,
        max_per_txn=150_00,
        allowed_payees=["merchant-a"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    return Envelope(**{**defaults, **kw})


@pytest.fixture
def engine():
    return PolicyEngine(chain=EvidenceChain.new("order-1"))


@pytest.fixture
def verified_rail():
    """A rail whose partial-debit support carries a primary citation.

    SBMD carries its own primary citation as of 21 Aug 2026, so this no longer
    stands in for it. It exists to keep the gate testable in isolation: the
    engine must key off the evidence tier, not off a hardcoded rail id.
    Registered and torn down so the real table stays honest.
    """
    from amanat.rails.semantics import RAILS, Capability, RailProfile, SourceTier

    rail = RailProfile(
        rail_id="_verified_fixture", display_name="Verified Fixture Rail",
        capabilities=[
            Capability(
                name="partial_debit", supported=True,
                source_tier=SourceTier.PRIMARY,
                citation="fixture", url="https://example.test",
                quote="partial debit against a standing block is permitted",
            ),
        ],
    )
    RAILS[rail.rail_id] = rail
    yield rail.rail_id
    del RAILS[rail.rail_id]


class TestEnvelopeContainment:
    def test_reserve_within_budget_is_allowed(self, engine):
        d = engine.evaluate(
            Proposal(Action.RESERVE, 100_00, "merchant-a", "sbmd"),
            _envelope(), LedgerState(),
        )
        assert d.allowed is True

    def test_exceeding_total_budget_is_refused(self, engine):
        d = engine.evaluate(
            Proposal(Action.RESERVE, 300_00, "merchant-a", "sbmd"),
            _envelope(), LedgerState(),
        )
        assert d.allowed is False
        assert "budget" in d.reason.lower()

    def test_exceeding_per_transaction_cap_is_refused(self, engine):
        d = engine.evaluate(
            Proposal(Action.RESERVE, 180_00, "merchant-a", "sbmd"),
            _envelope(), LedgerState(),
        )
        assert d.allowed is False

    def test_payee_outside_allowlist_is_refused(self, engine):
        d = engine.evaluate(
            Proposal(Action.RESERVE, 50_00, "merchant-evil", "sbmd"),
            _envelope(), LedgerState(),
        )
        assert d.allowed is False
        assert "payee" in d.reason.lower()

    def test_expired_envelope_refuses_everything(self, engine):
        env = _envelope(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        d = engine.evaluate(
            Proposal(Action.RESERVE, 10_00, "merchant-a", "sbmd"), env, LedgerState(),
        )
        assert d.allowed is False
        assert "expired" in d.reason.lower()

    def test_debit_cannot_exceed_the_block(self, engine):
        """Amount-contingent settlement: debit the actual, never more than the ceiling."""
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 120_00, "merchant-a", "sbmd"), _envelope(), state,
        )
        assert d.allowed is False
        assert "block" in d.reason.lower()

    def test_partial_debit_on_sbmd_is_permitted_and_rests_on_oc228(self, engine):
        """Resolved 21 Aug 2026 by reading NPCI/UPI/OC-228/2025-26 itself.

        This test asserted the opposite until that date, and the inversion is
        the finding. Amount-contingent settlement - block a ceiling, debit the
        actual - was REFUSED on SBMD because `partial_debit` rested on vendor
        documentation rather than on the circular, and unverified capabilities
        are never permitted. The system refused its own core mechanism, by
        design, until the evidence was in.

        The evidence is now in. OC-228, "Acquiring entities - Obligations to be
        fulfilled by UPI Acquirer (PA/ PG/ Banks)", item 5(d):

            "The current block limits (unutilised) are always checked before
             initiating a debit."

        immediately followed by 5(e):

            "Display of original block value, remaining balance, expiry date and
             transaction history (including creation, debits, modification)."

        Read honestly: no clause in OC-228, and none in NPCI/UPI/OC.No.200/
        2024-25, explicitly permits a debit smaller than the block - and none
        forbids one either. It is settled by necessary implication. An
        "unutilised" current block limit, checked before *each* debit, and a
        "remaining balance" that is a different quantity from the "original
        block value", both presuppose a block drawn down in parts. OC-200
        issuer obligation 1 closes it: the issuer "shall allow multiple debits
        against the block", which is unreachable if the first debit must take
        the whole block.

        The framing that goes with it: SBMD is not authorize-then-partial-
        capture. It is a pre-funded drawdown pool, so a debit smaller than the
        block is the ordinary case rather than a concession. What the rail does
        NOT do is hand the remainder back - see
        `sbmd.remainder_auto_released`, which stays False on the same primary
        evidence.
        """
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 67_00, "merchant-a", "sbmd"), _envelope(), state,
        )
        assert d.allowed is True

    def test_partial_debit_is_permitted_once_evidence_exists(self, engine, verified_rail):
        """The same proposal on an arbitrary rail whose support is cited.

        SBMD now passes on its own primary citation, so this is no longer the
        only green partial-debit path. It is kept as the regression that the
        gate is the *evidence tier* and not the rail id: any rail carrying a
        fact-tier `partial_debit` is allowed, any rail without one is not.
        """
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 67_00, "merchant-a", verified_rail), _envelope(), state,
        )
        assert d.allowed is True


class TestRailConformance:
    """Layer 2: refusals that cite the rail's own rules."""

    def test_partial_debit_refused_on_razorpay_with_citation(self, engine):
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 67_00, "merchant-a", "razorpay_auth_capture"),
            _envelope(), state,
        )
        assert d.allowed is False
        assert "equal to the amount authorized" in d.quote

    def test_full_debit_allowed_on_razorpay(self, engine):
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 100_00, "merchant-a", "razorpay_auth_capture"),
            _envelope(), state,
        )
        assert d.allowed is True

    def test_unknown_rail_is_refused(self, engine):
        d = engine.evaluate(
            Proposal(Action.RESERVE, 10_00, "merchant-a", "imaginary_rail"),
            _envelope(), LedgerState(),
        )
        assert d.allowed is False

    def test_unverified_capability_is_never_permitted(self, engine):
        """Absence of evidence is not permission."""
        state = LedgerState(blocked=100_00)
        d = engine.evaluate(
            Proposal(Action.DEBIT, 67_00, "merchant-a", "upi_otm"), _envelope(), state,
        )
        assert d.allowed is False
        assert "unverified" in d.reason.lower()


class TestRefusalsAreRecorded:
    def test_every_refusal_lands_in_the_evidence_chain(self, engine):
        engine.evaluate(
            Proposal(Action.RESERVE, 300_00, "merchant-a", "sbmd"),
            _envelope(), LedgerState(),
        )
        refusals = engine.chain.refusals()
        assert len(refusals) == 1
        assert refusals[0].payload["proposed_amount"] == 300_00

    def test_allowed_decisions_are_recorded_too(self, engine):
        engine.evaluate(
            Proposal(Action.RESERVE, 50_00, "merchant-a", "sbmd"),
            _envelope(), LedgerState(),
        )
        kinds = [e.event_type for e in engine.chain.entries]
        assert EventType.POLICY_DECISION in kinds

    def test_chain_still_verifies_after_a_refusal(self, engine):
        engine.evaluate(
            Proposal(Action.RESERVE, 999_00, "merchant-evil", "sbmd"),
            _envelope(), LedgerState(),
        )
        engine.chain.verify()


class TestNoLLMInTheEnforcementPath:
    def test_engine_is_deterministic(self, engine):
        """Same inputs, same verdict, every time. No sampling, no temperature."""
        args = (Proposal(Action.RESERVE, 100_00, "merchant-a", "sbmd"),
                _envelope(), LedgerState())
        verdicts = {engine.evaluate(*args).allowed for _ in range(20)}
        assert verdicts == {True}
