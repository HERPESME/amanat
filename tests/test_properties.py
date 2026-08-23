"""Invariants proven over all input sequences, not just examples.

The adversarial suite fires hand-picked attacks. This fires thousands of random
reserve/debit/release sequences through the real governed core and asserts the
money invariants hold after every single step. "Bounded and gated, here are
examples" becomes "bounded and gated, here is the invariant."
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st

from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail

PAYEE = "citycabs"
amounts = st.integers(min_value=1, max_value=2_000_00)


@st.composite
def scenario(draw):
    budget = draw(st.integers(50_00, 1_000_00))
    per_txn = draw(st.integers(1_00, budget))
    actions = draw(st.lists(
        st.tuples(st.sampled_from(["reserve", "debit", "release"]), amounts),
        max_size=14))
    return budget, per_txn, actions


def _session(budget, per_txn):
    env = Envelope(subject="prop", max_total=budget, max_per_txn=per_txn,
                   allowed_payees=[PAYEE],
                   expires_at=datetime.now(timezone.utc) + timedelta(hours=6))
    return AgentSession(env, SimulatedRail("sbmd", customer_balance=10_000_00)), env


@given(scenario())
@settings(max_examples=300, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_money_invariants_hold_after_every_step(sc):
    budget, per_txn, actions = sc
    s, env = _session(budget, per_txn)
    for kind, amt in actions:
        if kind == "reserve":
            s.reserve(amt, PAYEE, "r")
        elif kind == "debit":
            s.debit(amt, "d")
        else:
            s.release(amt, "rel")
        st_ = s.state
        # the amount-contingent invariant
        assert st_.debited + st_.released <= st_.blocked
        # the envelope budget is never exceeded
        assert st_.blocked <= env.max_total
        # you can never over-draw a block
        assert st_.available >= 0
        assert st_.debited >= 0 and st_.released >= 0
    # and the signed record of all of it always verifies
    s.chain.verify()


@given(scenario())
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_a_refused_action_never_moves_money(sc):
    budget, per_txn, actions = sc
    s, _ = _session(budget, per_txn)
    for kind, amt in actions:
        before = (s.state.blocked, s.state.debited, s.state.released,
                  s.rail.customer_balance)
        r = (s.reserve(amt, PAYEE, "r") if kind == "reserve"
             else s.debit(amt, "d") if kind == "debit"
             else s.release(amt, "rel"))
        after = (s.state.blocked, s.state.debited, s.state.released,
                 s.rail.customer_balance)
        if not r.ok:
            assert before == after, "a refused action changed the ledger"


@given(st.integers(1, 2_000_00), st.integers(50_00, 1_000_00))
@settings(max_examples=100, deadline=None)
def test_no_single_reserve_can_exceed_the_budget(amount, budget):
    s, env = _session(budget, min(budget, 800_00))
    r = s.reserve(amount, PAYEE, "r")
    if amount > env.max_total:
        assert r.ok is False
    assert s.state.blocked <= env.max_total
