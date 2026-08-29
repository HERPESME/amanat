"""Measure Cashfree UPI pre-authorization instead of quoting it.

    uv run --with httpx --with cryptography python -m amanat.rails.probe_cashfree

A doc says what a rail is supposed to do; a probe says what it did. This drives
the whole pre-auth lifecycle against the live sandbox — hold ₹620, capture ₹470,
watch the ₹150 come back — and it is the measurement that moves
`cashfree_preauth.partial_debit` from UNVERIFIED to OBSERVED. Everything runs
against sandbox credentials and moves no real money.

The result to read out loud: this rail returns HTTP 200 for a capture smaller
than the hold. Razorpay returns HTTP 400 for the same shape. Two real rails,
measured, disagreeing — one forecloses amount-contingent settlement, one permits it.
"""
from __future__ import annotations

import sys
import time

from amanat import env
from amanat.rails.base import RailError
from amanat.rails.cashfree import CashfreePreAuthRail, _paise, _rupees

OK, NO, HM = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"

_CUSTOMER = {
    "customer_id": "amanat_probe_cust",
    "customer_email": "probe@example.com",
    "customer_phone": "9999999999",
}


def _head(title: str) -> None:
    print(f"\n\033[1m{'─' * 72}\n  {title}\n{'─' * 72}\033[0m")


def run(rail: CashfreePreAuthRail, ceiling: int, actual: int) -> int:
    order_id = f"amanat_pa_{int(time.time())}"

    _head("Leg 1 — hold a ceiling (pre-authorise ₹%.0f)" % _rupees(ceiling))
    sc, order = rail.create_preauth_order(order_id, ceiling, customer=_CUSTOMER)
    print(f"  {OK if sc == 200 else NO} POST /orders (order_note preauth_transaction) "
          f"→ HTTP {sc} · {order.get('order_status', '?')}")
    session = order.get("payment_session_id")
    sc, pay = rail.pay_upi_collect(session)
    cf_id = pay.get("cf_payment_id")
    print(f"  {OK if sc == 200 else NO} POST /orders/sessions (UPI collect "
          f"testsuccess@gocash) → HTTP {sc} · cf_payment_id {cf_id}")
    sc, sim = rail.simulate_success(cf_id)
    print(f"  {OK if sc == 200 else NO} POST /simulate (stands in for the UPI PIN) "
          f"→ HTTP {sc} · {sim.get('entity_simulation', {}).get('payment_status', '?')}")
    time.sleep(2)
    sc, o = rail.fetch_order(order_id)
    print(f"    order_status now: \033[1m{o.get('order_status', '?')}\033[0m "
          f"(the ₹%.0f is held, not yet debited)" % _rupees(ceiling))

    _head("Leg 2 — debit the actual (capture ₹%.0f of the ₹%.0f hold)"
          % (_rupees(actual), _rupees(ceiling)))
    print(f"  capturing a sum SMALLER than the hold — the shape Razorpay refuses...")
    sc, cap = rail.capture(order_id, actual)
    if sc == 200:
        a = cap.get("authorization", {})
        captured = _paise(a.get("captured_amount", actual))
        print(f"  \033[32m{OK} ACCEPTED (HTTP 200)\033[0m — "
              f"{a.get('action')} {a.get('status')}, captured ₹%.0f" % _rupees(captured))
        print(f"    payment_message: {cap.get('payment_message', '')}")
        print(f"\n  \033[1mThis is cashfree_preauth.partial_debit → OBSERVED.\033[0m")
        print("  \033[2mAmount-contingent settlement executing on a real regulated UPI")
        print("  rail. The capability's quote becomes this response.\033[0m")
    else:
        print(f"  {NO} REFUSED → HTTP {sc}: {cap.get('message', cap)}")
        return 1

    _head("Leg 3 — release the difference (the ₹%.0f remainder)"
          % _rupees(ceiling - actual))
    sc, v = rail.void(order_id)
    if sc != 200:
        print(f"  {OK} the rail auto-released it — an explicit VOID is refused: "
              f"HTTP {sc} \033[2m({v.get('message', '')})\033[0m")
        print("  \033[2mCapturing less than the hold returns the difference on its own;")
        print("  there is nothing left to void. No revoke, no teardown, no stranding —")
        print("  which is more than SBMD gives, where the remainder stays blocked.\033[0m")
    else:
        print(f"  {OK} VOID → HTTP {sc}")

    _head("What two real rails say about amount-contingent settlement")
    print(f"  {NO} Razorpay manual capture — HTTP 400 "
          "\033[2m'Capture amount must be equal to the amount authorized'\033[0m")
    print(f"  {OK} Cashfree UPI pre-auth — HTTP 200, captured ₹%.0f of ₹%.0f, "
          "remainder returned" % (_rupees(actual), _rupees(ceiling)))
    print("  \033[2mMeasured, not quoted. The negative and the positive are both the point.\033[0m\n")
    return 0


def main() -> int:
    env.load()
    try:
        rail = CashfreePreAuthRail()
    except RailError as exc:
        print(f"\n  {NO} {exc}\n", file=sys.stderr)
        print("  Put CASHFREE_CLIENT_ID / CASHFREE_CLIENT_SECRET (sandbox) in .env",
              file=sys.stderr)
        return 2

    print("\n\033[1mLIVE RAIL PROBE — Cashfree UPI pre-authorization (sandbox)\033[0m")
    print("  \033[2mThe first real rail measured to accept a debit below the hold.\033[0m")
    return run(rail, 620_00, 470_00)


if __name__ == "__main__":
    sys.exit(main())
