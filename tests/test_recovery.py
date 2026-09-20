"""A crash or a lost response must never leave money moved without evidence, or
move it twice.

The session writes its intent to a durable chain *before* it calls the rail,
gives the rail a key derived from that intent, and records an outcome after. If
the process dies or the response is lost, the outcome is unknown — the session
says so (IN_DOUBT), refuses further money actions, and on recovery re-issues the
same call with the same key. An idempotent rail then applies it once, whichever
side of the crash it was on.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.evidence.chain import (
    Actor, ChainVerificationError, EventType, EvidenceChain,
)
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.base import RailError
from amanat.rails.simulator import SimulatedRail


def _env():
    return Envelope(subject="cab", max_total=100_000, max_per_txn=80_000,
                    allowed_payees=["citycabs"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=6))


# --------------------------------------------------------------------------- rail


class TestSimulatedRailIsIdempotent:
    def test_a_repeated_reserve_places_one_block(self):
        rail = SimulatedRail("sbmd", customer_balance=100_000)
        a = rail.reserve(60_000, "citycabs", idempotency_key="k1")
        b = rail.reserve(60_000, "citycabs", idempotency_key="k1")
        assert a is b
        assert len(rail.blocks) == 1 and rail.customer_balance == 40_000

    def test_a_repeated_debit_moves_money_once(self):
        rail = SimulatedRail("sbmd", customer_balance=100_000)
        ref = rail.reserve(60_000, "citycabs")
        rail.debit(ref, 47_000, idempotency_key="k2")
        rail.debit(ref, 47_000, idempotency_key="k2")
        assert ref.debited == 47_000

    def test_a_repeated_release_returns_money_once(self):
        rail = SimulatedRail("sbmd", customer_balance=100_000)
        ref = rail.reserve(60_000, "citycabs")
        rail.release(ref, idempotency_key="k3")
        rail.release(ref, idempotency_key="k3")
        assert rail.customer_balance == 100_000 and ref.released == 60_000

    def test_reusing_a_key_for_a_different_request_is_refused(self):
        rail = SimulatedRail("sbmd", customer_balance=100_000)
        rail.reserve(60_000, "citycabs", idempotency_key="k4")
        with pytest.raises(RailError, match="already used"):
            rail.reserve(10_000, "citycabs", idempotency_key="k4")

    def test_a_refused_call_does_not_burn_its_key(self):
        rail = SimulatedRail("sbmd", customer_balance=1_000)
        with pytest.raises(RailError):
            rail.reserve(60_000, "citycabs", idempotency_key="k5")      # insufficient funds
        rail.customer_balance = 100_000
        assert rail.reserve(60_000, "citycabs", idempotency_key="k5").ceiling == 60_000


# --------------------------------------------------------------------------- chain


class TestDurableChain:
    def test_an_entry_is_on_disk_before_append_returns(self, tmp_path):
        path = tmp_path / "c.jsonl"
        c = EvidenceChain.with_key("s", Ed25519PrivateKey.generate(), store=path)
        c.append(Actor.HUMAN, EventType.INTENT, {"a": 1})
        assert len(path.read_text().splitlines()) == 2      # a header record, then the entry

    def test_a_reloaded_chain_verifies_and_continues(self, tmp_path):
        path, key = tmp_path / "c.jsonl", Ed25519PrivateKey.generate()
        c = EvidenceChain.with_key("s", key, store=path)
        c.append(Actor.HUMAN, EventType.INTENT, {"a": 1})
        c.append(Actor.AGENT, EventType.PROPOSAL, {"b": 2})
        loaded = EvidenceChain.load(path, key)
        assert [e.hash for e in loaded.entries] == [e.hash for e in c.entries]
        loaded.append(Actor.RAIL, EventType.RAIL_TRANSITION, {"c": 3})
        loaded.verify()
        assert len(EvidenceChain.load(path, key).entries) == 3

    def test_a_torn_final_line_is_dropped_and_cleaned_up(self, tmp_path):
        path, key = tmp_path / "c.jsonl", Ed25519PrivateKey.generate()
        c = EvidenceChain.with_key("s", key, store=path)
        c.append(Actor.HUMAN, EventType.INTENT, {"a": 1})
        with open(path, "ab") as f:
            f.write(b'{"seq": 1, "prev_ha')                        # the process died mid-write
        loaded = EvidenceChain.load(path, key)
        assert len(loaded.entries) == 1
        loaded.append(Actor.AGENT, EventType.PROPOSAL, {"b": 2})    # must not glue onto the tear
        assert len(EvidenceChain.load(path, key).entries) == 2

    def test_loading_with_the_wrong_key_fails(self, tmp_path):
        path = tmp_path / "c.jsonl"
        c = EvidenceChain.with_key("s", Ed25519PrivateKey.generate(), store=path)
        c.append(Actor.HUMAN, EventType.INTENT, {"a": 1})
        with pytest.raises(ChainVerificationError):
            EvidenceChain.load(path, Ed25519PrivateKey.generate())

    def test_a_tampered_line_fails_on_load(self, tmp_path):
        path, key = tmp_path / "c.jsonl", Ed25519PrivateKey.generate()
        c = EvidenceChain.with_key("s", key, store=path)
        c.append(Actor.HUMAN, EventType.INTENT, {"amount": 1})
        lines = path.read_text().splitlines()
        row = json.loads(lines[1])                          # lines[0] is the header
        row["payload"]["amount"] = 999
        lines[1] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n")
        with pytest.raises(ChainVerificationError):
            EvidenceChain.load(path, key)


# ------------------------------------------------------------------------- session


class Crash(BaseException):
    """The process dying. Not an Exception, so no handler in the session runs."""


class FlakyRail:
    """A real simulated rail with one injected fault, at a chosen call.

    `crash_before` / `crash_after` kill the process on either side of the call;
    `lose_response_after` lets the call succeed and then raises, as a timeout
    does when the rail acted but the answer never arrived.
    """

    supports_idempotency = True

    def __init__(self, inner, *, crash_before=None, crash_after=None,
                 lose_response_after=None):
        self.inner = inner
        self.crash_before, self.crash_after = crash_before, crash_after
        self.lose_response_after = lose_response_after

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _call(self, method, *args, **kwargs):
        if self.crash_before == method:
            self.crash_before = None
            raise Crash()
        result = getattr(self.inner, method)(*args, **kwargs)
        if self.crash_after == method:
            self.crash_after = None
            raise Crash()
        if self.lose_response_after == method:
            self.lose_response_after = None
            raise TimeoutError("the rail acted but its answer never arrived")
        return result

    def reserve(self, *a, **k): return self._call("reserve", *a, **k)
    def debit(self, *a, **k): return self._call("debit", *a, **k)
    def release(self, *a, **k): return self._call("release", *a, **k)


def _steps(upto: str):
    plan = [("reserve", lambda s: s.reserve(60_000, "citycabs", "ceiling")),
            ("debit", lambda s: s.debit(47_000, "fare")),
            ("release", lambda s: s.release(reason="done"))]
    return [fn for name, fn in plan[:[n for n, _ in plan].index(upto) + 1]]


def _snapshot(session):
    st = session.state
    return (st.blocked, st.debited, st.released, session.rail.customer_balance,
            [(b.ceiling, b.debited, b.released, b.state) for b in session.rail.blocks])


def _reference(upto: str):
    """The same steps with no fault: what recovery must converge to."""
    s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=1_000_000))
    for step in _steps(upto):
        assert step(s).ok
    return _snapshot(s)


def _crash_then_restart(tmp_path, upto: str, **fault):
    key, path = Ed25519PrivateKey.generate(), tmp_path / "chain.jsonl"
    inner = SimulatedRail("sbmd", customer_balance=1_000_000)
    rail = FlakyRail(inner, **fault)
    session = AgentSession(_env(), rail,
                           chain=EvidenceChain.with_key("cab", key, store=path))
    with pytest.raises(Crash):
        for step in _steps(upto):
            step(session)
    restarted = AgentSession(_env(), inner, resume=True,
                             chain=EvidenceChain.load(path, key))
    return restarted


@pytest.mark.parametrize("action", ["reserve", "debit", "release"])
@pytest.mark.parametrize("site", ["crash_before", "crash_after"])
class TestARestartAfterACrashConvergesExactlyOnce:
    def test_the_interrupted_call_is_in_doubt_after_restart(self, tmp_path, action, site):
        s = _crash_then_restart(tmp_path, action, **{site: action})
        assert s.in_doubt is not None
        assert s.in_doubt.action.value == action

    def test_recovery_reaches_the_state_a_fault_free_run_reaches(self, tmp_path, action, site):
        s = _crash_then_restart(tmp_path, action, **{site: action})
        assert s.resolve_in_doubt().ok
        assert s.in_doubt is None
        assert _snapshot(s) == _reference(action)
        s.chain.verify()

    def test_the_interrupted_call_is_recorded_as_applied_exactly_once(self, tmp_path, action, site):
        s = _crash_then_restart(tmp_path, action, **{site: action})
        s.resolve_in_doubt()
        applied = [e for e in s.chain.entries
                   if e.event_type is EventType.RAIL_TRANSITION
                   and e.payload.get("action") == action
                   and e.payload.get("outcome") == "applied"]
        assert len(applied) == 1

    def test_resolving_twice_does_nothing_the_second_time(self, tmp_path, action, site):
        s = _crash_then_restart(tmp_path, action, **{site: action})
        s.resolve_in_doubt()
        before = _snapshot(s)
        again = s.resolve_in_doubt()
        assert again.ok and "nothing in doubt" in again.detail
        assert _snapshot(s) == before


class TestWhileAnOutcomeIsUnknown:
    def _lost_response(self, tmp_path):
        key, path = Ed25519PrivateKey.generate(), tmp_path / "c.jsonl"
        inner = SimulatedRail("sbmd", customer_balance=1_000_000)
        s = AgentSession(_env(), FlakyRail(inner, lose_response_after="reserve"),
                         chain=EvidenceChain.with_key("cab", key, store=path))
        return s, inner

    def test_a_lost_response_is_recorded_as_in_doubt_not_as_a_refusal_or_success(self, tmp_path):
        s, _ = self._lost_response(tmp_path)
        r = s.reserve(60_000, "citycabs", "ceiling")
        assert r.ok is False and "unknown" in r.detail
        kinds = [e.event_type for e in s.chain.entries]
        assert EventType.RAIL_IN_DOUBT in kinds
        assert not any(e.payload.get("outcome") == "applied"
                       for e in s.chain.rail_transitions())

    def test_no_further_money_action_is_allowed_until_it_is_resolved(self, tmp_path):
        s, inner = self._lost_response(tmp_path)
        s.reserve(60_000, "citycabs", "ceiling")
        balance = inner.customer_balance
        r = s.debit(10_000, "fare")
        assert r.ok is False and "in doubt" in r.detail
        assert inner.customer_balance == balance
        assert s.chain.refusals()[-1].payload["rule"] == "in_doubt_unresolved"

    def test_resolving_applies_the_call_once_although_the_rail_already_had(self, tmp_path):
        s, inner = self._lost_response(tmp_path)
        s.reserve(60_000, "citycabs", "ceiling")          # the rail DID place it; the answer was lost
        assert len(inner.blocks) == 1
        assert s.resolve_in_doubt().ok
        assert len(inner.blocks) == 1 and inner.customer_balance == 940_000
        assert s.state.blocked == 60_000 and s.state.available == 60_000
        assert s.debit(47_000, "fare").ok
