"""Probes: declarative, recorded, and unable to mistake a broken run for a finding.

A probe is an ordered list of named harness operations plus rules that read the exchanges into
findings about capabilities. The runner records every exchange (redacted, never headers) as one
line in the evidence store. The hazard this suite guards is a *false negative from a bad run*:
a 401 from wrong credentials, a 503, a timeout or a failed precondition must never become
"this rail does not support X". Only an answer to the question is a finding.

Nothing here touches a network: a scripted harness stands in for a rail.
"""
import json

import pytest

from amanat.probes import model, runner
from amanat.probes.model import Exchange, OpResult, Probe, Ref, Rule, Step
from amanat.rails.semantics import Environment
from amanat.registry import store

T0 = "2026-09-20T10:00:00Z"


class Scripted:
    """A harness whose operations return scripted answers, in order, per operation name."""

    rail_id, environment, name = "r", Environment.SANDBOX, "scripted/sandbox"

    def __init__(self, **answers):
        self.answers = {k: list(v) if isinstance(v, list) else [v] for k, v in answers.items()}
        self.calls = []

    @property
    def ops(self):
        return frozenset(self.answers)

    def run_op(self, op, args):
        self.calls.append((op, args))
        a = self.answers[op].pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


def ex(label, status, response=None, request=None, error=None):
    return Exchange(label=label, request=request or {"method": "POST", "path": f"/{label}"},
                    status=status, response=response if response is not None else {}, error=error, at=T0)


def answer(status, body=None, *, label="x", handle=None, ok=True):
    return OpResult([ex(label, status, body)], handle=handle, ok=ok)


def probe(steps, rules, probe_id="r.p"):
    return Probe(probe_id=probe_id, rail_id="r", environment=Environment.SANDBOX,
                 steps=tuple(steps), rules=tuple(rules), summary="test")


def finding(obs, capability):
    return next(f for f in obs["findings"] if f["capability"] == capability)


class TestStatusClasses:
    @pytest.mark.parametrize("status,klass", [
        (200, "2xx"), (201, "2xx"), (204, "2xx"), (400, "4xx"), (404, "4xx"), (409, "4xx"), (422, "4xx"),
        (401, "inconclusive"), (403, "inconclusive"), (408, "inconclusive"), (429, "inconclusive"),
        (500, "inconclusive"), (502, "inconclusive"), (503, "inconclusive"), (None, "inconclusive"),
        (302, "inconclusive"),
    ])
    def test_only_a_2xx_or_a_semantic_4xx_is_an_answer(self, status, klass):
        assert model.status_class(status) == klass


CAPTURE = Rule("partial_debit", step="capture", status="2xx", supported=True,
               basis="a capture below the hold returned HTTP {status}")
REFUSED = Rule("partial_debit", step="capture", status="4xx", supported=False,
               basis="a capture below the hold was refused with HTTP {status}: {message}")
STEPS = [Step("hold", "hold", {"amount": 62000}), Step("capture", "capture", {"hold": Ref("hold"), "amount": 47000})]


def run(harness, rules=(CAPTURE, REFUSED), steps=STEPS):
    return runner.run(probe(steps, rules), harness, clock=lambda: T0)


class TestFindings:
    def test_a_2xx_supports_the_capability(self):
        h = Scripted(hold=answer(200, handle={"id": "o1"}), capture=answer(200, {"ok": 1}))
        f = finding(run(h), "partial_debit")
        assert f["supported"] is True and "HTTP 200" in f["basis"] and f["step"] == "capture"

    def test_a_4xx_refuses_it_and_the_message_is_carried(self):
        h = Scripted(hold=answer(200, handle={"id": "o1"}),
                     capture=answer(400, {"message": "Capture amount must be equal"}))
        f = finding(run(h), "partial_debit")
        assert f["supported"] is False and "Capture amount must be equal" in f["basis"]

    @pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
    def test_an_error_that_is_not_an_answer_is_inconclusive_never_a_refusal(self, status):
        h = Scripted(hold=answer(200, handle={"id": "o1"}), capture=answer(status, {"message": "nope"}))
        f = finding(run(h), "partial_debit")
        assert f["supported"] is None and "inconclusive" in f["basis"] and str(status) in f["basis"]

    def test_a_transport_failure_is_inconclusive(self):
        h = Scripted(hold=answer(200, handle={"id": "o1"}),
                     capture=OpResult([ex("capture", None, None, error="ReadTimeout: timed out")], ok=False))
        f = finding(run(h), "partial_debit")
        assert f["supported"] is None and "ReadTimeout" in f["basis"]

    def test_the_first_matching_rule_wins(self):
        rules = [Rule("c", "capture", True, "first", status="2xx"), Rule("c", "capture", False, "second", status="2xx")]
        h = Scripted(hold=answer(200, handle=1), capture=answer(200))
        assert finding(run(h, rules), "c")["basis"] == "first"

    def test_a_condition_on_the_body_must_hold(self):
        rules = [Rule("held", "look", True, "paid and not captured", status="2xx",
                      where=(("order_status", "PAID"), ("payments.0.is_captured", False))),
                 Rule("held", "look", False, "not held", status="2xx")]
        steps = [Step("look", "look", {})]
        yes = Scripted(look=answer(200, {"order_status": "PAID", "payments": [{"is_captured": False}]}))
        no = Scripted(look=answer(200, {"order_status": "PAID", "payments": [{"is_captured": True}]}))
        assert finding(run(yes, rules, steps), "held")["supported"] is True
        assert finding(run(no, rules, steps), "held")["supported"] is False

    def test_a_boolean_is_not_a_number(self):
        """`is_captured: 0` is not `is_captured: false`, and `1` is not `true`."""
        steps = [Step("look", "look", {})]
        for want, got in ((False, 0), (True, 1), (0, False), (1, True)):
            rules = [Rule("c", "look", True, "x", where=(("n", want),))]
            h = Scripted(look=answer(200, {"n": got}))
            assert finding(run(h, rules, steps), "c")["supported"] is None, (want, got)

    def test_numbers_compare_by_value_whatever_their_json_spelling(self):
        rules = [Rule("c", "look", True, "x", where=(("amount", 470),))]
        h = Scripted(look=answer(200, {"amount": 470.0}))
        assert finding(run(h, rules, [Step("look", "look", {})]), "c")["supported"] is True

    def test_a_missing_path_does_not_match(self):
        rules = [Rule("c", "look", True, "x", where=(("a.b", 1),))]
        h = Scripted(look=answer(200, {"a": {}}))
        assert finding(run(h, rules, [Step("look", "look", {})]), "c")["supported"] is None

    def test_a_rule_can_read_a_named_exchange_of_a_step(self):
        rules = [Rule("c", "look", True, "from the payments call", label="payments", status="2xx")]
        h = Scripted(look=OpResult([ex("order", 500), ex("payments", 200)]))
        assert finding(run(h, rules, [Step("look", "look", {})]), "c")["supported"] is True

    def test_the_default_exchange_of_a_step_is_its_last(self):
        rules = [Rule("c", "look", True, "last", status="2xx")]
        h = Scripted(look=OpResult([ex("first", 500), ex("second", 200)]))
        assert finding(run(h, rules, [Step("look", "look", {})]), "c")["supported"] is True

    def test_every_capability_a_probe_speaks_to_gets_a_finding(self):
        h = Scripted(hold=answer(200, handle=1), capture=answer(200))
        assert [f["capability"] for f in run(h)["findings"]] == ["partial_debit"]


class TestARuleCanRequireAnEarlierStepToHaveSucceeded:
    """A second capture refused means nothing if the first capture never happened."""

    RULES = [Rule("multiple_captures", "capture2", True, "the second capture returned HTTP {status}",
                  status="2xx", after=(("capture1", "2xx"),)),
             Rule("multiple_captures", "capture2", False, "the second capture was refused: {message}",
                  status="4xx", after=(("capture1", "2xx"),))]
    STEPS = [Step("hold", "hold", {}), Step("capture1", "capture", {"hold": Ref("hold")}),
             Step("capture2", "capture", {"hold": Ref("hold")})]

    def _go(self, first, second):
        h = Scripted(hold=answer(200, handle=1), capture=[answer(*first), answer(*second)])
        return finding(run(h, self.RULES, self.STEPS), "multiple_captures")

    def test_a_refusal_after_a_successful_first_capture_is_a_finding(self):
        f = self._go((200,), (400, {"message": "already captured"}))
        assert f["supported"] is False and "already captured" in f["basis"]

    def test_a_second_success_after_a_first_is_a_finding(self):
        assert self._go((200,), (200,))["supported"] is True

    def test_a_refusal_after_a_failed_first_capture_is_not(self):
        f = self._go((400, {"message": "bad request"}), (400, {"message": "bad request"}))
        assert f["supported"] is None and "capture1" in f["basis"]

    def test_a_refusal_after_a_first_capture_that_gave_no_answer_is_not_either(self):
        f = self._go((503,), (400, {"message": "x"}))
        assert f["supported"] is None


class TestPreconditions:
    def test_a_step_that_depends_on_a_failed_step_is_skipped_and_its_findings_are_inconclusive(self):
        h = Scripted(hold=OpResult([ex("order_create", 500)], ok=False), capture=answer(200))
        obs = run(h)
        assert [c[0] for c in h.calls] == ["hold"]                       # capture never ran
        assert obs["skipped"] == [{"step": "capture", "why": "depends on 'hold', which did not complete"}]
        f = finding(obs, "partial_debit")
        assert f["supported"] is None and "hold" in f["basis"]

    def test_independent_steps_still_run_after_a_failure(self):
        steps = [Step("a", "a", {}), Step("b", "b", {"x": Ref("a")}), Step("c", "c", {})]
        h = Scripted(a=OpResult([ex("a", 500)], ok=False), b=answer(200), c=answer(200))
        run(h, [], steps)
        assert [c[0] for c in h.calls] == ["a", "c"]

    def test_a_handle_is_passed_to_the_steps_that_ask_for_it(self):
        h = Scripted(hold=answer(200, handle={"order_id": "o9"}), capture=answer(200))
        run(h)
        assert h.calls[1] == ("capture", {"hold": {"order_id": "o9"}, "amount": 47000})

    def test_an_exception_in_an_operation_is_recorded_not_raised(self):
        h = Scripted(hold=answer(200, handle=1), capture=RuntimeError("boom"))
        obs = run(h)
        err = [e for e in obs["exchanges"] if e["error"]]
        assert err and "RuntimeError: boom" in err[0]["error"]
        assert finding(obs, "partial_debit")["supported"] is None

    def test_an_interrupt_is_not_swallowed(self):
        h = Scripted(hold=answer(200, handle=1), capture=KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            run(h)


class TestTheObservationRecord:
    def _obs(self):
        h = Scripted(hold=OpResult([ex("order", 200, {"payment_amount": 620.0, "note": "₹150"})], handle=1),
                     capture=answer(200, {"authorization": {"captured_amount": 470.0}}, label="capture"))
        return run(h)

    def test_it_says_what_was_asked_and_on_what(self):
        o = self._obs()
        assert o["probe_id"] == "r.p" and o["rail_id"] == "r" and o["environment"] == "sandbox"
        assert o["harness"] == "scripted/sandbox" and o["started_at"] == o["finished_at"] == T0
        assert [s["name"] for s in o["definition"]["steps"]] == ["hold", "capture"]
        assert len(o["definition_sha256"]) == 64

    def test_exchanges_are_in_order_with_their_step_and_operation(self):
        o = self._obs()
        assert [(e["step"], e["op"], e["label"]) for e in o["exchanges"]] == [
            ("hold", "hold", "order"), ("capture", "capture", "capture")]

    def test_it_is_plain_json_including_floats_and_unicode(self):
        o = self._obs()
        assert json.loads(json.dumps(o, allow_nan=False, ensure_ascii=False)) == o
        assert o["exchanges"][0]["response"]["payment_amount"] == 620.0

    def test_the_definition_hash_changes_when_the_question_changes(self):
        h1 = Scripted(hold=answer(200, handle=1), capture=answer(200))
        h2 = Scripted(hold=answer(200, handle=1), capture=answer(200))
        other = [Step("hold", "hold", {"amount": 62000}), Step("capture", "capture", {"hold": Ref("hold"), "amount": 99})]
        assert run(h1)["definition_sha256"] != run(h2, steps=other)["definition_sha256"]


class TestRedaction:
    SECRET = "s3cr3t-canary-value"

    def test_a_pre_authorization_object_is_not_mistaken_for_a_credential(self):
        """Cashfree's response has a body key called `authorization`; it is the finding."""
        body = {"authorization": {"action": "CAPTURE", "captured_amount": 470.0}}
        assert runner.redact(body) == body

    def test_secret_keys_are_dropped_at_any_depth(self):
        out = runner.redact({"payment_session_id": "x", "a": [{"customer_details": {"phone": "1"}, "ok": 1}],
                             "access_token": "y", "x-client-secret": "z", "keep": 2})
        assert out == {"a": [{"ok": 1}], "keep": 2}

    def test_known_secret_values_are_scrubbed_from_every_string(self):
        out = runner.redact({"m": f"bad key {self.SECRET} here", "n": [self.SECRET]}, secrets=[self.SECRET])
        assert self.SECRET not in json.dumps(out) and out["m"] == "bad key [redacted] here"

    def test_a_secret_that_is_too_short_to_scrub_safely_is_refused(self):
        """Scrubbing '1' would mangle every number; refuse rather than corrupt the record."""
        with pytest.raises(ValueError):
            runner.redact({}, secrets=["1"])

    def test_the_stored_run_never_contains_a_secret_from_a_body_or_an_error(self):
        h = Scripted(
            hold=OpResult([ex("order", 200, {"payment_session_id": "sess_1", "note": f"key={self.SECRET}"},
                              request={"method": "POST", "path": "/orders", "json": {"customer_details": {"x": 1}}})],
                          handle=1),
            capture=OpResult([ex("capture", None, None, error=f"ConnectError: {self.SECRET}")], ok=False))
        h.secrets = [self.SECRET]
        text = json.dumps(run(h))
        assert self.SECRET not in text and "sess_1" not in text and "customer_details" not in text


class TestRecording:
    def test_a_run_is_one_record_and_is_found_by_its_hash(self, tmp_path):
        path = tmp_path / "probes.r.jsonl"
        h = Scripted(hold=answer(200, handle=1), capture=answer(200))
        obs = run(h)
        ev = runner.record(obs, path, at=T0)
        rec = store.find(path, ev)
        assert rec["kind"] == "probe_run" and rec["data"]["probe_id"] == "r.p"

    def test_the_latest_conclusive_finding_ignores_a_run_that_could_not_answer(self, tmp_path):
        path = tmp_path / "p.jsonl"
        good = Scripted(hold=answer(200, handle=1), capture=answer(200))
        runner.record(run(good), path, at="2026-09-20T10:00:00Z")
        bad = Scripted(hold=answer(200, handle=1), capture=answer(503))
        runner.record(run(bad), path, at="2026-09-21T10:00:00Z")
        latest = runner.latest_findings(path)[("r.p", "partial_debit")]
        assert latest["supported"] is True and latest["observed_at"] == "2026-09-20T10:00:00Z"

    def test_a_later_conclusive_run_replaces_an_earlier_one(self, tmp_path):
        path = tmp_path / "p.jsonl"
        runner.record(run(Scripted(hold=answer(200, handle=1), capture=answer(200))), path, at="2026-09-20T10:00:00Z")
        runner.record(run(Scripted(hold=answer(200, handle=1), capture=answer(400, {"message": "m"}))), path,
                      at="2026-09-22T10:00:00Z")
        latest = runner.latest_findings(path)[("r.p", "partial_debit")]
        assert latest["supported"] is False and latest["observed_at"] == "2026-09-22T10:00:00Z"

    def test_the_history_lists_each_conclusive_answer_and_the_days_it_changed(self, tmp_path):
        path = tmp_path / "p.jsonl"
        for at, cap in (("2026-09-20T10:00:00Z", answer(200)), ("2026-09-21T10:00:00Z", answer(200)),
                        ("2026-09-22T10:00:00Z", answer(400, {"message": "m"}))):
            runner.record(run(Scripted(hold=answer(200, handle=1), capture=cap)), path, at=at)
        hist = runner.history(path)[("r.p", "partial_debit")]
        assert [h["supported"] for h in hist] == [True, True, False]
        assert runner.changes(hist) == [{"on": "2026-09-22", "from": True, "to": False}]

    def test_an_empty_store_has_no_findings(self, tmp_path):
        assert runner.latest_findings(tmp_path / "none.jsonl") == {}

    def test_the_default_stream_is_named_for_the_rail(self):
        assert runner.stream_for("cashfree_preauth") == "probes.cashfree_preauth"
