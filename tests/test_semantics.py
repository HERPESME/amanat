"""Rail capability encoding: every assertion cited or explicitly UNVERIFIED."""
import pytest
from amanat.rails.semantics import (
    Capability, SourceTier, RailProfile, RAILS, CapabilityError,
)


class TestCitationDiscipline:
    def test_cited_capability_requires_a_verbatim_quote(self):
        with pytest.raises(CapabilityError, match="quote"):
            Capability(
                name="partial_debit", supported=True,
                source_tier=SourceTier.PRIMARY,
                citation="NPCI OC-228", url="https://example.test",
                quote="",
            )

    def test_unverified_capability_needs_no_quote(self):
        cap = Capability(name="whatever", supported=False,
                         source_tier=SourceTier.UNVERIFIED)
        assert cap.source_tier is SourceTier.UNVERIFIED

    def test_marketing_tier_is_never_usable_as_fact(self):
        cap = Capability(name="x", supported=True, source_tier=SourceTier.MARKETING,
                         citation="blog", url="https://example.test", quote="best in class")
        assert cap.is_fact is False

    def test_primary_and_secondary_are_facts(self):
        for tier in (SourceTier.PRIMARY, SourceTier.SECONDARY):
            cap = Capability(name="x", supported=True, source_tier=tier,
                             citation="c", url="https://e.test", quote="q")
            assert cap.is_fact is True


class TestRailProfile:
    def test_permits_returns_false_for_unknown_capability(self):
        assert RAILS["sbmd"].permits("teleportation") is False

    def test_unverified_capability_is_not_permitted(self):
        """Absence of evidence is not permission. This is the safety property."""
        rail = RailProfile(rail_id="t", display_name="T", capabilities=[
            Capability(name="risky", supported=True, source_tier=SourceTier.UNVERIFIED),
        ])
        assert rail.permits("risky") is False

    def test_explain_names_the_citation(self):
        d = RAILS["razorpay_auth_capture"].explain("partial_debit")
        assert d.allowed is False
        assert "equal to the amount authorized" in d.quote


class TestKnownRails:
    def test_all_registered_rails_have_ids_matching_their_key(self):
        for key, rail in RAILS.items():
            assert rail.rail_id == key

    def test_no_registered_capability_uses_marketing_as_support(self):
        """A rail may never claim support on marketing copy alone."""
        for rail in RAILS.values():
            for cap in rail.capabilities.values():
                if cap.supported and cap.source_tier is SourceTier.MARKETING:
                    pytest.fail(f"{rail.rail_id}.{cap.name} rests on marketing")

    def test_razorpay_authorized_is_not_a_hold(self):
        """The trap: Razorpay 'authorized' has already debited the customer."""
        rail = RAILS["razorpay_auth_capture"]
        assert rail.permits("funds_held_in_customer_account") is False

    def test_sbmd_forbids_delivery_before_debit_for_goods(self):
        d = RAILS["sbmd"].explain("post_delivery_debit_goods")
        assert d.allowed is False
        assert "confirmation of successful debit" in d.quote

    def test_sbmd_debit_before_delivery_quote_is_not_truncated(self):
        """The stored quote elided a clause until 21 Aug 2026.

        OC-228 acquiring obligation 4 does not stop at "the delivery of goods
        and service should only be after the confirmation of successful debit".
        It continues "...to the Acquiring bank for categories such as quick
        commerce, food delivery, etc.", and it ends "post successful delivery of
        services", not "post successful delivery". Both were missing from the
        quote this project carried. A reviewer who pulls the PDF will check.

        The named categories do not narrow the rule to those categories - "such
        as" is exemplifying - but the honest thing is to quote the sentence that
        exists and build to the stricter reading, rather than to quote a
        sentence that reads as a flat all-goods prohibition.
        """
        q = RAILS["sbmd"].explain("post_delivery_debit_goods").quote
        assert "to the Acquiring bank for categories such as quick commerce" in q
        assert q.endswith("merchant may debit post successful delivery of services.")

    def test_sbmd_permits_partial_debit_on_a_primary_citation(self):
        """The capability this whole project rests on, and where it comes from.

        NPCI/UPI/OC-228/2025-26, acquiring obligations 5(d) and 5(e). Neither
        that circular nor OC-200 contains an explicit sentence permitting a
        debit smaller than the block, and neither forbids one; it follows by
        necessary implication from "current block limits (unutilised) ...
        checked before initiating a debit" and from "original block value,
        remaining balance" being two different quantities.
        """
        rail = RAILS["sbmd"]
        assert rail.permits("partial_debit") is True
        d = rail.explain("partial_debit")
        assert "unutilised" in d.quote
        assert "remaining balance" in d.quote
        assert "OC-228" in d.citation

    def test_sbmd_does_not_auto_release_the_unused_remainder(self):
        """Blocking a ceiling and debiting the actual does not return the rest.

        OC-200 issuer obligation 1: the fund "shall be blocked in the account
        till the time mandate is expired, revoked or the mandate amount is
        exhausted". Neither circular imposes a duty to release the remainder
        after a partial debit, and neither states any timeline for one.

        So the third leg of amount-contingent settlement - release the
        difference - is an action this system must take (revoke or update), not
        a guarantee the rail provides. Left alone, the remainder stays blocked
        until the customer-chosen end date, up to 90 days.
        """
        rail = RAILS["sbmd"]
        assert rail.permits("remainder_auto_released") is False
        assert rail.permits("merchant_revocable") is True
        assert rail.permits("customer_revocable") is True
        assert "till the time mandate is expired, revoked" in \
            rail.explain("remainder_auto_released").quote

    def test_sbmd_multi_debit_has_no_cap_on_the_number_of_debits(self):
        """"shall allow multiple debits against the block" - OC-200 issuer 1.

        The bounds are on value, time and concurrency, never on a count. The
        "3 retries in 24 hours" number that circulates is not a debit budget:
        OC-228 acquiring obligation 3 grants it only for a debit that timed out
        with the issuer/payer PSP, with no retries for any other decline.
        """
        d = RAILS["sbmd"].explain("multi_debit")
        assert d.allowed is True
        assert "shall allow multiple debits against the block" in d.quote

    def test_sbmd_purpose_codes_differ_only_on_transaction_limit(self):
        """76 vs 77 changes the per-transaction limit and nothing else.

        The only differential rule in either circular is OC-200 clause (c):
        Rs 5 lakh per transaction for purpose code 76, existing UPI limits for
        77. Nothing distinguishes them on partial debit, on the number of
        debits, or on the remainder.

        The claim this project used to carry - "76 is merchant-revoke-only, 77
        is customer-revocable" - appears in neither circular and is recorded as
        unsourced in the capability notes.
        """
        q = RAILS["sbmd"].explain("purpose_code_77_for_online_goods").quote
        assert "Rs 5 Lakh for the purpose code 76" in q
        assert "for purpose code 77 existing UPI limits shall be applicable" in q

    def test_sbmd_ninety_days_never_travels_without_the_ten_thousand(self):
        """The two limits are one sentence in OC-228 and must stay one claim.

        For a ceiling-selection thesis the Rs 10,000 block cap is the harder
        constraint of the two: a predicted ceiling above it cannot be blocked at
        all on purpose code 77.
        """
        d = RAILS["sbmd"].explain("block_validity_90_days")
        assert d.allowed is True
        assert "Rs.10,000 of block limit and up to 90 days" in d.quote

    def test_every_sbmd_capability_is_primary(self):
        """SBMD is fully evidenced as of 21 Aug 2026 — no vendor docs left.

        If this fails, someone added a capability without reading a circular.
        That is exactly the failure mode this module exists to prevent.
        """
        for cap in RAILS["sbmd"].capabilities.values():
            assert cap.source_tier is SourceTier.PRIMARY, cap.name
            assert cap.quote.strip(), cap.name
