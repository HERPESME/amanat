"""Amount-contingent settlement on Razorpay's real verbs: capture, then refund.

Razorpay cannot block a ceiling and debit less than it — capture must equal the
authorized amount (measured, not quoted: see
`test_semantics.TestRazorpayPartialCaptureWasMeasured`). The same *net* outcome
is still reachable: capture the full ceiling, then refund the difference.

That is deliberately not modelled as `reserve / debit / release`. Those verbs
describe SBMD's money path — funds blocked in the customer's account, only the
actual amount ever moving. Razorpay's path is genuinely different: the money
leaves in full at capture and part of it returns days later at refund, and the
merchant pays MDR on the ceiling rather than the actual. Naming this chain
AUTHORIZED → CAPTURED → REFUNDED, rather than borrowing SBMD's verbs, is what
keeps that difference legible — which is the whole point of the evidence chain.

The settlement talks to anything exposing `fetch_payment`, `capture` and
`refund`; the live path passes `RazorpayTestRail`, the tests pass a fake. No
float touches money.
"""
from __future__ import annotations

from dataclasses import dataclass

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.rails.semantics import RAILS

_PAYABLE = ("authorized", "captured")


@dataclass
class SettlementResult:
    ok: bool
    detail: str
    chain: EvidenceChain
    ceiling: int = 0
    actual: int = 0
    refunded: int = 0
    net: int = 0
    payment_id: str = ""
    refund_id: str = ""

    def summary(self) -> str:
        if not self.ok:
            return f"REFUSED: {self.detail}"
        return (f"captured ₹{self.ceiling / 100:,.2f} → "
                f"refunded ₹{self.refunded / 100:,.2f} → "
                f"merchant nets ₹{self.net / 100:,.2f}")


def settle_capture_refund(rail, payment_id: str, actual: int, *,
                          chain: EvidenceChain | None = None) -> SettlementResult:
    """Settle an authorized/captured payment down to `actual` via capture+refund.

    Every money action is gated and recorded. The gate that matters is the
    amount-contingent invariant: you cannot settle for more than the ceiling.
    """
    chain = chain or EvidenceChain.new(subject=payment_id)
    profile = RAILS.get(getattr(rail, "rail_id", "razorpay_auth_capture"))

    payment = rail.fetch_payment(payment_id)
    ceiling = int(payment["amount"])
    status = payment.get("status", "")
    already_refunded = int(payment.get("amount_refunded", 0) or 0)

    # Settling a payment that was already partly refunded would make the
    # ceiling/net arithmetic wrong — the difference is computed against the
    # original amount, not what remains. Refuse rather than report a bad number.
    if already_refunded > 0:
        detail = (f"payment {payment_id} already has {already_refunded} refunded; "
                  "settling it again would misstate the net. Use a fresh payment.")
        chain.append(Actor.POLICY, EventType.REFUSAL,
                     {"rule": "already_partly_refunded",
                      "amount_refunded": already_refunded, "payment_id": payment_id})
        return SettlementResult(False, detail, chain, ceiling=ceiling)

    if status not in _PAYABLE:
        detail = f"payment {payment_id} is {status!r}, not settleable"
        chain.append(Actor.POLICY, EventType.REFUSAL,
                     {"rule": "unsettleable_status", "status": status,
                      "payment_id": payment_id})
        return SettlementResult(False, detail, chain, ceiling=ceiling)

    # The gate. actual > ceiling would mean debiting more than was committed —
    # the exact thing amount-contingent settlement forbids.
    if actual > ceiling:
        detail = (f"actual {actual} exceeds the committed ceiling {ceiling}; "
                  "settlement cannot draw more than was authorized")
        chain.append(Actor.POLICY, EventType.REFUSAL,
                     {"rule": "actual_exceeds_ceiling",
                      "actual": actual, "ceiling": ceiling})
        return SettlementResult(False, detail, chain, ceiling=ceiling, actual=actual)

    # Record the authorized starting point.
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "transition": "AUTHORIZED", "amount": ceiling, "ref": payment_id,
        "note": "the customer has committed the full ceiling on this rail",
    })

    # Capture the FULL ceiling — partial capture is forbidden, which is the
    # reason this whole path exists. Cite the measured rule as the justification.
    if status == "authorized":
        rule = profile.explain("partial_debit") if profile else None
        sc, body = rail.capture(payment_id, ceiling)
        if sc != 200:
            detail = f"capture failed ({sc}): {body}"
            chain.append(Actor.POLICY, EventType.REFUSAL,
                         {"rule": "capture_failed", "response": str(body)[:200]})
            return SettlementResult(False, detail, chain, ceiling=ceiling,
                                    actual=actual)
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "transition": "CAPTURED", "amount": ceiling, "ref": payment_id,
            "note": "captured in full because partial capture is forbidden",
            "citation": rule.citation if rule else "",
            "quote": rule.quote if rule else "",
        })
    else:  # already captured (e.g. via a payment link, which auto-captures)
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "transition": "CAPTURED", "amount": ceiling, "ref": payment_id,
            "note": "payment was already captured on arrival",
        })

    difference = ceiling - actual
    refund_id = ""
    if difference > 0:
        sc, body = rail.refund(payment_id, difference)
        if sc != 200:
            detail = f"refund failed ({sc}): {body}"
            chain.append(Actor.POLICY, EventType.REFUSAL,
                         {"rule": "refund_failed", "response": str(body)[:200]})
            return SettlementResult(False, detail, chain, ceiling=ceiling,
                                    actual=actual)
        refund_id = body.get("id", "")
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "transition": "REFUNDED", "amount": difference, "ref": refund_id,
            "note": "the difference between ceiling and actual, returned",
        })

    # The honest cost of doing it this way rather than on SBMD. Recorded as a
    # decision entry so the dispute artifact carries it, not just the demo.
    chain.append(Actor.POLICY, EventType.POLICY_DECISION, {
        "rule": "settlement_strategy",
        "strategy": "capture_then_refund",
        "net_to_merchant": actual,
        "honest_cost": (
            "Unlike SBMD, where only the actual amount ever leaves the "
            "customer's account, here the full ceiling is captured and the "
            "difference is refunded — so the customer is out the full ceiling "
            "until the refund settles, and the merchant pays MDR on the ceiling."
        ),
    })

    return SettlementResult(
        True, "settled", chain, ceiling=ceiling, actual=actual,
        refunded=difference, net=actual, payment_id=payment_id,
        refund_id=refund_id)
