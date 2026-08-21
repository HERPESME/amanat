"""The evidence chain: append-only, hash-linked, signed, self-verifying."""
import pytest
from amanat.evidence.chain import (
    EvidenceChain, Actor, EventType, ChainVerificationError,
)


@pytest.fixture
def chain():
    return EvidenceChain.new(subject="order-1")


class TestChainInvariants:
    def test_first_entry_links_to_genesis(self, chain):
        e = chain.append(Actor.HUMAN, EventType.INTENT, {"text": "book a cab"})
        assert e.seq == 0
        assert e.prev_hash == EvidenceChain.GENESIS_HASH

    def test_sequence_is_monotonic_without_gaps(self, chain):
        for i in range(5):
            chain.append(Actor.AGENT, EventType.PROPOSAL, {"i": i})
        assert [e.seq for e in chain.entries] == [0, 1, 2, 3, 4]

    def test_each_entry_links_to_the_previous(self, chain):
        a = chain.append(Actor.HUMAN, EventType.INTENT, {})
        b = chain.append(Actor.AGENT, EventType.PROPOSAL, {})
        assert b.prev_hash == a.hash

    def test_valid_chain_verifies(self, chain):
        chain.append(Actor.HUMAN, EventType.INTENT, {})
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {"to": "BLOCKED"})
        chain.verify()  # must not raise

    def test_tampering_is_detected_and_names_the_entry(self, chain):
        chain.append(Actor.HUMAN, EventType.INTENT, {"amount": 100})
        chain.append(Actor.AGENT, EventType.PROPOSAL, {"amount": 100})
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {"amount": 100})
        chain.entries[1].payload["amount"] = 999_999
        with pytest.raises(ChainVerificationError) as exc:
            chain.verify()
        assert exc.value.seq == 1

    def test_signature_forgery_is_detected(self, chain):
        e = chain.append(Actor.HUMAN, EventType.INTENT, {})
        e.signature = b"\x00" * len(e.signature)
        with pytest.raises(ChainVerificationError, match="signature"):
            chain.verify()


class TestRefusalsAreEvidence:
    def test_a_refusal_is_recordable(self, chain):
        e = chain.append(
            Actor.POLICY, EventType.REFUSAL,
            {"rule": "ceiling_exceeded", "citation": "NPCI OC-228"},
        )
        assert e.event_type is EventType.REFUSAL

    def test_chain_reports_refusals_separately(self, chain):
        chain.append(Actor.AGENT, EventType.PROPOSAL, {})
        chain.append(Actor.POLICY, EventType.REFUSAL, {"rule": "r"})
        assert len(chain.refusals()) == 1


class TestExportedPacketIsSelfContained:
    def test_packet_verifies_without_the_originating_chain(self, chain):
        chain.append(Actor.HUMAN, EventType.INTENT, {})
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {"to": "DEBITED"})
        packet = chain.export_packet()
        EvidenceChain.verify_packet(packet)  # must not raise

    def test_tampered_packet_fails_verification(self, chain):
        chain.append(Actor.HUMAN, EventType.INTENT, {"amt": 1})
        packet = chain.export_packet()
        packet["entries"][0]["payload"]["amt"] = 2
        with pytest.raises(ChainVerificationError):
            EvidenceChain.verify_packet(packet)
