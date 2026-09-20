"""The signed real-rail packet says what the rail said, and only that.

The frozen packet the console serves as "the receipt from a real Cashfree run" once
carried an entry, attributed to the rail, saying the uncaptured remainder "is returned
by the rail on its own". No response said so. The bodies below are today's real
sandbox responses (session token and customer details removed).
"""
import pytest

from amanat.evidence.chain import EvidenceChain
from amanat.rails.cashfree import CashfreePreAuthRail
from amanat.rails.cashfree_settle import settle

ORDER = {"order_status": "PAID", "order_amount": 620.0, "order_currency": "INR",
         "order_note": "preauth_transaction"}
PAYMENT_CAPTURED = {
    "cf_payment_id": 1455181890977913344, "payment_status": "SUCCESS",
    "payment_amount": 620.0, "payment_message": "PRE_AUTH|Transaction Success",
    "is_captured": True,
    "authorization": {"action": "CAPTURE", "action_reference": "CAP_12121",
                      "approve_by": "", "captured_amount": 470.0, "end_time": "",
                      "start_time": "", "status": "SUCCESS"}}
CAPTURE_RESPONSE = {"authorization": PAYMENT_CAPTURED["authorization"],
                    "payment_message": "PRE_AUTH|Transaction Success"}


class ScriptedCashfree(CashfreePreAuthRail):
    """The real adapter with only the network replaced by today's real response shapes."""

    def __init__(self):
        super().__init__(client_id="id", client_secret="secret")

    def create_preauth_order(self, order_id, ceiling, *, customer):
        return 200, {"payment_session_id": "session_x", "order_id": order_id}

    def pay_upi_collect(self, payment_session_id, vpa=None):
        return 200, {"cf_payment_id": "1455181890977913344"}

    def simulate_success(self, cf_payment_id):
        return 200, {}

    def capture(self, order_id, amount):
        return 200, CAPTURE_RESPONSE

    def fetch_order(self, order_id):
        return 200, ORDER

    def fetch_payments(self, order_id):
        return 200, [PAYMENT_CAPTURED]


@pytest.fixture(scope="module")
def packet():
    return settle(ScriptedCashfree())


def _entries(packet, **match):
    return [e for e in packet["entries"]
            if all(e["payload"].get(k) == v or getattr(e, k, None) == v
                   for k, v in match.items())]


class TestNoUnreadReleaseIsWrittenIntoTheChain:
    def test_no_entry_attributes_a_release_to_the_rail(self, packet):
        rail_entries = [e for e in packet["entries"] if e["actor"] == "rail"]
        assert not any(e["payload"].get("action") == "release" for e in rail_entries)
        assert not any(e["payload"].get("outcome") == "auto_released" for e in rail_entries)

    def test_no_entry_asserts_the_remainder_returned_on_its_own(self, packet):
        text = " ".join(str(e["payload"]) for e in packet["entries"]).lower()
        assert "on its own" not in text and "returned by the rail" not in text

    def test_the_gap_is_stated_as_the_orchestrators_not_the_rails(self, packet):
        (note,) = [e for e in packet["entries"]
                   if e["payload"].get("rule") == "remainder_release_unverified"]
        assert note["actor"] == "policy"
        assert note["payload"]["amount"] == 150_00
        assert note["payload"]["hold_expiry_days"] == 7

    def test_what_the_rail_reported_after_the_capture_is_recorded_as_read(self, packet):
        (obs,) = [e for e in packet["entries"] if e["payload"].get("action") == "observe"]
        assert obs["actor"] == "rail" and obs["payload"]["outcome"] == "observed"
        state = obs["payload"]["state"]
        assert state["is_captured"] is True
        assert state["captured_amount"] == 470_00 and state["payment_amount"] == 620_00
        assert state["order_status"] == "PAID"


class TestThePacketIsPortable:
    def test_every_amount_is_integer_paise_so_a_browser_can_reproduce_the_hashes(self, packet):
        assert packet["canonicalization"] == "jcs-int"
        EvidenceChain.verify_packet(packet)          # would raise on any float

    def test_the_debit_is_the_amount_the_rail_stated_it_captured(self, packet):
        (debit,) = [e for e in packet["entries"]
                    if e["payload"].get("action") == "debit"
                    and e["payload"].get("outcome") == "applied"]
        assert debit["payload"]["captured_amount"] == 470_00

    def test_the_refused_over_budget_ceiling_is_still_in_the_chain(self, packet):
        assert any(e["event_type"] == "refusal" for e in packet["entries"])
