"""Signed, hash-linked evidence over payment-rail state transitions.

The claim this module exists to support:

    Every agent-payment evidence standard shipping today — AP2's mandate chain,
    Visa TAP, Mastercard Agentic Tokens, Pine Labs Grantex — terminates at
    authorization: they prove the agent was *permitted* to spend. This chain
    extends downward through the rail's own state transitions — block placed,
    partial debit, release, revoke — so the artifact proves not what was
    authorized, but what the money actually did.

Two design consequences follow directly, and neither is negotiable:

1. Rail transitions are first-class entries, not metadata on an authorization.
2. Refusals are evidence. A policy denial is recorded as carefully as a debit,
   because a chain of happy paths proves nothing about governance.

The chaining primitive is deliberately ordinary — hash-linked entries with
detached signatures. Generic tamper-evident logging is heavily prior-arted
(SCITT, in-toto, C2PA, and a wall of DLT patents). What is being claimed here is
*what* is chained, never *how*.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


class ChainVerificationError(Exception):
    """The chain does not verify. Carries the sequence number that broke it."""

    def __init__(self, message: str, seq: int | None = None) -> None:
        super().__init__(message)
        self.seq = seq


class Actor(Enum):
    """Who caused an entry. Kept separate so authority is auditable per-actor."""

    HUMAN = "human"
    AGENT = "agent"       # the LLM. Proposes; never decides.
    POLICY = "policy"     # deterministic engine. Decides; never proposes.
    RAIL = "rail"         # the payment rail itself.


class EventType(Enum):
    INTENT = "intent"                    # what the human asked for, in their words
    ENVELOPE = "envelope"                # the compiled constraint envelope
    PROPOSAL = "proposal"                # what the agent wants to do
    POLICY_DECISION = "policy_decision"  # the engine's independent verdict
    RAIL_TRANSITION = "rail_transition"  # block / debit / release / revoke
    REFUSAL = "refusal"                  # a boundary the system declined to cross


def _canonical(payload: Any) -> bytes:
    """Deterministic serialization. Verification depends on byte-stability."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str).encode()


@dataclass
class Entry:
    seq: int
    prev_hash: str
    timestamp: str
    actor: Actor
    event_type: EventType
    payload: dict[str, Any]
    hash: str = ""
    signature: bytes = b""

    def digest(self) -> str:
        """Hash over everything that binds this entry to its position and content."""
        return hashlib.sha256(_canonical({
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "timestamp": self.timestamp,
            "actor": self.actor.value,
            "event_type": self.event_type.value,
            "payload": self.payload,
        })).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "timestamp": self.timestamp,
            "actor": self.actor.value,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "hash": self.hash,
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entry:
        return cls(
            seq=d["seq"], prev_hash=d["prev_hash"], timestamp=d["timestamp"],
            actor=Actor(d["actor"]), event_type=EventType(d["event_type"]),
            payload=d["payload"], hash=d["hash"],
            signature=bytes.fromhex(d["signature"]),
        )


@dataclass
class EvidenceChain:
    """Append-only chain for one subject (an order, a session, a mandate)."""

    GENESIS_HASH = "0" * 64

    subject: str
    entries: list[Entry] = field(default_factory=list)
    _key: Ed25519PrivateKey | None = None

    @classmethod
    def new(cls, subject: str) -> EvidenceChain:
        return cls(subject=subject, _key=Ed25519PrivateKey.generate())

    @property
    def public_key_hex(self) -> str:
        assert self._key is not None
        return self._key.public_key().public_bytes_raw().hex()

    def append(self, actor: Actor, event_type: EventType,
               payload: dict[str, Any]) -> Entry:
        """Commit one entry. There is no update and no delete."""
        assert self._key is not None, "chain has no signing key"
        entry = Entry(
            seq=len(self.entries),
            prev_hash=self.entries[-1].hash if self.entries else self.GENESIS_HASH,
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=actor,
            event_type=event_type,
            payload=payload,
        )
        entry.hash = entry.digest()
        entry.signature = self._key.sign(bytes.fromhex(entry.hash))
        self.entries.append(entry)
        return entry

    def refusals(self) -> list[Entry]:
        """Every boundary the system declined to cross. The governance story."""
        return [e for e in self.entries if e.event_type is EventType.REFUSAL]

    def rail_transitions(self) -> list[Entry]:
        """What the money actually did — the half other standards do not cover."""
        return [e for e in self.entries if e.event_type is EventType.RAIL_TRANSITION]

    def verify(self) -> None:
        assert self._key is not None
        self._verify_entries(self.entries, self._key.public_key())

    def export_packet(self) -> dict[str, Any]:
        """A dispute artifact that verifies with no access to this system."""
        return {
            "version": 1,
            "subject": self.subject,
            "public_key": self.public_key_hex,
            "genesis_hash": self.GENESIS_HASH,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def verify_packet(cls, packet: dict[str, Any]) -> None:
        """Verify an exported packet standalone. Never consults the originator."""
        try:
            pub = Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(packet["public_key"]))
            entries = [Entry.from_dict(d) for d in packet["entries"]]
        except (KeyError, ValueError) as exc:
            raise ChainVerificationError(f"malformed packet: {exc}") from exc
        cls._verify_entries(entries, pub)

    @staticmethod
    def _verify_entries(entries: list[Entry], pub: Ed25519PublicKey) -> None:
        expected_prev = EvidenceChain.GENESIS_HASH
        for i, e in enumerate(entries):
            if e.seq != i:
                raise ChainVerificationError(
                    f"sequence gap: expected {i}, found {e.seq}", seq=e.seq)
            if e.prev_hash != expected_prev:
                raise ChainVerificationError(
                    f"broken link at seq {e.seq}: prev_hash does not match "
                    f"the preceding entry", seq=e.seq)
            recomputed = e.digest()
            if recomputed != e.hash:
                raise ChainVerificationError(
                    f"content tampered at seq {e.seq}: recomputed digest "
                    f"{recomputed[:16]}... != recorded {e.hash[:16]}...", seq=e.seq)
            try:
                pub.verify(e.signature, bytes.fromhex(e.hash))
            except InvalidSignature as exc:
                raise ChainVerificationError(
                    f"invalid signature at seq {e.seq}", seq=e.seq) from exc
            expected_prev = e.hash
