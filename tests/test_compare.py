"""The two-rail comparison — the heart of the pitch.

Same intent, two rails, two signed chains. Credential-free: the SBMD side uses
the real simulator, the Razorpay side runs the real settlement code through an
in-memory transport.
"""
from amanat.compare import sbmd_chain, razorpay_chain, CEILING, ACTUAL
from amanat.evidence.chain import EvidenceChain


class TestBothChainsAreHonestAndVerifiable:
    def test_sbmd_only_debits_the_actual(self):
        chain, _ = sbmd_chain()
        debited = [e.payload["amount"] for e in chain.rail_transitions()
                   if e.payload["transition"] == "DEBITED"]
        assert debited == [ACTUAL]

    def test_sbmd_transitions_are_block_debit_release(self):
        chain, _ = sbmd_chain()
        t = [e.payload["transition"] for e in chain.rail_transitions()]
        assert t == ["BLOCKED", "DEBITED", "RELEASED"]

    def test_razorpay_captures_the_full_ceiling(self):
        chain, _, _ = razorpay_chain(None)
        captured = [e.payload["amount"] for e in chain.rail_transitions()
                    if e.payload["transition"] == "CAPTURED"]
        assert captured == [CEILING]

    def test_razorpay_transitions_are_authorize_capture_refund(self):
        chain, _, live = razorpay_chain(None)
        assert live is False
        t = [e.payload["transition"] for e in chain.rail_transitions()]
        assert t == ["AUTHORIZED", "CAPTURED", "REFUNDED"]

    def test_the_two_rails_use_different_verbs(self):
        """If they used the same verbs, the difference would be erased."""
        sbmd_t = {e.payload["transition"] for e in sbmd_chain()[0].rail_transitions()}
        rzp_t = {e.payload["transition"] for e in razorpay_chain(None)[0].rail_transitions()}
        assert sbmd_t.isdisjoint(rzp_t)

    def test_both_chains_verify_standalone(self):
        for chain in (sbmd_chain()[0], razorpay_chain(None)[0]):
            EvidenceChain.verify_packet(chain.export_packet())
