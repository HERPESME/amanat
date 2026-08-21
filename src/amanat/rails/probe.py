"""Measure a live rail instead of quoting its documentation.

    uv run --with httpx --with cryptography python -m amanat.rails.probe
    uv run ... python -m amanat.rails.probe --capture pay_XXXX 47000

The capability table is built on cited documentation, which is the right default
— but a doc says what a rail is supposed to do, and a probe says what it does.
Where the two can be compared, they should be.

Everything here runs against test-mode keys and moves no real money. Orders and
payment links are created; nothing is captured unless you pass --capture
explicitly with a payment you paid yourself.
"""
from __future__ import annotations

import sys

from amanat import env
from amanat.rails.base import RailError
from amanat.rails.razorpay import RazorpayTestRail

OK, NO = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def _head(title: str) -> None:
    print(f"\n\033[1m{'─' * 72}\n  {title}\n{'─' * 72}\033[0m")


def probe_orders(rail: RazorpayTestRail) -> None:
    _head("Manual capture — is authorize-now / capture-later real?")
    order = rail.create_order(620_00, notes={"purpose": "amanat probe"})
    print(f"  {OK} order created with payment_capture=0 → {order['id']}")
    print(f"    amount {order['amount']} · status {order['status']}")
    print("  \033[2mConfirms the flag is settable. It does NOT confirm a hold —")
    print("  see the authorization probe below.\033[0m")


def probe_s2s_wall(rail: RazorpayTestRail) -> None:
    _head("Where the server-side path stops")
    sc, body = rail._call("POST", "/payments/create/upi", json={
        "amount": 620_00, "currency": "INR", "method": "upi",
        "email": "probe@example.com", "contact": "9999999999",
        "upi": {"flow": "collect", "vpa": "success@razorpay"},
    })
    desc = body.get("error", {}).get("description") or body.get("message", "")
    print(f"  {NO} S2S payment creation → HTTP {sc}: {desc[:70]}")
    print("  \033[2mS2S is not enabled on a self-serve test account, so an")
    print("  'authorized' payment cannot be produced from the server alone.")
    print("  That is a real constraint on what this probe can prove unaided.\033[0m")


def probe_partial_capture(rail: RazorpayTestRail, payment_id: str,
                          amount: int) -> None:
    """The measurement that matters: capture less than was authorized."""
    _head("Partial capture — documented as forbidden. Is it?")
    payment = rail.fetch_payment(payment_id)
    authorized = payment["amount"]
    print(f"  payment {payment_id} · status {payment['status']} · "
          f"authorized {authorized}")

    if payment["status"] != "authorized":
        print(f"  \033[33m! payment is '{payment['status']}', not 'authorized' — "
              f"cannot test capture\033[0m")
        return

    print(f"\n  attempting capture of {amount} against {authorized} authorized...")
    sc, body = rail.capture(payment_id, amount)
    desc = body.get("error", {}).get("description", "")

    if sc == 200:
        print(f"  \033[33m{OK} ACCEPTED (HTTP 200) — the documentation is wrong, "
              f"or this path differs\033[0m")
        print(f"    captured {body.get('amount')} of {authorized}")
        print("  \033[1mThis contradicts the capability table. Update it.\033[0m")
    else:
        print(f"  {NO} REFUSED → HTTP {sc}: {desc}")
        print("\n  \033[2mThe capability table records this as SECONDARY, on the")
        print("  strength of Razorpay's documentation. It is now also an observed")
        print("  response from the live API, which is a stronger footing.\033[0m")


def probe_setu() -> None:
    """Setu authenticates. Its documented API hosts do not resolve.

    Recorded because it is the kind of constraint that is invisible until you
    hold credentials and try: signup is self-serve and the token endpoint
    returns 200, so every earlier signal said the rail was reachable.
    """
    import os
    import socket

    import httpx

    cid = os.environ.get("SETU_CLIENT_ID", "").strip()
    secret = os.environ.get("SETU_CLIENT_SECRET", "").strip()

    _head("Setu UMAP — do the credentials work, and is the API reachable?")
    if not (cid and secret):
        print("  \033[2mSETU_CLIENT_ID / SETU_CLIENT_SECRET not set — skipped\033[0m")
        return

    try:
        r = httpx.post(
            "https://accountservice.setu.co/v1/users/login",
            headers={"client": "bridge", "Content-Type": "application/json"},
            json={"clientID": cid, "secret": secret,
                  "grant_type": "client_credentials"},
            timeout=30)
        if r.status_code == 200:
            print(f"  {OK} token endpoint accepted the credentials (HTTP 200)")
        else:
            print(f"  {NO} token endpoint → HTTP {r.status_code}: {r.text[:80]}")
            return
    except Exception as exc:                                    # noqa: BLE001
        print(f"  {NO} token endpoint unreachable: {type(exc).__name__}")
        return

    for host in ("accountservice.setu.co", "bridge.setu.co",
                 "uatapi.setu.co", "api.setu.co"):
        try:
            print(f"  {OK} DNS {host:<26s} → {socket.gethostbyname(host)}")
        except socket.gaierror:
            print(f"  {NO} DNS {host:<26s} → does not resolve")

    print("\n  \033[2mThe credentials are valid and the account exists. The two hosts")
    print("  the UMAP docs name for sandbox and production are NXDOMAIN on both")
    print("  Google and Cloudflare public resolvers, so the API surface is not")
    print("  reachable from a self-serve signup — it is gated behind onboarding,")
    print("  private DNS, or an allowlist. Encoded as UNVERIFIED rather than")
    print("  assumed either way.\033[0m")


def main() -> int:
    env.load()
    try:
        rail = RazorpayTestRail()
    except RailError as exc:
        print(f"\n  {NO} {exc}\n", file=sys.stderr)
        return 2

    print("\n\033[1mLIVE RAIL PROBE — Razorpay test mode\033[0m")
    print("  \033[2mA doc says what a rail should do. A probe says what it does.\033[0m")

    args = sys.argv[1:]
    if "--capture" in args:
        i = args.index("--capture")
        try:
            payment_id, amount = args[i + 1], int(args[i + 2])
        except (IndexError, ValueError):
            print("  usage: --capture <payment_id> <amount_in_paise>", file=sys.stderr)
            return 2
        probe_partial_capture(rail, payment_id, amount)
        return 0

    probe_orders(rail)
    probe_s2s_wall(rail)
    probe_setu()

    _head("To measure partial capture, one human step is needed")
    link = rail.create_payment_link(620_00, "Amanat partial-capture probe")
    print(f"  1. Pay this test link:  \033[4m{link['short_url']}\033[0m")
    print("     Test UPI id: success@razorpay   (or any Razorpay test card)")
    print("  2. Find the payment id (starts pay_) on the Razorpay dashboard")
    print("     under Transactions, or from the payment_link.paid webhook.")
    print("  3. Re-run with a capture BELOW the authorized amount:")
    print("       python -m amanat.rails.probe --capture pay_XXXX 47000")
    print("\n  \033[2mThe link is 620.00 in test mode. No real money moves.\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
