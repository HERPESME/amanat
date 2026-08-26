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

Two calls means two ways to be half-done, and the dangerous one is the second:
the capture has succeeded, the customer's full ceiling is gone, and the refund
fails. This module treats that as what it is — money owed — rather than as a
log line. The refund is retried within a bound, and if it still fails a
COMPENSATION entry records exactly what was captured and what is due, so the
obligation survives the process that failed to meet it. Running settlement
again later completes the refund without touching the capture; running it after
success is an idempotent no-op.

The settlement talks to anything exposing `fetch_payment`, `capture` and
`refund`; the live path passes `RazorpayTestRail`, the tests pass a fake. No
float touches money.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

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
    compensation_required: bool = False
    refund_due: int = 0

    def summary(self) -> str:
        if self.compensation_required:
            return (f"captured ₹{self.ceiling / 100:,.2f} — refund of "
                    f"₹{self.refund_due / 100:,.2f} FAILED and is OWED "
                    f"(compensation recorded; re-run settle to complete)")
        if not self.ok:
            return f"REFUSED: {self.detail}"
        return (f"captured ₹{self.ceiling / 100:,.2f} → "
                f"refunded ₹{self.refunded / 100:,.2f} → "
                f"merchant nets ₹{self.net / 100:,.2f}")


def settle_capture_refund(rail, payment_id: str, actual: int, *,
                          chain: EvidenceChain | None = None,
                          refund_attempts: int = 3,
                          sleep: Callable[[float], None] = time.sleep,
                          ) -> SettlementResult:
    """Settle an authorized/captured payment down to `actual` via capture+refund.

    Every money action is gated and recorded. The gate that matters is the
    amount-contingent invariant: you cannot settle for more than the ceiling.

    `refund_attempts` bounds the retry on the return leg; `sleep` is injectable
    so tests need not wait. Re-running on a payment whose refund already
    completed is a no-op success; re-running after a failed refund resumes it.
    """
    chain = chain or EvidenceChain.new(subject=payment_id)
    profile = RAILS.get(getattr(rail, "rail_id", "razorpay_auth_capture"))

    payment = rail.fetch_payment(payment_id)
    ceiling = int(payment["amount"])
    status = payment.get("status", "")
    already_refunded = int(payment.get("amount_refunded", 0) or 0)

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

    difference = ceiling - actual

    # Idempotency. A prior refund equal to what this settlement would return
    # means the work is already done — say so and touch nothing. A prior refund
    # of any other size means someone settled this to a different actual;
    # proceeding would misstate the net, so refuse.
    if already_refunded > 0:
        if already_refunded == difference:
            chain.append(Actor.POLICY, EventType.POLICY_DECISION, {
                "rule": "settlement_already_complete", "payment_id": payment_id,
                "refunded": already_refunded, "net_to_merchant": actual})
            return SettlementResult(
                True, "already settled to this amount; nothing to do", chain,
                ceiling=ceiling, actual=actual, refunded=already_refunded,
                net=actual, payment_id=payment_id)
        detail = (f"payment {payment_id} already has {already_refunded} refunded, "
                  f"which does not match the {difference} this settlement implies; "
                  "refusing rather than misstating the net")
        chain.append(Actor.POLICY, EventType.REFUSAL,
                     {"rule": "prior_refund_mismatch",
                      "amount_refunded": already_refunded,
                      "expected": difference, "payment_id": payment_id})
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
    else:  # already captured — a resumed settlement, or a payment-link payment
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "transition": "CAPTURED", "amount": ceiling, "ref": payment_id,
            "note": "payment was already captured on arrival",
        })

    # The return leg. From here the customer's full ceiling is gone, so a
    # failure is not a refusal — it is money owed. Retry within a bound, then
    # record the debt explicitly rather than let it live only in a log line.
    refund_id = ""
    if difference > 0:
        last_body: dict = {}
        for attempt in range(1, refund_attempts + 1):
            sc, body = rail.refund(payment_id, difference)
            if sc == 200:
                refund_id = body.get("id", "")
                break
            last_body = body
            chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
                "transition": "REFUND_ATTEMPT", "amount": difference,
                "ref": payment_id, "attempt": attempt, "outcome": "refund_failed",
                "response": str(body)[:160],
            })
            if attempt < refund_attempts:
                sleep(0.5 * attempt)
        else:
            chain.append(Actor.POLICY, EventType.COMPENSATION, {
                "rule": "refund_failed_after_capture",
                "payment_id": payment_id,
                "captured": ceiling,
                "refund_due": difference,
                "attempts": refund_attempts,
                "last_response": str(last_body)[:160],
                "note": ("the customer's full ceiling was captured and the "
                         "difference could not be returned; this obligation is "
                         "recorded here and is completed by re-running settlement"),
            })
            return SettlementResult(
                False,
                f"capture succeeded but the refund of {difference} failed after "
                f"{refund_attempts} attempts; compensation of {difference} is "
                "owed and recorded",
                chain, ceiling=ceiling, actual=actual, net=ceiling,
                payment_id=payment_id, compensation_required=True,
                refund_due=difference)

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
