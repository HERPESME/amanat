"""Reconcile a settlement chain against what the rail actually holds.

The chain says what the money did. Reconciliation asks the rail the same
question independently and reports agreement or drift — the difference between
"our record claims X" and "X is true on Razorpay." Credential-free: a fake rail
returns the authoritative numbers.
"""
import pytest

from amanat.rails.reconcile import reconcile, Reconciliation


class FakeRail:
    rail_id = "razorpay_auth_capture"

    def __init__(self, amount, captured, refunded):
        self._p = {"id": "pay_X", "amount": amount, "status": "captured",
                   "amount_captured": captured, "amount_refunded": refunded}

    def fetch_payment(self, payment_id):
        return dict(self._p)


def _chain_claiming(captured, refunded):
    """A minimal packet asserting a capture and a refund."""
    from amanat.evidence.chain import Actor, EventType, EvidenceChain
    c = EvidenceChain.new(subject="pay_X")
    c.append(Actor.RAIL, EventType.RAIL_TRANSITION,
             {"transition": "CAPTURED", "amount": captured, "ref": "pay_X"})
    if refunded:
        c.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"transition": "REFUNDED", "amount": refunded, "ref": "rfnd_X"})
    return c.export_packet()


class TestAgreement:
    def test_chain_and_rail_agree(self):
        packet = _chain_claiming(captured=620_00, refunded=150_00)
        r = reconcile(FakeRail(620_00, 620_00, 150_00), "pay_X", packet)
        assert r.reconciled is True
        assert r.chain_net == r.rail_net == 470_00
        assert r.drift == 0

    def test_a_verified_agreement_names_both_sides(self):
        packet = _chain_claiming(620_00, 150_00)
        r = reconcile(FakeRail(620_00, 620_00, 150_00), "pay_X", packet)
        assert "470" in r.detail
        assert isinstance(r, Reconciliation)


class TestDrift:
    def test_a_refund_the_rail_never_applied_is_caught(self):
        # chain thinks 150 was refunded; the rail shows 0.
        packet = _chain_claiming(captured=620_00, refunded=150_00)
        r = reconcile(FakeRail(620_00, 620_00, 0), "pay_X", packet)
        assert r.reconciled is False
        assert r.chain_net == 470_00
        assert r.rail_net == 620_00
        assert r.drift == 150_00
        assert "refund" in r.detail.lower()

    def test_a_capture_mismatch_is_caught(self):
        packet = _chain_claiming(captured=620_00, refunded=0)
        r = reconcile(FakeRail(620_00, 500_00, 0), "pay_X", packet)
        assert r.reconciled is False
        assert r.drift != 0


class TestTamperedChainIsRefusedNotReconciled:
    def test_a_chain_that_does_not_verify_is_not_reconciled(self):
        packet = _chain_claiming(620_00, 150_00)
        packet["entries"][0]["payload"]["amount"] = 1   # tamper
        r = reconcile(FakeRail(620_00, 620_00, 150_00), "pay_X", packet)
        assert r.reconciled is False
        assert "verify" in r.detail.lower() or "tamper" in r.detail.lower()
