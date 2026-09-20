"""Signed, hash-linked evidence over payment-rail state transitions.

The claim this module exists to support:

    Agent-payment specifications (AP2, x402, ACP, UCP, MPP) establish what an
    agent may spend and attest a payment's outcome. In the ones read for this
    project, none records the rail's intermediate states — a hold placed, a
    partial debit, a release — or the transitions the system refused to make.
    This chain records those, so the artifact shows not only what was authorized
    but what the money actually did. (Visa's Trusted Agent Protocol, Mastercard's
    agentic tokens and Pine Labs' Grantex were not reviewed and are not
    characterised here.)

Two design consequences follow directly, and neither is negotiable:

1. Rail transitions are first-class entries, not metadata on an authorization.
2. Refusals are evidence. A policy denial is recorded as carefully as a debit,
   because a chain of happy paths proves nothing about governance.

The chaining primitive is deliberately ordinary — hash-linked entries with
detached signatures. Generic tamper-evident logging is heavily prior-arted
(SCITT, in-toto, C2PA, transparency logs, and a wall of DLT patents). What is
being claimed here is *what* is chained, never *how*.

What a verified packet proves — and what it does not
----------------------------------------------------
A packet embeds the public key it was signed with. Verifying it on its own
therefore proves **internal consistency**: the entries hash-link, run in order
without gaps, and were signed by the key the packet names. It does not prove who
holds that key, and it cannot see entries removed from the end. Whoever holds the
signing key can rewrite history and re-sign it; anyone can mint a packet with a
fresh key. Two things the verifier already holds bind a packet to a party:

* the signer's public key, passed as ``trusted_keys``; and
* a **checkpoint** of the chain taken earlier (its length and head hash), passed
  as ``checkpoint``, which fails a packet that is shorter than, or diverges from,
  what was committed.

A checkpoint is only as strong as the party holding it: it must be kept outside
the operator's control (a counterparty, a witness, a timestamp authority) for the
chain to be verifiable by someone who does not trust the operator.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from amanat.evidence.canonical import CanonicalError, canonicalize

CANON_CURRENT = "jcs-int"     # RFC 8785 restricted to integers; see evidence.canonical
CANON_LEGACY = "amanat-v1"    # what packets exported before `canonicalization` was declared use


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
    RAIL_INTENT = "rail_intent"          # written BEFORE a rail call: what is about to be asked
    RAIL_TRANSITION = "rail_transition"  # block / debit / release / revoke
    RAIL_IN_DOUBT = "rail_in_doubt"      # the call did not complete; its outcome is unknown
    REFUSAL = "refusal"                  # a boundary the system declined to cross
    COMPENSATION = "compensation"        # money moved, the follow-up failed: owed, not forgotten


def _canonical_v1(payload: Any) -> bytes:
    """The serialisation used before `canonicalization` was declared.

    Kept only so packets exported earlier keep verifying. It sorts keys by code
    point and stringifies unknown types, which is why it was replaced; never use
    it to write.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


_CANONICALIZERS: dict[str, Callable[[Any], bytes]] = {
    CANON_CURRENT: canonicalize,
    CANON_LEGACY: _canonical_v1,
}


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

    def digest(self, canon: Callable[[Any], bytes] = canonicalize) -> str:
        """Hash over everything that binds this entry to its position and content."""
        return hashlib.sha256(canon({
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


@dataclass(frozen=True)
class Checkpoint:
    """A commitment to a chain's state: its length and what its last entry hashes to.

    Hand it to a party outside the operator's control. A packet later verified
    against it must be at least this long and must contain the committed head at
    the committed position — so a shorter packet (truncation) and a rewritten
    history both fail, even when the rewriter holds the signing key.
    """

    subject: str
    length: int
    head_hash: str
    public_key: str

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "length": self.length,
                "head_hash": self.head_hash, "public_key": self.public_key}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(subject=d["subject"], length=int(d["length"]),
                   head_hash=d["head_hash"], public_key=d["public_key"])


@dataclass(frozen=True)
class Verification:
    """What a successful verification established — and what it was pinned to."""

    subject: str
    entries: int
    head_hash: str
    public_key: str
    canonicalization: str
    key_pinned: bool            # the packet's key was in `trusted_keys`
    checkpoint_matched: bool    # the packet extends the supplied checkpoint


@dataclass
class EvidenceChain:
    """Append-only chain for one subject (an order, a session, a mandate)."""

    GENESIS_HASH = "0" * 64

    subject: str
    entries: list[Entry] = field(default_factory=list)
    _key: Ed25519PrivateKey | None = None
    _store: Path | None = None

    @classmethod
    def new(cls, subject: str) -> EvidenceChain:
        return cls.with_key(subject, Ed25519PrivateKey.generate())

    @classmethod
    def with_key(cls, subject: str, key: Ed25519PrivateKey,
                 store: str | Path | None = None) -> EvidenceChain:
        """A chain signed by a key the caller controls (custody is the caller's).

        With `store`, every entry is written to that file (one JSON record per
        line, fsynced) *before* `append` returns, so a crash never loses an
        entry the caller was told was recorded. `load` reads it back.
        """
        chain = cls(subject=subject, _key=key,
                    _store=Path(store) if store is not None else None)
        if chain._store is not None:
            if chain._store.exists() and chain._store.stat().st_size:
                raise FileExistsError(
                    f"{chain._store} already holds a chain; use EvidenceChain.load")
            chain._write(json.dumps(
                {"kind": "amanat.chain", "version": 1, "subject": subject,
                 "public_key": chain.public_key_hex, "canonicalization": CANON_CURRENT},
                sort_keys=True, ensure_ascii=False))
        return chain

    @classmethod
    def load(cls, path: str | Path, key: Ed25519PrivateKey) -> EvidenceChain:
        """Rebuild a chain from its store, verify it, and continue appending to it.

        A record torn by a crash mid-write (no trailing newline) was never
        acknowledged, so it is dropped and the file trimmed to the last whole one.
        """
        path = Path(path)
        raw = path.read_bytes()
        *lines, tail = raw.split(b"\n")
        if tail:
            with open(path, "r+b") as f:
                f.truncate(len(raw) - len(tail))
                f.flush()
                os.fsync(f.fileno())
        try:
            header = json.loads(lines[0])
            entries = [Entry.from_dict(json.loads(line)) for line in lines[1:]]
        except (IndexError, ValueError, KeyError, TypeError) as exc:
            raise ChainVerificationError(f"unreadable chain store {path}: {exc}") from exc
        if header.get("kind") != "amanat.chain":
            raise ChainVerificationError(f"{path} is not an amanat chain store")
        pub = key.public_key()
        if header.get("public_key") != pub.public_bytes_raw().hex():
            raise ChainVerificationError("the store was written under a different key")
        cls._verify_entries(entries, pub, _CANONICALIZERS[header["canonicalization"]])
        return cls(subject=header["subject"], entries=entries, _key=key, _store=path)

    def _write(self, line: str) -> None:
        with open(self._store, "ab") as f:
            f.write(line.encode("utf-8") + b"\n")
            f.flush()
            os.fsync(f.fileno())

    @property
    def public_key_hex(self) -> str:
        if self._key is None:
            raise RuntimeError("chain has no signing key")
        return self._key.public_key().public_bytes_raw().hex()

    def append(self, actor: Actor, event_type: EventType,
               payload: dict[str, Any]) -> Entry:
        """Commit one entry. There is no update and no delete.

        Raises `CanonicalError` — before anything is recorded — for a payload that
        cannot be canonicalised (a float, a lone surrogate, an unknown type).
        """
        if self._key is None:
            raise RuntimeError("chain has no signing key")
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
        if self._store is not None:        # durable first: memory only follows the disk
            self._write(json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=False))
        self.entries.append(entry)
        return entry

    def refusals(self) -> list[Entry]:
        """Every boundary the system declined to cross. The governance story."""
        return [e for e in self.entries if e.event_type is EventType.REFUSAL]

    def rail_transitions(self) -> list[Entry]:
        """What the money actually did — the half other standards do not cover."""
        return [e for e in self.entries if e.event_type is EventType.RAIL_TRANSITION]

    def checkpoint(self) -> Checkpoint:
        """Commit to the chain as it stands, for a party outside the operator."""
        return Checkpoint(
            subject=self.subject, length=len(self.entries),
            head_hash=self.entries[-1].hash if self.entries else self.GENESIS_HASH,
            public_key=self.public_key_hex)

    def verify(self) -> None:
        if self._key is None:
            raise RuntimeError("chain has no signing key")
        self._verify_entries(self.entries, self._key.public_key(), canonicalize)

    def export_packet(self) -> dict[str, Any]:
        """A dispute artifact that verifies with no access to this system."""
        return {
            "version": 2,
            "canonicalization": CANON_CURRENT,
            "subject": self.subject,
            "public_key": self.public_key_hex,
            "genesis_hash": self.GENESIS_HASH,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def verify_packet(cls, packet: dict[str, Any], *,
                      trusted_keys: Iterable[str] | None = None,
                      checkpoint: Checkpoint | dict[str, Any] | None = None,
                      ) -> Verification:
        """Verify an exported packet standalone. Never consults the originator.

        With no arguments this proves internal consistency only (see the module
        docstring). Pass `trusted_keys` to require the packet be signed by a key
        you already trust, and `checkpoint` to require it extend a commitment you
        already hold. Raises `ChainVerificationError` naming the entry that broke.
        """
        name = packet.get("canonicalization", CANON_LEGACY)
        canon = _CANONICALIZERS.get(name)
        if canon is None:
            raise ChainVerificationError(f"unknown canonicalization {name!r}")
        try:
            key_hex = packet["public_key"]
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
            entries = [Entry.from_dict(d) for d in packet["entries"]]
        except (KeyError, ValueError, TypeError) as exc:
            raise ChainVerificationError(f"malformed packet: {exc}") from exc

        key_pinned = False
        if trusted_keys is not None:
            if key_hex.lower() not in {k.lower() for k in trusted_keys}:
                raise ChainVerificationError(
                    "the packet's signing key is not one of the trusted keys")
            key_pinned = True

        cls._verify_entries(entries, pub, canon)

        matched = False
        if checkpoint is not None:
            cp = checkpoint if isinstance(checkpoint, Checkpoint) \
                else Checkpoint.from_dict(checkpoint)
            if cp.subject != packet.get("subject"):
                raise ChainVerificationError(
                    f"the checkpoint is for {cp.subject!r}, not {packet.get('subject')!r}")
            if cp.public_key.lower() != key_hex.lower():
                raise ChainVerificationError(
                    "the packet is signed by a different key than the checkpoint names")
            if len(entries) < cp.length:
                raise ChainVerificationError(
                    f"the packet is truncated: it has {len(entries)} entries but the "
                    f"checkpoint covers {cp.length}")
            if cp.length and entries[cp.length - 1].hash != cp.head_hash:
                raise ChainVerificationError(
                    f"the packet does not extend the checkpoint: entry "
                    f"{cp.length - 1} differs from the committed head", seq=cp.length - 1)
            matched = True

        return Verification(
            subject=str(packet.get("subject", "")), entries=len(entries),
            head_hash=entries[-1].hash if entries else cls.GENESIS_HASH,
            public_key=key_hex, canonicalization=name,
            key_pinned=key_pinned, checkpoint_matched=matched)

    @staticmethod
    def _verify_entries(entries: list[Entry], pub: Ed25519PublicKey,
                        canon: Callable[[Any], bytes]) -> None:
        expected_prev = EvidenceChain.GENESIS_HASH
        for i, e in enumerate(entries):
            if e.seq != i:
                raise ChainVerificationError(
                    f"sequence gap: expected {i}, found {e.seq}", seq=e.seq)
            if e.prev_hash != expected_prev:
                raise ChainVerificationError(
                    f"broken link at seq {e.seq}: prev_hash does not match "
                    f"the preceding entry", seq=e.seq)
            try:
                recomputed = e.digest(canon)
            except CanonicalError as exc:
                raise ChainVerificationError(
                    f"malformed payload at seq {e.seq}: {exc}", seq=e.seq) from exc
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
