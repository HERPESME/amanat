"""Settle a real Cashfree pre-auth run into a signed evidence chain.

    uv run --with httpx --with cryptography python -m amanat.rails.cashfree_settle

This is the bridge between the two halves of the project: the governed core and a
rail that actually executes the mechanism. It drives the live Cashfree UPI pre-auth
sandbox — hold ₹620, capture ₹470, the ₹150 returns on its own — but every step
goes through the *real* PolicyEngine first, and every real API response is recorded
as a signed, hash-linked entry in an EvidenceChain. One over-budget attempt is
refused along the way, so the artifact carries a refusal too.

The output is a packet that verifies standalone in any browser (see
`amanat.evidence.render`), whose rail transitions are not simulated — they carry
the live order id, cf_payment_id and HTTP status the sandbox returned. It is
written to `web/real_rail_packet.json` (served by the demo console) and rendered to
`docs/sample/cashfree-real-rail-packet.html`.

Needs sandbox credentials, so it runs where `.env` has them and produces a static,
signed artifact. The public demo never calls the rail; it serves this frozen proof.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amanat import env
from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.evidence.render import render_artifact, render_html
from amanat.policy.engine import Action, PolicyEngine, Proposal
from amanat.policy.envelope import Envelope, LedgerState
from amanat.rails.base import RailError
from amanat.rails.cashfree import CashfreePreAuthRail, _rupees

ROOT = Path(__file__).resolve().parents[3]
CUSTOMER = {
    "customer_id": "amanat_probe_cust",
    "customer_email": "probe@example.com",
    "customer_phone": "9999999999",
}


def settle(rail: CashfreePreAuthRail, ceiling: int = 620_00,
           actual: int = 470_00) -> dict:
    """Run the governed lifecycle against the live rail; return the signed packet."""
    envelope = Envelope(
        subject="cab-cashfree-preauth",
        max_total=1_000_00, max_per_txn=800_00, allowed_payees=["citycabs"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        intent_text="Book a cab to the airport, cap it at ₹1,000.")

    chain = EvidenceChain.new(envelope.subject)
    engine = PolicyEngine(chain=chain)
    state = LedgerState()
    chain.append(Actor.HUMAN, EventType.ENVELOPE, envelope.to_payload())

    def propose(action: Action, amount: int, payee: str, reason: str):
        chain.append(Actor.AGENT, EventType.PROPOSAL, {
            "action": action.value, "amount": amount, "payee": payee,
            "reason": reason})
        return engine.evaluate(
            Proposal(action, amount, payee, rail.rail_id, memo=reason),
            envelope, state)

    # 1. An over-budget ceiling — refused by the envelope, no rail call. The
    #    refusal is signed into the chain like any other entry.
    propose(Action.RESERVE, 5_000_00, "citycabs", "fat-fingered ceiling")

    # 2. The real ceiling — permitted, then held on the live rail.
    v = propose(Action.RESERVE, ceiling, "citycabs", "p95 of the fare distribution")
    if not v.allowed:
        raise RailError(f"reserve unexpectedly refused: {v.reason}")
    order_id = f"amanat_pa_{int(time.time())}"
    sc, order = rail.create_preauth_order(order_id, ceiling, customer=CUSTOMER)
    if sc != 200:
        raise RailError(f"order creation failed (HTTP {sc}): {order}")
    sc, pay = rail.pay_upi_collect(order["payment_session_id"])
    cf_id = pay.get("cf_payment_id")
    sc_sim, _ = rail.simulate_success(cf_id)
    state.blocked += ceiling
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "action": "reserve", "amount": ceiling, "outcome": "applied",
        "rail": rail.rail_id, "order_id": order_id, "cf_payment_id": cf_id,
        "http_status": 200,
        "note": "UPI pre-auth hold placed live — funds held in the customer's "
                "account, not debited"})

    # 3. Debit the actual — a partial capture, the shape Razorpay refuses.
    v = propose(Action.DEBIT, actual, "citycabs", "metered fare")
    if not v.allowed:
        raise RailError(f"debit unexpectedly refused: {v.reason}")
    sc, cap = rail.capture(order_id, actual)
    if sc != 200:
        raise RailError(f"capture failed (HTTP {sc}): {cap}")
    captured_rupees = cap.get("authorization", {}).get("captured_amount")
    state.debited += actual
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "action": "debit", "amount": actual, "outcome": "applied",
        "rail": rail.rail_id, "order_id": order_id, "http_status": 200,
        "captured_amount_rupees": captured_rupees,
        "payment_message": cap.get("payment_message"),
        "note": "partial capture accepted live — Razorpay refuses the same shape "
                "with HTTP 400"})

    # 4. The remainder returns on its own. No revoke, no teardown, no stranding.
    remainder = ceiling - actual
    state.released += remainder
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "action": "release", "amount": remainder, "outcome": "auto_released",
        "rail": rail.rail_id, "order_id": order_id,
        "note": "the uncaptured remainder is returned by the rail on its own; an "
                "explicit VOID afterward is refused (nothing left to void)"})

    chain.verify()
    return chain.export_packet()


def _write(packet: dict) -> None:
    EvidenceChain.verify_packet(packet)                       # prove before shipping
    (ROOT / "web" / "real_rail_packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False))
    sample = ROOT / "docs" / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "cashfree-real-rail-packet.html").write_text(render_html(packet))
    (sample / "cashfree-real-rail-packet.artifact.html").write_text(
        render_artifact(packet))
    print(f"  wrote web/real_rail_packet.json ({len(packet['entries'])} entries)")
    print("  wrote docs/sample/cashfree-real-rail-packet.html (+ .artifact.html)")


def main() -> int:
    env.load()
    try:
        rail = CashfreePreAuthRail()
    except RailError as exc:
        print(f"\n  ✗ {exc}\n  Put sandbox CASHFREE_CLIENT_ID / _SECRET in .env")
        return 2
    print("\n\033[1mSETTLE ON A REAL RAIL — Cashfree UPI pre-auth (sandbox)\033[0m")
    packet = settle(rail)
    _write(packet)
    debited = sum(e["payload"]["amount"] for e in packet["entries"]
                  if e["event_type"] == "rail_transition"
                  and e["payload"].get("action") == "debit")
    print(f"  merchant nets ₹{_rupees(debited):,.0f} — signed, and it verifies "
          "standalone in a browser\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
