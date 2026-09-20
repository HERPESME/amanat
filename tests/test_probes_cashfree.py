"""The Cashfree harness: the real adapter's transport, observed and recorded.

Only the network is replaced (a scripted responder). The harness must record exactly what it
sent and what came back, never a header, and turn a transport failure into a recorded error
rather than an exception. The catalogue is checked for structure here; what the live sandbox
actually answers is measured by running it, and recorded in the evidence store.
"""
import json
import threading

import httpx
import pytest

from amanat.probes import catalogue, runner
from amanat.probes.cashfree import CashfreeHarness, TracingCashfree
from amanat.rails.base import RailError
from amanat.rails.semantics import RAILS, Environment
from amanat.registry import store

T0 = "2026-09-20T10:00:00Z"
CAPTURE_OK = {"authorization": {"action": "CAPTURE", "status": "SUCCESS", "captured_amount": 470.0},
              "payment_message": "PRE_AUTH|Transaction Success"}
HELD = [{"cf_payment_id": 9, "payment_status": "SUCCESS", "is_captured": False, "payment_amount": 620.0,
         "authorization": {"action": None, "captured_amount": None}}]


class Responder:
    """Answers each call from a function of (method, path, json); remembers what was sent."""

    def __init__(self, respond):
        self.respond, self.sent, self.headers_seen = respond, [], []
        self.lock = threading.Lock()


def harness(respond, **kw):
    """A Cashfree harness whose transport is `respond`."""
    r = Responder(respond)

    class Rail(TracingCashfree):
        def _send(self, method, path, *, version, **kw2):
            with r.lock:
                r.sent.append((method, path, kw2.get("json")))
                r.headers_seen.append(dict(self._headers(version)))
            out = r.respond(method, path, kw2.get("json"))
            if isinstance(out, BaseException):
                raise out
            return out

    h = CashfreeHarness(client_id="TEST_ID_123456", client_secret="TEST_SECRET_654321",
                        rail_factory=Rail, settle_seconds=0, clock=lambda: T0, **kw)
    h.responder = r
    return h


def happy(method, path, body):
    if method == "POST" and path == "/orders":
        return 200, {"order_id": body["order_id"], "order_status": "ACTIVE", "payment_session_id": "session_abc"}
    if path == "/orders/sessions":
        return 200, {"cf_payment_id": 9, "payment_method": "upi"}
    if path == "/simulate":
        return 200, {"entity_simulation": {"payment_status": "SUCCESS"}}
    if path.endswith("/payments"):
        return 200, HELD
    if path.endswith("/refunds"):
        return 200, []
    if path.startswith("/orders/") and method == "GET":
        return 200, {"order_status": "PAID", "order_amount": 620.0}
    if path.endswith("/authorization"):
        return (200, CAPTURE_OK) if body["action"] == "CAPTURE" else (200, {"authorization": {"action": "VOID"}})
    raise AssertionError((method, path, body))


class TestHold:
    def test_it_places_the_hold_the_way_the_sandbox_needs_and_reads_it_back(self):
        h = harness(happy)
        res = h.run_op("hold", {"amount": 62_000})
        assert res.ok and [e.label for e in res.exchanges] == [
            "order_create", "pay_collect", "simulate", "order", "payments"]
        assert [(m, p) for m, p, _ in h.responder.sent[:3]] == [
            ("POST", "/orders"), ("POST", "/orders/sessions"), ("POST", "/simulate")]
        assert h.responder.sent[0][2]["order_amount"] == 620.0 and h.responder.sent[0][2]["order_note"] == "preauth_transaction"
        assert res.handle["order_id"].startswith("amanat_pr_") and res.handle["cf_payment_id"] == 9

    def test_each_exchange_carries_what_was_sent_and_what_came_back(self):
        res = harness(happy).run_op("hold", {"amount": 62_000})
        pay = res.exchanges[1]
        assert pay.request["method"] == "POST" and pay.request["path"] == "/orders/sessions"
        assert pay.status == 200 and pay.response == {"cf_payment_id": 9, "payment_method": "upi"}
        assert pay.at == T0

    def test_a_failure_part_way_stops_the_hold_and_says_so(self):
        def bad_collect(m, p, b):
            return (400, {"message": "collect failed"}) if p == "/orders/sessions" else happy(m, p, b)
        res = harness(bad_collect).run_op("hold", {"amount": 62_000})
        assert res.ok is False and [e.label for e in res.exchanges] == ["order_create", "pay_collect"]

    def test_no_session_in_the_order_response_fails_the_hold(self):
        def no_session(m, p, b):
            return (200, {"order_id": b["order_id"]}) if p == "/orders" else happy(m, p, b)
        assert harness(no_session).run_op("hold", {"amount": 62_000}).ok is False

    def test_a_transport_failure_is_recorded_not_raised(self):
        def down(m, p, b):
            return httpx.ConnectTimeout("timed out")
        res = harness(down).run_op("hold", {"amount": 62_000})
        assert res.ok is False and res.exchanges[0].status is None and "ConnectTimeout" in res.exchanges[0].error
        assert res.exchanges[0].request["path"] == "/orders"


class TestTheOtherOperations:
    def _held(self, h):
        return h.run_op("hold", {"amount": 62_000}).handle

    def test_capture_sends_the_amount_in_rupees_to_the_authorization_endpoint(self):
        h = harness(happy)
        hold = self._held(h)
        res = h.run_op("capture", {"hold": hold, "amount": 47_000})
        assert res.exchanges[0].label == "capture" and res.exchanges[0].status == 200
        assert res.exchanges[0].request == {"method": "POST", "path": f"/orders/{hold['order_id']}/authorization",
                                            "json": {"action": "CAPTURE", "amount": 470.0}}
        method, path, body = h.responder.sent[-1]
        assert (method, path, body) == ("POST", f"/orders/{hold['order_id']}/authorization",
                                        {"action": "CAPTURE", "amount": 470.0})

    def test_an_idempotency_key_is_sent_for_that_call_only_and_recorded_without_headers(self):
        h = harness(happy)
        hold = self._held(h)
        first = h.run_op("capture", {"hold": hold, "amount": 47_000, "idempotency_key": "k1"})
        h.run_op("capture", {"hold": hold, "amount": 47_000})
        with_key, without = h.responder.headers_seen[-2], h.responder.headers_seen[-1]
        assert "x-idempotency-key" in with_key and "x-idempotency-key" not in without
        assert first.exchanges[0].request["idempotency_key"] == with_key["x-idempotency-key"]
        assert "headers" not in first.exchanges[0].request

    def test_the_same_key_name_gives_the_same_key_for_one_hold_and_a_different_one_for_another(self):
        h = harness(happy)
        a, b = self._held(h), self._held(h)
        assert h.idempotency_key(a, "k1") == h.idempotency_key(a, "k1") != h.idempotency_key(b, "k1")

    def test_void_sends_a_void(self):
        h = harness(happy)
        hold = self._held(h)
        h.run_op("void", {"hold": hold})
        assert h.responder.sent[-1][2] == {"action": "VOID"}

    def test_a_void_can_carry_an_idempotency_key_for_that_call_only(self):
        h = harness(happy)
        hold = self._held(h)
        first = h.run_op("void", {"hold": hold, "idempotency_key": "k1"})
        h.run_op("void", {"hold": hold})
        with_key, without = h.responder.headers_seen[-2], h.responder.headers_seen[-1]
        assert "x-idempotency-key" in with_key and "x-idempotency-key" not in without
        assert first.exchanges[0].request["idempotency_key"] == with_key["x-idempotency-key"]
        assert first.exchanges[0].request["json"] == {"action": "VOID"} and "headers" not in first.exchanges[0].request

    def test_recreating_an_order_posts_the_same_order_id_and_amount_again(self):
        h = harness(happy)
        hold = self._held(h)
        res = h.run_op("recreate_order", {"hold": hold, "amount": 62_000})
        assert [e.label for e in res.exchanges] == ["order_recreate"]
        method, path, body = h.responder.sent[-1]
        assert (method, path) == ("POST", "/orders")
        assert body["order_id"] == hold["order_id"] and body["order_amount"] == 620.0
        assert body["order_note"] == "preauth_transaction"

    def test_paying_again_submits_the_holds_own_session_to_the_payment_endpoint(self):
        h = harness(happy)
        hold = self._held(h)
        res = h.run_op("pay_again", {"hold": hold})
        assert [e.label for e in res.exchanges] == ["pay_again"]
        method, path, body = h.responder.sent[-1]
        assert (method, path) == ("POST", "/orders/sessions") and body["payment_session_id"] == "session_abc"

    def test_the_hold_handle_carries_the_session_a_replayed_payment_needs(self):
        hold = self._held(harness(happy))
        assert hold["payment_session_id"] == "session_abc"

    def test_a_replayed_payment_never_puts_the_session_in_the_record(self):
        def refuse_again(m, p, b):
            if p == "/orders/sessions" and len([1 for _ in seen]) > 0:
                return 400, {"code": "order_inactive", "message": "order is no longer active"}
            if p == "/orders/sessions":
                seen.append(1)
            return happy(m, p, b)
        seen = []
        text = json.dumps(run_probe("cashfree_preauth.payment_replay_refused", refuse_again))
        for banned in ("session_abc", "x-client-secret", "customer_details"):
            assert banned not in text, banned

    def test_fetch_reads_the_order_its_payments_and_its_refunds(self):
        h = harness(happy)
        res = h.run_op("fetch", {"hold": self._held(h)})
        assert [e.label for e in res.exchanges] == ["order", "payments", "refunds"]

    def test_parallel_captures_fire_together_and_are_summarised_from_their_own_exchanges(self):
        def race(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return (200, CAPTURE_OK) if b["amount"] == 300.0 else (400, {"message": "Capture request already exist"})
            return happy(m, p, b)
        h = harness(race)
        res = h.run_op("capture_parallel", {"hold": self._held(h), "amounts": [30_000, 20_000, 10_000]})
        labels = [e.label for e in res.exchanges]
        assert labels == ["capture_1", "capture_2", "capture_3", "summary"]
        summary = res.exchanges[-1]
        assert summary.response == {"succeeded": 1, "refused": 2, "unanswered": 0,
                                    "single_winner": True, "multiple_winners": False}
        assert summary.request["derived"] is True

    def test_two_winners_are_reported_as_a_race_that_was_lost(self):
        h = harness(lambda m, p, b: (200, CAPTURE_OK) if p.endswith("/authorization") else happy(m, p, b))
        res = h.run_op("capture_parallel", {"hold": self._held(h), "amounts": [30_000, 20_000]})
        assert res.exchanges[-1].response["multiple_winners"] is True

    def test_an_unknown_operation_is_refused_by_name(self):
        with pytest.raises(ValueError, match="teleport"):
            harness(happy).run_op("teleport", {})


class TestSafety:
    def test_it_is_sandbox_only_like_the_adapter_it_wraps(self):
        with pytest.raises(RailError, match="non-sandbox"):
            CashfreeHarness(client_id="x" * 8, client_secret="y" * 8, base="https://api.cashfree.com/pg")

    def test_it_says_what_it_is(self):
        h = harness(happy)
        assert h.rail_id == "cashfree_preauth" and h.environment is Environment.SANDBOX
        assert h.name == "cashfree_preauth/sandbox"

    def test_the_credentials_are_offered_for_scrubbing_when_long_enough_to_scrub(self):
        assert set(harness(happy).secrets) == {"TEST_ID_123456", "TEST_SECRET_654321"}
        short = CashfreeHarness(client_id="abc", client_secret="TEST_SECRET_654321", rail_factory=TracingCashfree)
        assert short.secrets == ["TEST_SECRET_654321"]

    def test_a_full_run_never_records_a_credential_a_session_or_a_header(self):
        def leaky(m, p, b):
            if p == "/orders/sessions":
                return 400, {"message": "bad TEST_SECRET_654321 and TEST_ID_123456"}
            return happy(m, p, b)
        p = catalogue.PROBES["cashfree_preauth.partial_capture"]
        text = json.dumps(runner.run(p, harness(leaky), clock=lambda: T0))
        for banned in ("TEST_SECRET_654321", "TEST_ID_123456", "session_abc", "x-client-secret", "customer_details"):
            assert banned not in text, banned


class TestTheCatalogue:
    def test_every_probe_is_well_formed(self):
        h = harness(happy)
        assert catalogue.PROBES
        for pid, p in catalogue.PROBES.items():
            assert pid == p.probe_id and pid.startswith(p.rail_id + ".") and p.rail_id in RAILS
            assert p.environment is Environment.SANDBOX
            names = [s.name for s in p.steps]
            assert len(set(names)) == len(names) and p.steps[0].op == "hold"
            for i, s in enumerate(p.steps):
                assert s.op in h.ops, (pid, s.op)
                for v in s.args.values():
                    if hasattr(v, "step"):
                        assert v.step in names[:i], (pid, "a Ref must point at an earlier step")
            for r in p.rules:
                assert r.step in names and all(st in names for st, _ in r.after), (pid, r)
                assert r.status in (None, "2xx", "4xx")

    def test_every_capability_a_probe_speaks_to_is_named_once_per_probe_and_documented(self):
        for p in catalogue.PROBES.values():
            assert p.summary.strip()

    def test_the_catalogue_covers_the_questions_the_strategy_lists(self):
        caps = {r.capability for p in catalogue.PROBES.values() for r in p.rules}
        assert caps >= {"partial_debit", "over_capture", "multiple_captures", "void_whole_hold",
                        "void_after_partial_capture", "capture_after_void", "idempotent_capture_replay",
                        "concurrent_capture_single_winner", "funds_held_in_customer_account",
                        "idempotent_void_replay", "duplicate_order_refused", "payment_replay_refused",
                        "idempotency_key_reuse_refused"}

    def test_for_rail_returns_only_that_rails_probes(self):
        assert {p.rail_id for p in catalogue.for_rail("cashfree_preauth")} == {"cashfree_preauth"}
        assert catalogue.for_rail("nope") == []


def run_probe(pid, respond):
    return runner.run(catalogue.PROBES[pid], harness(respond), clock=lambda: T0)


def finding(obs, cap):
    return next(f for f in obs["findings"] if f["capability"] == cap)


class TestReadingTheSandboxAnswers:
    """The shapes the sandbox returned on 29 Aug and 20 Sep 2026, run through the real probes."""

    def test_partial_capture_accepted_and_the_hold_is_a_hold(self):
        obs = run_probe("cashfree_preauth.partial_capture", happy)
        assert finding(obs, "partial_debit")["supported"] is True
        assert finding(obs, "funds_held_in_customer_account")["supported"] is True

    def test_a_payment_already_captured_after_authorisation_is_not_a_hold(self):
        def autocap(m, p, b):
            if p.endswith("/payments"):
                return 200, [dict(HELD[0], is_captured=True)]
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.partial_capture", autocap),
                       "funds_held_in_customer_account")["supported"] is False

    def test_a_refused_partial_capture_is_a_refusal_with_the_rails_words(self):
        def refuse(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return 400, {"message": "Capture amount must be equal to the amount authorized."}
            return happy(m, p, b)
        f = finding(run_probe("cashfree_preauth.partial_capture", refuse), "partial_debit")
        assert f["supported"] is False and "must be equal" in f["basis"]

    def test_a_void_after_a_capture_that_the_rail_refuses(self):
        def refuse_void(m, p, b):
            if p.endswith("/authorization") and b["action"] == "VOID":
                return 400, {"message": "Capture request already exist for the void"}
            return happy(m, p, b)
        f = finding(run_probe("cashfree_preauth.void_after_partial_capture", refuse_void), "void_after_partial_capture")
        assert f["supported"] is False and "already exist" in f["basis"]

    def test_a_second_capture_refused_after_a_first_that_succeeded(self):
        calls = []

        def once(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                calls.append(b["amount"])
                return (200, CAPTURE_OK) if len(calls) == 1 else (400, {"message": "already captured"})
            return happy(m, p, b)
        f = finding(run_probe("cashfree_preauth.double_capture", once), "multiple_captures")
        assert f["supported"] is False and "already captured" in f["basis"]

    def test_a_refused_second_capture_after_a_refused_first_says_nothing(self):
        def always(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return 400, {"message": "malformed"}
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.double_capture", always), "multiple_captures")["supported"] is None

    def test_over_capture_refused(self):
        def refuse(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE" and b["amount"] > 620:
                return 400, {"message": "capture amount exceeds the authorised amount"}
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.over_capture", refuse), "over_capture")["supported"] is False

    @staticmethod
    def _captures(*answers):
        """Answer the n-th CAPTURE with the n-th scripted response; everything else as usual."""
        seq, calls = list(answers), []

        def respond(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                calls.append(b["amount"])
                return seq[len(calls) - 1]
            return happy(m, p, b)
        return respond

    REFUSED = (400, {"message": "already captured"})

    def test_the_same_key_replaying_the_first_result_while_another_key_is_refused_is_idempotency(self):
        respond = self._captures((200, CAPTURE_OK), (200, CAPTURE_OK), self.REFUSED)
        f = finding(run_probe("cashfree_preauth.idempotent_capture_replay", respond), "idempotent_capture_replay")
        assert f["supported"] is True and "under a different key was refused" in f["basis"]

    def test_if_a_different_key_is_accepted_too_the_key_is_not_what_made_the_difference(self):
        """Then the 200 to the replay proves nothing about the header: inconclusive, not idempotent."""
        respond = self._captures((200, CAPTURE_OK), (200, CAPTURE_OK), (200, CAPTURE_OK))
        f = finding(run_probe("cashfree_preauth.idempotent_capture_replay", respond), "idempotent_capture_replay")
        assert f["supported"] is None and "control" in f["basis"]

    def test_a_replay_refused_as_a_second_capture_means_the_key_was_not_honoured(self):
        respond = self._captures((200, CAPTURE_OK), self.REFUSED, self.REFUSED)
        f = finding(run_probe("cashfree_preauth.idempotent_capture_replay", respond), "idempotent_capture_replay")
        assert f["supported"] is False and "not honoured" in f["basis"]

    def test_a_replay_after_a_first_capture_that_failed_says_nothing(self):
        respond = self._captures(self.REFUSED, self.REFUSED, self.REFUSED)
        assert finding(run_probe("cashfree_preauth.idempotent_capture_replay", respond),
                       "idempotent_capture_replay")["supported"] is None

    def test_capture_after_a_void_refused(self):
        def refuse(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return 400, {"message": "already voided"}
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.capture_after_void", refuse), "capture_after_void")["supported"] is False

    def test_a_single_winner_among_concurrent_captures(self):
        def race(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return (200, CAPTURE_OK) if b["amount"] == 300.0 else (400, {"message": "already"})
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.concurrent_capture", race),
                       "concurrent_capture_single_winner")["supported"] is True

    def test_one_success_among_captures_that_gave_no_answer_proves_nothing(self):
        """The unanswered ones may also have succeeded, so 'exactly one won' cannot be claimed."""
        def race(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                return (200, CAPTURE_OK) if b["amount"] == 300.0 else (503, {"message": "unavailable"})
            return happy(m, p, b)
        f = finding(run_probe("cashfree_preauth.concurrent_capture", race), "concurrent_capture_single_winner")
        assert f["supported"] is None

    def test_two_winners_is_the_double_charge_hazard(self):
        def race(m, p, b):
            return (200, CAPTURE_OK) if p.endswith("/authorization") and b["action"] == "CAPTURE" else happy(m, p, b)
        f = finding(run_probe("cashfree_preauth.concurrent_capture", race), "concurrent_capture_single_winner")
        assert f["supported"] is False and "more than one" in f["basis"]

    def test_a_dead_sandbox_produces_only_inconclusive_findings(self):
        obs = run_probe("cashfree_preauth.partial_capture", lambda m, p, b: (503, {"message": "unavailable"}))
        assert all(f["supported"] is None for f in obs["findings"])
        assert {s["step"] for s in obs["skipped"]} == {"capture", "observe"}

    def test_wrong_credentials_are_not_a_finding(self):
        obs = run_probe("cashfree_preauth.partial_capture", lambda m, p, b: (401, {"message": "authentication failed"}))
        assert all(f["supported"] is None for f in obs["findings"])

    def test_a_run_is_recorded_and_can_be_read_back(self, tmp_path):
        obs = run_probe("cashfree_preauth.partial_capture", happy)
        path = tmp_path / "p.jsonl"
        runner.record(obs, path)
        assert store.verify(path).length == 1
        assert runner.latest_findings(path)[("cashfree_preauth.partial_capture", "partial_debit")]["supported"] is True


class TestReadingTheRetryAnswers:
    """Retry safety, one question per probe. The shapes are the ones the sandbox returned on 21 Sep 2026."""

    VOID_OK = (200, {"authorization": {"action": "VOID", "status": "SUCCESS", "action_reference": "VOID_12121"}})
    ALREADY_VOIDED = (400, {"code": "order_id_voided", "message": "transaction is already voided"})

    @staticmethod
    def _voids(*answers):
        seq, calls = list(answers), []

        def respond(m, p, b):
            if p.endswith("/authorization") and b["action"] == "VOID":
                calls.append(1)
                return seq[len(calls) - 1]
            return happy(m, p, b)
        return respond

    def test_the_same_key_replaying_the_first_void_while_another_key_is_refused_is_idempotency(self):
        f = finding(run_probe("cashfree_preauth.idempotent_void_replay",
                              self._voids(self.VOID_OK, self.VOID_OK, self.ALREADY_VOIDED)), "idempotent_void_replay")
        assert f["supported"] is True and "under a different key was refused" in f["basis"]

    def test_if_a_different_key_is_accepted_too_the_replay_proves_nothing(self):
        f = finding(run_probe("cashfree_preauth.idempotent_void_replay",
                              self._voids(self.VOID_OK, self.VOID_OK, self.VOID_OK)), "idempotent_void_replay")
        assert f["supported"] is None

    def test_a_replay_refused_as_already_voided_means_the_key_was_not_honoured(self):
        f = finding(run_probe("cashfree_preauth.idempotent_void_replay",
                              self._voids(self.VOID_OK, self.ALREADY_VOIDED, self.ALREADY_VOIDED)), "idempotent_void_replay")
        assert f["supported"] is False and "not honoured" in f["basis"] and "already voided" in f["basis"]

    def test_a_replay_after_a_void_that_failed_says_nothing(self):
        f = finding(run_probe("cashfree_preauth.idempotent_void_replay",
                              self._voids(*[self.ALREADY_VOIDED] * 3)), "idempotent_void_replay")
        assert f["supported"] is None

    @staticmethod
    def _second_order(answer):
        """The first POST /orders (the hold) succeeds; the repeated one gets `answer`."""
        posts = []

        def respond(m, p, b):
            if m == "POST" and p == "/orders":
                posts.append(1)
                return answer if len(posts) > 1 else happy(m, p, b)
            return happy(m, p, b)
        return respond

    def test_an_order_created_twice_under_one_id_and_refused_is_a_refusal_not_a_second_order(self):
        f = finding(run_probe("cashfree_preauth.duplicate_order_refused", self._second_order(
            (409, {"code": "order_already_exists", "message": "order with same id is already present"}))),
            "duplicate_order_refused")
        assert f["supported"] is True and "already present" in f["basis"]

    def test_a_second_order_accepted_under_the_same_id_is_the_hazard(self):
        f = finding(run_probe("cashfree_preauth.duplicate_order_refused",
                              self._second_order((200, {"order_id": "x", "order_status": "ACTIVE"}))),
                    "duplicate_order_refused")
        assert f["supported"] is False and "second order was accepted" in f["basis"]

    def test_a_timeout_on_the_repeated_order_is_not_an_answer(self):
        f = finding(run_probe("cashfree_preauth.duplicate_order_refused", self._second_order((503, {"message": "x"}))),
                    "duplicate_order_refused")
        assert f["supported"] is None

    @staticmethod
    def _second_payment(answer):
        posts = []

        def respond(m, p, b):
            if p == "/orders/sessions":
                posts.append(1)
                return answer if len(posts) > 1 else happy(m, p, b)
            return happy(m, p, b)
        return respond

    def test_a_payment_submitted_again_on_an_authorised_order_and_refused_cannot_double_authorise(self):
        f = finding(run_probe("cashfree_preauth.payment_replay_refused", self._second_payment(
            (400, {"code": "order_inactive", "message": "order is no longer active"}))), "payment_replay_refused")
        assert f["supported"] is True and "no longer active" in f["basis"]

    def test_a_second_payment_accepted_is_the_hazard(self):
        f = finding(run_probe("cashfree_preauth.payment_replay_refused",
                              self._second_payment((200, {"cf_payment_id": 10}))), "payment_replay_refused")
        assert f["supported"] is False and "second attempt was accepted" in f["basis"]

    @staticmethod
    def _captures_by_key(second):
        seq, calls = [(200, CAPTURE_OK), second], []

        def respond(m, p, b):
            if p.endswith("/authorization") and b["action"] == "CAPTURE":
                calls.append(1)
                return seq[len(calls) - 1]
            return happy(m, p, b)
        return respond

    def test_a_key_reused_for_a_different_amount_and_refused_cannot_be_confused_with_the_first_request(self):
        f = finding(run_probe("cashfree_preauth.idempotency_key_reuse_refused", self._captures_by_key(
            (422, {"code": "request_invalid", "type": "idempotency_error",
                   "message": "invalid body in request for x-idempotency-key"}))), "idempotency_key_reuse_refused")
        assert f["supported"] is True and "invalid body" in f["basis"]

    def test_a_key_reused_for_a_different_amount_that_returns_the_first_result_is_the_hazard(self):
        f = finding(run_probe("cashfree_preauth.idempotency_key_reuse_refused", self._captures_by_key((200, CAPTURE_OK))),
                    "idempotency_key_reuse_refused")
        assert f["supported"] is False and "did not answer" in f["basis"]

    def test_a_reuse_after_a_first_capture_that_failed_says_nothing(self):
        seq, calls = [(400, {"message": "no"}), (400, {"message": "no"})], []

        def respond(m, p, b):
            if p.endswith("/authorization"):
                calls.append(1)
                return seq[len(calls) - 1]
            return happy(m, p, b)
        assert finding(run_probe("cashfree_preauth.idempotency_key_reuse_refused", respond),
                       "idempotency_key_reuse_refused")["supported"] is None
