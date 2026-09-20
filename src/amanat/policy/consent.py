"""A human's signed consent to widen an envelope.

The cap is the human's grant, so only the human can raise it — and the proof is a
signature made by a key the orchestrator never holds. A session that is handed
the human's private key can sign consent no human gave; so the session takes a
finished consent, verifies it, and records it. Where the key lives (a passkey, a
wallet, the browser's WebCrypto) is the caller's business, not this system's.

The bytes signed are the consent body without its `signature`, canonicalised
(`amanat.evidence.canonical`), so a browser and this module sign and verify the
same bytes. The body binds the consent to the exact state it widens — the
subject, and the caps it starts from — so an old consent cannot be replayed
against a later, different envelope.
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from amanat.evidence.canonical import CanonicalError, canonicalize
from amanat.policy.envelope import Envelope


def build_widening(envelope: Envelope, *, new_max_total: int, new_max_per_txn: int,
                   reason: str, public_key_hex: str) -> dict:
    """The unsigned body of a request to raise `envelope`'s caps."""
    return {
        "event": "envelope_widened",
        "subject": envelope.subject,
        "from": {"max_total": envelope.max_total, "max_per_txn": envelope.max_per_txn},
        "to": {"max_total": new_max_total, "max_per_txn": new_max_per_txn},
        "reason": reason,
        "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519", "x": public_key_hex}},
    }


def consent_bytes(body: dict) -> bytes:
    """Canonical bytes of a consent minus its own signature field."""
    return canonicalize({k: v for k, v in body.items() if k != "signature"})


def sign_consent(body: dict, key: Ed25519PrivateKey) -> dict:
    """The body with a signature made by `key` — for tests and local tools.

    A hosted service must not call this: the signature belongs on the human's
    device, and the service only ever verifies it.
    """
    return {**body, "signature": key.sign(consent_bytes(body)).hex()}


def verify_consent(consent: object) -> bool:
    """True if the consent's signature verifies against the key it names."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(consent["cnf"]["jwk"]["x"]))
        pub.verify(bytes.fromhex(consent["signature"]), consent_bytes(consent))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature, CanonicalError):
        return False
