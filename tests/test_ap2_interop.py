"""Ingest and emit real AP2 Open Payment Mandates.

The schema is AP2's own: vct "mandate.payment.open.1", a list of typed
constraints (allowed_payees, amount_range, budget, execution_date), amounts in
ISO-4217 minor units — which for INR is paise, exactly this project's unit.
Source: google-agentic-commerce/AP2,
code/sdk/python/ap2/sdk/generated/open_payment_mandate.py.

These prove the envelope round-trips through the real schema — not that it
borrows AP2's field names, but that it reads and writes AP2's actual documents.
"""
from datetime import datetime, timezone

import pytest

from amanat.interop.ap2 import (
    from_open_payment_mandate, to_open_payment_mandate, Ap2Error,
)
from amanat.policy.envelope import Envelope


# A real-shaped AP2 Open Payment Mandate: cap ₹800/txn, ₹1,000 total, one payee.
REAL_MANDATE = {
    "vct": "mandate.payment.open.1",
    "transaction_id": "txn_abc123",
    "constraints": [
        {"type": "payment.allowed_payees",
         "allowed": [{"id": "citycabs", "name": "City Cabs"}]},
        {"type": "payment.amount_range", "currency": "INR", "max": 80000, "min": 0},
        {"type": "payment.budget", "max": 100000, "currency": "INR"},
        {"type": "payment.execution_date", "not_after": "2026-09-01T00:00:00+00:00"},
    ],
    "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519", "x": "…"}},
    "exp": 1788307200,
}


class TestIngestingARealMandate:
    def test_a_mandate_becomes_an_envelope_with_the_right_bounds(self):
        env = from_open_payment_mandate(REAL_MANDATE)
        assert env.max_per_txn == 80000       # amount_range.max, in paise
        assert env.max_total == 100000        # budget.max, in paise
        assert env.allowed_payees == ["citycabs"]

    def test_the_expiry_comes_from_the_execution_date_window(self):
        env = from_open_payment_mandate(REAL_MANDATE)
        assert env.expires_at == datetime(2026, 9, 1, tzinfo=timezone.utc)

    def test_minor_units_are_read_as_paise_not_rupees(self):
        """AP2 amounts are ISO-4217 minor units; for INR that is paise."""
        env = from_open_payment_mandate(REAL_MANDATE)
        assert env.max_per_txn == 80000       # ₹800.00, not ₹800*100

    def test_a_non_open_mandate_is_refused(self):
        bad = dict(REAL_MANDATE, vct="mandate.payment.1")
        with pytest.raises(Ap2Error, match="open payment mandate"):
            from_open_payment_mandate(bad)

    def test_a_missing_amount_range_is_refused(self):
        bad = dict(REAL_MANDATE,
                   constraints=[c for c in REAL_MANDATE["constraints"]
                                if c["type"] != "payment.amount_range"])
        with pytest.raises(Ap2Error, match="amount_range"):
            from_open_payment_mandate(bad)

    def test_a_non_inr_currency_is_refused_with_a_clear_message(self):
        bad = dict(REAL_MANDATE, constraints=[
            dict(c, currency="USD") if c["type"] == "payment.amount_range" else c
            for c in REAL_MANDATE["constraints"]])
        with pytest.raises(Ap2Error, match="INR"):
            from_open_payment_mandate(bad)

    def test_budget_absent_falls_back_to_the_per_txn_cap(self):
        no_budget = dict(REAL_MANDATE,
                         constraints=[c for c in REAL_MANDATE["constraints"]
                                      if c["type"] != "payment.budget"])
        env = from_open_payment_mandate(no_budget)
        assert env.max_total == env.max_per_txn == 80000


class TestEmittingAMandate:
    def test_an_envelope_emits_a_valid_open_payment_mandate(self):
        env = Envelope(subject="s", max_total=100000, max_per_txn=80000,
                       allowed_payees=["citycabs"],
                       expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        m = to_open_payment_mandate(env)
        assert m["vct"] == "mandate.payment.open.1"
        types = {c["type"] for c in m["constraints"]}
        assert {"payment.allowed_payees", "payment.amount_range",
                "payment.budget", "payment.execution_date"} <= types

    def test_round_trip_preserves_every_bound(self):
        env = Envelope(subject="s", max_total=100000, max_per_txn=80000,
                       allowed_payees=["citycabs", "metrocab"],
                       expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        back = from_open_payment_mandate(to_open_payment_mandate(env))
        assert back.max_total == env.max_total
        assert back.max_per_txn == env.max_per_txn
        assert back.allowed_payees == env.allowed_payees
        assert back.expires_at == env.expires_at
