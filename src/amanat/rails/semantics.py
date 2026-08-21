"""Machine-readable encoding of what each payment rail actually permits.

The governing rule of this module, and the reason it exists:

    Every capability assertion carries a citation to a primary source,
    or it is marked UNVERIFIED. There is no third state.

Two expert reviewers of this project's design independently reached a wrong
conclusion by reasoning from a *summary* of the NPCI circular rather than the
circular. A third error came from lifting a rail-comparison table out of vendor
marketing copy and treating it as fact. `SourceTier` exists so that class of
error is representable in the type system instead of living in someone's head.

Safety property: an UNVERIFIED capability is never permitted. Absence of
evidence is not permission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CapabilityError(ValueError):
    """A capability was declared without the evidence its tier requires."""


class SourceTier(Enum):
    """Where a capability assertion comes from, in descending authority.

    PRIMARY and SECONDARY are usable as fact. MARKETING and UNVERIFIED are not,
    and `RailProfile.permits` will refuse anything resting on them.
    """

    PRIMARY = "primary"          # NPCI circular, RBI directive, network operating regs
    SECONDARY = "secondary"      # PSP integration docs — fact for that PSP's own behaviour
    MARKETING = "marketing"      # blog posts, product pages, comparison tables. Never fact.
    UNVERIFIED = "unverified"    # believed, not confirmed. Never fact.

    @property
    def is_fact(self) -> bool:
        return self in (SourceTier.PRIMARY, SourceTier.SECONDARY)


@dataclass
class Capability:
    """One thing a rail does or does not permit, with the evidence for it.

    `quote` must contain text actually read from the source. If you cannot fill
    it, the tier is UNVERIFIED — that is the honest encoding, and it is always
    available.
    """

    name: str
    supported: bool
    source_tier: SourceTier
    citation: str = ""
    url: str = ""
    quote: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.source_tier is not SourceTier.UNVERIFIED and not self.quote.strip():
            raise CapabilityError(
                f"capability {self.name!r} claims tier {self.source_tier.value!r} "
                f"but carries no verbatim quote; downgrade it to UNVERIFIED"
            )

    @property
    def is_fact(self) -> bool:
        return self.source_tier.is_fact


@dataclass
class Decision:
    """Why a capability was allowed or refused — carries its own evidence."""

    capability: str
    allowed: bool
    reason: str
    citation: str = ""
    url: str = ""
    quote: str = ""

    def __str__(self) -> str:
        verdict = "ALLOWED" if self.allowed else "REFUSED"
        line = f"{verdict}: {self.capability} — {self.reason}"
        return f"{line}\n  {self.citation}: “{self.quote}”" if self.quote else line


@dataclass
class RailProfile:
    """One payment rail and everything we can evidence about it."""

    rail_id: str
    display_name: str
    capabilities: dict[str, Capability] = field(default_factory=dict)

    def __init__(self, rail_id: str, display_name: str,
                 capabilities: list[Capability] | None = None) -> None:
        self.rail_id = rail_id
        self.display_name = display_name
        self.capabilities = {c.name: c for c in (capabilities or [])}

    def permits(self, capability: str) -> bool:
        """True only if the rail supports it AND we can evidence that it does."""
        cap = self.capabilities.get(capability)
        return bool(cap and cap.supported and cap.is_fact)

    def explain(self, capability: str) -> Decision:
        cap = self.capabilities.get(capability)
        if cap is None:
            return Decision(capability, False,
                            f"{self.display_name} declares no capability {capability!r}")
        if not cap.is_fact:
            return Decision(
                capability, False,
                f"rests on {cap.source_tier.value} evidence, which is not usable as fact",
                cap.citation, cap.url, cap.quote,
            )
        return Decision(
            capability, cap.supported,
            "permitted by the rail" if cap.supported else "forbidden by the rail",
            cap.citation, cap.url, cap.quote,
        )


# --------------------------------------------------------------------------
# The registry.
#
# Every SBMD quote below was read from the NPCI circular PDFs themselves on
# 21 Aug 2026 (local copies in docs/sources/), not from a summary and not from
# a PSP's description of the rail. The Razorpay entries are SECONDARY, which is
# the right tier for a PSP describing its own behaviour. Everything still
# marked UNVERIFIED is deliberately unverified rather than guessed.
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Verbatim clauses, transcribed 21 Aug 2026 from the two governing NPCI
# circulars. Both PDFs are image-only scans with no text layer, so every clause
# below was read off pages rendered at 220 dpi and re-checked line by line.
# Apostrophes are ASCII here; the scans set them typographically.
# Local copies: docs/sources/NPCI-UPI-OC-228-*.pdf and NPCI-UPI-OC-200-*.pdf
# ---------------------------------------------------------------------------

OC228 = "NPCI/UPI/OC-228/2025-26, 8 October 2025"
OC228_URL = (
    "https://www.npci.org.in/uploads/UPI_OC_No_228_FY_2025_26_Enhancement_in_"
    "UPI_Single_Block_Multiple_Debits_UPI_Reserve_Pay_a9095c181d.pdf"
)
OC200 = "NPCI/UPI/OC.No.200/2024-25, 31 July 2024"
OC200_URL = (
    "https://www.npci.org.in/uploads/UPI_OC_No_200_FY_24_25_Enablement_of_UPI_"
    "Mandate_feature_of_Single_Block_Multiple_Debits_f2f9bc9230.pdf"
)

# OC-228, opening paragraph.
_OC228_DRAWDOWN = (
    "UPI Reserve Pay feature facilitates the customer to block the funds in the "
    "account for multiple debits which can be initiated by the customer on the "
    "merchant's platform, till the reserved funds gets exhausted or the block "
    "has been revoked or expired."
)

# OC-228, "Acquiring entities - Obligations to be fulfilled by UPI Acquirer",
# item 5(d) followed immediately by 5(e). This is the clause that decides
# partial debit.
_OC228_UNUTILISED = (
    "The current block limits (unutilised) are always checked before initiating "
    "a debit. Display of original block value, remaining balance, expiry date "
    "and transaction history (including creation, debits, modification)."
)

# OC-228, "Issuer Banks shall ensure", item 3.
_OC228_UTILIZED_ONLY = (
    "Only utilized amount debited after actual purchase to be considered for "
    "bill generation as applicable for credit accounts on UPI."
)

# OC-200, issuer obligation 1.
_OC200_MULTIPLE_DEBITS = (
    "The issuer banks shall support the functionality of Single Block Multiple "
    "Debit services wherein the bank shall have the mechanism to create blocking "
    "of funds in the customer's account and shall allow multiple debits against "
    "the block. The fund shall be blocked in the account till the time mandate "
    "is expired, revoked or the mandate amount is exhausted."
)

# OC-228, Acquiring entities obligation 2. The project previously carried only
# the first eleven words of this sentence; the rest is what names the debit
# trigger.
_OC228_NO_GUARANTEE = (
    "The block created shall not be treated as the guarantee of payment, only "
    "the successful debit response received by the merchant (for the debit "
    "initiated by the customer action on merchant's platform) shall be "
    "considered for payment."
)

# OC-228, Acquiring entities obligation 4, in full. The version this project
# carried until 21 Aug 2026 elided "to the Acquiring bank for categories such as
# quick commerce, food delivery, etc." and truncated the last clause to
# "post successful delivery". Both are corrected here.
_OC228_DEBIT_BEFORE_DELIVERY = (
    "The purchase action by the customer must result into instant debit request "
    "without any delay, and the delivery of goods and service should only be "
    "after the confirmation of successful debit to the Acquiring bank for "
    "categories such as quick commerce, food delivery, etc. For use cases "
    "wherein amount is not fixed and is determined based on the services "
    "consumed (e.g.: cab aggregators, EVs, etc.), merchant may debit post "
    "successful delivery of services."
)

# OC-228, "UPI Apps - Obligations to be fulfilled by authorised UPI Apps and
# their PSP Banks", item 1.
_OC228_APP_REVOKE = "Easy access to revoke the block."

# OC-228, Acquiring entities obligation 5(c).
_OC228_MERCHANT_REVOKE = (
    "Easy access on merchant's platform to update and revoke along with the "
    "responsibility of issuer to validate every debit."
)

# OC-228, Acquiring entities obligation 5(b). Issuer obligation 5 states the
# same limits as "The block created to be maximum of Rs.10,000 of block limit
# and up to 90 days."
_OC228_BLOCK_CEILING = (
    "Allow user to enter the amount and select the end date as per their choice "
    "up to maximum of Rs.10,000 of block limit and up to 90 days."
)

# OC-200, clause (c). The purpose-code table at clause (a) reads:
# 76 = Securities brokers and dealers (Secondary Market);
# 77 = Online goods and service delivery; 78 and 79 = To be reserved for future use.
_OC200_PURPOSE_CODE_LIMITS = (
    "The per transaction limit for such mandate creations shall be Rs 5 Lakh "
    "for the purpose code 76, while for purpose code 77 existing UPI limits "
    "shall be applicable."
)

_RAZORPAY_FULL_CAPTURE = "Capture amount must be equal to the amount authorized."

_PAYU_OTM_CAPTURE = (
    "Once the merchant decides to capture the amount (usually after the goods or "
    "services are delivered)..."
)


SBMD = RailProfile(
    rail_id="sbmd",
    display_name="UPI Reserve Pay (NPCI Single Block Multiple Debit)",
    capabilities=[
        Capability(
            name="payment_guarantee", supported=False,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligation 2",
            url=OC228_URL,
            quote=_OC228_NO_GUARANTEE,
            notes=(
                "A block is not a guarantee. Do not model it as one. "
                "Read the parenthesis too: the debit is one 'initiated by the "
                "customer action on merchant's platform', which is the same "
                "trigger named in the opening paragraph and in acquiring "
                "obligation 4. There is no merchant-discretionary draw on this "
                "rail, so an agent debit is standing in for a customer act."
            ),
        ),
        Capability(
            name="post_delivery_debit_goods", supported=False,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligation 4",
            url=OC228_URL,
            quote=_OC228_DEBIT_BEFORE_DELIVERY,
            notes=(
                "For fixed-price goods, debit must precede delivery. This "
                "forecloses delivery-contingent settlement for goods.\n"
                "QUOTE CORRECTED 21 Aug 2026. The version carried until then "
                "elided 'to the Acquiring bank for categories such as quick "
                "commerce, food delivery, etc.'. That phrase matters: the rule "
                "is written with named example categories rather than as a flat "
                "all-goods prohibition. 'such as' is exemplifying, not "
                "exhaustive, so the stricter reading (it binds all fixed-price "
                "goods) is the one to build to - but say out loud that the "
                "circular states it by example."
            ),
        ),
        Capability(
            name="post_delivery_debit_variable_amount_services", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligation 4",
            url=OC228_URL,
            quote=_OC228_DEBIT_BEFORE_DELIVERY,
            notes=(
                "The carve-out is scoped by AMOUNT UNCERTAINTY, not delivery "
                "contingency. Cabs and EV charging qualify; a fixed-price kurta "
                "does not. Grocery is a trap: amount resolves at picking, "
                "before dispatch, so debit can and must precede delivery. "
                "Note also that the carve-out changes only WHEN the debit "
                "happens - it says nothing about the amount relative to the "
                "block, which is governed by 5(d) for every use case alike."
            ),
        ),
        # Amount-contingency is orthogonal to delivery timing: the circular
        # constrains WHEN debit happens, never that debit == block.
        Capability(
            name="partial_debit", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligations 5(d) and 5(e)",
            url=OC228_URL,
            quote=_OC228_UNUTILISED,
            notes=(
                "VERIFIED 21 Aug 2026 against the circular PDF. This is the "
                "load-bearing capability for amount-contingent settlement, and "
                "the reason this system stopped refusing its own core mechanism.\n"
                "Honest reading: neither OC-228 nor OC-200 contains an explicit "
                "sentence permitting a debit smaller than the block, and neither "
                "forbids one. It is decided by necessary implication from four "
                "independent clauses that are incoherent under a debit-equals-"
                "block rule. Two are in the quote above. The other two, "
                f"verbatim - OC-228 issuer obligation 3: \"{_OC228_UTILIZED_ONLY}\" "
                f"and OC-228 opening paragraph: \"{_OC228_DRAWDOWN}\"\n"
                "Framing correction: SBMD is not authorize-then-partial-capture. "
                "It is a pre-funded drawdown pool, so a debit smaller than the "
                "block is the ordinary case rather than an exception. The "
                "explicit amount rule sits in Annexure A, the 'Product Document "
                "on Mandate with Single Block and Multiple Debit' that OC-200 "
                "references but NPCI does not publish."
            ),
        ),
        Capability(
            name="multi_debit", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC200}, issuer obligation 1",
            url=OC200_URL,
            quote=_OC200_MULTIPLE_DEBITS,
            notes=(
                "'shall allow multiple debits against the block' is explicit. "
                "There is NO cap on the number of debits in either circular. "
                "The block is bounded by value, time and concurrency instead: "
                "Rs 10,000 and 90 days (OC-228), one block at a time per mobile "
                "number per merchant (OC-228 issuer obligation 4), P2M only "
                "(OC-200 clause g).\n"
                "The '3 retries in 24 hours' figure is not a debit budget. "
                "OC-228 acquiring obligation 3 grants it only where the debit "
                "TIMED OUT with the issuer/payer PSP, with 'no retries for any "
                "other declines'."
            ),
        ),
        Capability(
            name="funds_held_in_customer_account", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC200}, issuer obligation 1",
            url=OC200_URL,
            quote=_OC200_MULTIPLE_DEBITS,
            notes=(
                "'create blocking of funds in the customer's account' - the "
                "money never leaves the payer. OC-228 issuer obligation 1 adds "
                "'The reserve amount details is shown to the customer in the "
                "statement and other channels as applicable in due course.' "
                "Contrast Razorpay's 'authorized', which has already debited."
            ),
        ),
        Capability(
            name="remainder_auto_released", supported=False,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC200}, issuer obligation 1",
            url=OC200_URL,
            quote=_OC200_MULTIPLE_DEBITS,
            notes=(
                "THE THIRD LEG OF THIS PROJECT'S MECHANISM IS NOT AUTOMATIC, "
                "and this is the most consequential thing found on 21 Aug 2026.\n"
                "'The fund shall be blocked in the account till the time mandate "
                "is expired, revoked or the mandate amount is exhausted.' The "
                "rail KEEPS the unused remainder blocked. Neither circular "
                "imposes any duty to release it after a partial debit, and "
                "neither states any timeline for doing so. Release happens only "
                "because somebody calls revoke or update - see "
                "`merchant_revocable` and `customer_revocable`.\n"
                "Consequence for the ceiling model: debit Rs 470 against a "
                "Rs 620 block and walk away, and Rs 150 stays stranded until the "
                "customer-chosen end date, up to 90 days. Stranding duration is "
                "'until someone revokes, else end-of-block', not 'until "
                "settlement'. Price it that way."
            ),
        ),
        Capability(
            name="merchant_revocable", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligation 5(c)",
            url=OC228_URL,
            quote=_OC228_MERCHANT_REVOKE,
            notes=(
                "This is how the unused difference actually gets released: an "
                "explicit update or revoke from the merchant platform. Both are "
                "first-class lifecycle events - OC-228 issuer obligation 2 "
                "requires notifications for 'block creation, modification, "
                "debit, revoke and expiry' - so a block can be revised downward "
                "as well as torn down."
            ),
        ),
        Capability(
            name="customer_revocable", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, UPI Apps obligation 1",
            url=OC228_URL,
            quote=_OC228_APP_REVOKE,
            notes=(
                "Unconditional for Reserve Pay: OC-228 makes it a flat "
                "obligation on authorised UPI Apps, and OC-228 identifies its "
                "transactions by purpose code 77. For the merchant this is a "
                "liability rather than a feature - revocation is one tap and "
                "neither circular grants the merchant a lock period or any "
                "protection for a debit in flight.\n"
                "CORRECTION: this project previously recorded 'code 76 is "
                "merchant-revoke-only; code 77 is customer-revocable'. That "
                "distinction appears in NEITHER circular and should be treated "
                "as unsourced. What OC-200 clause (e) actually says, for all "
                "SBMD, is 'Customer shall also be provided with an option of "
                "revoking the mandate based on the merchant use case' - i.e. "
                "conditional on the use case. OC-228 then removes the "
                "conditionality for Reserve Pay."
            ),
        ),
        Capability(
            name="purpose_code_77_for_online_goods", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC200}, clause (c) and purpose-code table at clause (a)",
            url=OC200_URL,
            quote=_OC200_PURPOSE_CODE_LIMITS,
            notes=(
                "OC-200's table: 76 = 'Securities brokers and dealers (Secondary "
                "Market)', 77 = 'Online goods and service delivery', 78 and 79 "
                "'To be reserved for future use'. E-commerce is 77, and OC-228 "
                "names only 77.\n"
                "Answer to 'is there a 76-vs-77 rule for partial debit, debit "
                "count, or the remainder?' - NO. The only differential rule in "
                "the primary text is a per-transaction LIMIT: Rs 5 lakh for 76, "
                "existing UPI limits for 77. Everything about drawdown, "
                "remaining balance and revocation is stated once, for SBMD as a "
                "whole.\n"
                "One asymmetry worth carrying: OC-228's Rs 10,000 / 90-day block "
                "ceiling is stated in a circular scoped to purpose code 77, so "
                "it is not evidenced as binding on a 76 block."
            ),
        ),
        Capability(
            name="block_validity_90_days", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Acquiring entities obligation 5(b)",
            url=OC228_URL,
            quote=_OC228_BLOCK_CEILING,
            notes=(
                "90 days is a MAXIMUM with a customer-chosen end date, not a "
                "default. OC-228 issuer obligation 5 states the same limits: "
                "'The block created to be maximum of Rs.10,000 of block limit "
                "and up to 90 days.'\n"
                "Never quote the 90 days without the Rs 10,000 in the same "
                "sentence. For a ceiling-selection thesis the Rs 10,000 is the "
                "harder constraint: any predicted ceiling above it cannot be "
                "blocked at all on purpose code 77.\n"
                "The '90d vs cards 7d vs mandate 60d' comparison still "
                "originates in PayU MARKETING copy and mislabels OTM as "
                "'standard mandate'. Real card figures: Visa India 2-4 days, "
                "Mastercard 4 days final / 30 days preauth. Do not cite it."
            ),
        ),
    ],
)


RAZORPAY_AUTH_CAPTURE = RailProfile(
    rail_id="razorpay_auth_capture",
    display_name="Razorpay manual capture (payment_capture=0)",
    capabilities=[
        Capability(
            name="partial_debit", supported=False,
            source_tier=SourceTier.SECONDARY,
            citation="Razorpay capture docs",
            url="https://razorpay.com/docs/api/payments/capture/",
            quote=_RAZORPAY_FULL_CAPTURE,
            notes="Forecloses amount-contingent settlement on this rail. Demo the negative.",
        ),
        Capability(
            name="funds_held_in_customer_account", supported=False,
            source_tier=SourceTier.SECONDARY,
            citation="Razorpay payment lifecycle docs",
            url="https://razorpay.com/docs/payments/payments/",
            quote=_RAZORPAY_FULL_CAPTURE,
            notes=(
                "THE TRAP: Razorpay's 'authorized' state has ALREADY DEBITED the "
                "customer. It is not a hold. Volunteer this in the pitch."
            ),
        ),
        Capability(
            name="manual_capture", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation="Razorpay orders API (payment_capture flag)",
            url="https://razorpay.com/docs/api/orders/",
            quote=_RAZORPAY_FULL_CAPTURE,
            notes="Authorize-now / capture-later exists, but capture must be for the full amount.",
        ),
    ],
)


UPI_OTM = RailProfile(
    rail_id="upi_otm",
    display_name="UPI One Time Mandate",
    capabilities=[
        Capability(
            name="post_delivery_debit_goods", supported=True,
            source_tier=SourceTier.UNVERIFIED,
            notes=(
                f"CONFLICT, UNRESOLVED. PayU documents: “{_PAYU_OTM_CAPTURE}” which "
                "contradicts the SBMD debit-before-delivery rule. That is a PSP doc, "
                "not an NPCI circular. Build to the STRICTER rule and expose this as "
                "a config flag so the system is correct under either reading."
            ),
        ),
        Capability(
            name="partial_debit", supported=True, source_tier=SourceTier.UNVERIFIED,
            notes="PSP docs describe native partial debit with bank-side release. Confirm.",
        ),
    ],
)


CASHFREE_PREAUTH = RailProfile(
    rail_id="cashfree_preauth",
    display_name="Cashfree pre-authorization",
    capabilities=[
        Capability(
            name="partial_debit", supported=True, source_tier=SourceTier.UNVERIFIED,
            notes="Best-fit primitive found: UPI + partial capture + ~1-year window. Confirm in UAT.",
        ),
        Capability(
            name="self_serve_enablement", supported=False,
            source_tier=SourceTier.UNVERIFIED,
            notes="Requires a support request. Fire it in hour 1; assume it does not land.",
        ),
    ],
)


RAILS: dict[str, RailProfile] = {
    r.rail_id: r for r in (SBMD, RAZORPAY_AUTH_CAPTURE, UPI_OTM, CASHFREE_PREAUTH)
}


def unverified_report() -> list[tuple[str, str, str]]:
    """Every capability still resting on unverified evidence.

    This is the project's honest-weakness list. Print it in the demo rather than
    waiting to be asked what you did not confirm.
    """
    return [
        (rail.rail_id, cap.name, cap.notes)
        for rail in RAILS.values()
        for cap in rail.capabilities.values()
        if not cap.is_fact
    ]
