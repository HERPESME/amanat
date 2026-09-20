"""Obligation clocks: a hold that outlives what it was for is noticed, and the notice is evidence.

A ceiling placed on a rail is the customer's money, held. Some rails release it by themselves
after a documented time (Cashfree's seven days, Razorpay's three), some never do (an SBMD block
stays until it is revoked or expires), and whether a partial capture frees the remainder is on
some rails not known at all. So a hold carries clocks: the rail's own deadline, where the registry
cites one; the deadline the human gave (`release_remainder_within`); and the end of the human's
authority (the envelope's expiry). When one passes with money still held, the hold is an orphan.

The detector is a pure function of a chain's entries and a time. It reads the chain, moves no
money, and calls no model. `sweep` writes what it found into the chain, once.
"""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.policy.obligations import ObligationPolicy, holds, obligations, overdue
from amanat.rails.simulator import SimulatedRail

T0 = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)


def at(minutes=0, **kw):
    return (T0 + timedelta(minutes=minutes, **kw)).isoformat()


def transition(action, amount, when, block="b1", outcome="applied"):
    p = {"action": action, "amount": amount, "payee": "citycabs", "block_id": block, "outcome": outcome,
         "proposal_seq": 1, "idempotency_key": "k"}
    return {"event_type": "rail_transition", "timestamp": when, "payload": p}


def cab(*, debit=47_000, released=0):
    """Reserve ₹620, debit ₹470, at 10:00 / 10:30, optionally release some."""
    es = [transition("reserve", 62_000, at(0))]
    if debit:
        es.append(transition("debit", debit, at(30)))
    if released:
        es.append(transition("release", released, at(45)))
    return es


def one(entries, kind, *, now, rail="cashfree_preauth", policy=None):
    got = [o for o in obligations(entries, rail_id=rail, now=now, policy=policy or ObligationPolicy())
           if o.kind == kind]
    return got[0] if got else None


class TestHoldsAsTheChainTellsThem:
    def test_a_hold_is_what_was_reserved_less_what_was_drawn_and_returned(self):
        (h,) = holds(cab(released=5_000))
        assert (h.block_id, h.held, h.debited, h.released, h.remainder) == ("b1", 62_000, 47_000, 5_000, 10_000)
        assert h.placed_at == T0 and h.last_debit_at == T0 + timedelta(minutes=30) and not h.closed

    def test_a_hold_with_nothing_left_is_closed(self):
        assert holds(cab(debit=62_000))[0].closed
        assert holds(cab(released=15_000))[0].closed

    def test_a_rejected_or_unknown_outcome_is_not_money_moved(self):
        es = cab() + [transition("release", 15_000, at(50), outcome="rail_rejected"),
                      transition("release", 15_000, at(51), outcome="in_doubt")]
        assert holds(es)[0].remainder == 15_000

    def test_two_blocks_are_two_holds(self):
        es = cab() + [transition("reserve", 10_000, at(60), block="b2")]
        assert [h.block_id for h in holds(es)] == ["b1", "b2"]

    def test_only_the_sessions_own_vocabulary_makes_a_hold(self):
        es = [{"event_type": "rail_transition", "timestamp": at(0),
               "payload": {"transition": "captured", "amount": 1, "outcome": None}}]
        assert holds(es) == []

    def test_a_revoke_closes_the_hold(self):
        assert holds(cab() + [transition("revoke", 0, at(50))])[0].closed

    def test_other_entries_are_ignored(self):
        es = cab() + [{"event_type": "refusal", "timestamp": at(5), "payload": {"rule": "x"}}]
        assert len(holds(es)) == 1

    def test_an_unreadable_timestamp_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError, match="timestamp"):
            holds([transition("reserve", 1, "yesterday-ish")])

    def test_a_timestamp_without_a_timezone_is_refused_rather_than_read_as_local(self):
        with pytest.raises(ValueError, match="timezone"):
            holds([transition("reserve", 1, "2026-09-20T10:00:00")])

    def test_a_reserve_repeated_for_the_same_block_is_one_hold_not_two(self):
        """A recovered call can leave the same block reserved twice in a replayed record."""
        es = [transition("reserve", 62_000, at(0)), transition("reserve", 62_000, at(1))]
        (h,) = holds(es)
        assert h.held == 62_000 and h.placed_at == T0


class TestTheRailsOwnDeadline:
    """Cashfree documents that an authorisation not captured within seven days is released."""

    def test_the_clock_runs_from_placement_for_the_documented_number_of_days(self):
        o = one(cab(), "rail_hold_expiry", now=T0 + timedelta(hours=1))
        assert o.due_at == T0 + timedelta(days=7) and o.status == "pending" and o.remainder == 15_000

    def test_it_says_what_it_rests_on(self):
        o = one(cab(), "rail_hold_expiry", now=T0)
        assert "Cashfree" in o.basis and "7 days" in o.basis
        assert "If not captured within 7 days" in o.quote and o.citation

    def test_past_the_deadline_with_money_still_shown_held_it_is_overdue(self):
        o = one(cab(), "rail_hold_expiry", now=T0 + timedelta(days=7))
        assert o.status == "overdue"

    def test_one_second_before_the_deadline_it_is_still_pending(self):
        o = one(cab(), "rail_hold_expiry", now=T0 + timedelta(days=7) - timedelta(seconds=1))
        assert o.status == "pending"

    def test_a_hold_that_was_closed_has_met_it_however_late(self):
        o = one(cab(released=15_000), "rail_hold_expiry", now=T0 + timedelta(days=30))
        assert o.status == "met"

    def test_a_rail_with_no_documented_expiry_has_no_such_clock(self):
        assert one(cab(), "rail_hold_expiry", now=T0 + timedelta(days=365), rail="sbmd") is None

    def test_razorpays_three_days_are_its_own(self):
        o = one(cab(), "rail_hold_expiry", now=T0, rail="razorpay_auth_capture")
        assert o.due_at == T0 + timedelta(days=3)

    def test_a_rail_that_is_not_in_the_registry_has_no_rail_clock_and_is_not_an_error(self):
        assert obligations(cab(), rail_id="no_such_rail", now=T0, policy=ObligationPolicy()) == []


class TestTheDeadlineTheHumanGave:
    POLICY = ObligationPolicy(release_remainder_within=timedelta(minutes=15))

    def test_without_a_policy_there_is_no_such_clock(self):
        assert one(cab(), "remainder_release", now=T0 + timedelta(hours=9)) is None

    def test_it_runs_from_the_last_debit(self):
        due = T0 + timedelta(minutes=30) + timedelta(minutes=15)
        assert one(cab(), "remainder_release", now=due - timedelta(seconds=1), policy=self.POLICY).status == "pending"
        o = one(cab(), "remainder_release", now=due, policy=self.POLICY)
        assert o.status == "overdue" and o.due_at == due and o.remainder == 15_000

    def test_a_later_debit_restarts_the_clock(self):
        es = cab(debit=30_000) + [transition("debit", 17_000, at(50))]
        o = one(es, "remainder_release", now=T0 + timedelta(minutes=50, seconds=1), policy=self.POLICY)
        assert o.due_at == T0 + timedelta(minutes=65) and o.status == "pending"

    def test_with_no_debit_yet_it_runs_from_placement(self):
        o = one(cab(debit=0), "remainder_release", now=T0 + timedelta(minutes=16), policy=self.POLICY)
        assert o.due_at == T0 + timedelta(minutes=15) and o.status == "overdue" and o.remainder == 62_000

    def test_releasing_the_remainder_meets_it(self):
        o = one(cab(released=15_000), "remainder_release", now=T0 + timedelta(days=1), policy=self.POLICY)
        assert o.status == "met"

    def test_it_says_it_is_the_humans_deadline(self):
        assert "policy" in one(cab(), "remainder_release", now=T0, policy=self.POLICY).basis.lower()

    def test_a_rail_that_releases_the_remainder_by_itself_leaves_nothing_to_do(self):
        """Only where the registry says, on evidence usable as fact, that the rail returns it."""
        from amanat.rails import semantics as s
        rail = s.RailProfile("auto", "Auto", [s.Capability(
            name="remainder_auto_released", supported=True, source_tier=s.SourceTier.PRIMARY,
            citation="c", url="u", quote="the rest is returned")])
        assert one(cab(), "remainder_release", now=T0 + timedelta(days=1), rail="auto", policy=self.POLICY,
                   ) is not None                       # not in RAILS: no evidence, so the clock still runs
        s.RAILS["auto"] = rail
        try:
            assert one(cab(), "remainder_release", now=T0 + timedelta(days=1), rail="auto", policy=self.POLICY) is None
        finally:
            del s.RAILS["auto"]

    def test_an_unverified_claim_of_auto_release_does_not_switch_the_clock_off(self):
        """Cashfree's remainder release is UNVERIFIED, so the human's deadline still applies."""
        assert one(cab(), "remainder_release", now=T0 + timedelta(days=1), policy=self.POLICY).status == "overdue"

    def test_sbmd_never_frees_the_remainder_so_the_clock_applies(self):
        assert one(cab(), "remainder_release", now=T0 + timedelta(days=1), rail="sbmd", policy=self.POLICY).status == "overdue"


class TestTheEndOfTheHumansAuthority:
    END = T0 + timedelta(hours=6)

    def test_nothing_may_stay_held_past_the_grant(self):
        p = ObligationPolicy(resolve_by=self.END)
        assert one(cab(), "resolve_by", now=self.END - timedelta(seconds=1), policy=p).status == "pending"
        o = one(cab(), "resolve_by", now=self.END, policy=p)
        assert o.status == "overdue" and o.due_at == self.END

    def test_a_closed_hold_has_nothing_to_resolve(self):
        assert one(cab(released=15_000), "resolve_by", now=self.END + timedelta(days=1),
                   policy=ObligationPolicy(resolve_by=self.END)).status == "met"

    def test_no_end_no_clock(self):
        assert one(cab(), "resolve_by", now=T0 + timedelta(days=99)) is None


class TestReadingTheResult:
    def test_overdue_returns_only_the_orphans_in_order_of_their_deadline(self):
        p = ObligationPolicy(release_remainder_within=timedelta(minutes=15), resolve_by=T0 + timedelta(hours=6))
        got = overdue(obligations(cab(), rail_id="cashfree_preauth", now=T0 + timedelta(hours=7), policy=p))
        assert [o.kind for o in got] == ["remainder_release", "resolve_by"]      # 10:45 then 16:00; the 7 days is not yet
        assert all(o.status == "overdue" for o in got)

    def test_the_order_is_by_deadline_not_by_the_order_the_clocks_were_listed_in(self):
        """Each hold lists its clocks in a fixed order; the orphans come back soonest-first."""
        p = ObligationPolicy(release_remainder_within=timedelta(minutes=15), resolve_by=T0 + timedelta(minutes=20))
        got = overdue(obligations(cab(), rail_id="cashfree_preauth", now=T0 + timedelta(hours=1), policy=p))
        assert [o.kind for o in got] == ["resolve_by", "remainder_release"]          # 10:20 before 10:45
        assert [o.due_at for o in got] == sorted(o.due_at for o in got)

    def test_holds_are_ordered_across_blocks_by_deadline_too(self):
        early = [transition("reserve", 10_000, at(0), block="early")]
        late = [transition("reserve", 10_000, at(120), block="late")]
        p = ObligationPolicy(release_remainder_within=timedelta(minutes=5))
        got = overdue(obligations(late + early, rail_id="sbmd", now=T0 + timedelta(hours=5), policy=p))
        assert [o.block_id for o in got] == ["early", "late"]

    def test_a_naive_time_is_refused_rather_than_read_as_local(self):
        with pytest.raises(ValueError, match="timezone"):
            obligations(cab(), rail_id="sbmd", now=datetime(2026, 9, 20, 10, 0), policy=ObligationPolicy())

    def test_it_accepts_a_packets_dict_entries_and_a_chains_entries_alike(self):
        chain = EvidenceChain.with_key("s", Ed25519PrivateKey.generate())
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "action": "reserve", "amount": 62_000, "payee": "p", "block_id": "b9", "outcome": "applied"})
        via_entries = holds(chain.entries)
        via_packet = holds(chain.export_packet()["entries"])
        assert [(h.block_id, h.held) for h in via_entries] == [(h.block_id, h.held) for h in via_packet] == [("b9", 62_000)]


# ------------------------------------------------------------------------- the session

def _env(hours=6):
    return Envelope(subject="cab", max_total=100_000, max_per_txn=80_000, allowed_payees=["citycabs"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=hours))


def _session(**kw):
    return AgentSession(_env(), SimulatedRail("sbmd", customer_balance=1_000_000), **kw)


def _drawn(**kw):
    s = _session(**kw)
    assert s.reserve(62_000, "citycabs", "ceiling").ok and s.debit(47_000, "fare").ok
    return s


class TestTheSessionKnowsItsClocks:
    def test_a_forgotten_remainder_is_an_orphan_once_the_humans_deadline_passes(self):
        s = _drawn(release_remainder_within=timedelta(minutes=15))
        soon = datetime.now(timezone.utc)
        assert [o.status for o in s.obligations(now=soon) if o.kind == "remainder_release"] == ["pending"]
        later = soon + timedelta(minutes=16)
        (o,) = [o for o in s.obligations(now=later) if o.kind == "remainder_release"]
        assert o.status == "overdue" and o.remainder == 15_000

    def test_the_end_of_the_envelope_is_a_clock_without_being_asked(self):
        s = _drawn()
        later = datetime.now(timezone.utc) + timedelta(hours=7)
        assert [o.kind for o in overdue(s.obligations(now=later))] == ["resolve_by"]

    def test_releasing_meets_every_clock(self):
        s = _drawn(release_remainder_within=timedelta(minutes=15))
        assert s.release(reason="done").ok
        later = datetime.now(timezone.utc) + timedelta(days=30)
        assert overdue(s.obligations(now=later)) == [] and {o.status for o in s.obligations(now=later)} == {"met"}

    def test_the_default_time_is_now(self):
        assert isinstance(_drawn().obligations(), list)


class TestSweepingWritesWhatItFindsOnce:
    def _orphan(self):
        s = _drawn(release_remainder_within=timedelta(minutes=15))
        return s, datetime.now(timezone.utc) + timedelta(minutes=20)

    def test_an_overdue_hold_becomes_an_entry_in_the_chain(self):
        s, later = self._orphan()
        noted = s.sweep(now=later)
        assert [o.kind for o in noted] == ["remainder_release"]
        e = s.chain.entries[-1]
        assert e.event_type is EventType.OBLIGATION and e.actor is Actor.POLICY
        p = e.payload
        assert p["rule"] == "obligation_overdue" and p["kind"] == "remainder_release" and p["remainder"] == 15_000
        assert p["block_id"] == s.block.block_id and p["noticed_at"] == later.isoformat() and p["basis"]
        s.chain.verify()

    def test_sweeping_again_records_nothing_new(self):
        s, later = self._orphan()
        s.sweep(now=later)
        n = len(s.chain.entries)
        assert s.sweep(now=later + timedelta(minutes=5)) == [] and len(s.chain.entries) == n

    def test_a_pending_hold_is_not_recorded(self):
        s = _drawn(release_remainder_within=timedelta(minutes=15))
        n = len(s.chain.entries)
        assert s.sweep(now=datetime.now(timezone.utc)) == [] and len(s.chain.entries) == n

    def test_a_released_hold_is_not_recorded(self):
        s, later = self._orphan()
        s.release(reason="done")
        n = len(s.chain.entries)
        assert s.sweep(now=later) == [] and len(s.chain.entries) == n

    def test_each_kind_is_noted_once_per_hold(self):
        s, _ = self._orphan()
        far = datetime.now(timezone.utc) + timedelta(hours=7)
        assert sorted(o.kind for o in s.sweep(now=far)) == ["remainder_release", "resolve_by"]
        assert s.sweep(now=far) == []

    def test_sweeping_moves_no_money_and_asks_the_rail_nothing(self):
        s, later = self._orphan()
        before, calls = s._snapshot(), len(s.rail.blocks)
        s.sweep(now=later)
        assert s._snapshot() == before and len(s.rail.blocks) == calls
        assert s.rail.blocks[0].debited == 47_000 and s.rail.blocks[0].released == 0

    def test_a_restarted_session_does_not_note_the_same_orphan_again(self, tmp_path):
        key, path = Ed25519PrivateKey.generate(), tmp_path / "c.jsonl"
        inner = SimulatedRail("sbmd", customer_balance=1_000_000)
        s = AgentSession(_env(), inner, chain=EvidenceChain.with_key("cab", key, store=path),
                         release_remainder_within=timedelta(minutes=15))
        s.reserve(62_000, "citycabs", "c"); s.debit(47_000, "fare")
        later = datetime.now(timezone.utc) + timedelta(minutes=20)
        assert len(s.sweep(now=later)) == 1
        again = AgentSession(_env(), inner, resume=True, chain=EvidenceChain.load(path, key),
                             release_remainder_within=timedelta(minutes=15))
        assert again.sweep(now=later) == []

    def test_the_obligation_entry_survives_export_and_verification(self):
        s, later = self._orphan()
        s.sweep(now=later)
        packet = s.chain.export_packet()
        assert EvidenceChain.verify_packet(packet).entries == len(packet["entries"])
        assert any(e["event_type"] == "obligation" for e in packet["entries"])

    def test_the_standalone_page_hashes_an_obligation_entry_as_python_does(self):
        from tests.support_node import run_page_js
        s, later = self._orphan()
        s.sweep(now=later)
        packet = s.chain.export_packet()
        canon = "page.canonical" if packet["canonicalization"] == "jcs-int" else "page.canonicalV1"
        out = run_page_js(f"e => sha256(page.digestInputFor(e, {canon}))", packet["entries"])
        assert all("error" not in r for r in out), out
        assert [r["ok"] for r in out] == [e["hash"] for e in packet["entries"]]

    def test_a_chain_saved_with_an_obligation_note_loads_again(self, tmp_path):
        key, path = Ed25519PrivateKey.generate(), tmp_path / "c.jsonl"
        s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=1_000_000),
                         chain=EvidenceChain.with_key("cab", key, store=path),
                         release_remainder_within=timedelta(minutes=15))
        s.reserve(62_000, "citycabs", "c"); s.debit(47_000, "fare")
        s.sweep(now=datetime.now(timezone.utc) + timedelta(minutes=20))
        loaded = EvidenceChain.load(path, key)
        assert loaded.entries[-1].event_type is EventType.OBLIGATION
