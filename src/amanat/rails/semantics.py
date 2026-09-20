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

import re
from dataclasses import dataclass, field
from datetime import date
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

    PRIMARY = "primary"          # the rail's own governing text: a circular, a regulation, a protocol spec
    OBSERVED = "observed"        # measured against the live API — the quote is its response
    SECONDARY = "secondary"      # PSP integration docs — fact for that PSP's own behaviour
    MARKETING = "marketing"      # blog posts, product pages, comparison tables. Never fact.
    UNVERIFIED = "unverified"    # not established: says neither yes nor no. Never fact.

    @property
    def is_fact(self) -> bool:
        return self in (SourceTier.PRIMARY, SourceTier.OBSERVED, SourceTier.SECONDARY)

    @property
    def meaning(self) -> str:
        return _TIER_MEANING[self]


_TIER_MEANING = {
    SourceTier.PRIMARY: "the rail's own governing text: an NPCI circular, an RBI directive, a network "
                        "operating regulation, or an open protocol's normative specification read at a "
                        "pinned revision",
    SourceTier.OBSERVED: "measured against the rail's API; the quote is the response it returned "
                         "(see `environment`)",
    SourceTier.SECONDARY: "PSP integration docs — for that PSP's own behaviour",
    SourceTier.MARKETING: "Blog posts, product pages, comparison tables",
    SourceTier.UNVERIFIED: "Not established: the row says neither yes nor no (`supported` is null) "
                           "and is refused",
}


# The vocabulary rails are compared in. A capability name that two or more rails use is defined
# here once, so `partial_debit` on Visa and on Cashfree are the same question; a name only one
# rail uses is that rail's own. A test keeps this honest in both directions: every shared name is
# defined, and every definition is used.
CONCEPTS = {
    "funds_held_in_customer_account":
        "Authorisation holds funds in the payer's account or an escrow, rather than debiting them.",
    "payment_guarantee":
        "A successful hold guarantees that the merchant will be paid.",
    "partial_debit":
        "A settlement may be for less than the amount authorised or blocked. Who may initiate it differs by "
        "rail: on card rails the merchant captures unilaterally; on UPI Reserve Pay the debit is initiated by "
        "the customer's action on the merchant's platform (OC-228 acquiring obligation 2); in x402 `upto` the "
        "resource server sets the amount at settlement.",
    "over_capture":
        "The merchant may capture more than the amount authorised.",
    "multiple_captures":
        "One authorisation may be drawn on more than once.",
    "partial_void":
        "Part of a hold may be released without capturing it, by a partial reversal or a downward adjustment.",
    "void_whole_hold":
        "A whole hold may be released before anything is captured.",
    "void_after_partial_capture":
        "The uncaptured remainder may be released by an explicit action (a void, or its equivalent on the rail) "
        "once a partial capture has been made.",
    "capture_after_void":
        "A hold that has been voided may still be captured.",
    "remainder_auto_released":
        "After a partial capture the uncaptured remainder is released without any further action by the merchant.",
    "expiry_auto_release":
        "A hold that is never captured is released automatically when it expires. Where the rail has "
        "already debited the payer, this is a refund rather than a release, and the rail's own limit says "
        "how long the money takes to arrive.",
    "incremental_authorization":
        "The amount of a hold may be raised after it has been placed.",
    "buffered_authorisation":
        "A merchant may authorise more than it expects to charge, as a safety margin.",
    "capped_initial_authorization":
        "A fixed, capped amount may be authorised before the final amount is known, with no increments.",
    "manual_capture":
        "A payment can be authorised now and captured later.",
    "idempotent_replay":
        "A repeated request under the same idempotency key acts once and returns the first result.",
    "idempotent_capture_replay":
        "A repeated capture under the same idempotency key returns the first result, while the same call under another key is refused.",
    "idempotent_void_replay":
        "A repeated void under the same idempotency key returns the first result, while the same call under "
        "another key is refused.",
    "duplicate_order_refused":
        "Creating an order under an id that already exists is refused rather than making a second order.",
    "payment_replay_refused":
        "Submitting a payment again on an order that is already authorised is refused rather than authorising "
        "it twice.",
    "idempotency_key_reuse_refused":
        "Reusing an idempotency key for a different request is refused rather than answered with the first "
        "request's result.",
    "concurrent_capture_single_winner":
        "Of several simultaneous captures against one hold, exactly one succeeds and the rest are refused.",
    "settled_amount_verifiable_against_usage":
        "The payer can verify from protocol data that the amount settled matches what was actually consumed.",
    "post_delivery_debit_goods":
        "A merchant may debit after delivering goods, rather than before delivery.",
    "block_amount_modifiable_without_revoke":
        "The amount of a standing block may be changed without tearing the block down.",
}


class Environment(Enum):
    """What an OBSERVED row was observed on.

    A vendor's sandbox that forces an authorisation is not an issuer, and its answer can
    differ from production; a public service or DNS lookup is real infrastructure, though
    this project moved no money against it. An observation says which it was.
    """

    SANDBOX = "sandbox"   # a vendor's test environment or simulator
    LIVE = "live"         # production or public infrastructure; no funds moved by this project


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _check_evidence(kind: str, name: str, tier: SourceTier, quote: str,
                    environment: Environment | None, obtained_on: str) -> None:
    """The evidence discipline every row shares, capabilities and limits alike."""
    if tier is not SourceTier.UNVERIFIED and not quote.strip():
        raise CapabilityError(
            f"{kind} {name!r} claims tier {tier.value!r} "
            f"but carries no verbatim quote; downgrade it to UNVERIFIED"
        )
    if tier is SourceTier.OBSERVED and environment is None:
        raise CapabilityError(
            f"{kind} {name!r} is OBSERVED but does not say what it was observed on: "
            f"sandbox or live"
        )
    if tier is not SourceTier.OBSERVED and environment is not None:
        raise CapabilityError(
            f"{kind} {name!r} names an environment but is not OBSERVED; an environment "
            f"describes a measurement"
        )
    if tier is SourceTier.OBSERVED and not obtained_on:
        raise CapabilityError(
            f"{kind} {name!r} is OBSERVED but does not say when: obtained_on is required. "
            f"A measurement without a date is not a measurement; rails change behaviour"
        )
    if obtained_on:
        try:
            valid = _ISO_DATE.fullmatch(obtained_on) and date.fromisoformat(obtained_on)
        except ValueError:
            valid = False
        if not valid:
            raise CapabilityError(
                f"{kind} {name!r}: obtained_on {obtained_on!r} is not an ISO date (YYYY-MM-DD)"
            )


@dataclass
class Capability:
    """One thing a rail does or does not permit, with the evidence for it.

    `quote` must contain text actually read from the source. If you cannot fill
    it, the tier is UNVERIFIED — that is the honest encoding, and it is always
    available.
    """

    name: str
    supported: bool | None          # None, and only None, for UNVERIFIED: nothing was established
    source_tier: SourceTier
    citation: str = ""
    url: str = ""
    quote: str = ""
    notes: str = ""
    environment: Environment | None = None   # required for OBSERVED, forbidden otherwise
    obtained_on: str = ""                    # ISO date the evidence was read or measured
    probe_id: str = ""                       # the probe that keeps checking this row, if any

    def __post_init__(self) -> None:
        _check_evidence("capability", self.name, self.source_tier, self.quote,
                        self.environment, self.obtained_on)
        if self.source_tier is SourceTier.UNVERIFIED:
            if self.supported is not None:
                raise CapabilityError(
                    f"capability {self.name!r} is unverified, so it says neither yes nor no: "
                    f"supported must be None. Put what is believed in `notes`."
                )
        elif self.supported is None:
            raise CapabilityError(
                f"capability {self.name!r} claims tier {self.source_tier.value!r} "
                f"so it must say yes or no; only an UNVERIFIED row may leave supported as None"
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
    unit: str                      # "paise", "days", "hours", "count"
    source_tier: SourceTier
    citation: str = ""
    url: str = ""
    quote: str = ""
    notes: str = ""
    environment: Environment | None = None
    obtained_on: str = ""
    probe_id: str = ""

    def __post_init__(self) -> None:
        _check_evidence("limit", self.name, self.source_tier, self.quote,
                        self.environment, self.obtained_on)

    @property
    def is_fact(self) -> bool:
        return self.source_tier.is_fact

    def render(self) -> str:
        if self.unit == "paise":
            return f"₹{self.value / 100:,.0f}"
        return f"{self.value} {self.unit}"


# Hyperswitch (Apache-2.0) publishes a per-connector capability flag. A rail that is one of its
# connectors carries that connector's name, so the registry can be joined to their matrix. The
# mapping is checked offline against this committed, unmodified copy of their `Connector` enum at a
# pinned commit. Cashfree and Setu are not connectors there, and networks, schemes and protocols
# never are, so those rails carry none.
HYPERSWITCH = {
    "repo": "juspay/hyperswitch",
    "commit": "329f7d7d3d3d178c9be1efa08ec39c3eaaf9fc0a",
    "file": "crates/common_enums/src/connector_enums.rs",
    "snapshot_path": "docs/sources/hyperswitch-connector_enums-329f7d7.rs",
    "snapshot_sha256": "f2356327613a3c791777089593bd166d30213a534471be5bc99f63a5e5e85840",
    "checked_on": "2026-09-21",
}


@dataclass
class RailProfile:
    """One payment rail and everything we can evidence about it."""

    rail_id: str
    display_name: str
    capabilities: dict[str, Capability] = field(default_factory=dict)
    limits: dict[str, Limit] = field(default_factory=dict)
    hyperswitch_connector: str | None = None

    def __init__(self, rail_id: str, display_name: str,
                 capabilities: list[Capability] | None = None,
                 limits: list[Limit] | None = None,
                 hyperswitch_connector: str | None = None) -> None:
        self.rail_id = rail_id
        self.display_name = display_name
        self.hyperswitch_connector = hyperswitch_connector
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
        return bool(cap and cap.supported is True and cap.is_fact)

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

# The day the two circulars were read off the scanned PDFs (see the block below). The
# circulars' own dates are in the citations above; this is when *this project* obtained the text.
NPCI_READ_ON = "2026-08-21"

# The circulars are image-only scans and the regulator's site refuses scripted clients (HTTP
# 403 to any non-browser request), so these committed copies are what the quotes were
# transcribed from. Their SHA-256 is exported in the registry, so anyone can download the
# circular in a browser and compare.
SOURCE_COPIES = {
    OC228_URL: ("docs/sources/NPCI-UPI-OC-228-2025-26-Enhancements-in-UPI-Single-Block-"
                "Multiple-Debits-Reserve-Pay.pdf"),
    OC200_URL: ("docs/sources/NPCI-UPI-OC-200-2024-25-Enablement-of-UPI-Mandate-feature-of-"
                "Single-Block-Multiple-Debits.pdf"),
}

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

# OC-200 clause (b), page 2 of the scan. The list items are separate lines in the circular, so the gaps
# are marked rather than joined with punctuation that is not there.
_OC200_PAYER_INITIATED = (
    "Such mandate creations shall be payer-initiated mandates wherein the customer can create "
    "mandate from the below mentioned methods: … QR based … Intent … SDK/Plug In … "
    "Other mode of initiation shall be envisaged later."
)

# OC-200 clause (g), page 3 of the scan.
_OC200_P2M_ONLY = (
    "The Single Block Multiple Debit mandates shall be only applicable for P2M category of transactions."
)

# OC-228, Acquiring entities obligation 1, page 2 of the scan.
_OC228_ELIGIBLE_MERCHANTS = (
    "To begin with UPI Reserve Pay shall be enabled only for online verified merchants with low ticket "
    "and high frequency transactions and hence selection of the online merchants must adhere to this principle."
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

# Razorpay, Payments > payment states, the `authorized` state.
_RAZORPAY_AUTHORIZED_DEBITED = (
    "The payment state changes to authorized when the bank successfully "
    "authenticates the customer's payment details. The money is deducted from "
    "the customer's account by Razorpay."
)

# Razorpay, Payment Capture Settings > Manually Capture Payments.
RAZORPAY_CAPTURE_SETTINGS_URL = "https://razorpay.com/docs/payments/payments/capture-settings/"
RAZORPAY_CAPTURE_URL = "https://razorpay.com/docs/api/payments/capture/"
_RAZORPAY_MANUAL_CAPTURE = (
    "You can manually capture payments in the authorized state using our "
    "Capture API or from the Dashboard. All payments that are not captured "
    "within the manual timeout period will be auto-refunded."
)

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
# followed by a two-item bulleted list, and, further down past a request sample,
# a standalone callout. Each piece is verbatim; "…" marks where the page has
# something else between them, and `amanat.registry.watch` checks the pieces in order.
_SETU_TWO_UPDATES = (
    "There are only two updates possible on a UPI mandate … "
    "Changing the mandate end date … Changing the mandate amount … "
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
    "ways: … Use the Cancel Token API below to release the blocked funds. When "
    "this API is called, all remaining funds under the token are unblocked and "
    "credited to the customer's bank account instantly. … If you do not cancel "
    "the token and the token balance is not fully utilised before expiry, "
    "Razorpay automatically triggers a reversal of the remaining funds 10 "
    "minutes before the token expires."
)

# Razorpay, Manage Mandates and Tokens > Cancel Tokens, read live on 21 Sep 2026. "Automatic release" is the
# adjacent tab's label, and the endpoint sentence follows the two ways of releasing.
_RZP_BUSINESS_INITIATED_RELEASE = (
    "Business-initiated release … Use the Cancel Token API below to release the blocked funds. When this "
    "API is called, all remaining funds under the token are unblocked and credited to the customer's bank "
    "account instantly. … This initiates the cancellation of the mandate from NPCI."
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
    "Reserve allows a merchant to block funds upto ₹1 lakh for all MCCs "
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
        source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON, citation=OC228, url=OC228_URL,
        quote=_OC228_BLOCK_CEILING,
        notes=(
            "OC-228 states this cap twice (issuer obligation 5 and acquiring "
            "obligation 5(b)) with no purpose-code qualifier, in a circular that "
            "renames SBMD as a whole ('UPI Single Block and Multiple Debits "
            "(henceforth to be referred as UPI Reserve Pay)') and says all prior "
            "NPCI guidelines for it continue to apply. OC-200(c) separately gives "
            "Rs 5 lakh per transaction for purpose code 76 (securities). Which "
            "governs a code-76 block is not stated in anything read. Per this "
            "registry's own rule for limits, thin evidence refuses more, never "
            "less: treat Rs 10,000 as binding until NPCI says otherwise. For a "
            "ceiling-selection thesis this is the BINDING constraint: a "
            "predicted ceiling above it cannot be blocked at all, whatever the "
            "model says."
        ),
    ),
    Limit(
        name="max_block_validity_days", value=90, unit="days",
        source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON, citation=OC228, url=OC228_URL,
        quote=_OC228_BLOCK_CEILING,
        notes=(
            "Same sentence as the Rs 10,000 ceiling, so the two are cited together: "
            "quoting the window alone reads as a much more permissive rail than "
            "the one that exists."
        ),
    ),
    Limit(
        name="max_active_blocks_per_merchant", value=1, unit="count",
        source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON, citation=OC228, url=OC228_URL,
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
                "goods) is the one the engine uses, and the "
                "circular states the rule by example."
            ),
        ),
        Capability(
            name="post_delivery_debit_variable_amount_services", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
            citation=f"{OC228}, Acquiring entities obligation 4",
            url=OC228_URL,
            quote=_OC228_DEBIT_BEFORE_DELIVERY,
            notes=(
                "The carve-out is scoped by AMOUNT UNCERTAINTY, not delivery "
                "contingency. Cabs and EV charging qualify; a fixed-price kurta "
                "does not. Grocery is outside the carve-out: the amount resolves at picking, "
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
            citation=f"{OC228}, Acquiring entities obligations 5(d) and 5(e)",
            url=OC228_URL,
            quote=_OC228_UNUTILISED,
            notes=(
                "VERIFIED 21 Aug 2026 against the circular PDF. This is the "
                "capability that amount-contingent settlement depends on, and "
                "the reason the engine stopped refusing its own core mechanism.\n"
                "Reading: neither OC-228 nor OC-200 contains an explicit "
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
                "references but NPCI does not publish.\n"
                "Read this tick with who starts the debit. OC-228's opening paragraph "
                "calls the debits ones 'which can be initiated by the customer on the "
                "merchant's platform', acquiring obligation 2 names the debit as 'initiated "
                "by the customer action on merchant's platform', and the block itself is "
                "payer-initiated (`block_creation_agent_initiable`). So this means the rail "
                "permits a debit smaller than the block, not that a merchant may take it "
                "unilaterally, as it can capture on a card rail. Whether a server-to-server "
                "debit against an existing block needs a fresh customer action in practice "
                "has not been tested."
            ),
        ),
        Capability(
            name="block_creation_agent_initiable", supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-21",
            citation=f"{OC200}, clause (b)",
            url=OC200_URL,
            quote=_OC200_PAYER_INITIATED,
            notes=(
                "The block is created by the payer in a UPI app, by QR, intent or SDK. "
                "Neither circular gives a merchant or an agent a way to place one, and OC-200 "
                "reserves other modes for later: 'Other mode of initiation shall be envisaged "
                "later.' An agent can choose a ceiling and present it; it cannot place one. "
                "For an agent that must commit to an amount before that amount exists, "
                "this is the fact about the rail that most constrains it, and it was in the scan "
                "and in no row until a payments review of 21 Sep 2026 pointed at it. The quote "
                "marks the list's gaps with an ellipsis because "
                "the three methods are separate lines in the circular."
            ),
        ),
        Capability(
            name="merchant_eligibility_restricted", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-21",
            citation=f"{OC228}, Acquiring entities obligation 1",
            url=OC228_URL,
            quote=_OC228_ELIGIBLE_MERCHANTS,
            notes=(
                "Not open to every merchant: acquirers must select online verified merchants "
                "with low ticket and high frequency, 'to begin with'. Pair it with the Rs 10,000 "
                "cap (`max_block_amount`): the rail is scoped to small, repeated online "
                "purchases, not to arbitrary agentic commerce."
            ),
        ),
        Capability(
            name="p2m_only", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-21",
            citation=f"{OC200}, clause (g)",
            url=OC200_URL,
            quote=_OC200_P2M_ONLY,
            notes=(
                "Person-to-merchant only. The clause was quoted inside the `multi_debit` note "
                "but had no row of its own, so it did not appear in the comparison."
            ),
        ),
        Capability(
            name="multi_debit", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
            citation=f"{OC200}, issuer obligation 1",
            url=OC200_URL,
            quote=_OC200_MULTIPLE_DEBITS,
            notes=(
                "'create blocking of funds in the customer's account' - the "
                "money never leaves the payer. OC-228 issuer obligation 1 adds "
                "'The reserve amount details is shown to the customer in the "
                "statement and other channels as applicable in due course.' "
                "Contrast Razorpay's 'authorized', which has already debited.\n"
                "The quote is about accounts held at the issuing bank. OC-228 "
                "extends Reserve Pay to 'all UPI-permitted source of funds "
                "(including SA, CA, OD, RuPay Credit Card, pre-sanctioned Credit "
                "lines, etc.)', and its issuer obligation 3 is the credit carve-out: "
                "'Only utilized amount debited after actual purchase to be "
                "considered for bill generation as applicable for credit accounts "
                "on UPI.' On a credit source there is no deposit balance to freeze, "
                "and NPCI does not say how the block is held. Read this row as "
                "'nothing is debited before the draw', not as 'a deposit balance is "
                "frozen'."
            ),
        ),
        Capability(
            name="remainder_auto_released", supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
            citation=f"{OC200}, issuer obligation 1",
            url=OC200_URL,
            quote=_OC200_MULTIPLE_DEBITS,
            notes=(
                "The third leg of the mechanism, releasing the difference, is not automatic, "
                "and this was found on 21 Aug 2026.\n"
                "'The fund shall be blocked in the account till the time mandate "
                "is expired, revoked or the mandate amount is exhausted.' The "
                "rail keeps the unused remainder blocked. Neither circular "
                "imposes any duty to release it after a partial debit, and "
                "neither states any timeline for doing so. Release happens only "
                "because somebody calls revoke or update - see "
                "`customer_revocable` (PRIMARY) and `merchant_revocable` "
                "(SECONDARY, on a PSP's documentation: OC-228 5(c) does not say who may revoke).\n"
                "Consequence for a ceiling model: debit Rs 470 against a "
                "Rs 620 block and stop, and Rs 150 stays stranded until the "
                "customer-chosen end date, up to 90 days. Stranding duration is "
                "'until someone revokes, else end-of-block', not 'until "
                "settlement', and a cost model should use that.\n"
                "Corroborated on 21 Aug 2026 by three independent PSP docs, which "
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
                "One source conflicts, and it is disclosed here rather "
                "than dropped. PayU's Reserve Pay page asserts the opposite in "
                "its examples - 'After finalizing the recharge (e.g., Rs.499), "
                "the balance Rs.51 is released' - but the same page's feature "
                "list gives the mechanism away: 'Currently, the releasing of "
                "funds is done by remiters but PayU has built a functionality "
                "(internal) to revoke the transactions based on end date to "
                "minimise the funds on hold.' A scheduled revoke is not an "
                "automatic release, so the stricter reading is the one used.\n"
                "The contrast that matters: this is a multi-debit finding. "
                "On the single-debit sibling - UPI OTM, Setu's 'Reserve' - the "
                "rail does hand the remainder back by itself; see "
                "`upi_otm.partial_debit`. If the agent commits to exactly one "
                "debit per block, leg three is free and there is nothing to "
                "revoke. The stranding problem is the price of keeping the pool "
                "open for a second debit, and that is a choice made by whoever "
                "plans the debits, not a constraint the rail imposes."
            ),
        ),
        Capability(
            name="merchant_revocable", supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-21",
            citation=f"{RZP_MANAGE}, Cancel Tokens",
            url=RZP_MANAGE_URL,
            quote=_RZP_BUSINESS_INITIATED_RELEASE,
            notes=(
                "[PARTIAL] - a PSP doc describing a rail behaviour: fact for Razorpay, a lead for the rail. "
                "Razorpay's Reserve Pay page lists a 'Business-initiated release': a server-to-server "
                "`PUT /customers/:customer_id/tokens/:token_id/cancel`, shown with `curl -u [YOUR_KEY_ID]:"
                "[YOUR_KEY_SECRET]`, so the merchant's own key and no customer step. Cashfree's manage "
                "action CANCEL is the same shape (see `remainder_release_without_teardown`).\n"
                "What the circular says is narrower, and it does not say who may initiate. OC-228 "
                "acquiring obligation 5(c) says: '" + _OC228_MERCHANT_REVOKE + "' It sits in a "
                "list of what merchants and acquirers 'shall ensure', between 5(b) 'Allow user to enter "
                "the amount and select the end date as per their choice' and 5(e) 'Display of original "
                "block value, remaining balance, expiry date and transaction history'. Every neighbouring "
                "item is something the USER is given on the merchant's platform, and UPI Apps obligation 1 "
                "gives the customer 'Easy access to revoke the block' in the same terms. OC-200(e) says the "
                "customer shall 'also' be provided with an option of revoking, which hints that another "
                "party may revoke; a hint is not a statement.\n"
                "History: this row was PRIMARY on 5(c), was UNVERIFIED after a "
                "payments review read the sentence in context on 21 Sep 2026, and is SECONDARY now on the "
                "PSP page that does say it. `customer_revocable` is PRIMARY and unaffected.\n"
                "CORRECTION, 21 Aug 2026, kept because it still applies. This note used to end '...so a "
                "block can be revised downward as well as torn down.' That was an inference from the word "
                "'update', not a finding. A modify operation does exist and does preserve the block - see "
                "`block_amount_modifiable_without_revoke` - but nothing in either circular or in any PSP "
                "doc says it may revise an amount DOWNWARD. See `block_amount_reducible_without_revoke`, "
                "which is UNVERIFIED for exactly that reason."
            ),
        ),
        Capability(
            name="customer_revocable", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
                "CORRECTION: an earlier version of this registry recorded 'code 76 is "
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
                "CORRECTION after review, 21 Sep 2026. This note used to say "
                "OC-228's block ceiling is 'stated in a circular scoped to purpose "
                "code 77'. It is not: OC-228 mentions code 77 once, as the "
                "reconciliation identifier (general guideline 3), and its Rs 10,000 "
                "/ 90-day ceiling carries no purpose-code qualifier. Whether that "
                "later flat cap displaces OC-200(c)'s Rs 5 lakh for code 76 is not "
                "stated in either circular; treat Rs 10,000 as binding on every "
                "SBMD block until NPCI says otherwise."
            ),
        ),
        Capability(
            name="block_validity_90_days", supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
            citation=f"{OC228}, Acquiring entities obligation 5(b)",
            url=OC228_URL,
            quote=_OC228_BLOCK_CEILING,
            notes=(
                "90 days is a MAXIMUM with a customer-chosen end date, not a "
                "default. OC-228 issuer obligation 5 states the same limits: "
                "'The block created to be maximum of Rs.10,000 of block limit "
                "and up to 90 days.'\n"
                "The 90 days and the Rs 10,000 are one sentence in the circular, "
                "cited together here. For a ceiling-selection thesis the Rs 10,000 is the "
                "harder constraint: any predicted ceiling above it cannot be "
                "blocked at all on purpose code 77.\n"
                "The '90d vs cards 7d vs mandate 60d' comparison still "
                "originates in PayU MARKETING copy and mislabels OTM as "
                "'standard mandate', and is not cited here. For the card comparison use "
                "this registry's own rows: `visa_card_auth.hold_expiry_days_*` "
                "(5 to 30 days by channel and merchant category, from Visa's own "
                "guide) and `stripe_card_manual_capture.hold_expiry_days`. "
                "Country-specific approval-response validity periods are in the "
                "Visa Rules, which have not been read."
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
            source_tier=SourceTier.PRIMARY, obtained_on=NPCI_READ_ON,
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
            source_tier=SourceTier.SECONDARY, obtained_on=NPCI_READ_ON,
            citation=f"{SETU_UPDATE} (corroborated by {SETU_RESERVE_PLUS})",
            url=SETU_UPDATE_URL,
            quote=_SETU_TWO_UPDATES,
            notes=(
                "[PARTIAL] - a PSP doc describing a rail rule. It is "
                "fact for Setu and a lead, not a "
                "fact, for the rail.\n"
                "What was established: a modify operation exists that is "
                "distinct from revoke. Setu's OpenAPI "
                "(/api-specs/payments/umap.json) carries "
                "'PUT /api/v1/merchants/mandates/{id}/modify - Modify a mandate "
                "by id' alongside a separate "
                "'PUT /api/v1/merchants/mandates/{id}/revoke - Revoke a mandate "
                "by id'. The ReservePlus page - Setu's name for the single "
                "block multi-debit product - lists 'Updating a single block "
                "multi debit mandate' first among the operations available "
                "'once it is LIVE'. The mandate survives: the update flow emits "
                "'mandate.updated' and the docs warn that the updated state is "
                "'a pseudo status. Do not update mandate status based on this.'\n"
                "The quote above is what narrows it to the amount. Only two "
                "fields are updatable at all, and one of them - endDate - is "
                "explicitly excluded for SBMD. By elimination, on an SBMD "
                "mandate a modify can change the amount and nothing else.\n"
                "Primary corroboration that a modify exists at scheme level, "
                "though never that it may decrease. OC-228 names modification "
                "four times as a first-class lifecycle event: acquiring 5(c) "
                f"'{_OC228_MERCHANT_REVOKE}' - update and revoke as two "
                "things; issuer 2 requires notifications for 'block creation, "
                "modification, debit, revoke and expiry'; acquiring 5(e) and "
                "UPI Apps 2 both require transaction history 'including "
                "creation, debits, modification'.\n"
                "A second independent source, on the payer-PSP side: Razorpay's TPAP Pro "
                "API exposes PATCH /v1/upi/tpap/mandates/:umn whose `action` "
                "parameter takes the two values 'update' and 'revoke' side by "
                "side, and documents `amount` as: "
                f"'{_RZP_TPAP_UPDATE_AMOUNT}'\n"
                "Adoption: six "
                "merchant-side PSPs were surveyed by enumerating published API "
                "surface, and exactly one - Setu - exposes modify. Razorpay's "
                "'Manage Mandates and Tokens' page has two lifecycle calls, "
                "cancel and delete, and no modify; its Reserve Pay webhooks are "
                "token.confirmed and token.cancellation_initiated, with no "
                "token.updated. Cashfree's manage action enum is CANCEL | PAUSE "
                "| ACTIVATE | CHANGE_PLAN and SBMD supports CANCEL only. PayU "
                "and Juspay expose no modify. BoxPay publishes no API surface "
                "for ReservePay at all. So the gap is implementation, not "
                "regulation."
            ),
        ),
        Capability(
            name="block_amount_reducible_without_revoke", supported=None,
            source_tier=SourceTier.UNVERIFIED,
            notes=(
                "Still unverified after a survey of the PSPs, and deliberately. "
                "An earlier review named this as the assumption most likely to be "
                "false in the whole project, and a later survey could not settle it.\n"
                "What is now evidenced is that a block's amount is modifiable "
                "without revoking - see "
                "`block_amount_modifiable_without_revoke`. What is not "
                "evidenced anywhere is the direction. Not one of the six PSP "
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
                "To settle it takes a one-hour test, because the endpoint is named. On Setu staging "
                "(umap.setu.co), create a ReservePlus mandate for Rs 500, "
                "execute Rs 200, then "
                "PUT /v1/merchants/mandates/{id}/modify with amountLimit 30000 "
                "paise. Three outcomes, all informative: it succeeds (capability "
                "becomes SECONDARY [PARTIAL], supported=True); it is rejected "
                "with a directional error (supported=False, and the error string "
                "is the citation); or it succeeds at the API and the issuer "
                "declines, which is the answer that matters most and the one no "
                "document would state."
            ),
        ),
        Capability(
            name="block_modify_requires_customer_afa", supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on=NPCI_READ_ON,
            citation=SETU_UPDATE,
            url=SETU_UPDATE_URL,
            quote=_SETU_UPDATE_MPIN,
            notes=(
                "[PARTIAL] - PSP doc describing a rail rule.\n"
                "The asymmetry that decides what the third leg costs, and it runs "
                "the wrong way for an autonomous agent.\n"
                "The destructive operation is unattended. Razorpay's cancel is "
                "a server-to-server 'PUT /customers/:cid/tokens/:tid/cancel' "
                "authenticated with the merchant key; Cashfree's is "
                "'POST /pg/subscriptions/:id/manage' with action CANCEL. No "
                "customer, no PIN, no app.\n"
                "The non-destructive operation is not. Setu's modify requires "
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
                "saving from modifying rather than revoking is therefore not "
                "'one AFA'; it is 'one AFA now instead of one AFA at the next "
                "purchase', plus the option value of the block surviving in "
                "between. That is a real saving but a much smaller one than a "
                "cost model that treats modifying as avoiding an interaction would "
                "assume, and it is better modelled as deferral, not avoidance."
            ),
        ),
        Capability(
            name="remainder_release_without_teardown", supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on=NPCI_READ_ON,
            citation=f"{RZP_MANAGE}; {CASHFREE_RESERVE_PAY}; {JUSPAY_OTM_RELEASE}",
            url=RZP_MANAGE_URL,
            quote=_RZP_RELEASE_IS_CANCEL,
            notes=(
                "[PARTIAL] - three PSP docs describing a rail behaviour.\n"
                "Answer to 'is there a release-remainder or close-block-"
                "early call?': yes, and on all three PSPs that document one "
                "it is the revoke. There is no partial release.\n"
                "Razorpay, above: the two ways to release are the Cancel Token "
                "API and expiry. 'All remaining funds under the token' - never "
                "some of them.\n"
                "Cashfree heads its step 6 'Release unused blocked funds "
                "back to the customer using the manage subscription API' and "
                f"then warns: '{_CASHFREE_CANCEL_ONLY}'\n"
                "Juspay, on a page literally titled 'Release the Blocked "
                f"Funds': '{_JUSPAY_RELEASE_IS_REVOKE}'\n"
                "PayU states the mechanism plainly. Its Reserve Pay page says under "
                "'Amount Unblocking': 'Currently, the releasing of funds is "
                "done by remiters but PayU has built a functionality (internal) "
                "to revoke the transactions based on end date to minimise the "
                "funds on hold.' A PSP whose answer to stranded funds is a "
                "scheduled revoke has said, in effect, that there is no decrease operation.\n"
                "One mitigation the circulars do not give: Razorpay "
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
    hyperswitch_connector="razorpay",
    capabilities=[
        Capability(
            name="partial_debit", supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-08-22",
            citation=("Razorpay Docs, Capture API, Errors table; the same sentence was returned by a "
                      "test-mode capture on 22 Aug 2026, by hand, and that exchange was not stored"),
            url="https://razorpay.com/docs/api/payments/capture/",
            quote=_RAZORPAY_FULL_CAPTURE,
            notes=(
                "The evidence is Razorpay's own documented error, which the watcher re-reads. "
                "It was also observed by hand: a test-mode payment was driven to "
                "'authorized' (captured=False) through Razorpay Checkout against "
                "an order created with payment_capture=0, then a capture of "
                "47000 was attempted against 62000 authorized. The API returned "
                "HTTP 400 with this exact sentence, so the doc and the rail agreed "
                "word for word. That exchange was not stored, so the row does not "
                "claim a measurement: an earlier version was OBSERVED, and an adopter "
                "review pointed out that the one negative about a named vendor carried "
                "no stored exchange while every Cashfree positive did. Reproduce with "
                "`python -m amanat.rails.authorize` then "
                "`python -m amanat.rails.probe --capture <pay_id> 47000`. "
                "Forecloses amount-contingent settlement on this rail."
            ),
        ),
        Capability(
            name="funds_held_in_customer_account", supported=False,
            source_tier=SourceTier.SECONDARY,
            citation="Razorpay docs, Payments, payment states (authorized)",
            url="https://razorpay.com/docs/payments/payments/",
            quote=_RAZORPAY_AUTHORIZED_DEBITED,
            obtained_on="2026-09-20",
            notes=(
                "Razorpay's 'authorized' state is not a hold: its payment-states table says the money "
                "is deducted from the customer's account by Razorpay at that point and is settled to "
                "the merchant on capture, and a payment not captured within the manual-capture "
                "timeout is auto-refunded (`hold_expiry_days`, 3 days at most). Contrast the rows that "
                "hold funds in the payer's account: `sbmd.funds_held_in_customer_account` and "
                "`cashfree_preauth.funds_held_in_customer_account`."
            ),
        ),
        Capability(
            name="manual_capture", supported=True,
            source_tier=SourceTier.SECONDARY,
            citation="Razorpay docs, Payment Capture Settings (Manually Capture Payments)",
            url=RAZORPAY_CAPTURE_SETTINGS_URL,
            quote=_RAZORPAY_MANUAL_CAPTURE,
            obtained_on="2026-09-20",
            notes=("Authorize-now / capture-later exists, but capture must be for the full amount "
                   "(the `partial_debit` row: measured, and stated in the Capture API's error list)."),
        ),
        Capability(
            name='expiry_auto_release', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Razorpay Docs, Payment Capture Settings, Manually Capture Payments', url=RAZORPAY_CAPTURE_SETTINGS_URL,
            quote=(
                'All payments that are not captured within the manual timeout period will be '
                'auto-refunded.'
            ),
            notes=(
                'New capability name expiry_auto_release: an authorisation that is never captured is '
                'released back to the payer automatically at expiry, with no action by merchant or '
                'payer. Razorpay auto-refunds it, and the credit reaches the customer in 5-7 working '
                'days.'
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Razorpay Docs API Reference, Capture a Payment, Errors, Capture amount must be equal '
                'to the amount authorized (400), Solution'
            ), url=RAZORPAY_CAPTURE_URL,
            quote='Ensure that the amount to be captured is equal to the authorised amount.',
            notes=(
                'Same rule from the other side: the capture amount must equal the authorised amount, so '
                "capturing more is refused. The parameter table also says the amount 'should be equal "
                "to the order amount'."
            ),
        ),
        Capability(
            name='multiple_captures', supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Razorpay Docs API Reference, Capture a Payment, Errors, Only payments which have been '
                'authorized and not yet captured can be captured (400)'
            ), url=RAZORPAY_CAPTURE_URL,
            quote='Only payments which have been authorized and not yet captured can be captured.',
            notes=(
                'A captured payment cannot be captured again (HTTP 400), so there is one capture per '
                'authorised payment. Neither page describes splitting one authorisation across several '
                'captures.'
            ),
        ),
    ],
    limits=[
        Limit(
            name='hold_expiry_days', value=3, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Razorpay Docs, Payment Capture Settings, Options table, Manual capture timeout', url=RAZORPAY_CAPTURE_SETTINGS_URL,
            quote=(
                'Allows you to define custom manual capture timeout. The minimum value is 12 minutes. '
                'The maximum value (default) is 3 days.'
            ),
            notes=(
                'Default and maximum manual-capture timeout is 3 days (minimum 12 minutes); an '
                'authorised payment not captured in time is refunded automatically. The timeout is a '
                "merchant's own setting, so 3 days is an upper bound on a hold's life, not its life. By "
                'default payments auto-capture, and the page names late authorization and a merchant '
                'choice as the cases where a payment stays authorized.'
            ),
        ),
        Limit(
            name='auto_refund_speed_working_days_max', value=7, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Razorpay Docs, Payment Capture Settings, Options table, Auto-refund speed', url=RAZORPAY_CAPTURE_SETTINGS_URL,
            quote=(
                'Payments in the authorized state are auto-refunded after the timeout. The available '
                'option is Normal Refund where the payment is refunded to your customer in 5-7 working '
                'days.'
            ),
            notes=(
                'Working days, not calendar days: how long an auto-refunded (uncaptured) payment takes '
                'to reach the customer after the timeout. The page says this speed applies only to '
                'payments that are auto-refunded.'
            ),
        ),
    ],
)


UPI_OTM = RailProfile(
    rail_id="upi_otm",
    display_name="UPI One Time Mandate",
    capabilities=[
        Capability(
            name="post_delivery_debit_goods", supported=None,
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
            source_tier=SourceTier.SECONDARY, obtained_on="2026-08-21",
            citation=SETU_RESERVE,
            url=SETU_RESERVE_URL,
            quote=_SETU_OTM_AUTO_UNBLOCK,
            notes=(
                "[PARTIAL] - PSP doc describing a rail rule.\n"
                "Confirmed 21 Aug 2026 from a PSP doc. On a one-shot block the rail "
                "returns the difference by itself: 'If a partial debit is done, remaining funds are "
                "unblocked in the customer bank A/C without any additional need "
                "for refund/reversal.' No revoke, no modify, no customer AFA, "
                "no stranded funds. That is precisely the third leg of "
                "amount-contingent settlement, and OTM gives it away free.\n"
                "The price: Setu's Reserve doc fixes sequenceNumber at 1 - one "
                "debit and the mandate is spent. BoxPay says the same for OTM: "
                "'The debit may be full or partial, but can be captured only "
                "once. If no capture is made, the funds are automatically "
                "released back to the customer's account at expiry.' So OTM "
                "buys automatic release by giving up the standing pool.\n"
                "The trade between the two rails: "
                "SBMD keeps the mandate and strands the change; OTM returns the "
                "change and spends the mandate. Neither gives both. Any claim "
                "that a rail does both is a claim to check.\n"
                "The limits differ too, and Setu's figures do not match OC-228's: "
                "'block funds upto Rs.1 lakh for all MCCs except 6211' with "
                "'MCC 6211 - Capital Markets & Securities Brokers merchants can "
                "block upto Rs.5 lakhs'. OC-228 caps a Reserve Pay block at "
                "Rs 10,000 / 90 days. A Rs 1 lakh OTM ceiling does not carry "
                "over to SBMD."
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# Cashfree UPI pre-authorization. Enabled in sandbox via a support request
# on 28 Aug 2026, then MEASURED end to end on 29 Aug 2026 with the probe
# `amanat.rails.probe_cashfree`. These are OBSERVED answers from a sandbox that
# accepted a partial capture, where every earlier real rail probed refused it.
# The quotes below are the responses the sandbox actually returned. The sandbox
# forces the authorisation (`POST /simulate`), so they are the sandbox API's word.
# ---------------------------------------------------------------------------
CASHFREE_PREAUTH_URL = (
    "https://www.cashfree.com/docs/api-reference/payments/latest/payments/authorize"
)
CASHFREE_PREAUTH_GUIDE_URL = "https://www.cashfree.com/docs/payments/features/pre-authorisation"

CASHFREE_PREAUTH = RailProfile(
    rail_id="cashfree_preauth",
    display_name="Cashfree UPI pre-authorization",
    capabilities=[
        Capability(
            name="partial_debit", supported=True, probe_id="cashfree_preauth.partial_capture",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-08-29",
            citation=("measured 29 Aug 2026 (sandbox; the authorisation was forced "
                      "with POST /simulate) — POST /orders/{id}/authorization "
                      "action CAPTURE ₹470 of a ₹620 hold, HTTP 200"),
            url=CASHFREE_PREAUTH_URL,
            quote=('HTTP 200 · authorization {"action":"CAPTURE","status":"SUCCESS",'
                   '"captured_amount":470.0} · payment_message '
                   '"PRE_AUTH|Transaction Success"'),
            notes=(
                "A sandbox confirmation of the mechanism's debit leg: the same "
                "operation (capture less than the authorised amount) that Razorpay's "
                "Capture API refuses, on a different product. Cashfree's UPI "
                "pre-authorisation is measured here; Razorpay's standard payments "
                "capture is measured there. A pre-auth order (order_note "
                "preauth_transaction) was driven to a ₹620 hold in the sandbox — UPI "
                "collect on testsuccess@gocash, then POST /simulate to SUCCESS, "
                "order_status PAID — and a CAPTURE of ₹470 against it returned HTTP "
                "200 with captured_amount 470.0. Reproduce with "
                "`python -m amanat.rails.probe_cashfree`.\n"
                "The authorisation was forced by the sandbox's simulator, so this "
                "measures Cashfree's sandbox API, not an issuer's hold. This is a PSP "
                "pre-auth primitive (authorize-then-capture-once), a different rail "
                "SHAPE from NPCI SBMD's pre-funded drawdown pool. OBSERVED sits below "
                "PRIMARY on purpose: a rail can change behaviour after a deploy, a "
                "circular cannot — so SBMD's PRIMARY evidence and this OBSERVED "
                "evidence are complementary, not redundant."
            ),
        ),
        Capability(
            name="void_after_partial_capture", supported=False,
            probe_id="cashfree_preauth.void_after_partial_capture",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-08-29",
            citation=("measured 29 Aug 2026 (sandbox) — VOID after a partial "
                      "CAPTURE, HTTP 400"),
            url=CASHFREE_PREAUTH_URL,
            quote=('HTTP 400 · "Capture request already exist for the void"'),
            notes=(
                "An explicit VOID after a partial CAPTURE is refused. Cashfree "
                "documents the rule behind it: \"Once captured, a transaction cannot "
                "be voided.\" What the refusal does NOT show is where the uncaptured "
                "remainder went — see `remainder_auto_released`."
            ),
        ),
        Capability(
            name="remainder_auto_released", supported=None,
            source_tier=SourceTier.UNVERIFIED,
            citation="not established",
            url=CASHFREE_PREAUTH_URL,
            notes=(
                "Not established. The 29 Aug probe inferred the remainder was "
                "returned from the refused VOID above and from arithmetic; neither "
                "shows it. Cashfree documents that an authorisation not captured "
                "within seven days is released back to the customer, and is silent on "
                "the uncaptured remainder of a PARTIAL capture. Cashfree support's own "
                "enablement reply (28 Aug 2026, private correspondence) names \"the "
                "applicable operation for processing the unused balance/remainder\" as a "
                "separate operation the probe did not identify. To be measured: poll the order after a partial "
                "capture (and a capture-nothing control) at t+0, 5 min, 1 h, 24 h and "
                "7 d 1 h — `python -m amanat.rails.probe_cashfree_release`. Until then "
                "the engine does not plan around an instant return."
            ),
        ),
        Capability(
            name="partial_void", supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=("Cashfree, Pre-Authorisation docs, FAQ (fetched 20 Sep 2026)"),
            url=CASHFREE_PREAUTH_GUIDE_URL,
            quote="No, voiding must be for the entire authorised amount.",
            notes=("A hold can be released only whole, so 'void just the remainder' "
                   "is not a verb on this rail."),
        ),
        Capability(
            name="multiple_captures", supported=False, probe_id="cashfree_preauth.double_capture",
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=("Cashfree, Pre-Authorisation docs, Managing preauthorisation "
                      "transactions (fetched 20 Sep 2026)"),
            url=CASHFREE_PREAUTH_GUIDE_URL,
            quote="A transaction can only be captured or voided once.",
            notes=("Single-shot: unlike SBMD, where OC-200 says the bank \"shall allow "
                   "multiple debits against the block\", one authorisation takes one "
                   "capture. A basket with substitutions or a fare with a waiting "
                   "charge cannot be drawn down in steps."),
        ),
        Capability(
            name="funds_held_in_customer_account", supported=True,
            probe_id="cashfree_preauth.partial_capture",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-08-29",
            citation=("measured 29 Aug 2026 — order_status PAID, is_captured false "
                      "before any capture"),
            url=CASHFREE_PREAUTH_URL,
            quote=('payment {"is_captured":false,"payment_status":"SUCCESS"} with order_status '
                   'PAID, until an explicit CAPTURE'),
            notes=(
                "Consistent with a pre-auth hold rather than Razorpay's 'authorized' "
                "state (where the customer has already been debited): after POST "
                "/simulate the order is PAID (authorised) but the payment carries "
                "is_captured=false, and only a CAPTURE moved it. The authorisation was "
                "forced by the sandbox simulator, so this is the sandbox API's "
                "statement, not an issuer's."
            ),
        ),
        Capability(
            name="self_serve_enablement", supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-08-28",
            citation=("Cashfree support, reply of 28 Aug 2026 - private correspondence, "
                      "not a public page"),
            quote=("successfully enabled in the Sandbox/Test environment ... "
                   "block creation, partial debit against the standing block, and "
                   "the applicable operation for processing the unused balance/"
                   "remainder"),
            notes=(
                "Not self-serve: UPI pre-authorization had to be requested from "
                "Cashfree support and was enabled per-account. Recorded as a real "
                "constraint on reproducibility - a fresh sandbox signup does NOT "
                "have this until the request is answered. Production access was "
                "explicitly not granted ('No Production access has been enabled').\n"
                "This is the one row that cannot be checked from a public source: Cashfree "
                "can confirm or correct it, and a correction will be recorded as one.\n"
                "SECONDARY, not OBSERVED: this is the vendor's statement about its own "
                "product in a message to the owner of this repository, not a response "
                "the API returned, and a reader cannot open it. It was OBSERVED, on a "
                "sandbox, until a review on 21 Sep 2026 pointed out that an email is not "
                "a measurement. The ticket number is deliberately not published."
            ),
        ),
        # ---- measured 20 Sep 2026 by `python -m amanat.probes` (sandbox; authorisation forced with
        # POST /simulate). Each row names its probe; the suite fails if the latest conclusive run
        # disagrees with it. The raw exchanges are in docs/observations/store/.
        Capability(
            name="void_whole_hold", supported=True, probe_id="cashfree_preauth.void_whole_hold",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-20",
            citation=("measured 20 Sep 2026 (sandbox) — POST /orders/{id}/authorization action VOID on a "
                      "hold nothing was captured from, HTTP 200"),
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 200 · authorization {"action":"VOID","status":"SUCCESS"}',
            notes=("A hold can be released whole, before any capture. Cashfree documents the same shape: "
                   "\"voiding must be for the entire authorised amount\" (`partial_void`)."),
        ),
        Capability(
            name="over_capture", supported=False, probe_id="cashfree_preauth.over_capture",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-20",
            citation=("measured 20 Sep 2026 (sandbox) — CAPTURE ₹700 against a ₹620 hold, HTTP 400"),
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 400 · "Total capture amount can not be grater than transaction amount"',
            notes=("A capture cannot exceed the hold. The message says *total* capture amount, so the "
                   "bound is on the sum drawn against one authorisation — though `multiple_captures` "
                   "says it is drawn once."),
        ),
        Capability(
            name="capture_after_void", supported=False, probe_id="cashfree_preauth.capture_after_void",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-20",
            citation="measured 20 Sep 2026 (sandbox) — CAPTURE after a VOID, HTTP 400",
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 400 · "transaction is already voided"',
            notes=("A released hold cannot be drawn on. Cashfree's guide states the same rule: \"Once voided, "
                   "a transaction cannot be captured.\""),
        ),
        Capability(
            name="idempotent_capture_replay", supported=True,
            probe_id="cashfree_preauth.idempotent_capture_replay",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-20",
            citation=("measured 20 Sep 2026 (sandbox) — the same CAPTURE repeated under one "
                      "x-idempotency-key, then under a different key"),
            url=CASHFREE_PREAUTH_URL,
            quote=('same key: HTTP 200 · authorization {"action":"CAPTURE","status":"SUCCESS",'
                   '"captured_amount":470}; a different key: HTTP 400 · "Duplicate capture_id present"'),
            notes=("Repeating a capture with the same idempotency key returns the first result instead of "
                   "being refused as a second capture, and the control (the identical call under another "
                   "key) is refused — so the key is what the rail honours. That makes a retry after a lost "
                   "response safe on this endpoint. The same holds for VOID (`idempotent_void_replay`), and a "
                   "repeated order creation, a repeated payment and a reused key with another amount are each "
                   "refused (`duplicate_order_refused`, `payment_replay_refused`, "
                   "`idempotency_key_reuse_refused`). Sandbox caveat: on every hold that saw a successful "
                   "capture the capture carries the same action_reference (CAP_12121), so the refusal text "
                   "is probably a sandbox artefact and should not be read as production's wording."),
        ),
        Capability(
            name="idempotent_void_replay", supported=True,
            probe_id="cashfree_preauth.idempotent_void_replay",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-21",
            citation=("measured 21 Sep 2026 (sandbox) — the same VOID repeated under one "
                      "x-idempotency-key, then under a different key"),
            url=CASHFREE_PREAUTH_URL,
            quote=('same key: HTTP 200 · authorization {"action":"VOID","status":"SUCCESS"}; a different key: '
                   'HTTP 400 · "transaction is already voided"'),
            notes=("Repeating a void with the same idempotency key returns the first result, and the control "
                   "(the identical call under another key) is refused as already voided, so the key is what "
                   "the rail honours. A retry of a release after a lost response is therefore safe on this "
                   "endpoint. Sandbox caveat: the void reference is the same constant (VOID_12121) on every "
                   "voided hold."),
        ),
        Capability(
            name="duplicate_order_refused", supported=True,
            probe_id="cashfree_preauth.duplicate_order_refused",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-21",
            citation=("measured 21 Sep 2026 (sandbox) — POST /orders repeated under the id of an order that "
                      "already exists"),
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 409 · order_already_exists · "order with same id is already present"',
            notes=("A repeated order creation is refused, not accepted as a second order, so an order id "
                   "derived from a retry key makes the creation of a hold safe to repeat: the second attempt "
                   "learns the order exists and reads it. The probe repeats the same amount; in exploration "
                   "(not stored) a different amount, and a call carrying an idempotency key, were refused the "
                   "same way and the stored order kept its first amount."),
        ),
        Capability(
            name="payment_replay_refused", supported=True,
            probe_id="cashfree_preauth.payment_replay_refused",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-21",
            citation=("measured 21 Sep 2026 (sandbox) — the UPI collect submitted again against an order "
                      "that was already authorised"),
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 400 · order_inactive · "order is no longer active"',
            notes=("A repeated payment on an authorised order is refused, so replaying the second step of "
                   "placing a hold cannot authorise it twice. The authorisation was forced by the sandbox "
                   "simulator; what an issuer does with a second collect request is not measured."),
        ),
        Capability(
            name="idempotency_key_reuse_refused", supported=True,
            probe_id="cashfree_preauth.idempotency_key_reuse_refused",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-21",
            citation=("measured 21 Sep 2026 (sandbox) — CAPTURE of ₹470 under an idempotency key, then the "
                      "same key for a CAPTURE of ₹300"),
            url=CASHFREE_PREAUTH_URL,
            quote='HTTP 422 · idempotency_error · "invalid body in request for x-idempotency-key"',
            notes=("A key reused for a different request is refused instead of being answered with the "
                   "first request's result, so a retry cannot be confused with a different request. The "
                   "same refusal was seen in exploration (not stored) when a key that had captured was "
                   "reused for a void."),
        ),
        Capability(
            name="concurrent_capture_single_winner", supported=True,
            probe_id="cashfree_preauth.concurrent_capture",
            source_tier=SourceTier.OBSERVED, environment=Environment.SANDBOX, obtained_on="2026-09-20",
            citation=("measured 20 Sep 2026 (sandbox) — three CAPTUREs (₹300, ₹200, ₹100) fired at the "
                      "same instant against one ₹620 hold"),
            url=CASHFREE_PREAUTH_URL,
            quote=('one HTTP 200 · authorization {"action":"CAPTURE","status":"SUCCESS"}; two HTTP 400 · '
                   '"Event has already been initiated"'),
            notes=("Exactly one of three simultaneous captures succeeded; the others were refused, not "
                   "applied. Which one wins varies from run to run (capture 3 won the first, capture 1 "
                   "the recorded one). One sandbox, one hold, three threads: this shows the guard exists "
                   "here, not that no interleaving can defeat it."),
        ),
    ],
    limits=[
        Limit(
            name="hold_expiry_days", value=7, unit="days",
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=("Cashfree, Pre-Authorisation docs, FAQ (fetched 20 Sep 2026)"),
            url=CASHFREE_PREAUTH_GUIDE_URL,
            quote=("If not captured within 7 days, the authorisation expires, and the "
                   "funds are released back to the customer."),
            notes=("The documented deadline for capture or void. A hold that outlives "
                   "an agent session is released by this expiry, not by the agent."),
        ),
    ],
)


SETU_UMAP = RailProfile(
    rail_id="setu_umap",
    display_name="Setu UMAP (UPI mandates)",
    capabilities=[
        Capability(
            name="credentials_self_serve", supported=True,
            source_tier=SourceTier.OBSERVED, environment=Environment.LIVE, obtained_on="2026-08-21",
            citation="probed 21 Aug 2026 — accountservice.setu.co/v1/users/login",
            url="https://docs.setu.co/payments/umap/quickstart",
            quote="HTTP 200, access_token issued",
            notes="Signup at bridge.setu.co is genuinely self-serve and the "
                  "token endpoint accepts the resulting credentials.",
        ),
        Capability(
            name="documented_api_hosts_resolve", supported=False,
            source_tier=SourceTier.OBSERVED, environment=Environment.LIVE, obtained_on="2026-09-21",
            citation="probed 21 Sep 2026 — DNS via Google 8.8.8.8 and Cloudflare 1.1.1.1",
            url="https://docs.setu.co/payments/umap/quickstart",
            quote=("uatapi.setu.co: no address record; api.setu.co: no address record "
                   "(NOERROR with an empty answer)"),
            notes=(
                "The two hosts the UMAP docs name for sandbox and production have no address "
                "record in public DNS, while accountservice.setu.co and bridge.setu.co resolve "
                "normally. This measures a resolver, not Setu's API: it says the documented "
                "hostnames cannot be reached by address from a self-serve signup, and nothing "
                "about why. The likeliest reasons are an allowlist, private DNS or onboarding "
                "gating, and DNS alone does not show which; that explanation is an inference. "
                "It is invisible until you hold credentials and try: every earlier signal, "
                "including a 200 from the token endpoint, said the rail was reachable. "
                "Reproduce with `python -m amanat.rails.probe` or `dig uatapi.setu.co`.\n"
                "History: on 21 Aug 2026 both names answered NXDOMAIN. Re-run on 21 Sep 2026, "
                "they answer NOERROR with no address record from both resolvers, and a name "
                "that cannot exist (`zz-amanat-1.setu.co`) answers exactly the same way, so the "
                "zone now returns an empty answer for any name it has no address for. Neither "
                "host has an A, AAAA or CNAME record and neither is reachable by HTTPS from "
                "here. The finding stands as 'no address record', not as NXDOMAIN. The row was "
                "named `api_publicly_reachable` until an adopter review pointed out that a DNS "
                "lookup does not measure an API."
            ),
        ),
        Capability(
            name="block_amount_modifiable_without_revoke", supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on=NPCI_READ_ON,
            citation="Setu, Mandate operations > Update",
            url="https://docs.setu.co/payments/umap/mandates/generic/update",
            quote=("There are only two updates possible on a UPI mandate … "
                   "Changing the mandate end date … Changing the mandate amount"),
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


# The reference rails (card networks, PSPs, x402 schemes) are defined in their own module and
# registered here, after everything they import exists.
from amanat.rails import reference as _reference  # noqa: E402,F401
