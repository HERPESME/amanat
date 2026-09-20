"""Adjudicate a signed settlement chain against an AP2 authorization.

AP2, ACP and x402 establish what an agent may spend and attest a payment's
outcome; in the specifications read for this project, post-authorisation dispute
evidence is out of scope or an open request rather than a defined record. The
contested question comes after: a cardholder says "my agent did it." This project
produces a record to settle it against — a signed chain of what the money did,
refusals included — so this module reads the chain against the AP2 mandate that authorized it and
states, with citations, what the evidence shows.

Two things are checked before any reasoning happens, because a finding is only
as good as what it rests on: the evidence chain must verify (nobody altered the
record), and — where the mandate is signed — the mandate must verify against
the user key named in its `cnf` (nobody altered the grant, and the party who
supposedly granted it did). Those are two different keys held by two different
parties; the adjudicator trusts neither. An unsigned mandate is adjudicated but
the finding says so, because "conformed to the recorded grant" and "conformed to
what the user actually signed" are different claims.

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
from amanat.evidence.transitions import (
    DEBIT_LIKE, REFUND_LIKE, is_effective, transition_name, unresolved_in_doubt,
)
from amanat.interop.ap2 import from_open_payment_mandate, verify_mandate

DISCLAIMER = ("This is an evidence finding, not an issuer decision. It states "
              "what the signed record shows about authorization and settlement; "
              "it makes no claim about whether a dispute would be won.")

class Finding(Enum):
    SUPPORTS_MERCHANT = "supports_merchant"        # authorized and within bounds
    SUPPORTS_CARDHOLDER = "supports_cardholder"    # a charge that broke the grant
    CHARGE_NOT_IN_CHAIN = "charge_not_in_chain"    # the disputed charge never settled
    OUTSIDE_EVIDENCE = "outside_evidence"          # the chain cannot speak to this
    EVIDENCE_TAMPERED = "evidence_tampered"        # the record itself failed to verify
    MANDATE_UNVERIFIED = "mandate_unverified"      # the grant itself failed to verify


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

    # 1. So does the grant. A signed mandate that fails its own signature is a
    #    grant that was altered after the user signed it, or was never signed by
    #    the key it names. Nothing can be adjudicated against that.
    bound = verify_mandate(mandate)
    if bound is False:
        return Adjudication(
            assertion, Finding.MANDATE_UNVERIFIED,
            "The authorization itself does not verify against the key it names.",
            ["The mandate carries a signature, but it does not match its cnf key "
             "over the mandate's current contents — the grant was altered after "
             "signing, or signed by someone other than the key it names. Nothing "
             "can be adjudicated against a grant that cannot be trusted."],
            [], 0, {})

    # A chain that began under a signed mandate records which one. A different
    # mandate — even one validly signed by someone else — is not the grant it ran under.
    first = next((e for e in packet["entries"] if e["event_type"] == "envelope"), {})
    grant = (first.get("payload") or {}).get("grant") or {}
    if grant.get("signed") and mandate.get("signature") != grant.get("mandate_signature"):
        return Adjudication(
            assertion, Finding.MANDATE_UNVERIFIED,
            "The mandate supplied is not the grant this record ran under.",
            ["The chain records that it began under a mandate with a specific signature "
             "(entry #0); the mandate supplied carries a different one. Nothing can be "
             "adjudicated against a grant the record never ran under."],
            [], 0, {})

    key_hint = ((mandate.get("cnf") or {}).get("jwk", {}).get("x") or "")[:12]
    binding = "verified" if bound else "absent"
    binding_note = (
        f"The mandate is signed by the user key {key_hint}… named in its cnf — "
        "the grant reasoned against is the one the user actually signed."
        if bound else
        "The mandate is not signed (no cnf key binding): this finding assumes "
        "the recorded grant is genuine; it cannot say who granted it.")

    adj = _reason(packet, mandate, assertion, disputed_amount)
    adj.authorized["consent_binding"] = binding
    adj.reasons.insert(0, binding_note)
    return adj


def _reason(packet: dict, mandate: dict, assertion: str,
            disputed_amount: int | None) -> Adjudication:
    """The substantive walk, once record and grant are both trusted."""
    env = from_open_payment_mandate(mandate)
    authorized = {"max_total": env.max_total, "max_per_txn": env.max_per_txn,
                  "allowed_payees": list(env.allowed_payees)}

    entries = packet["entries"]
    transitions = [e for e in entries if e["event_type"] == "rail_transition"]
    debits = [e for e in transitions if transition_name(e["payload"]) in DEBIT_LIKE
              and is_effective(e["payload"])]
    refunds = [e for e in transitions if transition_name(e["payload"]) in REFUND_LIKE
               and is_effective(e["payload"])]
    # A debit the rail rejected is an attempt, not a charge: it moved no money.
    rejected = [e for e in transitions if transition_name(e["payload"]) in DEBIT_LIKE
                and not is_effective(e["payload"])]
    rejected_note = [f"The rail rejected a debit of {_rs(e['payload'].get('amount', 0))} "
                     f"at entry #{e['seq']} — it moved no money." for e in rejected]
    # A call that ended in doubt may have moved money the record cannot show.
    in_doubt = unresolved_in_doubt(entries)
    debits_in_doubt = [e for e in in_doubt if e["payload"].get("action") in DEBIT_LIKE]
    doubt_note = [f"The rail call at entry #{e['seq']} ({e['payload'].get('action')} of "
                  f"{_rs(e['payload'].get('amount', 0))}) ended in doubt and has no recorded "
                  "outcome, so money may have moved that this record cannot show."
                  for e in in_doubt]
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

    nothing_charged = net <= 0 or (
        disputed_amount is not None and
        not any(e["payload"].get("amount") == disputed_amount for e in debits))
    if nothing_charged and debits_in_doubt:
        return Adjudication(
            assertion, Finding.OUTSIDE_EVIDENCE,
            "The signed record cannot say whether money was charged.",
            doubt_note + rejected_note, [e["seq"] for e in debits_in_doubt], net, authorized)

    # A specific disputed amount that never became a debit — most often because
    # the policy engine refused it. That is the strongest thing the chain can say.
    if disputed_amount is not None and not any(
            e["payload"].get("amount") == disputed_amount for e in debits):
        blocked = [e for e in refusals
                   if e["payload"].get("proposed_amount") == disputed_amount]
        reasons = [f"No debit of {_rs(disputed_amount)} appears anywhere in the chain."]
        reasons += [n for n, e in zip(rejected_note, rejected)
                    if e["payload"].get("amount") == disputed_amount]
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
            [f"The chain records {len(refusals)} refusal(s) and no net debit."]
            + rejected_note,
            [r["seq"] for r in refusals] + [e["seq"] for e in rejected], net, authorized)

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
        reasons += doubt_note
        return Adjudication(
            assertion, Finding.SUPPORTS_CARDHOLDER,
            "A charge fell outside the authorization.",
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
    reasons += rejected_note + doubt_note
    return Adjudication(
        assertion, Finding.SUPPORTS_MERCHANT,
        f"The {_rs(net)} charged was authorized and within every bound.",
        reasons, cited, net, authorized)


def export_representment_packet(adj: Adjudication, evidence: dict,
                                mandate: dict) -> dict:
    """Bundle the three things a dispute response needs into one artifact.

    The authorization that permitted the spend (the AP2 mandate, with its user
    signature if it has one), the signed record of what the money did (the
    evidence packet, still standalone-verifiable), and the cited finding over
    the two. This is the one-click, signed export that replaces the manual
    evidence scramble — and nothing more: it collects and structures evidence,
    it does not decide the dispute.
    """
    return {
        "kind": "amanat.representment.v1",
        "authorization": mandate,
        "evidence": evidence,
        "finding": adj.to_dict(),
    }
