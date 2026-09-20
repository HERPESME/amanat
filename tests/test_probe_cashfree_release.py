"""The release-timing probe: what does Cashfree *report* about the uncaptured remainder?

Its output is evidence that gets published, so what it writes must be safe (no
session tokens, no customer details), self-dating (when, and how long after the
capture) and append-only (an observation is never rewritten).
"""
import json
from datetime import datetime, timedelta, timezone

from amanat.rails.probe_cashfree_release import (
    append_record, poll, record, redact,
)

T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


class ScriptedRail:
    """Returns each order's canned bodies; records what was asked."""

    def __init__(self, orders, payments):
        self._orders, self._payments, self.asked = orders, payments, []

    def fetch_order(self, order_id):
        self.asked.append(("order", order_id))
        return 200, self._orders[order_id]

    def fetch_payments(self, order_id):
        self.asked.append(("payments", order_id))
        return 200, self._payments[order_id]

    def fetch_refunds(self, order_id):
        self.asked.append(("refunds", order_id))
        return 200, []


class TestRedaction:
    def test_the_session_token_and_customer_details_are_removed_at_any_depth(self):
        body = {"order_status": "PAID", "payment_session_id": "session_secret",
                "customer_details": {"customer_email": "a@b.c", "customer_phone": "9"},
                "nested": [{"payment_session_id": "again", "keep": 1}]}
        out = redact(body)
        assert out == {"order_status": "PAID", "nested": [{"keep": 1}]}

    def test_redaction_does_not_mutate_its_input(self):
        body = {"payment_session_id": "x", "a": 1}
        redact(body)
        assert body == {"payment_session_id": "x", "a": 1}


class TestARecordDatesItself:
    def test_it_carries_when_it_was_observed_and_how_long_after_the_start(self):
        r = record("capture", "order_1", T0, T0 + timedelta(minutes=5),
                   {"order_status": "PAID"}, [{"authorization": {"status": "SUCCESS"}}])
        assert r["observed_at"] == "2026-09-20T12:05:00+00:00"
        assert r["elapsed_seconds"] == 300
        assert r["label"] == "capture" and r["order_id"] == "order_1"
        assert r["order"] == {"order_status": "PAID"}

    def test_a_record_is_plain_json(self):
        r = record("control", "o", T0, T0, {"a": 470.0}, [])
        assert json.loads(json.dumps(r)) == r


class TestPollingReadsEveryOrderAndAppendsOnly:
    def test_each_order_is_read_and_yields_one_record(self):
        rail = ScriptedRail(
            {"o1": {"order_status": "PAID"}, "o2": {"order_status": "PAID"}},
            {"o1": [{"authorization": {"captured_amount": 470.0}}], "o2": [{"authorization": {}}]})
        state = {"started_at": T0.isoformat(), "orders": [
            {"label": "capture", "order_id": "o1"}, {"label": "control", "order_id": "o2"}]}
        recs = poll(rail, state, T0 + timedelta(hours=1))
        assert [r["label"] for r in recs] == ["capture", "control"]
        assert [r["elapsed_seconds"] for r in recs] == [3600, 3600]
        assert rail.asked == [("order", "o1"), ("payments", "o1"), ("refunds", "o1"),
                              ("order", "o2"), ("payments", "o2"), ("refunds", "o2")]
        assert recs[0]["refunds_http_status"] == 200 and recs[0]["refunds"] == []

    def test_a_failed_read_is_recorded_as_a_failure_not_dropped(self):
        class Down(ScriptedRail):
            def fetch_order(self, order_id):
                return 503, {"message": "unavailable"}
        rail = Down({}, {"o1": []})
        state = {"started_at": T0.isoformat(), "orders": [{"label": "capture", "order_id": "o1"}]}
        (rec,) = poll(rail, state, T0)
        assert rec["order_http_status"] == 503

    def test_the_log_only_ever_grows(self, tmp_path):
        log = tmp_path / "observations.jsonl"
        append_record(log, {"n": 1})
        append_record(log, {"n": 2})
        assert [json.loads(line) for line in log.read_text().splitlines()] == [{"n": 1}, {"n": 2}]
