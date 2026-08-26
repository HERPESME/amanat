"""Read and write AP2 Open Payment Mandates.

AP2 (Google's Agent Payments Protocol) is the authorization standard this project
sits downstream of. Its `OpenPaymentMandate` is what a human signs to grant an
agent future spending authority — the *permission* half of the problem. This
module converts one into an `Envelope` and back, so the rest of the system can
adjudicate its settlement chain against a real AP2 authorization rather than a
paraphrase of one.

The schema is AP2's own, not an approximation of it. Source:
google-agentic-commerce/AP2, code/sdk/python/ap2/sdk/generated/open_payment_mandate.py
— `vct: "mandate.payment.open.1"`, a list of typed `constraints`, amounts in
ISO-4217 minor units. For INR the minor unit is paise, which is exactly this
project's unit, so no scaling is needed or done.

Scope, stated honestly: INR only (the project is paise-native), and the mandate
is read as a plain dict rather than through AP2's pydantic SDK, to stay
dependency-free. The constraint *types* and *field names* are AP2's, verbatim.
"""
from __future__ import annotations

from datetime import datetime, timezone

VCT_OPEN = "mandate.payment.open.1"


class Ap2Error(ValueError):
    """An AP2 mandate could not be read, or an envelope could not be emitted."""


def _constraints_by_type(mandate: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in mandate.get("constraints", []):
        if isinstance(c, dict) and "type" in c:
            out[c["type"]] = c
    return out


def from_open_payment_mandate(mandate: dict) -> "Envelope":
    """Convert a real AP2 Open Payment Mandate into a spending envelope.

    Refuses anything it cannot map cleanly — a missing amount range, a currency
    that is not INR — rather than guessing, because an authorization read wrong
    is worse than one not read at all.
    """
    from amanat.policy.envelope import Envelope

    vct = mandate.get("vct", "")
    if not vct.startswith("mandate.payment.open"):
        raise Ap2Error(
            f"not an open payment mandate: vct is {vct!r}, expected {VCT_OPEN!r}")

    by = _constraints_by_type(mandate)

    amount_range = by.get("payment.amount_range")
    if amount_range is None:
        raise Ap2Error("mandate has no payment.amount_range constraint; "
                       "there is no per-transaction cap to enforce")
    currency = amount_range.get("currency", "")
    if currency != "INR":
        raise Ap2Error(
            f"currency {currency!r} is not supported; this rail settles in INR "
            "(paise), so only INR mandates can be adjudicated here")
    max_per_txn = int(amount_range["max"])

    budget = by.get("payment.budget")
    if budget is not None:
        if budget.get("currency", "INR") != "INR":
            raise Ap2Error("budget currency must be INR")
        # AP2 types Budget.max as a float; round to whole paise (no float money).
        max_total = int(round(float(budget["max"])))
    else:
        max_total = max_per_txn        # no total stated → the per-txn cap bounds it

    payees = []
    ap = by.get("payment.allowed_payees")
    if ap is not None:
        payees = [m["id"] for m in ap.get("allowed", []) if "id" in m]
    if not payees and mandate.get("payee", {}).get("id"):
        payees = [mandate["payee"]["id"]]
    if not payees:
        raise Ap2Error("mandate names no allowed payee")

    expires_at = _expiry(by.get("payment.execution_date"), mandate.get("exp"))

    return Envelope(
        subject=mandate.get("transaction_id", "ap2-mandate"),
        max_total=max_total,
        max_per_txn=max_per_txn,
        allowed_payees=payees,
        expires_at=expires_at,
        intent_text=mandate.get("intent_text", ""),
        notes="compiled from AP2 OpenPaymentMandate " + vct,
    )


def _expiry(execution_date: dict | None, exp: int | None) -> datetime:
    """Prefer the execution-date window's not_after; fall back to `exp` epoch."""
    if execution_date and execution_date.get("not_after"):
        return _parse_iso(execution_date["not_after"])
    if exp is not None:
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    raise Ap2Error("mandate states no expiry (no execution_date.not_after, no exp)")


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_open_payment_mandate(env: "Envelope") -> dict:
    """Emit a valid AP2 Open Payment Mandate from an envelope.

    Emitted unsigned: the `cnf` slot is filled, and the document bound to a
    user's key, by `sign_mandate` — which is a separate step because the party
    that signs a grant is the user, never the orchestrator that emits it.
    """
    return {
        "vct": VCT_OPEN,
        "transaction_id": env.subject,
        "constraints": [
            {"type": "payment.allowed_payees",
             "allowed": [{"id": p, "name": p} for p in env.allowed_payees]},
            {"type": "payment.amount_range", "currency": "INR",
             "max": env.max_per_txn, "min": 0},
            {"type": "payment.budget", "max": env.max_total, "currency": "INR"},
            {"type": "payment.execution_date",
             "not_after": env.expires_at.isoformat()},
        ],
        "cnf": {"note": "amanat signs evidence, not mandates; placeholder"},
        "intent_text": env.intent_text,
    }


# ---------------------------------------------------------------------------
# Consent binding. Real AP2 binds a mandate to the user's key via `cnf`
# (RFC 7800) inside an SD-JWT. This mirrors that structure honestly — the
# user's Ed25519 public key sits in cnf.jwk and the mandate carries a signature
# over its canonical bytes — without claiming to be SD-JWT. The point is
# structural: the grant is signed by a key that is NOT the orchestrator's, so
# the adjudicator can check who granted it while trusting neither party.
# ---------------------------------------------------------------------------
import json as _json

from cryptography.exceptions import InvalidSignature as _InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey as _Priv, Ed25519PublicKey as _Pub,
)


def _mandate_bytes(mandate: dict) -> bytes:
    """Canonical bytes of the mandate minus its own signature field."""
    body = {k: v for k, v in mandate.items() if k != "signature"}
    return _json.dumps(body, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str).encode("utf-8")


def sign_mandate(mandate: dict, user_key: _Priv) -> dict:
    """Return a copy of `mandate` bound to and signed by `user_key`.

    Deep-copies the input so the caller's document is never mutated.
    """
    m = _json.loads(_json.dumps(mandate, default=str))
    m.pop("signature", None)
    m["cnf"] = {"jwk": {"kty": "OKP", "crv": "Ed25519",
                        "x": user_key.public_key().public_bytes_raw().hex()}}
    m["signature"] = user_key.sign(_mandate_bytes(m)).hex()
    return m


def verify_mandate(mandate: dict) -> bool | None:
    """True if the mandate's signature verifies against the key in its cnf.

    False if it is signed but does not verify (tampered, or signed by a key
    other than the one it names). None if it carries no signature at all —
    unbound, which is a different fact from invalid and is reported as such.
    """
    sig = mandate.get("signature")
    if not sig:
        return None
    x = (mandate.get("cnf") or {}).get("jwk", {}).get("x")
    if not x:
        return False
    try:
        pub = _Pub.from_public_bytes(bytes.fromhex(x))
        pub.verify(bytes.fromhex(sig), _mandate_bytes(mandate))
        return True
    except (ValueError, _InvalidSignature):
        return False
