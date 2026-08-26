"""Razorpay test-mode adapter — including what it refuses to do.

This adapter exists to be honest about a rail rather than to carry the demo. Two
properties of Razorpay's manual-capture flow make it a poor fit for
amount-contingent settlement, and both are worth showing rather than hiding:

  * `authorized` is not a hold. The customer has already been debited. Modelling
    it as blocked funds would misrepresent where the money is.
  * Capture must equal the authorized amount — "Capture amount must be equal to
    the amount authorized" — so debit-the-actual is impossible here.

So `reserve` and `debit` raise, with the citation attached. An adapter that
silently degraded to full capture would produce a demo that works and a claim
that is false, which is the worse failure.

Everything the API *can* do server-side is implemented and exercised by
`amanat.rails.probe` against live test keys.
"""
from __future__ import annotations

import os

import httpx

from amanat.rails.base import BlockRef, BlockState, RailError
from amanat.rails.semantics import RAILS

BASE = "https://api.razorpay.com/v1"


class RazorpayTestRail:
    """Talks to Razorpay test mode. Refuses what the rail cannot honour."""

    rail_id = "razorpay_auth_capture"

    def __init__(self, key_id: str | None = None, key_secret: str | None = None,
                 timeout: float = 30.0) -> None:
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not (self.key_id and self.key_secret):
            raise RailError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required")
        if not self.key_id.startswith("rzp_test_"):
            raise RailError(
                f"refusing a non-test key ({self.key_id[:9]!r}). This project "
                "never touches live credentials.")
        self.profile = RAILS[self.rail_id]
        self._timeout = timeout

    # -- transport ---------------------------------------------------------

    def _call(self, method: str, path: str, **kw) -> tuple[int, dict]:
        r = httpx.request(method, f"{BASE}{path}",
                          auth=(self.key_id, self.key_secret),
                          timeout=self._timeout, **kw)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": r.text[:400]}

    # -- what the rail genuinely supports ----------------------------------

    def create_order(self, amount: int, *, manual_capture: bool = True,
                     notes: dict | None = None) -> dict:
        """An order with `payment_capture: 0` — authorize now, capture later."""
        sc, body = self._call("POST", "/orders", json={
            "amount": amount, "currency": "INR",
            "payment_capture": 0 if manual_capture else 1,
            "notes": notes or {},
        })
        if sc != 200:
            raise RailError(f"order creation failed ({sc}): {body}")
        return body

    def create_payment_link(self, amount: int, description: str) -> dict:
        """A link a human can pay in test mode.

        The only server-side route to an `authorized` payment on an account
        without S2S enabled — see `probe.py`, which measures that wall.
        """
        sc, body = self._call("POST", "/payment_links", json={
            "amount": amount, "currency": "INR",
            "description": description, "accept_partial": False,
        })
        if sc != 200:
            raise RailError(f"payment link creation failed ({sc}): {body}")
        return body

    def fetch_payment(self, payment_id: str) -> dict:
        sc, body = self._call("GET", f"/payments/{payment_id}")
        if sc != 200:
            raise RailError(f"payment fetch failed ({sc}): {body}")
        return body

    def capture(self, payment_id: str, amount: int) -> tuple[int, dict]:
        """Attempt a capture. Returns the raw result rather than raising.

        `probe.py` uses this to record what the API actually says when asked to
        capture less than the authorized amount, so the capability rests on an
        observed response rather than on a documentation quote.
        """
        return self._call("POST", f"/payments/{payment_id}/capture",
                          json={"amount": amount, "currency": "INR"})

    def refund(self, payment_id: str, amount: int) -> tuple[int, dict]:
        """Refund part or all of a captured payment. Returns the raw result.

        Measured to work in test mode: a partial refund returns HTTP 200 with
        `refund_status: partial`. This is the return leg of capture-then-refund
        settlement — see `amanat.rails.settlement`.
        """
        return self._call("POST", f"/payments/{payment_id}/refund",
                          json={"amount": amount})

    def fetch_payment_authoritative(self, payment_id: str) -> dict:
        """A payment dict carrying the rail's own captured/refunded totals.

        Razorpay reports `amount` (authorized), `amount_refunded`, and a
        `captured` flag; a fully-captured manual-capture payment has captured ==
        amount. Normalised here into the shape `reconcile` expects, so the
        reconciliation never has to know Razorpay's field names.
        """
        p = self.fetch_payment(payment_id)
        p["amount_captured"] = p["amount"] if p.get("captured") else 0
        return p

    # -- what it cannot honour ---------------------------------------------

    def reserve(self, ceiling: int, payee: str) -> BlockRef:
        d = self.profile.explain("funds_held_in_customer_account")
        raise RailError(
            "Razorpay manual capture cannot reserve funds: an 'authorized' "
            "payment has already debited the customer, so nothing is held. "
            f"{d.citation}: {d.quote}")

    def debit(self, ref: BlockRef, amount: int) -> BlockRef:
        d = self.profile.explain("partial_debit")
        raise RailError(
            "Razorpay cannot debit an amount below the authorized total. "
            f"{d.citation}: {d.quote}")

    def release(self, ref: BlockRef, amount: int | None = None) -> BlockRef:
        raise RailError(
            "nothing is held on this rail, so there is nothing to release; "
            "the equivalent action is a refund against a captured payment")

    def revoke(self, ref: BlockRef) -> BlockRef:
        raise RailError(
            "an uncaptured authorization auto-voids rather than being revoked")

    def status(self, ref: BlockRef) -> BlockState:
        return BlockState.IDLE
