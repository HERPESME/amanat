"""Containment tests for the governed execution core.

No API key, no network, no sampled token. If proving the agent is bounded
required a model, the agent would not be bounded — the guarantees live in
deterministic code, and these tests are what demonstrate that.
"""
import pytest

from amanat.evidence.chain import Actor, EventType
from amanat.orchestrator.session import AgentSession
from amanat.rails.simulator import SimulatedRail


@pytest.fixture
def session(envelope, verified_rail):
    return AgentSession(envelope, SimulatedRail(verified_rail,
                                                customer_balance=10_000_00))


class TestAmountContingentHappyPath:
    def test_reserve_debit_release_settles_and_returns_the_difference(self, session):
        assert session.reserve(620_00, "citycabs", "p95 fare estimate").ok
        assert session.debit(470_00, "metered fare").ok
        r = session.release(reason="trip complete")

        assert r.ok
        assert r.state["debited"] == 470_00
        assert r.state["released"] == 150_00
        assert r.state["stranded"] == 0

    def test_customer_balance_is_restored_for_the_unused_portion(self, session):
        start = session.rail.customer_balance
        session.reserve(620_00, "citycabs")
        session.debit(470_00)
        session.release()
        assert session.rail.customer_balance == start - 470_00


class TestTheAgentCannotEscapeTheEnvelope:
    def test_overspend_is_refused_not_raised(self, session):
        """Refusals are results the model can read, not exceptions it cannot."""
        r = session.reserve(5_000_00, "citycabs", "greedy")
        assert r.ok is False
        assert "budget" in r.detail.lower()

    def test_unlisted_payee_is_refused(self, session):
        assert session.reserve(100_00, "not-on-the-list").ok is False

    def test_debit_beyond_the_block_is_refused(self, session):
        session.reserve(300_00, "citycabs")
        assert session.debit(400_00).ok is False

    def test_a_refused_proposal_never_reaches_the_rail(self, session):
        """The architectural invariant: no path to money that skips policy."""
        before = session.rail.customer_balance
        session.reserve(5_000_00, "citycabs", "over budget")
        session.reserve(100_00, "not-on-the-list", "bad payee")
        assert session.rail.customer_balance == before
        assert session.block is None


class TestPolicyFailureIsDistinctFromRailFailure:
    def test_ceiling_set_too_low_is_caught_by_policy_before_the_rail(self, session):
        """Predictable failures are refused locally, without spending a rail call.

        The engine already knows the block size, so it can see this debit will
        fail. Refusing here is both correct and cheaper than letting the rail
        reject it.
        """
        session.reserve(380_00, "citycabs", "p50 estimate — too low")
        r = session.debit(470_00, "actual metered fare")
        assert r.ok is False
        assert "exceeds the remaining block" in r.detail
        outcomes = [e.payload.get("outcome") for e in session.chain.rail_transitions()]
        assert "rail_rejected" not in outcomes

    def test_customer_revoking_mid_flight_is_a_rail_rejection(self, session):
        """The failure policy cannot predict, and the one that matters.

        The customer can tear the block down from their own UPI app at any
        moment — including while the goods are already out for delivery. Nothing
        local knows this has happened, so it can only surface as a rail
        rejection, and it must be recorded as one.
        """
        session.reserve(620_00, "citycabs", "p95 estimate")
        session.rail.revoke(session.block)          # customer acts, out of band

        r = session.debit(470_00, "actual metered fare")
        assert r.ok is False
        assert "rail rejected" in r.detail

        outcomes = [e.payload.get("outcome") for e in session.chain.rail_transitions()]
        assert "rail_rejected" in outcomes

    def test_the_two_failure_kinds_are_separable_in_the_audit_trail(self, session):
        """A dispute needs to distinguish 'we refused' from 'the bank refused'."""
        session.reserve(620_00, "citycabs")
        session.debit(900_00)                        # policy refusal
        session.rail.revoke(session.block)
        session.debit(100_00)                        # rail rejection

        assert len(session.chain.refusals()) >= 1
        rail_rejections = [e for e in session.chain.rail_transitions()
                           if e.payload.get("outcome") == "rail_rejected"]
        assert len(rail_rejections) == 1


class TestEverythingIsRecorded:
    def test_proposal_and_verdict_are_recorded_separately(self, session):
        """Never collapse these — the separation IS the governance story."""
        session.reserve(5_000_00, "citycabs", "greedy")
        kinds = [(e.actor, e.event_type) for e in session.chain.entries]
        assert (Actor.AGENT, EventType.PROPOSAL) in kinds
        assert (Actor.POLICY, EventType.REFUSAL) in kinds

    def test_the_agents_stated_reason_is_preserved_verbatim(self, session):
        session.reserve(620_00, "citycabs", "p95 of predicted fare distribution")
        proposals = [e for e in session.chain.entries
                     if e.event_type is EventType.PROPOSAL]
        assert proposals[-1].payload["reason"] == "p95 of predicted fare distribution"

    def test_chain_verifies_after_a_full_session(self, session):
        session.reserve(620_00, "citycabs")
        session.debit(470_00)
        session.release()
        session.chain.verify()

    def test_packet_verifies_standalone(self, session):
        from amanat.evidence.chain import EvidenceChain
        session.reserve(620_00, "citycabs")
        session.debit(470_00)
        EvidenceChain.verify_packet(session.evidence_packet())

    def test_rail_transitions_record_what_the_money_did(self, session):
        """The surviving claim: evidence below authorization, not just of it."""
        session.reserve(620_00, "citycabs")
        session.debit(470_00)
        session.release()
        actions = [e.payload.get("action") for e in session.chain.rail_transitions()]
        assert actions == ["reserve", "debit", "release"]


class TestReturningTheRemainderCostsSomething:
    """Leg three of amount-contingent settlement is not free, and not uniform.

    A survey of six merchant-side PSPs found exactly one exposing a modify that
    preserves the mandate. On every other rail, handing back the change means
    revoking — which returns all of it and kills the block. OC-228 allows only
    one block at a time per merchant, so the next purchase needs fresh
    authentication.

    A pitch that says "we just release the difference" claims a call four of six
    PSPs do not expose. These tests keep the simulator honest about that.
    """

    def test_release_tears_the_block_down_on_a_teardown_only_rail(
            self, envelope, verified_rail):
        from amanat.rails.base import BlockState

        s = AgentSession(envelope, SimulatedRail(verified_rail,
                                                 customer_balance=10_000_00))
        s.reserve(620_00, "citycabs")
        s.debit(470_00)
        assert s.release(reason="trip complete").ok
        assert s.block.state is BlockState.REVOKED

    def test_partial_release_is_refused_on_a_teardown_only_rail(
            self, envelope, verified_rail):
        """You cannot hand back some of it. It is all, or nothing."""
        from amanat.rails.base import RailError

        rail = SimulatedRail(verified_rail, customer_balance=10_000_00)
        s = AgentSession(envelope, rail)
        s.reserve(620_00, "citycabs")
        s.debit(400_00)
        with pytest.raises(RailError, match="tears down all of it"):
            rail.release(s.block, 100_00)

    def test_release_preserves_the_block_where_the_rail_supports_it(
            self, envelope, modifiable_rail):
        from amanat.rails.base import BlockState

        s = AgentSession(envelope, SimulatedRail(modifiable_rail,
                                                 customer_balance=10_000_00))
        s.reserve(620_00, "citycabs")
        s.debit(470_00)
        s.release(reason="trip complete")
        assert s.block.state is not BlockState.REVOKED

    def test_the_customer_is_made_whole_either_way(self, envelope, verified_rail):
        """Whichever lever is used, the money comes back. Only the mandate differs."""
        rail = SimulatedRail(verified_rail, customer_balance=10_000_00)
        s = AgentSession(envelope, rail)
        s.reserve(620_00, "citycabs")
        s.debit(470_00)
        s.release()
        assert rail.customer_balance == 10_000_00 - 470_00
