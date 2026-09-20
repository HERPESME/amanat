"""The envelope is a frozen grant; the ledger tracks money the way the rail does.

Four defects, each reproduced before it was fixed:

* the "frozen" envelope's payee list could be appended to in place;
* the budget counted every ceiling ever blocked, so released money kept
  consuming it;
* a second reserve replaced the session's only handle on the first block, which
  stayed standing on the rail while the session believed it was spendable;
* a debit was recorded against the first allowed payee, not the payee the block
  was placed for.
"""
from datetime import datetime, timedelta, timezone

import pytest

from amanat.evidence.chain import EventType
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.base import BlockState
from amanat.rails.semantics import RAILS
from amanat.rails.simulator import SimulatedRail


def _env(total=80_000, per_txn=30_000, payees=("citycabs",)):
    return Envelope(subject="t", max_total=total, max_per_txn=per_txn,
                    allowed_payees=list(payees),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=6))


def _session(env=None, rail_id="sbmd"):
    return AgentSession(env or _env(), SimulatedRail(rail_id, customer_balance=10_000_00))


class TestTheEnvelopeIsReallyFrozen:
    def test_payees_cannot_be_appended_to_in_place(self):
        env = _env()
        with pytest.raises(AttributeError):
            env.allowed_payees.append("attacker")
        assert env.permits_payee("attacker") is False

    def test_a_list_the_caller_keeps_is_copied_not_shared(self):
        payees = ["citycabs"]
        env = Envelope(subject="t", max_total=1, max_per_txn=1, allowed_payees=payees,
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        payees.append("attacker")
        assert env.permits_payee("attacker") is False


class TestBudgetCountsWhatIsCommittedNotWhatWasEverBlocked:
    def test_a_released_ceiling_stops_consuming_budget(self):
        """Budget 80,000; each ride blocks 30,000 but spends 10,000 and releases the rest."""
        s = _session()
        for ride in range(1, 7):                     # 6 x 10,000 = 60,000 spent
            assert s.reserve(30_000, "citycabs", f"ride {ride}").ok, f"ride {ride} refused"
            assert s.debit(10_000, "fare").ok
            assert s.release(reason="done").ok
        assert s.state.debited == 60_000

    def test_money_actually_spent_still_counts_against_the_budget(self):
        s = _session(_env(total=50_000, per_txn=50_000))
        assert s.reserve(30_000, "citycabs").ok
        assert s.debit(30_000, "fully consumed").ok
        refused = s.reserve(30_000, "citycabs", "second")     # 30,000 spent + 30,000 > 50,000
        assert refused.ok is False
        assert "budget" in refused.detail

    def test_the_budget_refusal_names_what_is_committed(self):
        s = _session(_env(total=50_000, per_txn=50_000))
        s.reserve(30_000, "citycabs")
        s.debit(10_000, "part")
        r = s.reserve(45_000, "citycabs", "too much")          # 10,000 spent + 20,000 held + 45,000
        assert r.ok is False
        assert "committed 30000" in r.detail


class TestOneStandingBlock:
    def test_a_second_reserve_is_refused_while_a_block_stands(self):
        s = _session()
        assert s.reserve(30_000, "citycabs").ok
        balance = s.rail.customer_balance
        r = s.reserve(20_000, "citycabs", "second block")
        assert r.ok is False
        assert s.rail.customer_balance == balance          # nothing moved

    def test_the_rail_and_the_session_agree_on_what_is_standing(self):
        s = _session()
        s.reserve(30_000, "citycabs")
        s.reserve(20_000, "citycabs", "refused")
        standing = [b for b in s.rail._blocks.values()
                    if b.state is BlockState.BLOCKED and b.available > 0]
        assert len(standing) == 1
        assert standing[0].available == s.state.available == 30_000

    def test_the_refusal_cites_the_rails_own_rule_where_it_has_one(self):
        s = _session(rail_id="sbmd")
        s.reserve(30_000, "citycabs")
        s.reserve(20_000, "citycabs", "second")
        refusal = s.chain.refusals()[-1].payload
        cap = RAILS["sbmd"].explain("single_active_block_per_merchant")
        assert cap.allowed is True                      # the rail does declare the rule
        assert refusal["citation"] == cap.citation != ""
        assert refusal["quote"] == cap.quote != ""

    def test_a_rail_without_the_rule_still_gets_one_block_per_session(self, verified_rail):
        s = _session(rail_id=verified_rail)
        assert s.reserve(30_000, "citycabs").ok
        r = s.reserve(20_000, "citycabs", "second")
        assert r.ok is False and "already standing" in r.detail

    def test_a_new_block_is_allowed_once_the_previous_is_fully_consumed(self):
        s = _session(_env(total=80_000, per_txn=50_000))
        assert s.reserve(30_000, "citycabs").ok
        assert s.debit(30_000, "all of it").ok
        assert s.reserve(20_000, "citycabs", "next").ok

    def test_a_new_block_is_allowed_after_the_previous_is_released(self):
        s = _session()
        assert s.reserve(30_000, "citycabs").ok
        assert s.release(reason="changed my mind").ok
        assert s.reserve(20_000, "citycabs", "next").ok


class TestDebitsAreAttributedToTheBlocksPayee:
    def test_a_debit_records_the_payee_the_block_was_placed_for(self):
        s = _session(_env(payees=("citycabs", "othercabs")))
        s.reserve(30_000, "othercabs", "block for othercabs")
        s.debit(20_000, "fare")
        proposal = [e for e in s.chain.entries if e.event_type is EventType.PROPOSAL][-1]
        assert proposal.payload["action"] == "debit"
        assert proposal.payload["payee"] == "othercabs"

    def test_a_release_records_the_same_payee(self):
        s = _session(_env(payees=("citycabs", "othercabs")))
        s.reserve(30_000, "othercabs")
        s.debit(20_000, "fare")
        s.release(reason="done")
        proposal = [e for e in s.chain.entries if e.event_type is EventType.PROPOSAL][-1]
        assert proposal.payload["action"] == "release"
        assert proposal.payload["payee"] == "othercabs"
