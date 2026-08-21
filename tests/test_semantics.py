"""Rail capability encoding: every assertion cited or explicitly UNVERIFIED."""
import pytest
from amanat.rails.semantics import (
    Capability, SourceTier, RailProfile, RAILS, CapabilityError, Limit,
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

    def test_every_sbmd_capability_is_primary_or_declared_partial(self):
        """No SBMD claim may rest on a vendor doc without saying so.

        Until 21 Aug 2026 this test demanded PRIMARY for every SBMD capability.
        Round 5 had to add three that a circular cannot decide — whether a PSP
        exposes a modify endpoint, and what it costs — so the rule is relaxed by
        exactly one notch and no further:

        - PRIMARY is always fine.
        - SECONDARY is fine only if the notes carry the literal string
          "[PARTIAL]", which is what the rail-semantics skill requires of a PSP
          doc describing a *rail's* rule rather than that PSP's own behaviour.
        - UNVERIFIED is fine and carries no quote, but must explain itself.
        - MARKETING is never fine.

        If this fails, someone claimed a rail rule from a vendor page without
        labelling it. That is the exact failure mode this module exists for.
        """
        for cap in RAILS["sbmd"].capabilities.values():
            if cap.source_tier is SourceTier.PRIMARY:
                assert cap.quote.strip(), cap.name
            elif cap.source_tier is SourceTier.SECONDARY:
                assert cap.quote.strip(), cap.name
                assert "[PARTIAL]" in cap.notes, cap.name
            elif cap.source_tier is SourceTier.UNVERIFIED:
                assert cap.notes.strip(), cap.name
            else:
                pytest.fail(f"sbmd.{cap.name} rests on {cap.source_tier.name}")


class TestBlockModification:
    """Round 5. Does releasing the difference have to destroy the block?

    Round 4 named one assumption as the most likely to be false in the whole
    project: "releasing the difference returns the money without destroying the
    block". These tests pin down the half of it that was settled and the half
    that was not, so neither can quietly drift back to the convenient answer.
    """

    def test_only_one_block_per_customer_per_merchant(self):
        """OC-228 issuer obligation 4, read off the scan at 200 dpi.

        This is what makes a revoke expensive rather than free: a customer holds
        at most one live block with a given merchant, so revoking to hand back
        the change clears the only slot and the next purchase needs a fresh
        block and a fresh UPI PIN.
        """
        rail = RAILS["sbmd"]
        assert rail.permits("single_active_block_per_merchant") is True
        d = rail.explain("single_active_block_per_merchant")
        assert "only one block at a time for the particular merchant" in d.quote
        assert d.citation.startswith("NPCI/UPI/OC-228")

    def test_modify_exists_and_is_secondary_not_primary(self):
        """A modify op exists and preserves the block — but a PSP says so, not NPCI.

        OC-228 names "modification" as a notifiable lifecycle event and gives
        merchants "update and revoke" as two things, so a modify exists at
        scheme level. What the circular never says is what a modify may change.
        Only Setu, of six merchant-side PSPs surveyed, publishes an endpoint —
        PUT /v1/merchants/mandates/{id}/modify, separate from .../revoke — and
        that is SECONDARY evidence about the rail, marked [PARTIAL].
        """
        rail = RAILS["sbmd"]
        cap = rail.capabilities["block_amount_modifiable_without_revoke"]
        assert cap.source_tier is SourceTier.SECONDARY
        assert "[PARTIAL]" in cap.notes
        assert "only two updates possible" in cap.quote
        assert "endDate cannot be updated for a single block multi debit" in cap.quote

    def test_reducing_a_block_is_unverified_and_therefore_refused(self):
        """The direction of a modify is evidenced by nobody. So we refuse it.

        Neither circular, and not one of six PSP doc sets read on 21 Aug 2026,
        states whether a modify may LOWER a block's amount. The only published
        constraints are non-directional: Setu bounds amountLimit by
        minimum 100 / maximum 20000000 paise; Razorpay's payer-side modify
        documents one amount failure, "Amount must be greater than 0".

        Silence is not permission. An agent that assumes it can shrink a block
        and cannot has stranded the user's money for up to 90 days with no
        fallback that keeps the mandate alive, so the safe default is to refuse.
        """
        rail = RAILS["sbmd"]
        cap = rail.capabilities["block_amount_reducible_without_revoke"]
        assert cap.source_tier is SourceTier.UNVERIFIED
        assert cap.supported is True, "we believe it; we have not evidenced it"
        assert rail.permits("block_amount_reducible_without_revoke") is False
        assert "not usable as fact" in \
            rail.explain("block_amount_reducible_without_revoke").reason

    def test_the_nondestructive_op_is_the_one_that_needs_the_customer(self):
        """The asymmetry that decides what leg three costs.

        Revoke is a merchant server-to-server call — Razorpay's
        PUT /customers/:cid/tokens/:tid/cancel, Cashfree's manage action CANCEL
        — with no customer present. Modify is not: Setu requires the customer to
        enter their mPIN, and Razorpay's payer-side modify carries
        upi_credentials from the UPI Common Library.

        So an unattended agent's only money-returning lever is the destructive
        one. Modifying instead defers an AFA rather than avoiding one, and the
        cost model must say so.
        """
        rail = RAILS["sbmd"]
        assert rail.permits("block_modify_requires_customer_afa") is True
        assert "mPIN" in rail.explain("block_modify_requires_customer_afa").quote

    def test_releasing_the_remainder_means_tearing_the_block_down(self):
        """Every PSP has a release call, and on all of them it is the revoke.

        Razorpay: "all remaining funds under the token are unblocked" — never
        some of them. Cashfree heads its step "Release unused blocked funds" and
        then supports only action CANCEL for SBMD. Juspay, on a page titled
        "Release the Blocked Funds", says release happens only by revoking.
        """
        rail = RAILS["sbmd"]
        assert rail.permits("remainder_release_without_teardown") is False
        d = rail.explain("remainder_release_without_teardown")
        assert d.allowed is False
        assert "all remaining funds under the token are unblocked" in d.quote
        assert "10" in d.quote and "minutes before the token expires" in d.quote

    def test_one_shot_otm_does_return_the_change_by_itself(self):
        """The straight trade, and the reason SBMD is not automatically right.

        On the single-debit sibling the rail hands the remainder back with no
        revoke, no modify and no AFA. SBMD keeps the mandate and strands the
        change; OTM returns the change and spends the mandate. Neither gives
        both, and a proposal that claims both is a proposal to check.
        """
        otm = RAILS["upi_otm"]
        assert otm.permits("partial_debit") is True
        q = otm.explain("partial_debit").quote
        assert "remaining funds are unblocked in the customer bank A/C" in q
        assert RAILS["sbmd"].permits("remainder_auto_released") is False


class TestNumericLimits:
    """Booleans were not enough.

    OC-228's Rs 10,000 block ceiling lived only inside a capability's quote
    text, where no code could read it, so the policy engine would approve a
    Rs 50,000 reserve on a rail that caps blocks at Rs 10,000. These pin the
    numbers as enforceable rather than decorative.
    """

    def test_sbmd_declares_the_block_ceiling_as_a_number(self):
        lim = RAILS["sbmd"].limit("max_block_amount")
        assert lim.value == 10_000_00
        assert lim.unit == "paise"
        assert lim.source_tier is SourceTier.PRIMARY
        assert "Rs.10,000 of block limit" in lim.quote

    def test_a_breach_is_reported_with_the_circular_that_decides_it(self):
        d = RAILS["sbmd"].exceeds("max_block_amount", 50_000_00)
        assert d is not None and d.allowed is False
        assert "OC-228" in d.citation
        assert "Rs.10,000" in d.quote

    def test_a_value_inside_the_limit_returns_no_refusal(self):
        assert RAILS["sbmd"].exceeds("max_block_amount", 6_000_00) is None

    def test_an_undeclared_limit_is_not_a_limit_of_zero(self):
        """Absence of a stated bound must not silently refuse everything."""
        assert RAILS["sbmd"].exceeds("max_hovercraft_eels", 10**9) is None

    def test_ninety_days_and_one_block_are_declared_numerically_too(self):
        rail = RAILS["sbmd"]
        assert rail.limit("max_block_validity_days").value == 90
        assert rail.limit("max_active_blocks_per_merchant").value == 1

    def test_a_cited_limit_requires_a_verbatim_quote(self):
        with pytest.raises(CapabilityError, match="quote"):
            Limit(name="x", value=1, unit="paise",
                  source_tier=SourceTier.PRIMARY, citation="c", quote="")

    def test_unverified_limits_are_still_enforced(self):
        """Opposite of capabilities, and deliberately so.

        An unverified capability is not permitted; an unverified limit is still
        enforced. Both resolve the same way — thin evidence means refuse more.
        """
        rail = RailProfile(
            rail_id="_limit_fixture", display_name="Fixture",
            limits=[Limit(name="max_block_amount", value=500,
                          unit="paise", source_tier=SourceTier.UNVERIFIED)],
        )
        d = rail.exceeds("max_block_amount", 900)
        assert d is not None
        assert "unverified evidence" in d.reason
