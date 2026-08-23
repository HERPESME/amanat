"""Adjudicate a signed chain against an AP2 authorization.

The findings are about what the *evidence* shows — authorized-and-within-bounds,
or a charge that isn't in the chain, or something the record cannot speak to.
They are deliberately not claims about who wins the dispute; that is issuer
discretion, and the resolver never pretends otherwise.
"""
from datetime import datetime, timedelta, timezone

import pytest

from amanat.dispute.adjudicate import adjudicate, export_representment_packet, Finding
from amanat.evidence.chain import EvidenceChain
from amanat.interop.ap2 import to_open_payment_mandate
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail


def _env():
    return Envelope(subject="cab-1", max_total=1_000_00, max_per_txn=800_00,
                    allowed_payees=["citycabs"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                    intent_text="Book a cab, cap ₹1,000.")


def _settled_session():
    s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=5_000_00))
    s.reserve(620_00, "citycabs", "p95"); s.debit(470_00, "fare"); s.release(reason="done")
    return s


def _mandate():
    return to_open_payment_mandate(_env())


class TestUnauthorizedClaim:
    def test_a_within_bounds_debit_is_shown_authorized(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        assert a.finding is Finding.SUPPORTS_MERCHANT
        assert 470_00 == a.net_charged
        assert a.cited_seqs                      # it points at specific entries

    def test_the_explanation_cites_the_authorization_and_the_debit(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        blob = " ".join(a.reasons).lower()
        assert "authoris" in blob or "authoriz" in blob
        assert "#" in " ".join(a.reasons)        # cites entry numbers

    def test_a_charge_that_was_refused_is_not_in_the_chain(self):
        """The cardholder disputes ₹5,000 — but that attempt was refused."""
        s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=5_000_00))
        s.reserve(5_000_00, "citycabs", "over budget")   # refused
        s.reserve(620_00, "citycabs", "p95"); s.debit(470_00, "fare")
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized",
                       disputed_amount=5_000_00)
        assert a.finding is Finding.CHARGE_NOT_IN_CHAIN
        assert any("refus" in r.lower() for r in a.reasons)


class TestAmountAndPayeeClaims:
    def test_total_charged_within_budget_supports_the_merchant(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "amount")
        assert a.finding is Finding.SUPPORTS_MERCHANT
        assert a.authorized["max_total"] == 1_000_00

    def test_a_disputed_wrong_payee_that_never_settled(self):
        s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=5_000_00))
        s.reserve(100_00, "randomcab", "wrong payee")    # refused
        a = adjudicate(s.evidence_packet(), _mandate(), "wrong_payee",
                       disputed_amount=100_00)
        assert a.finding is Finding.CHARGE_NOT_IN_CHAIN


class TestHonestLimits:
    def test_non_delivery_is_outside_what_the_chain_can_prove(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "non_delivery")
        assert a.finding is Finding.OUTSIDE_EVIDENCE
        assert any("deliver" in r.lower() for r in a.reasons)

    def test_a_tampered_packet_is_caught_before_any_finding(self):
        s = _settled_session()
        packet = s.evidence_packet()
        for e in packet["entries"]:
            if e["event_type"] == "rail_transition":
                e["payload"]["amount"] = 1; break
        a = adjudicate(packet, _mandate(), "unauthorized")
        assert a.finding is Finding.EVIDENCE_TAMPERED

    def test_every_adjudication_carries_the_not_an_issuer_decision_disclaimer(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        assert "issuer" in a.disclaimer.lower()
        assert "not" in a.disclaimer.lower()


class TestRepresentmentExport:
    def test_the_export_bundles_authorization_evidence_and_finding(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        pkt = export_representment_packet(a, s.evidence_packet(), _mandate())
        assert pkt["authorization"]["vct"].startswith("mandate.payment.open")
        assert pkt["evidence"]["entries"]
        assert pkt["finding"]["finding"] == a.finding.value

    def test_the_bundled_evidence_still_verifies_standalone(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        pkt = export_representment_packet(a, s.evidence_packet(), _mandate())
        EvidenceChain.verify_packet(pkt["evidence"])
