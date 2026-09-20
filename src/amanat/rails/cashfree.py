"""Cashfree UPI pre-authorization adapter — sandbox only, and honest about what it saw.

Razorpay returns HTTP 400 on a partial capture ("Capture amount must be equal to
the amount authorized" — OBSERVED, 22 Aug 2026). Cashfree's pre-authorization
(enabled in the sandbox by a support request, 28 Aug 2026) accepts one. The
lifecycle, against `sandbox.cashfree.com/pg`, measured 29 Aug 2026:

  * hold    → POST /orders                     order_note "preauth_transaction"
              POST /orders/sessions            UPI collect (testsuccess@gocash)
              POST /simulate                   force the sandbox auth to SUCCESS
  * debit   → POST /orders/{id}/authorization  action CAPTURE, amount < hold  → 200
  * void    → the same endpoint, action VOID: releases the WHOLE hold. After a
              capture it is refused ("Capture request already exist for the void").

What was observed: a CAPTURE of ₹470 against a ₹620 hold returned HTTP 200 with
`captured_amount 470.0`. What was NOT observed: where the uncaptured ₹150 went. The
refused VOID follows from Cashfree's documented rule that a captured authorisation
cannot be voided; it says nothing about the remainder. Cashfree documents that an
authorisation not captured within seven days is released, and says nothing about
the remainder of a partial capture. So this adapter records the capture, leaves the
remainder unreleased in its own bookkeeping, and never writes a release it did not
read — see `sandbox authorisation is forced with /simulate`, below, and
`probe_cashfree_release`, which measures what the API reports over time.

The authorisation itself is forced to SUCCESS with `POST /simulate` (the sandbox's
stand-in for the customer's UPI PIN), so what is measured is Cashfree's sandbox, not
an issuer's hold.

Money is integer paise everywhere inside this system; Cashfree's orders API takes
rupees as a decimal. The conversion happens only here, at the edge, exactly (through
`Decimal`, refusing a fraction of a paisa), and the API response is treated as
untrusted input on the way back in.

Never touches production. The base URL is fixed to the sandbox host and the
adapter refuses to be pointed anywhere else — the same discipline as the Razorpay
adapter's `rzp_test_` guard.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from decimal import Decimal, InvalidOperation
from typing import Callable

import httpx

from amanat.rails.base import BlockRef, BlockState, RailError, RailOutcomeUnknown
from amanat.rails.semantics import RAILS

SANDBOX_BASE = "https://sandbox.cashfree.com/pg"
ORDERS_VERSION = "2025-01-01"      # create / pay / simulate
AUTH_VERSION = "2026-01-01"        # the pre-auth CAPTURE / VOID actions
SANDBOX_SUCCESS_VPA = "testsuccess@gocash"
SANDBOX_CUSTOMER = {"customer_id": "amanat_probe_cust", "customer_email": "probe@example.com",
                    "customer_phone": "9999999999"}

# The measured rows that make repeating a call safe on this rail (the sandbox, 20 and 21 Sep 2026). The
# adapter claims idempotency exactly while all of them say so.
IDEMPOTENCY_ROWS = ("idempotent_capture_replay", "idempotent_void_replay", "duplicate_order_refused",
                    "payment_replay_refused", "idempotency_key_reuse_refused")


def _rupees(paise: int) -> float:
    """Paise → rupees for the API edge. Exact; the float exists only for the JSON body."""
    return float(Decimal(paise) / 100)


def _paise(rupees) -> int:
    """Rupees (number or decimal string) from the API → integer paise, exactly.

    A fraction of a paisa is refused rather than rounded: rounding a rail's number
    to a plausible amount is how a wrong figure gets signed.
    """
    try:
        exact = Decimal(str(rupees)) * 100
    except InvalidOperation:
        raise RailError(f"{rupees!r} is not a number of rupees") from None
    if exact != exact.to_integral_value():
        raise RailError(f"{rupees!r} rupees is not a whole number of paise")
    return int(exact)


class CashfreePreAuthRail:
    """Talks to Cashfree pre-authorization sandbox. Sandbox only, by construction.

    A call repeated after a lost response acts once. The session relies on that to recover an
    outcome in doubt by re-issuing the call under the same key, and the claim rests on the rail's
    measured behaviour, not on a constant: `supports_idempotency` is true exactly while the registry's
    OBSERVED rows for capture, void, order creation, payment and key reuse all say a retry is safe.
    """

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
        self._seen: dict[str, tuple[tuple, BlockRef]] = {}    # what this process already did, by key

    @property
    def supports_idempotency(self) -> bool:
        caps = self.profile.capabilities
        return all(n in caps and caps[n].supported is True and caps[n].is_fact for n in IDEMPOTENCY_ROWS)

    # -- transport ---------------------------------------------------------

    def _headers(self, version: str) -> dict:
        return {
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "x-api-version": version,
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, *, version: str = ORDERS_VERSION,
              headers: dict | None = None, **kw) -> tuple[int, dict]:
        r = httpx.request(method, f"{self.base}{path}",
                          headers={**self._headers(version), **(headers or {})}, timeout=self._timeout, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:400]}
        return r.status_code, body

    @staticmethod
    def _check(what: str, status: int, body) -> None:
        """Nothing for a success. A refusal (`RailError`) for a 4xx: nothing happened. Silence
        (`RailOutcomeUnknown`) for a timeout status or a server error: the rail may have acted."""
        if 200 <= status < 300:
            return
        if status == 408 or status >= 500 or status < 200:
            raise RailOutcomeUnknown(f"{what}: HTTP {status}; the rail may or may not have acted: {body}")
        raise RailError(f"{what} failed (HTTP {status}): {body}")

    @staticmethod
    def _key_header(order_id: str, key: str | None) -> dict:
        """The header that makes a repeated call act once, scoped to the order so that one key can
        never answer for another order's request."""
        if not key:
            return {}
        return {"x-idempotency-key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{order_id}:{key}"))}

    @staticmethod
    def _order_id_for(key: str | None) -> str:
        """A repeat of a reservation must land on the same order: the rail refuses a second order
        under one id, so a stable id is what makes the creation safe to repeat."""
        if key:
            return "amanat_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return "amanat_" + secrets.token_hex(16)

    def _once(self, key: str | None, request: tuple, apply: Callable[[], BlockRef]) -> BlockRef:
        """Apply at most once per key in this process, as the simulator does; the rail's own key
        handling covers a repeat from a process that has forgotten."""
        if key is None:
            return apply()
        seen = self._seen.get(key)
        if seen is not None:
            if seen[0] != request:
                raise RailError(f"idempotency key {key!r} was already used for a different request")
            return seen[1]
        result = apply()
        self._seen[key] = (request, result)
        return result

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

    def capture(self, order_id: str, amount: int, *,
                idempotency_key: str | None = None) -> tuple[int, dict]:
        """Partial debit against the hold. THE measurement that matters.

        A CAPTURE below the held amount is amount-contingent settlement on a live
        rail. Razorpay's Capture API refuses the same operation with HTTP 400; this returns 200.
        """
        return self._call("POST", f"/orders/{order_id}/authorization",
                          version=AUTH_VERSION, headers=self._key_header(order_id, idempotency_key),
                          json={"action": "CAPTURE", "amount": _rupees(amount)})

    def void(self, order_id: str, *, idempotency_key: str | None = None) -> tuple[int, dict]:
        """Release the whole hold, capturing nothing. The alternative to CAPTURE."""
        return self._call("POST", f"/orders/{order_id}/authorization",
                          version=AUTH_VERSION, headers=self._key_header(order_id, idempotency_key),
                          json={"action": "VOID"})

    def fetch_order(self, order_id: str) -> tuple[int, dict]:
        return self._call("GET", f"/orders/{order_id}")

    def fetch_payments(self, order_id: str) -> tuple[int, dict]:
        return self._call("GET", f"/orders/{order_id}/payments")

    def fetch_refunds(self, order_id: str) -> tuple[int, dict]:
        """Refund entities on the order — where a return of the remainder would show."""
        return self._call("GET", f"/orders/{order_id}/refunds")

    # -- RailAdapter surface -----------------------------------------------
    #
    # reserve() bundles the sandbox authorisation (create + collect + simulate),
    # which in production is the customer approving in their UPI app. State is
    # read from the rail's own response, never assumed. Each call takes an optional
    # idempotency key and, given one, acts once however many times it is repeated.

    def reserve(self, ceiling: int, payee: str, *, idempotency_key: str | None = None,
                order_id: str | None = None, customer: dict | None = None,
                vpa: str = SANDBOX_SUCCESS_VPA) -> BlockRef:
        order_id = order_id or self._order_id_for(idempotency_key)
        return self._once(idempotency_key, ("reserve", ceiling, payee, order_id),
                          lambda: self._reserve(ceiling, order_id, customer or SANDBOX_CUSTOMER, vpa))

    def _reserve(self, ceiling: int, order_id: str, customer: dict, vpa: str) -> BlockRef:
        sc, body = self.create_preauth_order(order_id, ceiling, customer=customer)
        if sc == 409 and isinstance(body, dict) and body.get("code") == "order_already_exists":
            return self._resume(order_id, ceiling, vpa)         # a repeat of a reservation the rail has seen
        self._check("pre-auth order creation", sc, body)
        session = body.get("payment_session_id")
        if not session:
            raise RailError(f"no payment_session_id in order response: {body}")
        return self._authorise(order_id, ceiling, session, vpa,
                               note=f"hold {ceiling} via pre-auth")

    def _authorise(self, order_id: str, ceiling: int, session: str, vpa: str, *, note: str) -> BlockRef:
        sc, pay = self.pay_upi_collect(session, vpa)
        self._check("UPI collect", sc, pay)
        cf_payment_id = pay.get("cf_payment_id")

        sc, sim = self.simulate_success(cf_payment_id)
        self._check("sandbox auth simulation", sc, sim)

        return BlockRef(
            block_id=order_id, rail_id=self.rail_id, ceiling=ceiling,
            state=BlockState.BLOCKED,
            events=[f"{note} (cf_payment_id {cf_payment_id})"],
        )

    def _resume(self, order_id: str, ceiling: int, vpa: str) -> BlockRef:
        """The order exists: an earlier attempt under this key reached the rail. Read what it
        did and finish only what is safe to finish; never place a second hold."""
        sc, order = self.fetch_order(order_id)
        self._check(f"order {order_id} exists but could not be read", sc, order)
        stated = order.get("order_amount")
        if stated is None or _paise(stated) != ceiling:
            raise RailError(f"order {order_id} exists for a different request ({stated!r} rupees, "
                            f"not {ceiling} paise): an idempotency key was reused for a different request")
        status = str(order.get("order_status", "")).upper()
        if status == "PAID":
            return BlockRef(block_id=order_id, rail_id=self.rail_id, ceiling=ceiling,
                            state=BlockState.BLOCKED,
                            events=[f"hold {ceiling} already placed for this key (order {order_id}); "
                                    "read back, not placed again"])
        if status == "ACTIVE":
            sc, payments = self.fetch_payments(order_id)
            self._check(f"payments of order {order_id}", sc, payments)
            if payments == []:
                session = order.get("payment_session_id")
                if not session:
                    raise RailError(f"order {order_id} is active but names no payment session")
                return self._authorise(order_id, ceiling, session, vpa,
                                       note=f"hold {ceiling} finished after an earlier attempt stopped part-way")
            raise RailError(f"order {order_id} has a payment attempt that has not completed: cannot tell "
                            "whether repeating the reservation is safe, so reconcile it before retrying")
        raise RailError(f"order {order_id} exists with status {status or 'unknown'}, so it cannot be "
                        "the hold this key placed")

    def debit(self, ref: BlockRef, amount: int, *, idempotency_key: str | None = None) -> BlockRef:
        return self._once(idempotency_key, ("debit", ref.block_id, amount),
                          lambda: self._debit(ref, amount, idempotency_key))

    def _debit(self, ref: BlockRef, amount: int, key: str | None) -> BlockRef:
        # Without a key a second capture is a second capture, and the rail documents that it refuses
        # one. With a key it may be the repeat of a capture whose answer was lost, and a block rebuilt
        # from the rail already shows that capture: the repeat must still reach the rail, which returns
        # the first result, and must not be counted twice (see the assignment below).
        if ref.state is BlockState.CAPTURED and not key:
            raise RailError(
                "this authorisation was already captured; Cashfree documents that "
                "\"A transaction can only be captured or voided once.\"")
        held = ref.ceiling - ref.released
        if amount > held:
            raise RailError(
                f"capture {amount} exceeds the held {held} on this order")
        sc, body = self.capture(ref.block_id, amount, idempotency_key=key)
        self._check("capture", sc, body)
        stated = body.get("authorization", {}).get("captured_amount")
        if stated is None:
            raise RailError(
                "the capture response did not state the captured amount; check the "
                "order before retrying rather than assuming what was captured")
        captured = _paise(stated)
        # An assignment, not a sum: Cashfree allows one capture per hold, so what the rail says was
        # captured is the whole of `debited`, and a repeat that returns the first result cannot add to it.
        ref.debited = captured
        # The rail confirmed the capture. It did NOT say what became of the uncaptured
        # remainder, so none is recorded as released: `available` stays the remainder
        # held, and only a later read of the order can say more.
        ref.state = BlockState.CAPTURED
        ref.events.append(f"debit {captured} → HTTP 200 (captured; the remainder's "
                          "release was not observed)")
        return ref

    def release(self, ref: BlockRef, amount: int | None = None, *,
                idempotency_key: str | None = None) -> BlockRef:
        return self._once(idempotency_key, ("release", ref.block_id, amount),
                          lambda: self._release(ref, idempotency_key))

    def _release(self, ref: BlockRef, key: str | None) -> BlockRef:
        if ref.debited:
            # Cashfree: "Once captured, a transaction cannot be voided." The VOID was
            # refused when probed; that is the rail's rule, not a claim about the remainder.
            raise RailError(
                "cannot release: the authorisation was captured and, as Cashfree "
                "documents, \"Once captured, a transaction cannot be voided.\" What "
                "becomes of the uncaptured remainder is the rail's and was not observed")
        sc, body = self.void(ref.block_id, idempotency_key=key)
        self._check("void", sc, body)
        ref.released = ref.ceiling
        ref.state = BlockState.REVOKED
        ref.events.append(f"release via VOID {ref.ceiling} → HTTP {sc}")
        return ref

    def revoke(self, ref: BlockRef) -> BlockRef:
        return self.release(ref)

    def get_block(self, block_id: str) -> BlockRef:
        """Rebuild a block from the rail's own state: what a restarted session has instead of a handle.

        The order says what was held; the payments say whether anything was captured or voided. Nothing
        here is assumed: an order that was never authorised is not a hold, and an order the rail cannot be
        asked about is silence, not absence.
        """
        sc, order = self.fetch_order(block_id)
        if sc == 404:
            raise RailError(f"unknown block {block_id!r}")
        self._check(f"order {block_id}", sc, order)
        if str(order.get("order_status", "")).upper() != "PAID":
            raise RailError(f"order {block_id} is {order.get('order_status')!r}, not a hold: "
                            "it was never authorised")
        ceiling = _paise(order.get("order_amount"))
        sc, payments = self.fetch_payments(block_id)
        self._check(f"payments of order {block_id}", sc, payments)
        ref = BlockRef(block_id=block_id, rail_id=self.rail_id, ceiling=ceiling, state=BlockState.BLOCKED,
                       events=[f"read back from the rail (order {block_id})"])
        for p in payments:
            action = p.get("authorization") or {}
            if action.get("status") != "SUCCESS":
                continue
            if action.get("action") == "CAPTURE" and action.get("captured_amount") is not None:
                ref.debited, ref.state = _paise(action["captured_amount"]), BlockState.CAPTURED
            elif action.get("action") == "VOID":
                ref.released, ref.state = ceiling, BlockState.REVOKED
        return ref

    def status(self, ref: BlockRef) -> BlockState:
        sc, body = self.fetch_order(ref.block_id)
        if sc != 200:
            return ref.state
        order_status = str(body.get("order_status", "")).upper()
        if order_status == "PAID":
            return ref.state if ref.state != BlockState.IDLE else BlockState.BLOCKED
        return ref.state
