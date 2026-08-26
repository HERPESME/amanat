"""Settle a real Razorpay payment down to an actual amount, live.

    uv run --with httpx --with cryptography python -m amanat.rails.settle \
        --payment pay_XXXX --actual 47000

Runs capture-then-refund against the live test-mode API and prints the signed
evidence chain, with the rail's own order/payment/refund ids as references. Test
mode throughout — no real money moves.

To produce a fresh authorized payment first:  python -m amanat.rails.authorize
"""
from __future__ import annotations

import sys

from amanat import env
from amanat.rails.base import RailError
from amanat.rails.razorpay import RazorpayTestRail
from amanat.rails.settlement import settle_capture_refund


def main() -> int:
    env.load()
    args = sys.argv[1:]
    if "--payment" not in args or "--actual" not in args:
        print("usage: python -m amanat.rails.settle --payment pay_XXXX "
              "--actual <paise>", file=sys.stderr)
        return 2
    payment_id = args[args.index("--payment") + 1]
    actual = int(args[args.index("--actual") + 1])

    try:
        rail = RazorpayTestRail()
    except RailError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    print("\n\033[1mLIVE SETTLEMENT — Razorpay test mode (capture then refund)\033[0m")
    result = settle_capture_refund(rail, payment_id, actual)

    colour = "33" if result.compensation_required else ("32" if result.ok else "31")
    print(f"\n  \033[{colour}m{result.summary()}\033[0m\n")
    for e in result.chain.rail_transitions():
        p = e.payload
        bad = p.get("outcome") == "refund_failed"
        print(f"  \033[{'31' if bad else '1'}m{p['transition']:<15s}\033[0m "
              f"₹{p['amount'] / 100:>9,.2f}  ref {p.get('ref', '')}"
              + (f"  attempt {p['attempt']}" if bad else ""))
        if p.get("note"):
            print(f"    \033[2m{p['note']}\033[0m")
    for e in result.chain.refusals():
        print(f"  \033[31mREFUSED\033[0m {e.payload.get('rule')}")
    from amanat.evidence.chain import EventType
    for e in result.chain.entries:
        if e.event_type is EventType.COMPENSATION:
            p = e.payload
            print(f"  \033[1;33mCOMPENSATION OWED\033[0m captured ₹{p['captured'] / 100:,.2f}, "
                  f"refund due ₹{p['refund_due'] / 100:,.2f} after {p['attempts']} attempts")
            print(f"    \033[2m{p['note']}\033[0m")

    from amanat.evidence.chain import EvidenceChain
    EvidenceChain.verify_packet(result.chain.export_packet())
    print("\n  evidence packet \033[32mverifies\033[0m standalone")
    print("  \033[2mThis chain reads AUTHORIZED → CAPTURED → REFUNDED, not")
    print("  BLOCKED → DEBITED → RELEASED. Same intent, different rail, different")
    print("  money path — and the chain shows which one actually happened.\033[0m\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
