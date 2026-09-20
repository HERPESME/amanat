"""Measure what Cashfree reports about the uncaptured remainder of a partial capture.

    uv run --with httpx python -m amanat.rails.probe_cashfree_release start
    uv run --with httpx python -m amanat.rails.probe_cashfree_release poll     # again, later

The question this answers is one the first probe could not: after a CAPTURE of ₹470
against a ₹620 hold, does the API ever report the ₹150 as released — and when? Cashfree
documents that an authorisation not captured within seven days is released, and is silent
on the remainder of a partial capture. The earlier probe inferred a release from a refused
VOID (which Cashfree documents as impossible after any capture) and from arithmetic. This
one only records what the API says.

`start` places two ₹620 holds in the sandbox: one is partially captured (₹470), the other
is left alone as a control. Both are read immediately. `poll` reads both again and appends
what came back; run it at about +5 minutes, +1 hour, +24 hours and +7 days 1 hour (the
seven-day expiry). Each read is one dated line in an append-only log:

    docs/observations/cashfree-release/observations.jsonl

Bodies are stored as the API returned them, minus the session token and the synthetic
customer details. Nothing is inferred and nothing is summarised: if a field changes over
time, the log shows when; if none does, that is the finding.

Sandbox only, by the adapter's construction; moves no real money. The authorisation is
forced with `POST /simulate`, so this measures Cashfree's sandbox API, not an issuer.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from amanat import env
from amanat.rails.base import RailError
from amanat.rails.cashfree import CashfreePreAuthRail

ROOT = Path(__file__).resolve().parents[3]
OBS_DIR = ROOT / "docs" / "observations" / "cashfree-release"
STATE = OBS_DIR / "orders.json"
LOG = OBS_DIR / "observations.jsonl"

HOLD, CAPTURED = 620_00, 470_00
_SECRET_KEYS = frozenset({"payment_session_id", "customer_details"})
_CUSTOMER = {"customer_id": "amanat_probe_cust", "customer_email": "probe@example.com",
             "customer_phone": "9999999999"}


def redact(value):
    """A copy of `value` without the session token or customer details, at any depth."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k not in _SECRET_KEYS}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def record(label: str, order_id: str, started_at: datetime, now: datetime,
           order_body, payments_body, *, order_http_status: int = 200,
           payments_http_status: int = 200, refunds_body=None,
           refunds_http_status: int | None = None) -> dict:
    """One dated observation: what the API said, when, and how long after the start."""
    rec = {
        "observed_at": now.isoformat(),
        "elapsed_seconds": int((now - started_at).total_seconds()),
        "label": label, "order_id": order_id,
        "order_http_status": order_http_status, "order": redact(order_body),
        "payments_http_status": payments_http_status, "payments": redact(payments_body),
    }
    if refunds_http_status is not None:
        rec.update(refunds_http_status=refunds_http_status, refunds=redact(refunds_body))
    return rec


def append_record(log: Path, rec: dict) -> None:
    """Append one observation. The log is never rewritten."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def poll(rail, state: dict, now: datetime) -> list[dict]:
    """Read every order in `state` once. A failed read is recorded, not dropped."""
    started = datetime.fromisoformat(state["started_at"])
    out = []
    for order in state["orders"]:
        o_sc, o_body = rail.fetch_order(order["order_id"])
        p_sc, p_body = rail.fetch_payments(order["order_id"])
        extra = {}
        if hasattr(rail, "fetch_refunds"):       # a refund/void entity would show up here
            r_sc, r_body = rail.fetch_refunds(order["order_id"])
            extra = {"refunds_body": r_body, "refunds_http_status": r_sc}
        out.append(record(order["label"], order["order_id"], started, now, o_body, p_body,
                          order_http_status=o_sc, payments_http_status=p_sc, **extra))
    return out


def _hold(rail: CashfreePreAuthRail, label: str) -> dict:
    order_id = f"amanat_rel_{label}_{int(time.time())}"
    sc, order = rail.create_preauth_order(order_id, HOLD, customer=_CUSTOMER)
    if sc != 200:
        raise RailError(f"order creation failed (HTTP {sc}): {redact(order)}")
    sc, pay = rail.pay_upi_collect(order["payment_session_id"])
    if sc != 200:
        raise RailError(f"UPI collect failed (HTTP {sc}): {pay}")
    cf_id = pay.get("cf_payment_id")
    sc, sim = rail.simulate_success(cf_id)
    if sc != 200:
        raise RailError(f"sandbox authorisation failed (HTTP {sc}): {sim}")
    time.sleep(2)
    return {"label": label, "order_id": order_id, "cf_payment_id": cf_id, "held_paise": HOLD}


def start(rail: CashfreePreAuthRail) -> dict:
    """Place the two holds, capture on one, and record the first observation of each."""
    if STATE.exists():
        raise RailError(f"{STATE} exists: a measurement is already running. Poll it, or "
                        "move the directory aside to begin a new one.")
    started = datetime.now(timezone.utc)
    capture, control = _hold(rail, "capture"), _hold(rail, "control")
    sc, cap = rail.capture(capture["order_id"], CAPTURED)
    capture.update(captured_paise=CAPTURED, capture_http_status=sc,
                   capture_response=redact(cap))
    if sc != 200:
        raise RailError(f"capture failed (HTTP {sc}): {cap}")
    state = {"started_at": started.isoformat(), "orders": [capture, control],
             "note": "sandbox; authorisation forced with POST /simulate; the 'control' "
                     "order is held and never captured or voided"}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False))
    return state


def main(argv: list[str]) -> int:
    env.load()
    cmd = argv[0] if argv else ""
    if cmd not in ("start", "poll"):
        print(__doc__)
        return 2
    try:
        rail = CashfreePreAuthRail()
    except RailError as exc:
        print(f"\n  ✗ {exc}\n  Put sandbox CASHFREE_CLIENT_ID / _SECRET in .env")
        return 2
    try:
        state = start(rail) if cmd == "start" else json.loads(STATE.read_text())
    except (RailError, OSError) as exc:
        print(f"\n  ✗ {exc}")
        return 1
    now = datetime.now(timezone.utc)
    for rec in poll(rail, state, now):
        append_record(LOG, rec)
        print(f"  {rec['label']:8s} {rec['order_id']}  +{rec['elapsed_seconds']}s  "
              f"order {rec['order_http_status']} payments {rec['payments_http_status']}")
    print(f"  appended to {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
