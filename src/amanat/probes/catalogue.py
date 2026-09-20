"""The probes we run, rail by rail. Each is a question and the exact calls that ask it.

A probe places a fresh hold and asks one thing of it, so a refusal is never confused with
the debris of an earlier step. A rule reads a step's answer into a finding about one capability,
and a rule that needs an earlier step to have succeeded says so (`after`): a second capture
refused means nothing if the first never happened.

Amounts are integer paise: a ₹620 hold, ₹470 the partial capture, ₹700 the over-capture.
"""
from __future__ import annotations

from amanat.probes.model import Probe, Ref, Rule, Step
from amanat.rails.semantics import Environment

HOLD, PART, OVER = 62_000, 47_000, 70_000
FIRST, SECOND, THIRD = 30_000, 20_000, 10_000

_HOLD = Step("hold", "hold", {"amount": HOLD})
_H = Ref("hold")


def _cashfree(name: str, summary: str, steps: list[Step], rules: list[Rule]) -> Probe:
    return Probe(f"cashfree_preauth.{name}", "cashfree_preauth", Environment.SANDBOX,
                 (_HOLD, *steps), tuple(rules), summary)


def _answers(capability: str, step: str, yes: str, no: str, *, after=(), where=()) -> list[Rule]:
    """The two rules almost every probe has: a 2xx supports it, a semantic 4xx refuses it."""
    return [Rule(capability, step, True, yes, status="2xx", after=after, where=where),
            Rule(capability, step, False, no, status="4xx", after=after)]


CASHFREE = [
    _cashfree(
        "partial_capture",
        "Hold ₹620, capture ₹470 of it, and read the order back. Does the rail accept a debit below "
        "the hold, and is the authorised payment a hold (not yet captured)?",
        [Step("capture", "capture", {"hold": _H, "amount": PART}), Step("observe", "fetch", {"hold": _H})],
        [*_answers("partial_debit", "capture",
                   "a capture of ₹470 against a ₹620 hold returned HTTP {status}",
                   "a capture of ₹470 against a ₹620 hold was refused with HTTP {status}: {message}"),
         Rule("funds_held_in_customer_account", "hold", True,
              "after authorisation the payment reads is_captured false and payment_status SUCCESS: "
              "authorised, not yet captured", status="2xx", label="payments",
              where=(("0.is_captured", False), ("0.payment_status", "SUCCESS"))),
         Rule("funds_held_in_customer_account", "hold", False,
              "straight after authorisation the payment already reads is_captured true",
              status="2xx", label="payments", where=(("0.is_captured", True),))]),
    _cashfree(
        "void_whole_hold",
        "Hold ₹620 and void it without capturing anything. Can a hold be released whole?",
        [Step("void", "void", {"hold": _H}), Step("observe", "fetch", {"hold": _H})],
        _answers("void_whole_hold", "void",
                 "voiding a ₹620 hold that nothing was captured from returned HTTP {status}",
                 "voiding a ₹620 hold that nothing was captured from was refused with HTTP {status}: {message}")),
    _cashfree(
        "void_after_partial_capture",
        "Hold ₹620, capture ₹470, then try to void. Can the remainder be released by a void?",
        [Step("capture", "capture", {"hold": _H, "amount": PART}), Step("void", "void", {"hold": _H})],
        _answers("void_after_partial_capture", "void",
                 "a void after a ₹470 capture returned HTTP {status}",
                 "a void after a ₹470 capture was refused with HTTP {status}: {message}",
                 after=(("capture", "2xx"),))),
    _cashfree(
        "over_capture",
        "Hold ₹620 and try to capture ₹700. Can a capture exceed the hold?",
        [Step("capture", "capture", {"hold": _H, "amount": OVER})],
        _answers("over_capture", "capture",
                 "a capture of ₹700 against a ₹620 hold returned HTTP {status}",
                 "a capture of ₹700 against a ₹620 hold was refused with HTTP {status}: {message}")),
    _cashfree(
        "capture_after_void",
        "Hold ₹620, void it, then try to capture. Can a voided hold still be captured?",
        [Step("void", "void", {"hold": _H}), Step("capture", "capture", {"hold": _H, "amount": PART})],
        _answers("capture_after_void", "capture",
                 "a capture after a void returned HTTP {status}",
                 "a capture after a void was refused with HTTP {status}: {message}",
                 after=(("void", "2xx"),))),
    _cashfree(
        "double_capture",
        "Hold ₹620, capture ₹300, then capture ₹200 more. Can one hold be drawn down in steps?",
        [Step("capture1", "capture", {"hold": _H, "amount": FIRST}),
         Step("capture2", "capture", {"hold": _H, "amount": SECOND})],
        _answers("multiple_captures", "capture2",
                 "a second capture of ₹200 after a first of ₹300 returned HTTP {status}",
                 "a second capture of ₹200 after a first of ₹300 was refused with HTTP {status}: {message}",
                 after=(("capture1", "2xx"),))),
    _cashfree(
        "idempotent_capture_replay",
        "Hold ₹620, capture ₹470 with an idempotency key, repeat the same call with the same key, then "
        "repeat it once more with a different key as a control. If the same key returns the first result "
        "and the different key is refused, the key is what the rail honours.",
        [Step("capture1", "capture", {"hold": _H, "amount": PART, "idempotency_key": "k1"}),
         Step("replay", "capture", {"hold": _H, "amount": PART, "idempotency_key": "k1"}),
         Step("control", "capture", {"hold": _H, "amount": PART, "idempotency_key": "k2"})],
        [Rule("idempotent_capture_replay", "replay", True,
              "repeating the capture with the same idempotency key returned HTTP {status} with the first "
              "result (captured_amount 470.0), while the same call under a different key was refused",
              status="2xx", after=(("capture1", "2xx"), ("control", "4xx")),
              where=(("authorization.captured_amount", 470.0),)),
         Rule("idempotent_capture_replay", "replay", False,
              "repeating the capture with the same idempotency key was refused as a second capture "
              "(HTTP {status}: {message}): the key was not honoured", status="4xx",
              after=(("capture1", "2xx"),))]),
    _cashfree(
        "concurrent_capture",
        "Hold ₹620 and fire three captures (₹300, ₹200, ₹100) at the same instant. Does exactly one win?",
        [Step("race", "capture_parallel", {"hold": _H, "amounts": [FIRST, SECOND, THIRD]})],
        [Rule("concurrent_capture_single_winner", "race", True,
              "exactly one of three simultaneous captures succeeded and the other two were refused",
              status="2xx", label="summary", where=(("single_winner", True),)),
         Rule("concurrent_capture_single_winner", "race", False,
              "more than one of three simultaneous captures succeeded: the hold was drawn more than once",
              status="2xx", label="summary", where=(("multiple_winners", True),))]),
]

PROBES: dict[str, Probe] = {p.probe_id: p for p in CASHFREE}


def for_rail(rail_id: str) -> list[Probe]:
    return [p for p in PROBES.values() if p.rail_id == rail_id]
