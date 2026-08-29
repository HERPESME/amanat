"""Cashfree UPI pre-authorization adapter — the rail that finally says yes.

Every real rail this project probed before Cashfree refused amount-contingent
settlement. Razorpay returns HTTP 400 on a partial capture ("Capture amount must
be equal to the amount authorized" — OBSERVED, 22 Aug 2026). Setu's documented
API hosts are NXDOMAIN. The mechanism was provable as *legal* on NPCI SBMD
(`sbmd.partial_debit`, PRIMARY tier) but never *observed* executing on a live
rail.

Cashfree's pre-authorization (enabled in sandbox via support ticket 8266875,
28 Aug 2026) is the first rail measured to accept it. The lifecycle, all against
`sandbox.cashfree.com/pg`, measured 29 Aug 2026:

  * hold    → POST /orders                     order_note "preauth_transaction"
              POST /orders/sessions            UPI collect (testsuccess@gocash)
              POST /simulate                   force the sandbox auth to SUCCESS
  * debit   → POST /orders/{id}/authorization  action CAPTURE, amount < hold  → 200
  * release → the uncaptured remainder auto-releases; an explicit VOID after a
              capture is refused ("Capture request already exist for the void").
              VOID is the alternative leg: release the whole hold, capturing none.

The finding that matters: a CAPTURE of ₹470 against a ₹620 hold returns HTTP 200,
and the ₹150 difference comes back on its own. That is the whole thesis — block a
ceiling, debit the actual, release the difference — running on a real regulated
UPI rail, with the release leg *free*, which is more than SBMD gives (there the
remainder stays blocked until an explicit revoke).

Money is integer paise everywhere inside this system; Cashfree's orders API takes
rupees as a decimal. The conversion happens only here, at the edge, and the API
response is treated as untrusted input on the way back in.

Never touches production. The base URL is fixed to the sandbox host and the
adapter refuses to be pointed anywhere else — the same discipline as the Razorpay
adapter's `rzp_test_` guard.
"""
from __future__ import annotations

import os

import httpx

from amanat.rails.base import BlockRef, BlockState, RailError
from amanat.rails.semantics import RAILS

SANDBOX_BASE = "https://sandbox.cashfree.com/pg"
ORDERS_VERSION = "2025-01-01"      # create / pay / simulate
AUTH_VERSION = "2026-01-01"        # the pre-auth CAPTURE / VOID actions
SANDBOX_SUCCESS_VPA = "testsuccess@gocash"


def _rupees(paise: int) -> float:
    """Paise → rupees for the API edge. Two decimals, no float kept internally."""
    return round(paise / 100, 2)


def _paise(rupees) -> int:
    """Rupees (number or decimal string) from the API → integer paise."""
    return int(round(float(rupees) * 100))


class CashfreePreAuthRail:
    """Talks to Cashfree pre-authorization sandbox. Sandbox only, by construction."""

    rail_id = "cashfree_preauth"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base: str = SANDBOX_BASE, timeout: float = 40.0) -> None:
        self.client_id = client_id or os.environ.get("CASHFREE_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("CASHFREE_CLIENT_SECRET", "")
        if not (self.client_id and self.client_secret):
            raise RailError(
                "CASHFREE_CLIENT_ID and CASHFREE_CLIENT_SECRET are required")
        if "sandbox.cashfree.com" not in base:
            raise RailError(
                f"refusing a non-sandbox base ({base!r}). This project never "
                "touches live credentials; Cashfree production is out of scope.")
        self.base = base.rstrip("/")
        self.profile = RAILS[self.rail_id]
        self._timeout = timeout

    # -- transport ---------------------------------------------------------

    def _headers(self, version: str) -> dict:
        return {
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "x-api-version": version,
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, *, version: str = ORDERS_VERSION,
              **kw) -> tuple[int, dict]:
        r = httpx.request(method, f"{self.base}{path}",
                          headers=self._headers(version), timeout=self._timeout, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:400]}
        return r.status_code, body

    # -- the lifecycle, returned raw so the probe records what the rail said --

    def create_preauth_order(self, order_id: str, ceiling: int, *,
                             customer: dict) -> tuple[int, dict]:
        """Create a pre-auth order for `ceiling` paise. `order_note` flags preauth."""
        return self._call("POST", "/orders", json={
            "order_id": order_id,
            "order_amount": _rupees(ceiling),
            "order_currency": "INR",
            "order_note": "preauth_transaction",
            "customer_details": customer,
        })

    def pay_upi_collect(self, payment_session_id: str,
                        vpa: str = SANDBOX_SUCCESS_VPA) -> tuple[int, dict]:
        """Submit a UPI collect against the order. Returns the cf_payment_id."""
        return self._call("POST", "/orders/sessions", json={
            "payment_session_id": payment_session_id,
            "payment_method": {"upi": {"channel": "collect", "upi_id": vpa}},
        })

    def simulate_success(self, cf_payment_id: str) -> tuple[int, dict]:
        """Force the sandbox UPI approval to SUCCESS — stands in for the UPI PIN.

        Sandbox-only, and honestly labelled: there is no real UPI app in the
        sandbox, so this is the documented way to move a collect from
        NOT_ATTEMPTED to an authorised hold. Nothing like it exists — or is
        needed — in production, where the customer approves in their own app.
        """
        return self._call("POST", "/simulate", json={
            "entity": "PAYMENTS", "entity_id": str(cf_payment_id),
            "entity_simulation": {"payment_status": "SUCCESS"},
        })

    def capture(self, order_id: str, amount: int) -> tuple[int, dict]:
        """Partial debit against the hold. THE measurement that matters.

        A CAPTURE below the held amount is amount-contingent settlement on a live
        rail. Razorpay refuses the equivalent with HTTP 400; this returns 200.
        """
        return self._call("POST", f"/orders/{order_id}/authorization",
                          version=AUTH_VERSION,
                          json={"action": "CAPTURE", "amount": _rupees(amount)})

    def void(self, order_id: str) -> tuple[int, dict]:
        """Release the whole hold, capturing nothing. The alternative to CAPTURE."""
        return self._call("POST", f"/orders/{order_id}/authorization",
                          version=AUTH_VERSION, json={"action": "VOID"})

    def fetch_order(self, order_id: str) -> tuple[int, dict]:
        return self._call("GET", f"/orders/{order_id}")

    def fetch_payments(self, order_id: str) -> tuple[int, dict]:
        return self._call("GET", f"/orders/{order_id}/payments")

    # -- RailAdapter surface -----------------------------------------------
    #
    # reserve() bundles the sandbox authorisation (create + collect + simulate),
    # which in production is the customer approving in their UPI app. State is
    # read from the rail's own response, never assumed.

    def reserve(self, ceiling: int, payee: str, *, order_id: str,
                customer: dict, vpa: str = SANDBOX_SUCCESS_VPA) -> BlockRef:
        sc, body = self.create_preauth_order(order_id, ceiling, customer=customer)
        if sc != 200:
            raise RailError(f"pre-auth order creation failed (HTTP {sc}): {body}")
        session = body.get("payment_session_id")
        if not session:
            raise RailError(f"no payment_session_id in order response: {body}")

        sc, pay = self.pay_upi_collect(session, vpa)
        if sc != 200:
            raise RailError(f"UPI collect failed (HTTP {sc}): {pay}")
        cf_payment_id = pay.get("cf_payment_id")

        sc, sim = self.simulate_success(cf_payment_id)
        if sc != 200:
            raise RailError(f"sandbox auth simulation failed (HTTP {sc}): {sim}")

        return BlockRef(
            block_id=order_id, rail_id=self.rail_id, ceiling=ceiling,
            state=BlockState.BLOCKED,
            events=[f"hold {ceiling} via pre-auth (cf_payment_id {cf_payment_id})"],
        )

    def debit(self, ref: BlockRef, amount: int) -> BlockRef:
        if amount > ref.available:
            raise RailError(
                f"capture {amount} exceeds the held {ref.available} on this order")
        sc, body = self.capture(ref.block_id, amount)
        if sc != 200:
            raise RailError(f"capture failed (HTTP {sc}): {body}")
        captured = _paise(body.get("authorization", {}).get("captured_amount", amount))
        ref.debited += captured
        # On this rail a partial capture auto-releases the uncaptured remainder:
        # the settlement completes in one call, with the difference returned free.
        ref.released = ref.ceiling - ref.debited
        ref.state = BlockState.SETTLED
        ref.events.append(f"debit {captured} → HTTP 200; ₹{_rupees(ref.released)} "
                          "auto-released")
        return ref

    def release(self, ref: BlockRef, amount: int | None = None) -> BlockRef:
        if ref.debited:
            # Already settled — the remainder came back with the capture. Voiding
            # now is what the rail rejects ("Capture request already exist").
            raise RailError(
                "nothing to release: a partial capture already returned the "
                "uncaptured remainder on this rail")
        sc, body = self.void(ref.block_id)
        if sc != 200:
            raise RailError(f"void failed (HTTP {sc}): {body}")
        ref.released = ref.ceiling
        ref.state = BlockState.REVOKED
        ref.events.append(f"release via VOID {ref.ceiling} → HTTP {sc}")
        return ref

    def revoke(self, ref: BlockRef) -> BlockRef:
        return self.release(ref)

    def status(self, ref: BlockRef) -> BlockState:
        sc, body = self.fetch_order(ref.block_id)
        if sc != 200:
            return ref.state
        order_status = str(body.get("order_status", "")).upper()
        if order_status == "PAID":
            return ref.state if ref.state != BlockState.IDLE else BlockState.BLOCKED
        return ref.state
