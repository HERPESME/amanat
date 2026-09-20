"""What a verified packet does — and does not — prove.

A packet embeds its own public key, so on its own it can only prove internal
consistency: the entries hash-link and were signed by the key the packet names.
Anyone can mint such a packet with a fresh key. What binds it to a party is
something the verifier already holds from another channel: the signer's public
key, or a checkpoint (length and head hash) of the chain taken earlier.

These tests pin both halves: the honest limit when nothing is pinned, and the
failures that pinning must produce.
"""
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.evidence.chain import (
    Actor, EventType, EvidenceChain, ChainVerificationError, Checkpoint,
)


def _chain(key=None, *, refusal=True, debit=47_000):
    c = EvidenceChain.with_key("cab-1", key or Ed25519PrivateKey.generate())
    c.append(Actor.HUMAN, EventType.ENVELOPE, {"max_total": 100_000})
    c.append(Actor.AGENT, EventType.PROPOSAL, {"action": "reserve", "amount": 500_000})
    if refusal:
        c.append(Actor.POLICY, EventType.REFUSAL, {"reason": "exceeds the budget"})
    c.append(Actor.AGENT, EventType.PROPOSAL, {"action": "debit", "amount": debit})
    c.append(Actor.RAIL, EventType.RAIL_TRANSITION,
             {"action": "debit", "amount": debit, "outcome": "applied"})
    return c


class TestUnpinnedVerification:
    def test_it_says_plainly_that_nothing_was_pinned(self):
        result = EvidenceChain.verify_packet(_chain().export_packet())
        assert result.key_pinned is False
        assert result.checkpoint_matched is False
        assert result.entries == 5

    def test_a_forgery_signed_by_a_fresh_key_still_passes_unpinned(self):
        """The limit, stated as a test: this is why pinning exists."""
        genuine = _chain()
        forged = _chain(refusal=False, debit=62_000)      # refusal dropped, amount raised
        result = EvidenceChain.verify_packet(forged.export_packet())
        assert result.key_pinned is False
        assert result.public_key != genuine.public_key_hex


class TestKeyPinning:
    def test_the_genuine_key_verifies_and_reports_it_was_pinned(self):
        c = _chain()
        result = EvidenceChain.verify_packet(
            c.export_packet(), trusted_keys=[c.public_key_hex])
        assert result.key_pinned is True

    def test_a_forgery_under_another_key_fails_when_the_genuine_key_is_pinned(self):
        genuine = _chain()
        forged = _chain(refusal=False, debit=62_000)
        with pytest.raises(ChainVerificationError, match="not one of the trusted keys"):
            EvidenceChain.verify_packet(
                forged.export_packet(), trusted_keys=[genuine.public_key_hex])


class TestCheckpoints:
    def test_a_truncated_packet_fails_against_a_checkpoint_of_the_whole(self):
        c = _chain()
        checkpoint = c.checkpoint()
        packet = c.export_packet()
        packet["entries"] = packet["entries"][:-2]        # still hash-links and verifies
        EvidenceChain.verify_packet(packet)               # (the gap this closes)
        with pytest.raises(ChainVerificationError, match="truncated"):
            EvidenceChain.verify_packet(packet, checkpoint=checkpoint)

    def test_a_packet_extending_the_checkpointed_prefix_verifies(self):
        c = _chain()
        checkpoint = c.checkpoint()
        c.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"action": "release", "amount": 15_000, "outcome": "applied"})
        result = EvidenceChain.verify_packet(c.export_packet(), checkpoint=checkpoint)
        assert result.checkpoint_matched is True
        assert result.entries == 6

    def test_history_rewritten_by_the_key_holder_fails_the_checkpoint(self):
        """The same key re-signs a rewritten chain: only the checkpoint catches it."""
        key = Ed25519PrivateKey.generate()
        genuine = _chain(key)
        checkpoint = genuine.checkpoint()
        rewritten = _chain(key, debit=62_000)   # same key, same length, edited amounts
        EvidenceChain.verify_packet(rewritten.export_packet())  # internally consistent
        with pytest.raises(ChainVerificationError, match="does not extend the checkpoint"):
            EvidenceChain.verify_packet(rewritten.export_packet(), checkpoint=checkpoint)

    def test_a_checkpoint_carries_length_head_and_key(self):
        c = _chain()
        cp = c.checkpoint()
        assert cp.length == 5
        assert cp.head_hash == c.entries[-1].hash
        assert cp.public_key == c.public_key_hex
        assert Checkpoint.from_dict(cp.to_dict()) == cp


class TestCanonicalisationIsDeclaredAndLegacyStillVerifies:
    def test_new_packets_declare_their_canonicalisation(self):
        assert _chain().export_packet()["canonicalization"] == "jcs-int"

    def test_an_unknown_canonicalisation_is_rejected(self):
        packet = _chain().export_packet()
        packet["canonicalization"] = "made-up"
        with pytest.raises(ChainVerificationError, match="canonicalization"):
            EvidenceChain.verify_packet(packet)

    def test_an_entry_hash_is_the_sha256_of_the_literal_canonical_bytes(self):
        c = EvidenceChain.with_key("s", Ed25519PrivateKey.generate())
        e = c.append(Actor.HUMAN, EventType.INTENT, {"a": 1})
        literal = ('{"actor":"human","event_type":"intent","payload":{"a":1},'
                   '"prev_hash":"' + "0" * 64 + '","seq":0,"timestamp":"'
                   + e.timestamp + '"}')
        assert e.hash == hashlib.sha256(literal.encode("utf-8")).hexdigest()

    def test_a_packet_exported_before_this_change_still_verifies(self):
        """Every packet already in the wild was hashed with the v1 algorithm.

        Rebuilt here by hand, independently of the production code, so that
        dropping legacy support would fail this test rather than pass silently.
        """
        import json
        key = Ed25519PrivateKey.generate()
        prev, entries = "0" * 64, []
        for seq, payload in enumerate([{"amount": 47000, "captured_rupees": 470.0},
                                       {"reason": "ok"}]):
            body = {"seq": seq, "prev_hash": prev, "timestamp": "2026-08-29T00:00:00+00:00",
                    "actor": "rail", "event_type": "rail_transition", "payload": payload}
            h = hashlib.sha256(json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                default=str).encode("utf-8")).hexdigest()
            entries.append({**body, "hash": h, "signature": key.sign(bytes.fromhex(h)).hex()})
            prev = h
        packet = {"version": 1, "subject": "old", "genesis_hash": "0" * 64,
                  "public_key": key.public_key().public_bytes_raw().hex(),
                  "entries": entries}                    # no "canonicalization" field
        result = EvidenceChain.verify_packet(packet)
        assert result.canonicalization == "amanat-v1"
        assert result.entries == 2
