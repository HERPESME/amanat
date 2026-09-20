"""Settle a real Cashfree pre-auth run into a signed evidence chain.

    uv run --with httpx --with cryptography python -m amanat.rails.cashfree_settle

This drives the Cashfree UPI pre-auth *sandbox* — hold ₹620, capture ₹470 — with every
step going through the real PolicyEngine first, and every real API response recorded as
a signed, hash-linked entry in an EvidenceChain. One over-budget attempt is refused along
the way, so the artifact carries a refusal too.

What the chain says is limited to what was read. The capture's response states
`captured_amount`; a read of the order and its payments afterwards is recorded verbatim
in paise. What the API did NOT show is the uncaptured ₹150 being released, and this script
does not write one: it records, as the orchestrator's own note (not the rail's), that the
release is unverified and that Cashfree documents a seven-day expiry. The measurement of
that question is `probe_cashfree_release`.

The output is a packet that verifies standalone in any browser (see
`amanat.evidence.render`). Amounts are integer paise throughout: a float in a payload
cannot be reproduced by a browser's `JSON.parse`, so the current packet format refuses
one. It is written to `web/real_rail_packet.json` (served by the demo console) and
rendered to `docs/sample/cashfree-real-rail-packet.html`.

Needs sandbox credentials, so it runs where `.env` has them and produces a static,
signed artifact. The public demo never calls the rail; it serves this frozen proof. The
authorisation is forced with `POST /simulate`, so the proof is of Cashfree's sandbox API.
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
from amanat.rails.cashfree import CashfreePreAuthRail, _paise, _rupees

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
    stated = cap.get("authorization", {}).get("captured_amount")
    if stated is None:
        raise RailError("the capture response did not state the captured amount")
    captured = _paise(stated)                                # read, not assumed
    state.debited += captured
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "action": "debit", "amount": actual, "outcome": "applied",
        "rail": rail.rail_id, "order_id": order_id, "http_status": 200,
        "captured_amount": captured,
        "payment_message": cap.get("payment_message"),
        "note": "partial capture accepted in the sandbox — Razorpay refuses the "
                "same shape with HTTP 400"})

    # 4. Read what the rail says now, and record exactly that. The order and its
    #    payments are the rail's own statement of state after the capture.
    o_sc, order_body = rail.fetch_order(order_id)
    p_sc, payments = rail.fetch_payments(order_id)
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
        "action": "observe", "outcome": "observed", "rail": rail.rail_id,
        "order_id": order_id, "http_status": max(o_sc, p_sc),
        "state": _observed_state(order_body, payments),
        "note": "read from GET /orders/{id} and /orders/{id}/payments after the capture; "
                "no field in either response reports the uncaptured remainder as released"})

    # 5. The gap, stated as the orchestrator's own note — not as a rail event. Nothing
    #    the rail returned shows the remainder going back; the refused VOID is
    #    Cashfree's documented "once captured, cannot be voided", not evidence of it.
    remainder = ceiling - actual
    chain.append(Actor.POLICY, EventType.POLICY_DECISION, {
        "rule": "remainder_release_unverified", "amount": remainder,
        "capability": "cashfree_preauth.remainder_auto_released", "tier": "unverified",
        "hold_expiry_days": 7,
        "note": "the uncaptured remainder is not recorded as released: this run did not "
                "observe it. Cashfree documents that an authorisation not captured within "
                "seven days is released and is silent on the remainder of a partial "
                "capture; see probe_cashfree_release"})

    chain.verify()
    return chain.export_packet()


def _observed_state(order: dict, payments: list | dict) -> dict:
    """The rail's reported state as integers, booleans and strings only.

    Amounts arrive as rupee decimals and are converted to paise exactly; anything
    that is not a plain scalar is left out rather than reformatted.
    """
    first = (payments[0] if isinstance(payments, list) and payments else {}) or {}
    auth = first.get("authorization") or {}

    def paise(value):
        return None if value is None else _paise(value)

    return {
        "order_status": order.get("order_status"),
        "order_amount": paise(order.get("order_amount")),
        "payment_status": first.get("payment_status"),
        "payment_amount": paise(first.get("payment_amount")),
        "is_captured": first.get("is_captured"),
        "authorization_action": auth.get("action"),
        "authorization_status": auth.get("status"),
        "captured_amount": paise(auth.get("captured_amount")),
    }


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
    print(f"  merchant nets ₹{_rupees(debited):,.0f}; the remainder's release is "
          "recorded as unverified — signed, and it verifies standalone in a browser\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
