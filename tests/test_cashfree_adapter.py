"""The Cashfree adapter records what the rail said — and does not fill in what it did not.

The live probe once wrote "the remainder came back on its own" into a signed
chain, as a rail transition, on the strength of a refused VOID. A refused VOID
says a capture exists; it does not say where the uncaptured remainder went. These
tests pin the adapter to what a response can show.
"""
import pytest

from amanat.rails.base import BlockRef, BlockState, RailError
from amanat.rails.cashfree import CashfreePreAuthRail, _paise, _rupees

# The shape the sandbox returned on 29 Aug 2026 for CAPTURE ₹470 against a ₹620 hold.
CAPTURE_OK = {"authorization": {"action": "CAPTURE", "status": "SUCCESS",
                                "captured_amount": 470.0},
              "payment_message": "PRE_AUTH|Transaction Success"}
VOID_REFUSED = {"message": "Capture request already exist for the void"}


class ScriptedCashfree(CashfreePreAuthRail):
    """The real adapter with only the network replaced by scripted responses."""

    def __init__(self, script):
        super().__init__(client_id="id", client_secret="secret")
        self.script, self.sent = list(script), []

    def _call(self, method, path, **kw):
        self.sent.append((method, path, kw.get("json")))
        return self.script.pop(0)


def _held(ceiling=620_00):
    return BlockRef(block_id="order_1", rail_id="cashfree_preauth", ceiling=ceiling)


class TestAPartialCaptureDoesNotClaimTheRemainderCameBack:
    def test_the_remainder_is_not_recorded_as_released(self):
        ref = _held()
        ScriptedCashfree([(200, CAPTURE_OK)]).debit(ref, 470_00)
        assert ref.debited == 470_00
        assert ref.released == 0 and ref.available == 150_00
        assert not any("released" in e.lower() for e in ref.events)   # no event claims it happened

    def test_the_block_is_captured_not_settled(self):
        ref = _held()
        ScriptedCashfree([(200, CAPTURE_OK)]).debit(ref, 470_00)
        assert ref.state is BlockState.CAPTURED

    def test_releasing_after_a_capture_is_refused_on_the_rails_own_rule(self):
        ref = _held()
        rail = ScriptedCashfree([(200, CAPTURE_OK)])
        rail.debit(ref, 470_00)
        with pytest.raises(RailError) as exc:
            rail.release(ref)
        assert "cannot be voided" in str(exc.value)
        assert "returned" not in str(exc.value)
        assert len(rail.sent) == 1                       # no VOID was even attempted

    def test_releasing_a_hold_nothing_was_captured_from_voids_the_whole_of_it(self):
        ref = _held()
        rail = ScriptedCashfree([(200, {"authorization": {"action": "VOID"}})])
        rail.release(ref)
        assert ref.released == 620_00 and ref.state is BlockState.REVOKED
        assert rail.sent[0][2] == {"action": "VOID"}

    def test_a_capture_larger_than_the_hold_is_refused_before_any_call(self):
        rail = ScriptedCashfree([])
        with pytest.raises(RailError):
            rail.debit(_held(), 700_00)
        assert rail.sent == []

    def test_the_captured_amount_is_what_the_response_says_not_what_was_asked(self):
        ref = _held()
        ScriptedCashfree([(200, {"authorization": {"captured_amount": 300.0}})]).debit(ref, 470_00)
        assert ref.debited == 300_00

    def test_a_refused_capture_changes_nothing(self):
        ref = _held()
        with pytest.raises(RailError, match="HTTP 400"):
            ScriptedCashfree([(400, VOID_REFUSED)]).debit(ref, 470_00)
        assert ref.debited == 0 and ref.state is BlockState.BLOCKED


class TestMoneyConversionAtTheApiEdge:
    """Cashfree takes rupees as a decimal. Inside the system money is integer paise,
    and the conversion must be exact both ways — never rounded to a plausible value."""

    @pytest.mark.parametrize("rupees, paise", [
        (470.0, 47_000), (0.07, 7), (1234.56, 123_456), ("620.00", 62_000), (0, 0)])
    def test_rupees_from_the_api_become_exact_paise(self, rupees, paise):
        assert _paise(rupees) == paise

    @pytest.mark.parametrize("rupees", [1.005, "0.001", 10.123])
    def test_a_fraction_of_a_paisa_is_refused_not_rounded(self, rupees):
        with pytest.raises(RailError, match="whole"):
            _paise(rupees)

    @pytest.mark.parametrize("paise, rupees", [(47_000, 470.0), (7, 0.07), (123_456, 1234.56)])
    def test_paise_become_the_rupees_the_request_body_carries(self, paise, rupees):
        assert _rupees(paise) == rupees


class TestACaptureThatStatesNoAmountIsNotAssumed:
    def test_a_200_without_the_captured_amount_is_not_taken_to_have_captured_what_was_asked(self):
        ref = _held()
        with pytest.raises(RailError, match="did not state"):
            ScriptedCashfree([(200, {"authorization": {"status": "SUCCESS"}})]).debit(ref, 470_00)
        assert ref.debited == 0

    def test_a_second_capture_is_refused_on_the_rails_documented_single_shot_rule(self):
        ref = _held()
        rail = ScriptedCashfree([(200, CAPTURE_OK)])
        rail.debit(ref, 470_00)
        with pytest.raises(RailError, match="captured or voided once"):
            rail.debit(ref, 50_00)
        assert len(rail.sent) == 1
