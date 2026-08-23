"""Same intent, two rails, two signed chains — side by side.

    uv run --with cryptography python -m amanat.compare
    uv run --with httpx --with cryptography python -m amanat.compare \
        --payment pay_XXXX          # use a real Razorpay payment for the right column

This is the centre of the argument. A ceiling of ₹620 is committed for a fare
that turns out to be ₹470. The merchant nets ₹470 either way — but *how the money
moves* differs, and the evidence chain is what makes that difference legible.

  SBMD (UPI Reserve Pay)    only ₹470 ever leaves the customer's account. The
                            ₹150 was blocked, never debited; the block is torn
                            down and it was never gone.

  Razorpay capture+refund   the full ₹620 leaves at capture and ₹150 returns
                            days later at refund. The customer is out ₹620 in the
                            meantime, and the merchant pays MDR on ₹620.

Without a --payment the right column runs through the same settlement code against
an in-memory transport, clearly labelled illustrative. With one, it runs live and
carries real order/payment/refund ids.
"""
from __future__ import annotations

import sys

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.rails.base import BlockState
from amanat.rails.settlement import settle_capture_refund
from amanat.rails.simulator import SimulatedRail

CEILING = 620_00
ACTUAL = 470_00


def _rs(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def sbmd_chain() -> tuple[EvidenceChain, str]:
    """Drive the SBMD simulator and record what the money did."""
    chain = EvidenceChain.new(subject="cab-sbmd")
    rail = SimulatedRail("sbmd", customer_balance=5_000_00)

    ref = rail.reserve(CEILING, "citycabs")
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "transition": "BLOCKED", "amount": CEILING, "ref": ref.block_id,
        "note": "funds set aside in the customer's own account, not taken"})

    rail.debit(ref, ACTUAL)
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "transition": "DEBITED", "amount": ACTUAL, "ref": ref.block_id,
        "note": "the only amount that actually leaves the customer"})

    # On SBMD, returning the remainder tears the block down (round-5 finding).
    # The customer never lost the 150 — it was blocked, not debited.
    rail.release(ref)
    torn = rail.status(ref) is BlockState.REVOKED
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "transition": "RELEASED", "amount": CEILING - ACTUAL, "ref": ref.block_id,
        "note": ("block torn down to return the remainder — but it was blocked, "
                 "never debited, so the customer was only ever out the actual")
        if torn else "remainder released"})

    chain.append(Actor.POLICY, EventType.POLICY_DECISION, {
        "rule": "money_semantics", "rail": "sbmd",
        "left_customer_account": ACTUAL,
        "note": "only the actual amount ever left the account"})
    return chain, "left the account: " + _rs(ACTUAL) + " (and that is all)"


class _IllustrativeRazorpay:
    """In-memory stand-in when no live payment is supplied. Same code path."""

    rail_id = "razorpay_auth_capture"

    def fetch_payment(self, payment_id):
        return {"id": payment_id, "amount": CEILING, "status": "authorized",
                "order_id": "order_ILLUSTRATIVE", "amount_refunded": 0}

    def capture(self, payment_id, amount):
        return 200, {"id": payment_id, "amount": amount, "status": "captured"}

    def refund(self, payment_id, amount):
        return 200, {"id": "rfnd_ILLUSTRATIVE", "amount": amount,
                     "status": "processed"}


def razorpay_chain(payment_id: str | None):
    live = bool(payment_id)
    if live:
        from amanat.rails.razorpay import RazorpayTestRail
        rail = RazorpayTestRail()
    else:
        rail = _IllustrativeRazorpay()
        payment_id = "pay_ILLUSTRATIVE"

    result = settle_capture_refund(rail, payment_id, ACTUAL)
    tag = "left the account: " + _rs(CEILING) + " until the refund settles"
    return result.chain, tag, live


def _render(title: str, chain: EvidenceChain, footer: str) -> list[str]:
    lines = [f"\033[1m{title}\033[0m", ""]
    for e in chain.rail_transitions():
        p = e.payload
        lines.append(f"  {p['transition']:<11s} {_rs(p['amount']):>10s}")
        if p.get("note"):
            for chunk in _wrap(p["note"], 40):
                lines.append(f"    \033[2m{chunk}\033[0m")
    lines.append("")
    lines.append(f"  \033[33m{footer}\033[0m")
    return lines


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def main() -> int:
    from amanat import env
    env.load()

    payment_id = None
    if "--payment" in sys.argv:
        payment_id = sys.argv[sys.argv.index("--payment") + 1]

    print("\n\033[1mSAME INTENT, TWO RAILS\033[0m")
    print(f"  Ceiling {_rs(CEILING)} committed for a fare that comes to {_rs(ACTUAL)}.")
    print(f"  The merchant nets {_rs(ACTUAL)} both ways — the money path differs.\n")

    left, left_foot = sbmd_chain()
    right, right_foot, live = razorpay_chain(payment_id)

    lcol = _render("UPI SBMD (Reserve Pay)", left, left_foot)
    rlabel = "Razorpay capture+refund" + ("  \033[32m[LIVE]\033[0m" if live
                                          else "  \033[2m[illustrative]\033[0m")
    rcol = _render(rlabel, right, right_foot)

    width = 46
    for i in range(max(len(lcol), len(rcol))):
        l = lcol[i] if i < len(lcol) else ""
        r = rcol[i] if i < len(rcol) else ""
        pad = width - len(_strip(l))
        print(f"  {l}{' ' * max(pad, 2)}│  {r}")

    EvidenceChain.verify_packet(left.export_packet())
    EvidenceChain.verify_packet(right.export_packet())
    print(f"\n  both packets \033[32mverify\033[0m standalone")
    print("  \033[2mThe merchant's revenue is identical. What the customer's money")
    print("  did is not — and only one of these rails leaves it untouched until")
    print("  the moment of purchase. The chain is the difference, made checkable.\033[0m")
    if not live:
        print("\n  \033[2mRun with --payment pay_XXXX (after python -m amanat.rails.authorize)")
        print("  to settle a real Razorpay payment and carry real ids.\033[0m")
    print()
    return 0


def _strip(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


if __name__ == "__main__":
    sys.exit(main())
