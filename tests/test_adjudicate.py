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


class TestAdjudicationChecksWhoSignedTheGrant:
    """Conformance to a grant is only worth something if the grant is real."""

    def _signed_mandate(self, key=None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from amanat.interop.ap2 import sign_mandate
        return sign_mandate(_mandate(), key or Ed25519PrivateKey.generate())

    def test_a_verified_user_signature_is_stated_in_the_finding(self):
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), self._signed_mandate(), "unauthorized")
        assert a.finding is Finding.SUPPORTS_MERCHANT
        assert a.authorized["consent_binding"] == "verified"
        assert any("signed" in r.lower() and "key" in r.lower() for r in a.reasons)

    def test_a_tampered_mandate_stops_adjudication_before_any_reasoning(self):
        s = _settled_session()
        m = self._signed_mandate()
        for c in m["constraints"]:
            if c["type"] == "payment.amount_range":
                c["max"] = 1                 # grant rewritten after signing
        a = adjudicate(s.evidence_packet(), m, "unauthorized")
        assert a.finding is Finding.MANDATE_UNVERIFIED
        assert a.cited_seqs == []

    def test_an_unsigned_mandate_still_adjudicates_but_says_so(self):
        """The web demo has no user key. That must be visible, not silent."""
        s = _settled_session()
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        assert a.finding is Finding.SUPPORTS_MERCHANT
        assert a.authorized["consent_binding"] == "absent"
        assert any("not signed" in r.lower() or "unsigned" in r.lower() for r in a.reasons)


class FailingDebitRail(SimulatedRail):
    """The real simulated rail, except that debits fail — as a real rail's can."""

    def debit(self, ref, amount, *, idempotency_key=None):
        from amanat.rails.base import RailError
        raise RailError("HTTP 503 from the rail")


class TestARejectedDebitIsNotMoneyCharged:
    """The chain records a debit the rail rejected as a rail transition — with an
    outcome saying it was rejected. Counting it as money charged would state, in a
    dispute finding, that the cardholder paid something they never paid."""

    def test_a_debit_the_rail_rejected_is_reported_as_not_charged(self):
        s = AgentSession(_env(), FailingDebitRail("sbmd", customer_balance=5_000_00))
        s.reserve(620_00, "citycabs", "p95")
        assert s.debit(470_00, "fare").ok is False           # the rail said no
        a = adjudicate(s.evidence_packet(), _mandate(), "amount")
        assert a.net_charged == 0
        assert a.finding is Finding.CHARGE_NOT_IN_CHAIN
        assert any("rejected" in r.lower() for r in a.reasons)

    def test_a_rejected_attempt_beside_an_applied_debit_counts_only_the_applied_one(self):
        s = AgentSession(_env(), SimulatedRail("sbmd", customer_balance=5_000_00))
        s.reserve(620_00, "citycabs", "p95")
        s.debit(470_00, "fare")
        s.rail.debit = FailingDebitRail.debit.__get__(s.rail)   # the rail starts failing
        s.debit(50_00, "tip")                                    # rejected
        a = adjudicate(s.evidence_packet(), _mandate(), "amount")
        assert a.net_charged == 470_00
        assert a.finding is Finding.SUPPORTS_MERCHANT

    def test_a_disputed_amount_that_only_ever_failed_is_not_in_the_chain(self):
        s = AgentSession(_env(), FailingDebitRail("sbmd", customer_balance=5_000_00))
        s.reserve(620_00, "citycabs", "p95")
        s.debit(470_00, "fare")                                  # rejected
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized",
                       disputed_amount=470_00)
        assert a.finding is Finding.CHARGE_NOT_IN_CHAIN
        assert any("rejected" in r.lower() for r in a.reasons)


class LosesTheDebitResponse(SimulatedRail):
    """The debit lands on the rail, but the answer never arrives."""

    lose_next = True

    def debit(self, ref, amount, *, idempotency_key=None):
        result = super().debit(ref, amount, idempotency_key=idempotency_key)
        if self.lose_next:
            self.lose_next = False
            raise TimeoutError("the answer never arrived")
        return result


class TestAnUnknownOutcomeIsNotReportedAsNoCharge:
    """If a debit's outcome is unknown the record cannot say it never happened."""

    def test_an_unresolved_debit_makes_the_finding_outside_the_evidence(self):
        s = AgentSession(_env(), LosesTheDebitResponse("sbmd", customer_balance=5_000_00))
        s.reserve(620_00, "citycabs", "p95")
        assert s.debit(470_00, "fare").ok is False                 # in doubt
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        assert a.finding is Finding.OUTSIDE_EVIDENCE
        assert any("in doubt" in r.lower() for r in a.reasons)

    def test_once_resolved_the_call_no_longer_clouds_the_finding(self):
        s = AgentSession(_env(), LosesTheDebitResponse("sbmd", customer_balance=5_000_00))
        s.reserve(620_00, "citycabs", "p95")
        s.debit(470_00, "fare")
        assert s.resolve_in_doubt().ok
        a = adjudicate(s.evidence_packet(), _mandate(), "unauthorized")
        assert a.finding is Finding.SUPPORTS_MERCHANT and a.net_charged == 470_00
