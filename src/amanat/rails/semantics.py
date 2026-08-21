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

One tier is unusual and worth explaining. OBSERVED means the assertion was
measured against the live API rather than read anywhere, and its `quote` is the
response the rail actually returned. A doc says what a rail is supposed to do;
an observation says what it did. Where the two can be compared they should be,
and `amanat.rails.probe` is what does the comparing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CapabilityError(ValueError):
    """A capability was declared without the evidence its tier requires."""


class SourceTier(Enum):
    """Where a capability assertion comes from, in descending authority.

    PRIMARY, OBSERVED and SECONDARY are usable as fact. MARKETING and
    UNVERIFIED are not, and `RailProfile.permits` refuses anything resting on
    them.

    OBSERVED sits second because a measurement beats a description of a
    measurement, but below PRIMARY because a rail can behave one way today and
    another after a deploy, whereas a circular changes only by amendment.
    """

    PRIMARY = "primary"          # NPCI circular, RBI directive, network operating regs
    OBSERVED = "observed"        # measured against the live API — the quote is its response
    SECONDARY = "secondary"      # PSP integration docs — fact for that PSP's own behaviour
    MARKETING = "marketing"      # blog posts, product pages, comparison tables. Never fact.
    UNVERIFIED = "unverified"    # believed, not confirmed. Never fact.

    @property
    def is_fact(self) -> bool:
        return self in (SourceTier.PRIMARY, SourceTier.OBSERVED, SourceTier.SECONDARY)


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
class Limit:
    """A cited numeric bound. Same evidence discipline as `Capability`.

    Booleans alone were not enough. OC-228's Rs 10,000 block ceiling lived only
    inside a capability's quote text, where no code could read it — so the
    policy engine happily approved a Rs 50,000 reserve on a rail that caps
    blocks at Rs 10,000. Numbers the rail enforces must be numbers this system
    can enforce.

    Safety semantics differ from `Capability`, deliberately. An unverified
    capability is NOT permitted; an unverified limit IS still enforced. Both
    resolve the same way — when the evidence is thin, refuse more, never less.
    """

    name: str
    value: int
    unit: str                      # "paise", "days", "count"
    source_tier: SourceTier
    citation: str = ""
    url: str = ""
    quote: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.source_tier is not SourceTier.UNVERIFIED and not self.quote.strip():
            raise CapabilityError(
                f"limit {self.name!r} claims tier {self.source_tier.value!r} "
                f"but carries no verbatim quote; downgrade it to UNVERIFIED"
            )

    @property
    def is_fact(self) -> bool:
        return self.source_tier.is_fact

    def render(self) -> str:
        if self.unit == "paise":
            return f"₹{self.value / 100:,.0f}"
        return f"{self.value} {self.unit}"


@dataclass
class RailProfile:
    """One payment rail and everything we can evidence about it."""

    rail_id: str
    display_name: str
    capabilities: dict[str, Capability] = field(default_factory=dict)
    limits: dict[str, Limit] = field(default_factory=dict)

    def __init__(self, rail_id: str, display_name: str,
                 capabilities: list[Capability] | None = None,
                 limits: list[Limit] | None = None) -> None:
        self.rail_id = rail_id
        self.display_name = display_name
        self.capabilities = {c.name: c for c in (capabilities or [])}
        self.limits = {l.name: l for l in (limits or [])}

    def limit(self, name: str) -> Limit | None:
        """The declared bound, or None if this rail declares none."""
        return self.limits.get(name)

    def exceeds(self, name: str, value: int) -> Decision | None:
        """A refusal if `value` breaches the declared limit, else None.

        Returns None when the rail declares no such limit — absence of a stated
        bound is not a bound of zero.
        """
        lim = self.limits.get(name)
        if lim is None or value <= lim.value:
            return None
        tier = "" if lim.is_fact else f" [{lim.source_tier.value} evidence]"
        return Decision(
            name, False,
            f"{value} exceeds {self.display_name} {name} of {lim.render()}{tier}",
            lim.citation, lim.url, lim.quote,
        )

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


# ---------------------------------------------------------------------------
# ROUND 5, 21 Aug 2026 - the PSP API references, read to settle one question:
# is there an operation that REDUCES a standing block without revoking it?
#
# These are SECONDARY. A PSP doc is fact for that PSP's own behaviour, and only
# [PARTIAL] evidence about the rail. Six merchant-side PSPs were surveyed by
# enumerating their published API surface, not their landing pages:
#
#   Razorpay   llms.txt -> the three UPI Reserve Pay pages. Lifecycle APIs are
#              exactly two: PUT /customers/:cid/tokens/:tid/cancel and
#              DELETE /customers/:cid/tokens/:tid. No modify. Webhooks are
#              token.confirmed and token.cancellation_initiated. No token.updated.
#   Cashfree   llms.txt -> upi-reserve-pay. POST /pg/subscriptions/:id/manage,
#              action enum CANCEL | PAUSE | ACTIVATE | CHANGE_PLAN, of which
#              SBMD supports CANCEL only.
#   PayU       readme.io ssr-props -> docs/upi-reserve-pay. No modify; PayU's
#              own stated workaround is a scheduled revoke.
#   Juspay     llms.txt -> one-time-mandate. Release IS revoke, explicitly.
#   BoxPay     wp-sitemap -> upi-reservepay. One paragraph, no API surface.
#   Setu       OpenAPI at /api-specs/payments/umap.json - the only merchant-side
#              PSP that exposes PUT /v1/merchants/mandates/{id}/modify as an
#              endpoint separate from PUT /v1/merchants/mandates/{id}/revoke.
#
# So the gap is IMPLEMENTATION, not regulation. OC-228 names modification as a
# lifecycle event four times; five of six merchant-side PSPs do not expose it.
# ---------------------------------------------------------------------------

SETU_UPDATE = "Setu UPI (UMAP), Mandate operations - Update"
SETU_UPDATE_URL = "https://docs.setu.co/payments/umap/mandates/generic/update"
SETU_RESERVE_PLUS = "Setu UPI (UMAP), ReservePlus (Single block multi-debit mandate)"
SETU_RESERVE_PLUS_URL = "https://docs.setu.co/payments/umap/mandates/reserve-plus"
RZP_MANAGE = "Razorpay UPI Reserve Pay (SBMD), Manage Mandates and Tokens"
RZP_MANAGE_URL = (
    "https://razorpay.com/docs/payments/payment-gateway/s2s-integration/"
    "recurring-payments/upi-reserve-pay/manage/"
)
CASHFREE_RESERVE_PAY = "Cashfree UPI Reserve Pay, Implementation Guide step 6 (Manage mandate)"
CASHFREE_RESERVE_PAY_URL = (
    "https://www.cashfree.com/docs/payments/upi-reserve-pay/upi-reserve-pay"
)
JUSPAY_OTM_RELEASE = "Juspay One Time Mandate, Release the Blocked Funds"
JUSPAY_OTM_RELEASE_URL = (
    "https://juspay.io/in/docs/one-time-mandate/docs/one-time-mandate/"
    "released-the-blocked-funds"
)
RZP_TPAP_UPDATE = "Razorpay TPAP Pro API, Update or Revoke a Mandate"
RZP_TPAP_UPDATE_URL = (
    "https://razorpay.com/docs/api/payments/tpap-pro/mandate-flow/"
    "update-revoke-mandate/"
)
SETU_RESERVE = "Setu UPI (UMAP), Reserve (One Time Mandates)"
SETU_RESERVE_URL = "https://docs.setu.co/payments/umap/mandates/reserve"

# OC-228, "Issuer Banks shall ensure", item 4. Re-read off the PDF page
# rendered at 200 dpi on 21 Aug 2026, character by character.
_OC228_ONE_BLOCK = (
    "One mobile number (assumed as one customer) is allowed to create only one "
    "block at a time for the particular merchant."
)

# Setu, Mandate operations > Update. Rendered on the page as a lead sentence
# followed by a two-item bulleted list; the bullets are flattened here with
# semicolons and are otherwise verbatim. The second sentence is a standalone
# callout on the same page.
_SETU_TWO_UPDATES = (
    "There are only two updates possible on a UPI mandate: Changing the "
    "mandate end date; Changing the mandate amount. "
    "endDate cannot be updated for a single block multi debit mandate"
)

# Setu, Mandate operations > Update, intent-based flow. The collect-based flow
# on the same page reads "once the customer clicks on the update mandate
# notification and enters the mPIN on their UPI app".
_SETU_UPDATE_MPIN = (
    "Post this, once the customer clicks on the intent link / scans the qr code "
    "and enters the mPIN, the merchant will receive webhooks for following "
    "events: mandate_operation.update.initiated"
)

# Razorpay, Manage Mandates and Tokens > Cancel Tokens. The "two ways" are the
# Cancel Token API and expiry - there is no third, and no partial release.
_RZP_RELEASE_IS_CANCEL = (
    "The blocked amount under a UPI Reserve Pay token can be released in two "
    "ways: Use the Cancel Token API below to release the blocked funds. When "
    "this API is called, all remaining funds under the token are unblocked and "
    "credited to the customer's bank account instantly. If you do not cancel "
    "the token and the token balance is not fully utilised before expiry, "
    "Razorpay automatically triggers a reversal of the remaining funds 10 "
    "minutes before the token expires."
)

# Razorpay, Manage Mandates and Tokens > Track Mandate Funds.
_RZP_REMAINING_STAYS_BLOCKED = (
    "To find the remaining amount available for future debits, subtract the "
    "amount_debited from the amount_blocked. This allows you to manage customer "
    "expectations and ensure you do not initiate a debit that exceeds the "
    "remaining authorised limit. Ensure customers are informed that their funds "
    "remain blocked until you explicitly release them or the token expires."
)

# Cashfree, UPI Reserve Pay implementation guide, step 6 "Manage mandate".
# The section is headed "Release unused blocked funds back to the customer using
# the manage subscription API" and then carries this warning.
_CASHFREE_CANCEL_ONLY = (
    "Only the CANCEL action is supported for SBMD subscriptions. Other "
    "management actions like PAUSE are not available."
)

# Juspay, One Time Mandate, on the page titled "Release the Blocked Funds".
_JUSPAY_RELEASE_IS_REVOKE = (
    "Once the funds are blocked during the mandate registration, the funds are "
    "released only after invoking Revoke Mandate API by the merchant. Upon "
    "revoking the mandate, the status changes from ACTIVE to REVOKED."
)

# Razorpay TPAP Pro, PATCH /v1/upi/tpap/mandates/:umn - the PAYER-PSP side of
# the same NPCI primitive, where action is "update | revoke". This is the
# clearest published statement that a mandate UPDATE carries a new amount.
_RZP_TPAP_UPDATE_AMOUNT = (
    "The amount of the mandate. This parameter is required when the mandated "
    "amount needs to be updated, and the request_type is set to update. Either "
    "the validity_end or the amount must be provided."
)

# Setu, Reserve (One Time Mandates) - the SINGLE-debit sibling of SBMD. Quoted
# because the contrast is the finding: on a one-shot block the rail hands the
# remainder back by itself; on a multi-debit block it does not.
_SETU_OTM_AUTO_UNBLOCK = (
    "Reserve allows a merchant to block funds upto Rs.1 lakh for all MCCs "
    "except 6211 and debit either the full amount or a partial amount from the "
    "customer. If a partial debit is done, remaining funds are unblocked in the "
    "customer bank A/C without any additional need for refund/reversal."
)

# Setu, ReservePlus > Execute mandate, request parameter table for `amount`.
_SETU_SBMD_CUMULATIVE = (
    "must be such that the cumulative amount debited for the given mandate post "
    "current debit is within the amount that is blocked in customer's account"
)


SBMD_LIMITS = [
    Limit(
        name="max_block_amount", value=10_000_00, unit="paise",
        source_tier=SourceTier.PRIMARY, citation=OC228, url=OC228_URL,
        quote=_OC228_BLOCK_CEILING,
        notes=(
            "Scoped to purpose code 77 (online goods and service delivery). "
            "OC-200(c) gives Rs 5 lakh per transaction for code 76 (securities), "
            "so this ceiling is not universal across SBMD. For a "
            "ceiling-selection thesis this is the BINDING constraint: a "
            "predicted ceiling above it cannot be blocked at all, whatever the "
            "model says."
        ),
    ),
    Limit(
        name="max_block_validity_days", value=90, unit="days",
        source_tier=SourceTier.PRIMARY, citation=OC228, url=OC228_URL,
        quote=_OC228_BLOCK_CEILING,
        notes=(
            "Same sentence as the Rs 10,000 ceiling. Never cite the 90 days "
            "without the amount cap — quoting the window alone reads as a much "
            "more permissive rail than the one that exists."
        ),
    ),
    Limit(
        name="max_active_blocks_per_merchant", value=1, unit="count",
        source_tier=SourceTier.PRIMARY, citation=OC228, url=OC228_URL,
        quote=("One mobile number (assumed as one customer) is allowed to create "
               "only one block at a time for the particular merchant."),
        notes="Scoped per merchant. Blocks with different merchants may coexist.",
    ),
]

SBMD = RailProfile(
    rail_id="sbmd",
    display_name="UPI Reserve Pay (NPCI Single Block Multiple Debit)",
    limits=SBMD_LIMITS,
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
                "settlement'. Price it that way.\n"
                "CORROBORATED 21 Aug 2026 by three independent PSP docs, which "
                "matters because the primary finding was a negative one and a "
                "negative read of a scan invites doubt. Razorpay: "
                "'Ensure customers are informed that their funds remain blocked "
                "until you explicitly release them or the token expires', and "
                "the way to know what is left is to 'subtract the "
                "amount_debited from the amount_blocked'. Setu's ReservePlus "
                "execute API constrains the debit amount so that 'the "
                "cumulative amount debited for the given mandate post current "
                "debit is within the amount that is blocked'. Cashfree: 'The "
                "remaining reserved balance reduces automatically after each "
                "debit.' All three describe a pool that draws down and stays "
                "blocked, not one that returns change.\n"
                "THE ONE CONFLICTING SOURCE, and it should be disclosed rather "
                "than dropped. PayU's Reserve Pay page asserts the opposite in "
                "its examples - 'After finalizing the recharge (e.g., Rs.499), "
                "the balance Rs.51 is released' - but the same page's feature "
                "list gives the mechanism away: 'Currently, the releasing of "
                "funds is done by remiters but PayU has built a functionality "
                "(internal) to revoke the transactions based on end date to "
                "minimise the funds on hold.' A scheduled revoke is not an "
                "automatic release. Build to the stricter reading.\n"
                "THE CONTRAST WORTH BUILDING ON. This is a MULTI-debit finding. "
                "On the SINGLE-debit sibling - UPI OTM, Setu's 'Reserve' - the "
                "rail does hand the remainder back by itself; see "
                "`upi_otm.partial_debit`. If the agent commits to exactly one "
                "debit per block, leg three is free and there is nothing to "
                "revoke. The stranding problem is the price of keeping the pool "
                "open for a second debit, and that is a design choice this "
                "project makes, not a constraint the rail imposes."
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
                "debit, revoke and expiry'.\n"
                "CORRECTION, 21 Aug 2026. This note used to end '...so a block "
                "can be revised downward as well as torn down.' That was an "
                "inference from the word 'update', not a finding, and it is the "
                "kind of leap this module exists to prevent. A modify operation "
                "does exist and does preserve the block - see "
                "`block_amount_modifiable_without_revoke` - but nothing in "
                "either circular or in any PSP doc says it may revise an amount "
                "DOWNWARD, and only one of six merchant-side PSPs exposes it at "
                "all. See `block_amount_reducible_without_revoke`, which is "
                "UNVERIFIED for exactly that reason."
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
        # ------------------------------------------------------------------
        # ROUND 5, 21 Aug 2026. The four capabilities below exist because
        # round 4 named one assumption as the most likely to be false:
        #
        #   "Releasing the difference returns the money without destroying
        #    the block."
        #
        # It is half false, and the half that is false is the expensive half.
        # ------------------------------------------------------------------
        Capability(
            name="single_active_block_per_merchant", supported=True,
            source_tier=SourceTier.PRIMARY,
            citation=f"{OC228}, Issuer Banks obligation 4",
            url=OC228_URL,
            quote=_OC228_ONE_BLOCK,
            notes=(
                "The concurrency bound, and the reason a revoke is expensive "
                "rather than free. Verified 21 Aug 2026 by re-rendering page 1 "
                "of the OC-228 scan at 200 dpi and reading item 4 directly.\n"
                "Consequence, and it is the whole cost of leg three: a customer "
                "has at most ONE live block with a given merchant. Revoking to "
                "hand back Rs 150 does not just end that block, it clears the "
                "only slot - the next purchase needs a fresh block, which needs "
                "a fresh UPI PIN from the customer. So the agent's choice on a "
                "standing-wallet merchant is: strand the user's money until the "
                "end date, or spend the user's standing authorisation to return "
                "it. There is no third option that any merchant-side PSP "
                "exposes today except Setu. Price both branches; do not model "
                "release as free.\n"
                "Scope note: the clause binds one MOBILE NUMBER to one block "
                "PER MERCHANT. It says nothing about how many blocks a customer "
                "may hold across different merchants, and OC-228 UPI Apps "
                "obligation 2 requires a 'consolidated view of all active "
                "blocks', plural, which confirms the cross-merchant case is "
                "expected. A multi-merchant agent is not blocked by this."
            ),
        ),
        Capability(
            name="block_amount_modifiable_without_revoke", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation=f"{SETU_UPDATE} (corroborated by {SETU_RESERVE_PLUS})",
            url=SETU_UPDATE_URL,
            quote=_SETU_TWO_UPDATES,
            notes=(
                "[PARTIAL] - a PSP doc describing a RAIL rule, per the "
                "rail-semantics skill. It is fact for Setu and a lead, not a "
                "fact, for the rail.\n"
                "WHAT WAS ESTABLISHED. A modify operation exists that is "
                "distinct from revoke. Setu's OpenAPI "
                "(/api-specs/payments/umap.json) carries "
                "'PUT /api/v1/merchants/mandates/{id}/modify - Modify a mandate "
                "by id' alongside a separate "
                "'PUT /api/v1/merchants/mandates/{id}/revoke - Revoke a mandate "
                "by id'. The ReservePlus page - Setu's name for the single "
                "block multi-debit product - lists 'Updating a single block "
                "multi debit mandate' first among the operations available "
                "'once it is LIVE'. The mandate SURVIVES: the update flow emits "
                "'mandate.updated' and the docs warn that the updated state is "
                "'a pseudo status. Do not update mandate status based on this.'\n"
                "The quote above is what narrows it to the amount. Only two "
                "fields are updatable at all, and one of them - endDate - is "
                "explicitly excluded for SBMD. By elimination, on an SBMD "
                "mandate a modify can change the amount and nothing else.\n"
                "PRIMARY CORROBORATION that a modify exists at scheme level, "
                "though never that it may decrease. OC-228 names modification "
                "four times as a first-class lifecycle event: acquiring 5(c) "
                f"'{_OC228_MERCHANT_REVOKE}' - update and revoke as two "
                "things; issuer 2 requires notifications for 'block creation, "
                "modification, debit, revoke and expiry'; acquiring 5(e) and "
                "UPI Apps 2 both require transaction history 'including "
                "creation, debits, modification'.\n"
                "SECOND INDEPENDENT SOURCE, payer-PSP side. Razorpay's TPAP Pro "
                "API exposes PATCH /v1/upi/tpap/mandates/:umn whose `action` "
                "parameter takes the two values 'update' and 'revoke' side by "
                "side, and documents `amount` as: "
                f"'{_RZP_TPAP_UPDATE_AMOUNT}'\n"
                "ADOPTION REALITY, and this is the finding that matters. Six "
                "merchant-side PSPs were surveyed by enumerating published API "
                "surface. Exactly ONE - Setu - exposes modify. Razorpay's "
                "'Manage Mandates and Tokens' page has two lifecycle calls, "
                "cancel and delete, and no modify; its Reserve Pay webhooks are "
                "token.confirmed and token.cancellation_initiated, with no "
                "token.updated. Cashfree's manage action enum is CANCEL | PAUSE "
                "| ACTIVATE | CHANGE_PLAN and SBMD supports CANCEL only. PayU "
                "and Juspay expose no modify. BoxPay publishes no API surface "
                "for ReservePay at all. So the gap is IMPLEMENTATION, not "
                "regulation - which is a much more interesting sentence to say "
                "out loud than 'the rail does not allow it'."
            ),
        ),
        Capability(
            name="block_amount_reducible_without_revoke", supported=True,
            source_tier=SourceTier.UNVERIFIED,
            notes=(
                "STILL UNVERIFIED AFTER A FULL PSP SURVEY, AND DELIBERATELY SO. "
                "This is the assumption round 4 named as the most likely to be "
                "false in the whole project. Round 5 could not settle it.\n"
                "What is now evidenced is that a block's amount is MODIFIABLE "
                "without revoking - see "
                "`block_amount_modifiable_without_revoke`. What is not "
                "evidenced anywhere is the DIRECTION. Not one of the six PSP "
                "doc sets read on 21 Aug 2026, and neither NPCI circular, "
                "contains a sentence stating whether a modify may lower a "
                "block's amount, or only raise it, or whether it must stay "
                "above the amount already drawn down.\n"
                "The only constraints published anywhere are non-directional. "
                "Setu's OpenAPI bounds the modify `amountLimit` by "
                "'minimum: 100, maximum: 20000000' paise and nothing else. "
                "Razorpay's TPAP Pro modify documents exactly one amount "
                "failure, 'Amount must be greater than 0'. Neither forbids a "
                "decrease. Neither permits one. Silence is not permission, so "
                "this stays UNVERIFIED and `permits()` returns False - the "
                "policy engine will refuse to plan around a downward revision, "
                "which is the correct default. An agent that assumes it can "
                "shrink a block and cannot has stranded the user's money for up "
                "to 90 days and has no fallback that keeps the mandate alive.\n"
                "HOW TO SETTLE IT, and it is now a one-hour test rather than a "
                "one-day one, because the endpoint is named: on Setu staging "
                "(umap.setu.co), create a ReservePlus mandate for Rs 500, "
                "execute Rs 200, then "
                "PUT /v1/merchants/mandates/{id}/modify with amountLimit 30000 "
                "paise. Three outcomes, all informative: it succeeds (capability "
                "becomes SECONDARY [PARTIAL], supported=True); it is rejected "
                "with a directional error (supported=False, and the error string "
                "is the citation); or it succeeds at the API and the issuer "
                "declines, which is the answer that matters most and the one no "
                "document would ever have told us."
            ),
        ),
        Capability(
            name="block_modify_requires_customer_afa", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation=SETU_UPDATE,
            url=SETU_UPDATE_URL,
            quote=_SETU_UPDATE_MPIN,
            notes=(
                "[PARTIAL] - PSP doc describing a rail rule.\n"
                "THE ASYMMETRY THAT DECIDES WHAT LEG THREE COSTS, and it runs "
                "the wrong way for an autonomous agent.\n"
                "The DESTRUCTIVE operation is unattended. Razorpay's cancel is "
                "a server-to-server 'PUT /customers/:cid/tokens/:tid/cancel' "
                "authenticated with the merchant key; Cashfree's is "
                "'POST /pg/subscriptions/:id/manage' with action CANCEL. No "
                "customer, no PIN, no app.\n"
                "The NON-DESTRUCTIVE operation is not. Setu's modify requires "
                "the customer to open a UPI app and enter their mPIN - by "
                "intent link or QR for an intent mandate, by responding to a "
                "collect notification for a collect mandate, and 'An intent "
                "based mandate can only be updated via an intent link / qr and "
                "a collect based mandate can only be updated via collect flow'. "
                "Razorpay's payer-side TPAP modify carries the same cost in its "
                "request body: 'upi_credentials: {} // Upi credentials received "
                "from WebCL' - WebCL is the UPI Common Library, i.e. the PIN "
                "pad.\n"
                "So an unattended agent has exactly one lever that returns "
                "money, and it is the one that destroys the mandate. Reducing "
                "the block instead costs a customer interaction - which is the "
                "same AFA the fresh block after a revoke would have cost. The "
                "saving from modifying rather than revoking is therefore NOT "
                "'one AFA'; it is 'one AFA now instead of one AFA at the next "
                "purchase', plus the option value of the block surviving in "
                "between. That is a real saving but a much smaller one than the "
                "project's cost function currently assumes, and it should be "
                "modelled as deferral, not avoidance."
            ),
        ),
        Capability(
            name="remainder_release_without_teardown", supported=False,
            source_tier=SourceTier.SECONDARY,
            citation=f"{RZP_MANAGE}; {CASHFREE_RESERVE_PAY}; {JUSPAY_OTM_RELEASE}",
            url=RZP_MANAGE_URL,
            quote=_RZP_RELEASE_IS_CANCEL,
            notes=(
                "[PARTIAL] - three PSP docs describing a rail behaviour.\n"
                "DIRECT ANSWER TO 'is there a release-remainder or close-block-"
                "early call?' - yes, every PSP has one, and on all three that "
                "document it, IT IS THE REVOKE. There is no partial release.\n"
                "Razorpay, above: the two ways to release are the Cancel Token "
                "API and expiry. 'All remaining funds under the token' - never "
                "some of them.\n"
                "Cashfree heads its step 6 'Release unused blocked funds "
                "back to the customer using the manage subscription API' and "
                f"then warns: '{_CASHFREE_CANCEL_ONLY}'\n"
                "Juspay, on a page literally titled 'Release the Blocked "
                f"Funds': '{_JUSPAY_RELEASE_IS_REVOKE}'\n"
                "PayU is the honest one. Its Reserve Pay page says under "
                "'Amount Unblocking': 'Currently, the releasing of funds is "
                "done by remiters but PayU has built a functionality (internal) "
                "to revoke the transactions based on end date to minimise the "
                "funds on hold.' A PSP whose answer to stranded funds is a "
                "scheduled revoke has told you there is no decrease operation.\n"
                "ONE PIECE OF GOOD NEWS the circulars do not give you. Razorpay "
                "bounds worst-case stranding below the 90-day ceiling: 'Razorpay "
                "automatically triggers a reversal of the remaining funds 10 "
                "minutes before the token expires.' That is a PSP behaviour, "
                "not a rail duty - `remainder_auto_released` stays False - but "
                "it means the ceiling model's worst case on Razorpay is "
                "'until the token's chosen end date', not 'indefinitely'."
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
            source_tier=SourceTier.OBSERVED,
            citation=("measured 22 Aug 2026 — POST /payments/{id}/capture, "
                      "HTTP 400 (docs agree: razorpay.com/docs/api/payments/capture/)"),
            url="https://razorpay.com/docs/api/payments/capture/",
            quote=_RAZORPAY_FULL_CAPTURE,
            notes=(
                "Not a documentation claim. A test-mode payment was driven to "
                "'authorized' (captured=False) through Razorpay Checkout against "
                "an order created with payment_capture=0, then a capture of "
                "47000 was attempted against 62000 authorized. The API returned "
                "HTTP 400 with this exact sentence — the doc and the live rail "
                "agree word for word. Reproduce with "
                "`python -m amanat.rails.authorize` then "
                "`python -m amanat.rails.probe --capture <pay_id> 47000`. "
                "Forecloses amount-contingent settlement on this rail; the "
                "negative is the thing worth demonstrating."
            ),
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
            name="partial_debit", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation=SETU_RESERVE,
            url=SETU_RESERVE_URL,
            quote=_SETU_OTM_AUTO_UNBLOCK,
            notes=(
                "[PARTIAL] - PSP doc describing a rail rule.\n"
                "CONFIRMED 21 Aug 2026, and it is the most useful thing round 5 "
                "found. On a ONE-SHOT block the rail returns the difference by "
                "itself: 'If a partial debit is done, remaining funds are "
                "unblocked in the customer bank A/C without any additional need "
                "for refund/reversal.' No revoke, no modify, no customer AFA, "
                "no stranded funds. That is precisely the third leg of "
                "amount-contingent settlement, and OTM gives it away free.\n"
                "THE PRICE. Setu's Reserve doc fixes sequenceNumber at 1 - one "
                "debit and the mandate is spent. BoxPay says the same for OTM: "
                "'The debit may be full or partial, but can be captured only "
                "once. If no capture is made, the funds are automatically "
                "released back to the customer's account at expiry.' So OTM "
                "buys automatic release by giving up the standing pool.\n"
                "THE STRAIGHT TRADE this project should state on camera: "
                "SBMD keeps the mandate and strands the change; OTM returns the "
                "change and spends the mandate. Neither gives both. Any claim "
                "that a rail does both is a claim to check.\n"
                "LIMITS DIFFER TOO, and Setu's figures do not match OC-228's: "
                "'block funds upto Rs.1 lakh for all MCCs except 6211' with "
                "'MCC 6211 - Capital Markets & Securities Brokers merchants can "
                "block upto Rs.5 lakhs'. OC-228 caps a Reserve Pay block at "
                "Rs 10,000 / 90 days. Do not carry a Rs 1 lakh OTM ceiling into "
                "an SBMD argument."
            ),
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


SETU_UMAP = RailProfile(
    rail_id="setu_umap",
    display_name="Setu UMAP (UPI mandates)",
    capabilities=[
        Capability(
            name="credentials_self_serve", supported=True,
            source_tier=SourceTier.OBSERVED,
            citation="probed 21 Aug 2026 — accountservice.setu.co/v1/users/login",
            url="https://docs.setu.co/payments/umap/quickstart",
            quote="HTTP 200, access_token issued",
            notes="Signup at bridge.setu.co is genuinely self-serve and the "
                  "token endpoint accepts the resulting credentials.",
        ),
        Capability(
            name="api_publicly_reachable", supported=False,
            source_tier=SourceTier.OBSERVED,
            citation="probed 21 Aug 2026 — DNS via Google 8.8.8.8 and Cloudflare 1.1.1.1",
            url="https://docs.setu.co/payments/umap/quickstart",
            quote="uatapi.setu.co NXDOMAIN; api.setu.co NXDOMAIN",
            notes=(
                "The two hosts the UMAP docs name for sandbox and production do "
                "not exist in public DNS, while accountservice.setu.co and "
                "bridge.setu.co resolve normally. So the API surface is gated "
                "behind onboarding, private DNS or an allowlist — not reachable "
                "from a self-serve signup. Invisible until you hold credentials "
                "and try: every earlier signal, including a 200 from the token "
                "endpoint, said the rail was reachable. Reproduce with "
                "`python -m amanat.rails.probe`."
            ),
        ),
        Capability(
            name="block_amount_modifiable_without_revoke", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation="Setu, Mandate operations > Update",
            url="https://docs.setu.co/payments/umap/mandates/generic/update",
            quote=("There are only two updates possible on a UPI mandate — "
                   "Changing the mandate end date — Changing the mandate amount"),
            notes="[PARTIAL] The only surveyed PSP exposing a modify that "
                  "preserves the mandate. Direction (whether it may LOWER the "
                  "amount) is documented nowhere and remains unverified.",
        ),
    ],
)


RAILS: dict[str, RailProfile] = {
    r.rail_id: r for r in (SBMD, RAZORPAY_AUTH_CAPTURE, UPI_OTM,
                           CASHFREE_PREAUTH, SETU_UMAP)
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
