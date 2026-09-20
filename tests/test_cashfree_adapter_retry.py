"""The Cashfree adapter survives a retry: a call repeated after a lost response acts once.

The session's crash recovery re-issues a call whose outcome is unknown, under the same key, and it may
do so only on a rail that says repeating is safe. Cashfree's sandbox was measured on 21 Sep 2026 (the
`cashfree_preauth` rows `idempotent_capture_replay`, `idempotent_void_replay`, `duplicate_order_refused`,
`payment_replay_refused`, `idempotency_key_reuse_refused`). The adapter's claim to idempotency is derived
from those rows, and what it does with them is tested here against a stateful fake that models exactly
those answers — including the ways a reservation can die half-way.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.evidence.chain import EventType, EvidenceChain
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.base import BlockRef, BlockState, RailError, RailOutcomeUnknown
from amanat.rails.cashfree import CashfreePreAuthRail
from amanat.rails.semantics import RAILS


class FakeSandbox:
    """Cashfree's sandbox as measured: what each call answers, and what state it leaves.

    Every branch is one of the recorded probe answers; a comment names it. Faults are injected
    around a call: the call acts and its response is lost (`lose_after`), or it never happens
    (`die_before`), which are the two things a timeout cannot tell apart.
    """

    def __init__(self):
        self.orders, self.payments, self.keys = {}, {}, {}
        self.calls = []
        self.lose_after, self.die_before, self.server_error = set(), set(), {}

    # -- transport seam ---------------------------------------------------------------
    def __call__(self, method, path, *, version=None, json=None, headers=None):
        tag = self._tag(method, path, json)
        self.calls.append((method, path, json, headers))
        if tag in self.die_before:
            self.die_before.discard(tag)
            raise httpx.ReadTimeout("timed out before the call reached the rail")
        if tag in self.server_error:
            return self.server_error.pop(tag), {"message": "internal error"}
        status, body = self._handle(method, path, json, headers or {})
        if tag in self.lose_after:
            self.lose_after.discard(tag)
            raise httpx.ReadTimeout("the response was lost after the rail acted")
        return status, body

    @staticmethod
    def _tag(method, path, json):
        if path.endswith("/authorization"):
            return json["action"].lower()          # "capture" | "void"
        return {("POST", "/orders"): "create", ("POST", "/orders/sessions"): "pay",
                ("POST", "/simulate"): "simulate"}.get((method, path), f"{method} {path}")

    # -- the sandbox's answers --------------------------------------------------------
    def _handle(self, method, path, body, headers):
        if (method, path) == ("POST", "/orders"):
            if body["order_id"] in self.orders:      # duplicate_order_refused: HTTP 409
                return 409, {"code": "order_already_exists", "message": "order with same id is already present"}
            o = {"order_id": body["order_id"], "order_amount": body["order_amount"], "order_status": "ACTIVE",
                 "order_note": body["order_note"], "payment_session_id": f"session_{len(self.orders)}"}
            self.orders[body["order_id"]] = o
            return 200, dict(o)
        if (method, path) == ("POST", "/orders/sessions"):
            order = next((o for o in self.orders.values() if o["payment_session_id"] == body["payment_session_id"]), None)
            if order is None or order["order_status"] != "ACTIVE" or self.payments.get(order["order_id"]):
                return 400, {"code": "order_inactive", "message": "order is no longer active"}   # payment_replay_refused
            pid = 9000 + sum(len(v) for v in self.payments.values())
            self.payments.setdefault(order["order_id"], []).append(
                {"cf_payment_id": pid, "payment_status": "NOT_ATTEMPTED", "is_captured": False,
                 "payment_amount": order["order_amount"], "authorization": {"action": None, "captured_amount": None}})
            return 200, {"cf_payment_id": pid}
        if (method, path) == ("POST", "/simulate"):
            for oid, ps in self.payments.items():
                for p in ps:
                    if str(p["cf_payment_id"]) == str(body["entity_id"]):
                        if p["payment_status"] == "SUCCESS":
                            return 400, {"code": "transaction_status_invalid",
                                         "message": "Transaction status cannot be changed from SUCCESS to SUCCESS"}
                        p["payment_status"] = "SUCCESS"
                        self.orders[oid]["order_status"] = "PAID"
                        return 200, {"entity": "PAYMENTS"}
            return 404, {"message": "no such payment"}
        if method == "GET" and path.endswith("/payments"):
            return 200, [dict(p) for p in self.payments.get(path.split("/")[2], [])]
        if method == "GET" and path.startswith("/orders/") and path.count("/") == 2:
            o = self.orders.get(path.split("/")[2])
            return (200, dict(o)) if o else (404, {"message": "order not found"})
        if method == "POST" and path.endswith("/authorization"):
            return self._authorise(path.split("/")[2], body, headers)
        raise AssertionError((method, path, body))

    def _authorise(self, oid, body, headers):
        key, sig = headers.get("x-idempotency-key"), (oid, body["action"], body.get("amount"))
        if key:
            if key in self.keys:
                first_sig, first = self.keys[key]
                if first_sig != sig:         # idempotency_key_reuse_refused: HTTP 422
                    return 422, {"code": "request_invalid", "type": "idempotency_error",
                                 "message": "invalid body in request for x-idempotency-key"}
                return 200, first            # idempotent_capture_replay / idempotent_void_replay: the first result
        pay = self.payments[oid][0]
        auth = pay["authorization"]
        if body["action"] == "CAPTURE":
            if auth.get("action") == "VOID":
                return 400, {"message": "transaction is already voided"}
            if auth.get("action") == "CAPTURE":
                return 400, {"message": "Duplicate capture_id present"}
            auth.update(action="CAPTURE", status="SUCCESS", captured_amount=body["amount"])
            pay["is_captured"] = True
            result = {"authorization": {"action": "CAPTURE", "status": "SUCCESS", "captured_amount": body["amount"]}}
        else:
            if auth.get("action") == "VOID":
                return 400, {"code": "order_id_voided", "message": "transaction is already voided"}
            if auth.get("action") == "CAPTURE":
                return 400, {"message": "Capture request already exist for the void"}
            auth.update(action="VOID", status="SUCCESS")
            result = {"authorization": {"action": "VOID", "status": "SUCCESS"}}
        if key:
            self.keys[key] = (sig, result)
        return 200, result

    # -- what a reader of the rail would count ----------------------------------------
    def captures(self, oid):
        return [p for p in self.payments.get(oid, []) if p["authorization"].get("action") == "CAPTURE"]

    def n(self, tag):
        return sum(1 for m, p, j, h in self.calls if self._tag(m, p, j) == tag)


def rail_on(sandbox):
    """The real adapter, its transport replaced by the fake."""
    class Rail(CashfreePreAuthRail):
        def _call(self, method, path, *, version=None, **kw):
            return sandbox(method, path, version=version, **kw)
    return Rail(client_id="id", client_secret="secret")


@pytest.fixture
def sandbox():
    return FakeSandbox()


@pytest.fixture
def rail(sandbox):
    return rail_on(sandbox)


def reserved(rail, key="k-reserve", ceiling=62_000):
    return rail.reserve(ceiling, "citycabs", idempotency_key=key)


# ----------------------------------------------------------------------------------
class TestTheClaimIsTheRegistrys:
    """`supports_idempotency` is not a constant a person typed: it is what the measured rows say."""

    ROWS = ("idempotent_capture_replay", "idempotent_void_replay", "duplicate_order_refused",
            "payment_replay_refused", "idempotency_key_reuse_refused")

    def test_it_claims_idempotency_because_every_measured_row_says_a_retry_is_safe(self, rail):
        profile = RAILS["cashfree_preauth"]
        for name in self.ROWS:
            cap = profile.capabilities[name]
            assert cap.supported is True and cap.source_tier.value == "observed", name
        assert rail.supports_idempotency is True

    def test_it_stops_claiming_it_if_a_row_stops_saying_so(self, rail, monkeypatch):
        cap = RAILS["cashfree_preauth"].capabilities["idempotent_void_replay"]
        monkeypatch.setattr(cap, "supported", False)
        assert rail.supports_idempotency is False

    def test_it_stops_claiming_it_if_a_row_is_no_longer_a_fact(self, rail, monkeypatch):
        from amanat.rails.semantics import SourceTier
        cap = RAILS["cashfree_preauth"].capabilities["duplicate_order_refused"]
        monkeypatch.setattr(cap, "source_tier", SourceTier.UNVERIFIED)
        assert rail.supports_idempotency is False


# ----------------------------------------------------------------------------------
class TestCaptureAndVoidCarryAnOrderScopedKey:
    def test_a_capture_with_a_key_sends_it_scoped_to_the_order(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="k1")
        header = sandbox.calls[-1][3]["x-idempotency-key"]
        assert header == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ref.block_id}:k1"))

    def test_the_same_key_on_another_order_is_a_different_header(self, sandbox, rail):
        """Two processes that happen to choose one key for two orders must not be answered alike."""
        a, b = reserved(rail, "a"), reserved(rail, "b")
        rail.debit(a, 47_000, idempotency_key="same")
        rail_on(sandbox).debit(b, 47_000, idempotency_key="same")
        headers = [c[3]["x-idempotency-key"] for c in sandbox.calls if c[1].endswith("/authorization")]
        assert len(set(headers)) == 2, "one key must never answer for another order"

    def test_one_key_is_one_request_in_a_process_so_a_second_order_under_it_is_refused_here(self, rail):
        a, b = reserved(rail, "a"), reserved(rail, "b")
        rail.debit(a, 47_000, idempotency_key="same")
        with pytest.raises(RailError, match="already used for a different request"):
            rail.debit(b, 47_000, idempotency_key="same")

    def test_no_key_no_header(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000)
        assert not (sandbox.calls[-1][3] or {}).get("x-idempotency-key")

    def test_a_void_with_a_key_sends_it_too(self, sandbox, rail):
        ref = reserved(rail)
        rail.release(ref, idempotency_key="k-void")
        assert "x-idempotency-key" in sandbox.calls[-1][3]


# ----------------------------------------------------------------------------------
class TestADebitRepeatedAfterALostResponseActsOnce:
    def test_the_rail_acted_and_the_answer_was_lost_so_the_retry_returns_the_first_result(self, sandbox, rail):
        ref = reserved(rail)
        sandbox.lose_after.add("capture")
        with pytest.raises(httpx.ReadTimeout):
            rail.debit(ref, 47_000, idempotency_key="k1")
        assert ref.debited == 0, "nothing is recorded from an answer that never arrived"
        rail.debit(ref, 47_000, idempotency_key="k1")
        assert ref.debited == 47_000 and ref.state is BlockState.CAPTURED
        assert len(sandbox.captures(ref.block_id)) == 1

    def test_the_call_never_reached_the_rail_so_the_retry_is_the_first_and_only_capture(self, sandbox, rail):
        ref = reserved(rail)
        sandbox.die_before.add("capture")
        with pytest.raises(httpx.ReadTimeout):
            rail.debit(ref, 47_000, idempotency_key="k1")
        rail.debit(ref, 47_000, idempotency_key="k1")
        assert ref.debited == 47_000 and len(sandbox.captures(ref.block_id)) == 1

    def test_calling_again_in_the_same_process_does_not_count_the_debit_twice(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="k1")
        again = rail.debit(ref, 47_000, idempotency_key="k1")
        assert again is ref and ref.debited == 47_000
        assert sandbox.n("capture") == 1, "the repeat is answered from what this process already did"

    def test_a_key_reused_for_a_different_amount_is_refused_here_as_the_rail_refuses_it(self, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="k1")
        with pytest.raises(RailError, match="already used for a different request"):
            rail.debit(ref, 30_000, idempotency_key="k1")

    def test_a_fresh_process_repeating_the_same_key_is_answered_by_the_rail(self, sandbox, rail):
        """The adapter's memory dies with the process; the rail's does not."""
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="k1")
        fresh, ref2 = rail_on(sandbox), BlockRef(ref.block_id, "cashfree_preauth", 62_000)
        fresh.debit(ref2, 47_000, idempotency_key="k1")
        assert ref2.debited == 47_000 and len(sandbox.captures(ref.block_id)) == 1

    def test_the_rail_refusing_a_reused_key_is_a_refusal(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="k1")
        fresh, ref2 = rail_on(sandbox), BlockRef(ref.block_id, "cashfree_preauth", 62_000)
        with pytest.raises(RailError, match="HTTP 422"):
            fresh.debit(ref2, 30_000, idempotency_key="k1")


class TestAReleaseRepeatedAfterALostResponseActsOnce:
    def test_the_void_acted_and_the_answer_was_lost(self, sandbox, rail):
        ref = reserved(rail)
        sandbox.lose_after.add("void")
        with pytest.raises(httpx.ReadTimeout):
            rail.release(ref, idempotency_key="v1")
        assert ref.released == 0
        rail.release(ref, idempotency_key="v1")
        assert ref.released == 62_000 and ref.state is BlockState.REVOKED

    def test_without_a_key_the_same_retry_is_refused_as_already_voided(self, sandbox, rail):
        """What idempotency buys: without it the retry reads as a failure of something that succeeded."""
        ref = reserved(rail)
        sandbox.lose_after.add("void")
        with pytest.raises(httpx.ReadTimeout):
            rail.release(ref)
        with pytest.raises(RailError, match="already voided"):
            rail.release(ref)


# ----------------------------------------------------------------------------------
class TestAReservationRepeatedAfterALostResponseActsOnce:
    def test_the_same_key_derives_the_same_order_and_different_keys_different_ones(self, rail):
        assert rail._order_id_for("k") == rail._order_id_for("k") != rail._order_id_for("k2")
        oid = rail._order_id_for("k")
        assert oid == "amanat_" + hashlib.sha256(b"k").hexdigest()[:32] and len(oid) <= 50

    def test_a_reservation_that_completed_but_lost_its_answer_is_read_back_not_placed_again(self, sandbox, rail):
        sandbox.lose_after.add("simulate")
        with pytest.raises(httpx.ReadTimeout):
            reserved(rail)
        assert len(sandbox.orders) == 1 and next(iter(sandbox.orders.values()))["order_status"] == "PAID"
        ref = reserved(rail)
        assert ref.state is BlockState.BLOCKED and ref.ceiling == 62_000
        assert len(sandbox.orders) == 1 and sandbox.n("pay") == 1, "one order, one payment"
        assert "already placed" in " ".join(ref.events)

    def test_a_reservation_that_died_after_creating_the_order_is_finished(self, sandbox, rail):
        sandbox.die_before.add("pay")
        with pytest.raises(httpx.ReadTimeout):
            reserved(rail)
        assert next(iter(sandbox.orders.values()))["order_status"] == "ACTIVE" and not sandbox.payments
        ref = reserved(rail)
        assert ref.state is BlockState.BLOCKED
        assert len(sandbox.orders) == 1 and sandbox.n("pay") == 2 and sandbox.n("simulate") == 1
        assert next(iter(sandbox.orders.values()))["order_status"] == "PAID"

    def test_a_reservation_with_a_payment_attempt_of_unknown_fate_is_not_guessed_at(self, sandbox, rail):
        sandbox.die_before.add("simulate")
        with pytest.raises(httpx.ReadTimeout):
            reserved(rail)
        with pytest.raises(RailError, match="reconcile"):
            reserved(rail)
        assert sandbox.n("pay") == 1, "no second payment attempt was made on a guess"

    def test_the_same_key_for_a_different_amount_is_refused(self, sandbox, rail):
        reserved(rail, ceiling=62_000)
        with pytest.raises(RailError, match="different request"):
            reserved(rail, ceiling=99_000)
        assert len(sandbox.orders) == 1

    def test_a_fresh_process_repeating_the_key_for_a_different_amount_is_refused_from_the_rails_own_order(self, sandbox, rail):
        """This process remembers nothing; the order the rail holds says what the key was for."""
        reserved(rail, ceiling=62_000)
        with pytest.raises(RailError, match="different request"):
            rail_on(sandbox).reserve(99_000, "citycabs", idempotency_key="k-reserve")
        assert len(sandbox.orders) == 1 and sandbox.n("pay") == 1

    def test_an_order_that_is_neither_paid_nor_active_cannot_be_the_hold_this_key_placed(self, sandbox, rail):
        reserved(rail)
        next(iter(sandbox.orders.values()))["order_status"] = "EXPIRED"
        with pytest.raises(RailError, match="cannot be the hold this key placed"):
            rail_on(sandbox).reserve(62_000, "citycabs", idempotency_key="k-reserve")

    def test_repeating_a_reservation_in_the_same_process_returns_the_same_block(self, sandbox, rail):
        a = reserved(rail)
        b = reserved(rail)
        assert a is b and sandbox.n("create") == 1

    def test_an_order_that_exists_but_cannot_be_read_is_not_assumed_to_be_ours(self, sandbox, rail):
        reserved(rail)
        fresh = rail_on(sandbox)
        sandbox.server_error["GET /orders/" + fresh._order_id_for("k-reserve")] = 503
        with pytest.raises(RailOutcomeUnknown):
            fresh.reserve(62_000, "citycabs", idempotency_key="k-reserve")


# ----------------------------------------------------------------------------------
class TestWhatCountsAsARefusalAndWhatCountsAsSilence:
    """The session records a `RailError` as a definite refusal and anything else as an outcome in doubt.
    A 500 is not a refusal: the rail may have acted."""

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 408])
    def test_a_server_error_or_a_timeout_status_leaves_the_outcome_in_doubt(self, sandbox, rail, status):
        ref = reserved(rail)
        sandbox.server_error["capture"] = status
        with pytest.raises(RailOutcomeUnknown):
            rail.debit(ref, 47_000, idempotency_key="k1")
        assert ref.debited == 0

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
    def test_a_client_error_is_a_definite_refusal(self, sandbox, rail, status):
        ref = reserved(rail)
        sandbox.server_error["capture"] = status
        with pytest.raises(RailError) as exc:
            rail.debit(ref, 47_000, idempotency_key="k1")
        assert not isinstance(exc.value, RailOutcomeUnknown)

    def test_an_outcome_in_doubt_is_not_a_rail_refusal(self):
        assert not issubclass(RailOutcomeUnknown, RailError)


# ----------------------------------------------------------------------------------
def _envelope():
    return Envelope(subject="cab", max_total=100_000, max_per_txn=80_000, allowed_payees=["citycabs"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=6))


class TestTheSessionRecoversFromALostResponseOnARealRailsShape:
    """The point of all of it: `AgentSession` drives the Cashfree adapter, a response is lost mid-debit,
    the session records the call as in doubt, resolves it under the same key, and the rail holds one capture."""

    def _session(self, rail):
        return AgentSession(_envelope(), rail, chain=EvidenceChain.with_key("cab", Ed25519PrivateKey.generate()))

    def test_a_lost_capture_response_is_resolved_and_the_money_moved_once(self, sandbox, rail):
        s = self._session(rail)
        assert s.reserve(62_000, "citycabs", "ceiling").ok
        oid = s.block.block_id
        sandbox.lose_after.add("capture")
        lost = s.debit(47_000, "the fare")
        assert lost.ok is False and s.in_doubt is not None and "unknown" in lost.detail
        assert s.debit(1_000, "another").ok is False, "money stops moving while a call is in doubt"
        resolved = s.resolve_in_doubt()
        assert resolved.ok and s.in_doubt is None
        assert len(sandbox.captures(oid)) == 1 and s.state.debited == 47_000
        s.chain.verify()

    def test_a_lost_reservation_response_is_resolved_into_one_hold(self, sandbox, rail):
        s = self._session(rail)
        sandbox.lose_after.add("simulate")
        lost = s.reserve(62_000, "citycabs", "ceiling")
        assert lost.ok is False and s.in_doubt is not None
        assert s.resolve_in_doubt().ok
        assert len(sandbox.orders) == 1 and sandbox.n("pay") == 1 and s.state.blocked == 62_000
        s.chain.verify()

    def test_a_lost_release_response_is_resolved_into_one_void(self, sandbox, rail):
        s = self._session(rail)
        assert s.reserve(62_000, "citycabs", "ceiling").ok
        sandbox.lose_after.add("void")
        assert s.release(reason="done").ok is False and s.in_doubt is not None
        assert s.resolve_in_doubt().ok and s.state.released == 62_000
        assert sandbox.n("void") == 2 and len(sandbox.keys) == 1, "two calls, one recorded action"
        s.chain.verify()

    def test_a_server_error_is_in_doubt_not_a_rejection(self, sandbox, rail):
        s = self._session(rail)
        assert s.reserve(62_000, "citycabs", "ceiling").ok
        sandbox.server_error["capture"] = 503
        assert s.debit(47_000, "fare").ok is False and s.in_doubt is not None
        assert s.resolve_in_doubt().ok and len(sandbox.captures(s.block.block_id)) == 1


# ----------------------------------------------------------------------------------
class TestABlockCanBeRebuiltFromTheRail:
    """A restarted session has the chain and a fresh adapter and nothing else: the block comes from the rail."""

    def test_a_held_order_is_a_blocked_block_of_the_orders_amount(self, sandbox, rail):
        ref = reserved(rail)
        got = rail_on(sandbox).get_block(ref.block_id)
        assert (got.block_id, got.ceiling, got.state, got.debited, got.released) == \
            (ref.block_id, 62_000, BlockState.BLOCKED, 0, 0)

    def test_a_captured_hold_shows_what_the_rail_captured(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="d")
        got = rail_on(sandbox).get_block(ref.block_id)
        assert got.state is BlockState.CAPTURED and got.debited == 47_000 and got.released == 0
        assert got.available == 15_000

    def test_a_voided_hold_shows_it_was_released_whole(self, sandbox, rail):
        ref = reserved(rail)
        rail.release(ref, idempotency_key="v")
        got = rail_on(sandbox).get_block(ref.block_id)
        assert got.state is BlockState.REVOKED and got.released == 62_000 and got.debited == 0

    def test_an_order_that_was_never_authorised_is_not_a_hold(self, sandbox, rail):
        sandbox.die_before.add("pay")
        with pytest.raises(httpx.ReadTimeout):
            reserved(rail)
        with pytest.raises(RailError, match="not a hold"):
            rail_on(sandbox).get_block(rail._order_id_for("k-reserve"))

    def test_a_capture_or_void_that_did_not_succeed_is_not_one(self, sandbox, rail):
        ref = reserved(rail)
        pay = sandbox.payments[ref.block_id][0]
        pay["authorization"] = {"action": "CAPTURE", "status": "FAILED", "captured_amount": 47_000}
        got = rail_on(sandbox).get_block(ref.block_id)
        assert got.state is BlockState.BLOCKED and got.debited == 0
        pay["authorization"] = {"action": "VOID", "status": "PENDING"}
        got = rail_on(sandbox).get_block(ref.block_id)
        assert got.state is BlockState.BLOCKED and got.released == 0

    def test_an_unknown_order_is_an_unknown_block(self, sandbox):
        with pytest.raises(RailError, match="unknown block"):
            rail_on(sandbox).get_block("nope")

    def test_an_unreadable_order_is_silence_not_absence(self, sandbox, rail):
        ref = reserved(rail)
        sandbox.server_error["GET /orders/" + ref.block_id] = 503
        with pytest.raises(RailOutcomeUnknown):
            rail_on(sandbox).get_block(ref.block_id)


class TestAKeyedRepeatOfACaptureReachesTheRailEvenWhenTheBlockAlreadyShowsIt:
    """After a restart the rebuilt block already shows the capture whose answer was lost. The retry must still
    go to the rail, which returns the first result, and must not count the debit a second time."""

    def test_the_repeat_is_sent_and_the_ledger_is_unchanged(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="d")
        rebuilt = rail_on(sandbox).get_block(ref.block_id)
        again = rail_on(sandbox).debit(rebuilt, 47_000, idempotency_key="d")
        assert again.debited == 47_000 and len(sandbox.captures(ref.block_id)) == 1
        assert sandbox.n("capture") == 2, "the repeat did go to the rail"

    def test_without_a_key_a_block_already_captured_is_still_refused_on_the_rails_documented_rule(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="d")
        rebuilt = rail_on(sandbox).get_block(ref.block_id)
        with pytest.raises(RailError, match="already captured"):
            rail_on(sandbox).debit(rebuilt, 47_000)

    def test_a_different_keyed_capture_on_a_captured_block_is_refused_by_the_rail(self, sandbox, rail):
        ref = reserved(rail)
        rail.debit(ref, 47_000, idempotency_key="d")
        rebuilt = rail_on(sandbox).get_block(ref.block_id)
        with pytest.raises(RailError, match="HTTP 400"):
            rail_on(sandbox).debit(rebuilt, 10_000, idempotency_key="other")
        assert rebuilt.debited == 47_000

    def test_a_capture_larger_than_the_hold_is_still_refused_before_any_call(self, sandbox, rail):
        ref = reserved(rail)
        before = len(sandbox.calls)
        with pytest.raises(RailError, match="exceeds"):
            rail.debit(ref, 70_000, idempotency_key="big")
        assert len(sandbox.calls) == before

    def test_a_repeated_release_of_a_block_already_voided_reaches_the_rail_and_changes_nothing(self, sandbox, rail):
        ref = reserved(rail)
        rail.release(ref, idempotency_key="v")
        rebuilt = rail_on(sandbox).get_block(ref.block_id)
        again = rail_on(sandbox).release(rebuilt, idempotency_key="v")
        assert again.released == 62_000 and sandbox.n("void") == 2


# ----------------------------------------------------------------------------------
class Crash(BaseException):
    """The process dying. Not an Exception, so no handler in the session runs, as in a real crash."""


class FlakyAdapter:
    """A crash in the middle of an adapter call: before the rail is asked, or after it has acted."""

    def __init__(self, inner, crash_before=None, crash_after=None):
        self.inner, self.crash_before, self.crash_after = inner, crash_before, crash_after
        self.rail_id, self.profile = inner.rail_id, inner.profile

    @property
    def supports_idempotency(self):
        return self.inner.supports_idempotency

    def _call(self, name, *a, **k):
        if self.crash_before == name:
            self.crash_before = None
            raise Crash()
        out = getattr(self.inner, name)(*a, **k)
        if self.crash_after == name:
            self.crash_after = None
            raise Crash()
        return out

    def reserve(self, *a, **k): return self._call("reserve", *a, **k)
    def debit(self, *a, **k): return self._call("debit", *a, **k)
    def release(self, *a, **k): return self._call("release", *a, **k)


def _plan(upto):
    """Reserve, then the action. On Cashfree a hold is either captured or voided, never both: the rail
    refuses a void after a capture, so a release is a void of an untouched hold."""
    reserve = lambda s: s.reserve(62_000, "citycabs", "ceiling")
    return {"reserve": [reserve],
            "debit": [reserve, lambda s: s.debit(47_000, "the fare")],
            "release": [reserve, lambda s: s.release(reason="done")]}[upto]


def _world(sandbox, session):
    """What the rail holds and what the ledger says: the two must agree, and match a fault-free run."""
    orders = {o: (v["order_status"], len(sandbox.payments.get(o, [])),
                  [p["authorization"].get("action") for p in sandbox.payments.get(o, [])]) for o, v in sandbox.orders.items()}
    st = session.state
    return orders, (st.blocked, st.debited, st.released)


def _fault_free(upto):
    sb = FakeSandbox()
    s = AgentSession(_envelope(), rail_on(sb), chain=EvidenceChain.with_key("cab", Ed25519PrivateKey.generate()))
    for step in _plan(upto):
        assert step(s).ok
    return _world(sb, s)


def _crash_then_restart(tmp_path, upto, **fault):
    key, path, sb = Ed25519PrivateKey.generate(), tmp_path / "chain.jsonl", FakeSandbox()
    session = AgentSession(_envelope(), FlakyAdapter(rail_on(sb), **fault),
                           chain=EvidenceChain.with_key("cab", key, store=path))
    with pytest.raises(Crash):
        for step in _plan(upto):
            step(session)
    # a new process: a fresh adapter that remembers nothing, and the chain from disk
    return sb, AgentSession(_envelope(), rail_on(sb), resume=True, chain=EvidenceChain.load(path, key))


@pytest.mark.parametrize("action", ["reserve", "debit", "release"])
@pytest.mark.parametrize("site", ["crash_before", "crash_after"])
class TestARestartOnCashfreeConvergesExactlyOnce:
    """The matrix the simulated rail already passes, on the Cashfree adapter and a rail that keeps its own state."""

    def test_the_interrupted_call_is_in_doubt_after_the_restart(self, tmp_path, action, site):
        _, s = _crash_then_restart(tmp_path, action, **{site: action})
        assert s.in_doubt is not None and s.in_doubt.action.value == action

    def test_recovery_leaves_the_rail_and_the_ledger_where_a_fault_free_run_leaves_them(self, tmp_path, action, site):
        sb, s = _crash_then_restart(tmp_path, action, **{site: action})
        assert s.resolve_in_doubt().ok and s.in_doubt is None
        assert _world(sb, s)[1] == _fault_free(action)[1]
        (got, want) = (_world(sb, s)[0], _fault_free(action)[0])
        assert [v for v in got.values()] == [v for v in want.values()], "one order, one payment, one action"
        s.chain.verify()

    def test_the_interrupted_call_is_recorded_as_applied_exactly_once(self, tmp_path, action, site):
        _, s = _crash_then_restart(tmp_path, action, **{site: action})
        s.resolve_in_doubt()
        applied = [e for e in s.chain.entries if e.event_type is EventType.RAIL_TRANSITION
                   and e.payload.get("action") == action and e.payload.get("outcome") == "applied"]
        assert len(applied) == 1
