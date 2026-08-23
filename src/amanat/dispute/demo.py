"""End-to-end dispute walkthrough — the edge, in one command.

    uv run --with cryptography python -m amanat.dispute.demo

An agent settles a cab fare against a real AP2 authorization. Then three
cardholder disputes are adjudicated against the signed record. The point the
market has no answer for: every agent-payment standard proves the agent was
*permitted* and stops; this says what actually happened, and cites the entries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amanat.dispute.adjudicate import Finding, adjudicate, export_representment_packet
from amanat.interop.ap2 import from_open_payment_mandate, to_open_payment_mandate
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail

_C = {Finding.SUPPORTS_MERCHANT: "32", Finding.SUPPORTS_CARDHOLDER: "31",
      Finding.CHARGE_NOT_IN_CHAIN: "36", Finding.OUTSIDE_EVIDENCE: "33",
      Finding.EVIDENCE_TAMPERED: "31"}


def _head(t: str) -> None:
    print(f"\n\033[1m{'─' * 74}\n  {t}\n{'─' * 74}\033[0m")


def _show(adj) -> None:
    c = _C.get(adj.finding, "0")
    print(f"  finding: \033[1;{c}m{adj.finding.value.replace('_', ' ').upper()}\033[0m")
    print(f"  {adj.headline}")
    for r in adj.reasons:
        print(f"    \033[2m· {r}\033[0m")
    print(f"    \033[2m{adj.disclaimer}\033[0m")


def run() -> None:
    print("\n\033[1mDISPUTE ADJUDICATION\033[0m — settle against a real AP2 mandate, "
          "then contest it")

    # An AP2 Open Payment Mandate is the authorization the agent runs under.
    env = Envelope(subject="cab-9001", max_total=1_000_00, max_per_txn=800_00,
                   allowed_payees=["citycabs"],
                   expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                   intent_text="Book a cab to the airport, cap ₹1,000.")
    mandate = to_open_payment_mandate(env)

    _head("The authorization — a real AP2 Open Payment Mandate")
    print(f"  vct {mandate['vct']}")
    grant = from_open_payment_mandate(mandate)
    print(f"  grants up to ₹{grant.max_per_txn/100:,.0f}/txn, "
          f"₹{grant.max_total/100:,.0f} total, to {', '.join(grant.allowed_payees)}")
    print("  \033[2mParsed from AP2's own schema, not a paraphrase — the envelope "
          "round-trips through it.\033[0m")

    # The agent settles, and one over-budget attempt is refused along the way.
    s = AgentSession(env, SimulatedRail("sbmd", customer_balance=5_000_00))
    s.reserve(5_000_00, "citycabs", "fat-fingered ceiling")   # refused: budget
    s.reserve(620_00, "citycabs", "p95 of the fare")
    s.debit(470_00, "metered fare")
    s.release(reason="trip complete")
    packet = s.evidence_packet()

    _head("What happened — a signed chain of what the money did")
    print(f"  {s.summary()}")
    print("  \033[2mblock ₹620 → debit ₹470 → release ₹150; one ₹5,000 attempt refused.\033[0m")

    _head("Dispute 1 — “I never authorized this charge”")
    _show(adjudicate(packet, mandate, "unauthorized"))

    _head("Dispute 2 — “I was charged ₹5,000 I never approved”")
    _show(adjudicate(packet, mandate, "unauthorized", disputed_amount=5_000_00))

    _head("Dispute 3 — “The cab never came”")
    _show(adjudicate(packet, mandate, "non_delivery"))

    _head("The one-click representment packet")
    adj = adjudicate(packet, mandate, "unauthorized")
    rep = export_representment_packet(adj, packet, mandate)
    print(f"  bundles: authorization ({rep['authorization']['vct']}) + "
          f"{len(rep['evidence']['entries'])} signed evidence entries + the finding")
    print("  \033[2mThe evidence still verifies standalone. This replaces the manual")
    print("  evidence scramble with a signed export — it collects evidence, it does")
    print("  not decide the dispute. No win-rate is claimed, because win-rate is the")
    print("  issuer's call, not the record's.\033[0m\n")


if __name__ == "__main__":
    run()
