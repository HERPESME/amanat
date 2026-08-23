"""Adjudicate a signed settlement chain against an AP2 authorization.

The market's agent-payment stacks — AP2, ACP, x402, Visa TAP, Mastercard Agent
Pay — all establish that an agent was *permitted* to spend, and stop there. The
contested question comes after: a cardholder says "my agent did it," and today
there is no post-transaction record to settle it against. This project produces
exactly that record — a signed chain of what the money did, refusals included —
so this module reads the chain against the AP2 mandate that authorized it and
states, with citations, what the evidence shows.

One line governs the whole module, and it is the line to say out loud:

    This is an evidence finding, not an issuer decision.

Whether a dispute is *won* is issuer discretion. What this establishes is
narrower and honest: what the signed record does and does not show. It never
claims an outcome, and it never invents a delivery fact the chain does not carry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from amanat.evidence.chain import ChainVerificationError, EvidenceChain
from amanat.interop.ap2 import from_open_payment_mandate

DISCLAIMER = ("This is an evidence finding, not an issuer decision. It states "
              "what the signed record shows about authorization and settlement; "
              "it makes no claim about whether a dispute would be won.")

# What the money actually did, by transition. Debit-like transitions moved money
# from the customer; refund-like ones returned it. A release returns funds that
# were blocked but never debited, so it does not reduce what was charged.
_DEBIT_LIKE = {"debit", "debited", "captured"}
_REFUND_LIKE = {"refunded"}


class Finding(Enum):
    SUPPORTS_MERCHANT = "supports_merchant"        # authorized and within bounds
    SUPPORTS_CARDHOLDER = "supports_cardholder"    # a charge that broke the grant
    CHARGE_NOT_IN_CHAIN = "charge_not_in_chain"    # the disputed charge never settled
    OUTSIDE_EVIDENCE = "outside_evidence"          # the chain cannot speak to this
    EVIDENCE_TAMPERED = "evidence_tampered"        # the record itself failed to verify


@dataclass
class Adjudication:
    assertion: str
    finding: Finding
    headline: str
    reasons: list[str] = field(default_factory=list)
    cited_seqs: list[int] = field(default_factory=list)
    net_charged: int = 0
    authorized: dict = field(default_factory=dict)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "assertion": self.assertion, "finding": self.finding.value,
            "headline": self.headline, "reasons": self.reasons,
            "cited_entries": self.cited_seqs, "net_charged": self.net_charged,
            "authorized": self.authorized, "disclaimer": self.disclaimer,
        }


def _rs(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _transition(e: dict) -> str:
    p = e["payload"]
    return str(p.get("action") or p.get("transition") or "").lower()


def adjudicate(packet: dict, mandate: dict, assertion: str, *,
               disputed_amount: int | None = None) -> Adjudication:
    """Walk `packet` against the AP2 `mandate` and state what the evidence shows.

    `assertion` is the cardholder's claim: "unauthorized", "amount",
    "wrong_payee", or "non_delivery".
    """
    # 0. The record has to be intact before anything it says can be relied on.
    try:
        EvidenceChain.verify_packet(packet)
    except ChainVerificationError as exc:
        return Adjudication(
            assertion, Finding.EVIDENCE_TAMPERED,
            "The evidence itself does not verify — it cannot be adjudicated.",
            [f"Verification failed at entry #{exc.seq}: {exc}"], [], 0, {})

    env = from_open_payment_mandate(mandate)
    authorized = {"max_total": env.max_total, "max_per_txn": env.max_per_txn,
                  "allowed_payees": list(env.allowed_payees)}

    entries = packet["entries"]
    debits = [e for e in entries
              if e["event_type"] == "rail_transition" and _transition(e) in _DEBIT_LIKE]
    refunds = [e for e in entries
               if e["event_type"] == "rail_transition" and _transition(e) in _REFUND_LIKE]
    refusals = [e for e in entries if e["event_type"] == "refusal"]
    auth_seq = next((e["seq"] for e in entries
                     if e["event_type"] in ("envelope", "intent")), None)

    net = sum(e["payload"].get("amount", 0) for e in debits) \
        - sum(e["payload"].get("amount", 0) for e in refunds)

    if assertion == "non_delivery":
        return Adjudication(
            assertion, Finding.OUTSIDE_EVIDENCE,
            "The signed record cannot establish delivery.",
            ["This chain proves the payment path — what was authorized, debited, "
             "and returned. It carries no proof of delivery, so whether the goods "
             "or service arrived is outside what it can settle. That boundary is "
             "stated rather than papered over."],
            [], net, authorized)

    # A specific disputed amount that never became a debit — most often because
    # the policy engine refused it. That is the strongest thing the chain can say.
    if disputed_amount is not None and not any(
            e["payload"].get("amount") == disputed_amount for e in debits):
        blocked = [e for e in refusals
                   if e["payload"].get("proposed_amount") == disputed_amount]
        reasons = [f"No debit of {_rs(disputed_amount)} appears anywhere in the chain."]
        if blocked:
            reasons.append(f"An attempt to move {_rs(disputed_amount)} was refused "
                           f"at entry #{blocked[0]['seq']} "
                           f"({blocked[0]['payload'].get('reason', 'policy refusal')}).")
        return Adjudication(
            assertion, Finding.CHARGE_NOT_IN_CHAIN,
            f"The disputed {_rs(disputed_amount)} was never charged.",
            reasons, [b["seq"] for b in blocked], net, authorized)

    if net <= 0:
        return Adjudication(
            assertion, Finding.CHARGE_NOT_IN_CHAIN,
            "Nothing was charged in this record.",
            [f"The chain records {len(refusals)} refusal(s) and no net debit."],
            [r["seq"] for r in refusals], net, authorized)

    # Something was charged. Was every debit inside the AP2 grant?
    cited = ([auth_seq] if auth_seq is not None else []) + [e["seq"] for e in debits]
    over_cap = [e for e in debits if e["payload"].get("amount", 0) > env.max_per_txn]
    bad_payee = [e for e in debits
                 if e["payload"].get("payee")
                 and e["payload"]["payee"] not in env.allowed_payees]

    if over_cap or bad_payee or net > env.max_total:
        reasons = []
        if auth_seq is not None:
            reasons.append(f"The AP2 mandate (recorded at entry #{auth_seq}) grants "
                           f"up to {_rs(env.max_per_txn)} per transaction, "
                           f"{_rs(env.max_total)} in total, to "
                           f"{', '.join(env.allowed_payees)}.")
        if over_cap:
            reasons.append(f"Entry #{over_cap[0]['seq']} debited "
                           f"{_rs(over_cap[0]['payload']['amount'])}, above the "
                           f"per-transaction cap.")
        if bad_payee:
            reasons.append(f"Entry #{bad_payee[0]['seq']} paid "
                           f"{bad_payee[0]['payload']['payee']}, not an allowed payee.")
        if net > env.max_total:
            reasons.append(f"Net charged {_rs(net)} exceeds the authorized total "
                           f"{_rs(env.max_total)}.")
        return Adjudication(
            assertion, Finding.SUPPORTS_CARDHOLDER,
            f"A charge fell outside the authorization.",
            reasons, cited, net, authorized)

    # Everything charged was inside the grant.
    reasons = []
    if auth_seq is not None:
        reasons.append(f"The AP2 mandate, recorded at entry #{auth_seq}, "
                       f"authorised up to {_rs(env.max_per_txn)} per transaction and "
                       f"{_rs(env.max_total)} in total to "
                       f"{', '.join(env.allowed_payees)}.")
    for e in debits:
        reasons.append(f"Entry #{e['seq']} debited "
                       f"{_rs(e['payload'].get('amount', 0))} to "
                       f"{e['payload'].get('payee', env.allowed_payees[0])} — "
                       f"within that grant.")
    if refunds:
        reasons.append(f"{_rs(sum(e['payload'].get('amount', 0) for e in refunds))} "
                       f"was returned, leaving a net charge of {_rs(net)}.")
    return Adjudication(
        assertion, Finding.SUPPORTS_MERCHANT,
        f"The {_rs(net)} charged was authorized and within every bound.",
        reasons, cited, net, authorized)


def export_representment_packet(adj: Adjudication, evidence: dict,
                                mandate: dict) -> dict:
    """Bundle the three things a dispute response needs into one artifact.

    The authorization that permitted the spend (the AP2 mandate), the signed
    record of what the money did (the evidence packet, still standalone-
    verifiable), and the cited finding over the two. This is the one-click,
    signed export that replaces the manual evidence scramble — and nothing more:
    it collects and structures evidence, it does not decide the dispute.
    """
    return {
        "kind": "amanat.representment.v1",
        "authorization": mandate,
        "evidence": evidence,
        "finding": adj.to_dict(),
    }
