"""Run the LLM agent against a governed session.

    ANTHROPIC_API_KEY=... uv run --with anthropic --with cryptography \\
        python -m amanat.orchestrator.cli "Book me a cab to the airport"

Everything the agent does is bounded by the envelope below and recorded in the
evidence chain, which is verified and summarised when the turn ends. The agent
is given a deliberately awkward instruction — commit to a ceiling before the
fare exists — because watching it reason about that tradeoff, and watching the
policy engine refuse it when it overreaches, is the point.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from amanat.evidence.chain import EvidenceChain
from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.semantics import (
    Capability, RAILS, RailProfile, SourceTier,
)
from amanat.rails.simulator import SimulatedRail

DEFAULT_PROMPT = (
    "Book me a cab to the airport. The fare is metered, so you will not know the "
    "final amount until the trip ends. Reserve a sensible ceiling, then debit "
    "4700 rupees-in-paise when the trip completes, then release the rest."
)


def _demo_rail() -> str:
    """A rail with cited partial-debit support, so the flow is exercisable.

    SBMD permits partial debit as of 21 Aug 2026 on NPCI/UPI/OC-228/2025-26,
    acquiring obligations 5(d)-(e). This fixture is kept so the agent loop can
    be exercised without asserting anything about a live rail.
    """
    RAILS.setdefault("_demo_rail", RailProfile(
        rail_id="_demo_rail", display_name="Demo Rail (cited partial debit)",
        capabilities=[Capability(
            name="partial_debit", supported=True, source_tier=SourceTier.PRIMARY,
            citation="demo fixture", url="https://example.test",
            quote="partial debit against a standing block is permitted",
        )],
    ))
    return "_demo_rail"


def main() -> int:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("No Anthropic credentials found.\n"
              "  Set ANTHROPIC_API_KEY, or run `ant auth login`.\n"
              "  The governed core needs no key — try: python -m amanat.demo",
              file=sys.stderr)
        return 2

    prompt = " ".join(sys.argv[1:]) or DEFAULT_PROMPT

    envelope = Envelope(
        subject="cab-001",
        max_total=1_000_00,
        max_per_txn=800_00,
        allowed_payees=["citycabs"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        intent_text=prompt,
    )
    session = AgentSession(envelope, SimulatedRail(_demo_rail(),
                                                  customer_balance=50_000_00))

    print(f"\n\033[1mENVELOPE\033[0m  budget ₹{envelope.max_total / 100:,.2f} · "
          f"payees {envelope.allowed_payees} · expires in 6h")
    print(f"\033[1mPROMPT\033[0m    {prompt}\n")

    from amanat.orchestrator import agent
    reply = agent.run(session, prompt)

    print(f"\033[1mAGENT\033[0m\n{reply}\n")
    print(f"\033[1mAUDIT\033[0m  {session.summary()}")
    for e in session.chain.rail_transitions():
        print(f"  rail  {e.payload.get('action', '?'):>8} "
              f"{e.payload.get('amount', ''):>8}  {e.payload.get('outcome', '')}")
    for e in session.chain.refusals():
        print(f"  \033[31mrefused\033[0m {e.payload.get('reason', '')}")

    EvidenceChain.verify_packet(session.evidence_packet())
    print("\n  evidence packet \033[32mverifies\033[0m standalone\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
