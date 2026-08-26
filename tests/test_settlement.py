"""Amount-contingent settlement on Razorpay's real verbs.

Razorpay cannot block-and-partial-debit — its capture must equal the authorized
amount (measured, see test_semantics). The same *net* outcome is reachable a
different way: capture the full ceiling, then refund the difference. The money
semantics are worse — the customer is out the full ceiling until the refund
settles, and the merchant pays MDR on the ceiling — and the evidence chain is
what makes that difference legible rather than hidden.

These tests drive the settlement through a fake transport, so they are
deterministic and need no credentials. The live path is the same code against
`RazorpayTestRail`.
"""
import pytest

from amanat.evidence.chain import EventType
from amanat.rails.settlement import settle_capture_refund


class FakeRazorpay:
    """Canned Razorpay responses, and a log of what was called."""

    rail_id = "razorpay_auth_capture"

    def __init__(self, authorized: int, status: str = "authorized") -> None:
        self._authorized = authorized
        self._status = status
        self.calls: list[tuple] = []

    def fetch_payment(self, payment_id: str) -> dict:
        self.calls.append(("fetch", payment_id))
        return {"id": payment_id, "amount": self._authorized,
                "status": self._status, "order_id": "order_FAKE",
                "amount_refunded": 0}

    def capture(self, payment_id: str, amount: int):
        self.calls.append(("capture", payment_id, amount))
        if amount != self._authorized:
            return 400, {"error": {"description":
                         "Capture amount must be equal to the amount authorized"}}
        self._status = "captured"
        return 200, {"id": payment_id, "amount": amount, "status": "captured"}

    def refund(self, payment_id: str, amount: int):
        self.calls.append(("refund", payment_id, amount))
        return 200, {"id": "rfnd_FAKE", "amount": amount, "status": "processed"}


class TestTheHappyPath:
    def test_capture_full_then_refund_the_difference(self):
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)

        assert r.ok
        assert r.ceiling == 620_00
        assert r.actual == 470_00
        assert r.refunded == 150_00
        assert r.net == 470_00
        assert r.refund_id == "rfnd_FAKE"

    def test_it_captures_the_full_ceiling_never_a_partial(self):
        """The whole reason this path exists: partial capture is forbidden."""
        rail = FakeRazorpay(authorized=620_00)
        settle_capture_refund(rail, "pay_X", actual=470_00)
        capture_calls = [c for c in rail.calls if c[0] == "capture"]
        assert capture_calls == [("capture", "pay_X", 620_00)]

    def test_exact_fare_needs_no_refund(self):
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=620_00)
        assert r.ok
        assert r.refunded == 0
        assert not any(c[0] == "refund" for c in rail.calls)


class TestTheGate:
    def test_settling_above_the_ceiling_is_refused(self):
        """actual <= ceiling is the amount-contingent invariant."""
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=700_00)
        assert r.ok is False
        assert "exceeds" in r.detail.lower()

    def test_a_refused_settlement_moves_no_money(self):
        rail = FakeRazorpay(authorized=620_00)
        settle_capture_refund(rail, "pay_X", actual=700_00)
        assert not any(c[0] in ("capture", "refund") for c in rail.calls)

    def test_a_refusal_is_recorded_in_the_chain(self):
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=700_00)
        assert len(r.chain.refusals()) == 1


class TestTheEvidenceChainTellsTheHonestStory:
    def test_the_transitions_are_named_for_what_they_are(self):
        """AUTHORIZED then CAPTURED then REFUNDED — not BLOCKED/DEBITED/RELEASED.

        The pitch is 'same intent, two rails, two different signed chains'. If
        this chain used the SBMD verbs it would erase the difference the whole
        project is trying to make legible.
        """
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        transitions = [e.payload["transition"] for e in r.chain.rail_transitions()]
        assert transitions == ["AUTHORIZED", "CAPTURED", "REFUNDED"]

    def test_real_rail_reference_ids_are_recorded(self):
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        refs = {e.payload["transition"]: e.payload.get("ref")
                for e in r.chain.rail_transitions()}
        assert refs["CAPTURED"] == "pay_X"
        assert refs["REFUNDED"] == "rfnd_FAKE"

    def test_the_money_semantics_cost_is_recorded_not_hidden(self):
        """The customer is out the full ceiling until the refund settles.

        On SBMD only the actual amount ever moves. That difference is the honest
        weakness of this rail, so it is written into the chain, not omitted.
        """
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        decisions = [e for e in r.chain.entries
                     if e.event_type is EventType.POLICY_DECISION]
        blob = " ".join(str(e.payload) for e in decisions).lower()
        assert "mdr" in blob or "settles" in blob or "full ceiling" in blob

    def test_the_chain_verifies_standalone(self):
        rail = FakeRazorpay(authorized=620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        r.chain.verify()


class TestAlreadyCaptured:
    def test_a_captured_payment_skips_capture_and_refunds(self):
        """Yesterday's payment link left a captured payment. Still settleable."""
        rail = FakeRazorpay(authorized=620_00, status="captured")
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        assert r.ok
        assert not any(c[0] == "capture" for c in rail.calls)
        assert r.refunded == 150_00

    def test_an_unpayable_status_is_refused(self):
        rail = FakeRazorpay(authorized=620_00, status="failed")
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        assert r.ok is False


class TestDoubleSettlementIsRefused:
    """A payment already partly refunded cannot be settled again.

    Found live: settling an already-refunded payment computed its net against
    the original amount, ignoring the prior refund, and reported a wrong figure.
    """

    def test_prior_refund_blocks_a_second_settlement(self):
        class AlreadyRefunded(FakeRazorpay):
            def fetch_payment(self, payment_id):
                d = super().fetch_payment(payment_id)
                d["amount_refunded"] = 5_00
                return d

        rail = AlreadyRefunded(authorized=620_00, status="captured")
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        assert r.ok is False
        assert "already" in r.detail.lower()
        assert not any(c[0] == "refund" for c in rail.calls)


class FlakyRefund(FakeRazorpay):
    """A rail whose refund fails N times before succeeding (or forever)."""

    def __init__(self, authorized, fail_times, status="authorized"):
        super().__init__(authorized, status)
        self.fail_times = fail_times

    def refund(self, payment_id, amount):
        self.calls.append(("refund", payment_id, amount))
        if self.fail_times > 0:
            self.fail_times -= 1
            return 503, {"error": {"description": "gateway timeout"}}
        return 200, {"id": "rfnd_LATE", "amount": amount, "status": "processed"}


class TestCompensationWhenTheRefundFails:
    """The capture has happened. The customer's ₹620 is gone. The refund fails.

    Before this existed, the chain merely noted the failure and stopped — money
    captured, nothing returned, nothing owed anywhere. That is the one gap a
    payments engineer finds in one question, and it violates the project's own
    'one failure handled gracefully' bar.
    """

    def test_a_transient_refund_failure_is_retried_and_succeeds(self):
        rail = FlakyRefund(620_00, fail_times=1)
        r = settle_capture_refund(rail, "pay_X", actual=470_00, sleep=lambda s: None)
        assert r.ok
        assert r.refund_id == "rfnd_LATE"
        assert [c for c in rail.calls if c[0] == "refund"] == [
            ("refund", "pay_X", 150_00), ("refund", "pay_X", 150_00)]

    def test_the_failed_attempt_is_recorded_in_the_chain(self):
        rail = FlakyRefund(620_00, fail_times=1)
        r = settle_capture_refund(rail, "pay_X", actual=470_00, sleep=lambda s: None)
        outcomes = [e.payload.get("outcome") for e in r.chain.rail_transitions()]
        assert "refund_failed" in outcomes
        assert outcomes[-1] is None or outcomes.count("refund_failed") == 1

    def test_retries_are_bounded(self):
        rail = FlakyRefund(620_00, fail_times=99)
        settle_capture_refund(rail, "pay_X", actual=470_00,
                              refund_attempts=3, sleep=lambda s: None)
        assert len([c for c in rail.calls if c[0] == "refund"]) == 3

    def test_exhausted_retries_raise_a_compensation_flag_not_silence(self):
        rail = FlakyRefund(620_00, fail_times=99)
        r = settle_capture_refund(rail, "pay_X", actual=470_00,
                                  refund_attempts=3, sleep=lambda s: None)
        assert r.ok is False
        assert r.compensation_required is True
        assert r.refund_due == 150_00
        assert "compensation" in r.detail.lower()

    def test_the_compensation_entry_carries_what_is_owed(self):
        from amanat.evidence.chain import EventType
        rail = FlakyRefund(620_00, fail_times=99)
        r = settle_capture_refund(rail, "pay_X", actual=470_00,
                                  refund_attempts=2, sleep=lambda s: None)
        comp = [e for e in r.chain.entries if e.event_type is EventType.COMPENSATION]
        assert len(comp) == 1
        p = comp[0].payload
        assert p["captured"] == 620_00
        assert p["refund_due"] == 150_00
        assert p["attempts"] == 2
        r.chain.verify()

    def test_settlement_resumes_to_complete_the_refund_later(self):
        """Run again once the gateway is back: it finishes the refund, no re-capture."""
        rail = FlakyRefund(620_00, fail_times=99, status="authorized")
        settle_capture_refund(rail, "pay_X", actual=470_00,
                              refund_attempts=1, sleep=lambda s: None)
        rail.fail_times = 0                      # gateway recovered
        r = settle_capture_refund(rail, "pay_X", actual=470_00, sleep=lambda s: None)
        assert r.ok
        assert len([c for c in rail.calls if c[0] == "capture"]) == 1   # never twice
        assert r.refund_id == "rfnd_LATE"


class TestSettlementIsIdempotent:
    def test_rerunning_a_completed_settlement_is_a_no_op_success(self):
        class Done(FakeRazorpay):
            def fetch_payment(self, payment_id):
                d = super().fetch_payment(payment_id)
                d["status"] = "captured"; d["amount_refunded"] = 150_00
                return d
        rail = Done(620_00)
        r = settle_capture_refund(rail, "pay_X", actual=470_00)
        assert r.ok
        assert "already" in r.detail.lower()
        assert not any(c[0] in ("capture", "refund") for c in rail.calls)

    def test_a_mismatched_prior_refund_is_still_refused(self):
        class Partial(FakeRazorpay):
            def fetch_payment(self, payment_id):
                d = super().fetch_payment(payment_id)
                d["status"] = "captured"; d["amount_refunded"] = 5_00
                return d
        r = settle_capture_refund(Partial(620_00), "pay_X", actual=470_00)
        assert r.ok is False
