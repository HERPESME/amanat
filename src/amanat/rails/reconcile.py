"""Reconcile a settlement chain against what the rail actually holds.

The evidence chain is the system's own account of what the money did. That is
necessary but not sufficient: a signed record can be internally perfect and
still disagree with the rail, if a call the chain believed succeeded did not, or
a webhook was missed. Reconciliation asks the rail the same question
independently — how much did you capture, how much did you refund — and reports
agreement or the exact drift.

It is the answer to "does your chain match reality?", made mechanical: fetch the
authoritative figures from the rail, verify the chain first (a record that does
not verify is not reconciled against, it is rejected), then compare net to net.

Credential-free by construction: it needs only a rail exposing `fetch_payment`,
so the tests pass a fake and the live path passes `RazorpayTestRail`.
"""
from __future__ import annotations

from dataclasses import dataclass

from amanat.evidence.chain import ChainVerificationError, EvidenceChain

_DEBIT_LIKE = {"captured", "debited", "debit"}
_REFUND_LIKE = {"refunded"}


@dataclass
class Reconciliation:
    reconciled: bool
    detail: str
    chain_net: int = 0
    rail_net: int = 0
    drift: int = 0          # magnitude of disagreement, |chain_net − rail_net|
    payment_id: str = ""

    def summary(self) -> str:
        if self.reconciled:
            return (f"reconciled — chain and rail agree the merchant nets "
                    f"₹{self.chain_net / 100:,.2f}")
        return (f"DRIFT ₹{self.drift / 100:,.2f} — chain says "
                f"₹{self.chain_net / 100:,.2f}, rail says ₹{self.rail_net / 100:,.2f}")


def _net_from_chain(packet: dict) -> int:
    debited = refunded = 0
    for e in packet.get("entries", []):
        if e["event_type"] != "rail_transition":
            continue
        p = e["payload"]
        t = str(p.get("transition") or p.get("action") or "").lower()
        if t in _DEBIT_LIKE:
            debited += int(p.get("amount", 0))
        elif t in _REFUND_LIKE:
            refunded += int(p.get("amount", 0))
    return debited - refunded


def reconcile(rail, payment_id: str, packet: dict) -> Reconciliation:
    """Compare what the chain claims for `payment_id` against what the rail holds."""
    # A record that does not verify cannot be reconciled — it is rejected, so a
    # drift is never blamed on the rail when the chain is the thing that moved.
    try:
        EvidenceChain.verify_packet(packet)
    except ChainVerificationError as exc:
        return Reconciliation(
            False, f"the chain does not verify (entry #{exc.seq}); nothing to "
                   "reconcile against", payment_id=payment_id)

    chain_net = _net_from_chain(packet)

    payment = rail.fetch_payment(payment_id)
    captured = int(payment.get("amount_captured",
                               payment.get("amount", 0) if payment.get("status") == "captured" else 0))
    refunded = int(payment.get("amount_refunded", 0) or 0)
    rail_net = captured - refunded

    drift = abs(chain_net - rail_net)
    if drift == 0:
        return Reconciliation(
            True, f"chain and rail agree: captured ₹{captured / 100:,.2f}, "
                  f"refunded ₹{refunded / 100:,.2f}, net ₹{rail_net / 100:,.2f}",
            chain_net, rail_net, 0, payment_id)

    chain_refunded = sum(int(e["payload"].get("amount", 0))
                         for e in packet["entries"]
                         if e["event_type"] == "rail_transition"
                         and str(e["payload"].get("transition", "")).lower() in _REFUND_LIKE)
    which = ("a refund the chain recorded is not reflected on the rail"
             if chain_refunded > refunded else
             "the captured or refunded amounts differ")
    return Reconciliation(
        False, f"drift of ₹{drift / 100:,.2f} — {which}. "
               f"Chain net ₹{chain_net / 100:,.2f}, rail net ₹{rail_net / 100:,.2f}.",
        chain_net, rail_net, drift, payment_id)
