"""End-to-end walkthrough: the whole argument in one command.

    uv run --with cryptography python -m amanat.demo

Eight acts:
  1. Human intent compiles into a bounded envelope.
  2. The agent proposes; the policy engine disposes; the rail enforces.
  3. The system refuses a transition its evidence does not support — with a citation.
  4. The ceiling set too low: policy allows, the rail declines, and NPCI grants no
     retry for this class of decline. Sale lost.
  5. The cap itself is too low: the agent asks, the human signs a widened grant,
     the sale is saved — and the agent never widened its own authority.
  6. The same order at a higher ceiling: settles — and the remainder does NOT come
     back on its own, which is a hole in the mechanism, not a feature of it.
  7. The evidence packet verifies, is tampered with, and fails — naming the entry.
  8. Everything this build has not verified, printed unprompted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.policy.engine import Action, PolicyEngine, Proposal
from amanat.policy.envelope import Envelope, LedgerState
from amanat.rails.base import RailError
from amanat.rails.semantics import unverified_report
from amanat.rails.simulator import SimulatedRail

RS = "₹"

# NPCI/UPI/OC-228/2025-26 grants a retry budget ONLY for a debit that timed out
# with the issuer or payer PSP — "no retries for any other declines". A debit
# declined because the block was too small is an *other decline*, so it gets
# none. An earlier version of this demo looped three times here, citing a rule
# that does not apply to this failure.
RETRIES_FOR_UNDER_BLOCK = 0

# OC-228 caps a purpose-code-77 block at Rs 10,000 in the same sentence that
# grants the 90-day window. For ceiling selection this is the binding
# constraint: a predicted ceiling above it cannot be blocked at all.
SBMD_BLOCK_CAP = 10_000_00


def rs(paise: int) -> str:
    return f"{RS}{paise / 100:,.2f}"


def act(n: int, title: str) -> None:
    print(f"\n\033[1m{'─' * 74}\n  ACT {n}. {title}\n{'─' * 74}\033[0m")


def run() -> None:
    print("\n\033[1mAMANAT\033[0m — amount-contingent settlement for agent-initiated payments")
    print("An agent must commit to an amount before that amount exists.")

    chain = EvidenceChain.new(subject="order-7741")
    engine = PolicyEngine(chain=chain)

    # ---------------------------------------------------------------- ACT 1
    act(1, "Human intent becomes a bounded envelope")
    intent = "Book me a cab to the airport. Don't spend more than 600 rupees."
    chain.append(Actor.HUMAN, EventType.INTENT, {"text": intent})

    envelope = Envelope(
        subject="order-7741",
        max_total=600_00,
        max_per_txn=600_00,
        allowed_payees=["citycabs"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        intent_text=intent,
    )
    chain.append(Actor.HUMAN, EventType.ENVELOPE, envelope.to_payload())
    print(f'  "{intent}"')
    print(f"  → budget {rs(envelope.max_total)} · payees {envelope.allowed_payees} "
          f"· expires in 6h")

    # ---------------------------------------------------------------- ACT 2
    act(2, "The agent proposes, the policy engine disposes")
    for amount, payee, note in [
        (900_00, "citycabs", "agent overshoots the budget"),
        (400_00, "randomcab", "agent picks an unlisted payee"),
    ]:
        v = engine.evaluate(Proposal(Action.RESERVE, amount, payee, "sbmd"),
                            envelope, LedgerState())
        print(f"  {note}: {rs(amount)} → \033[31mREFUSED\033[0m — {v.reason}")

    # ---------------------------------------------------------------- ACT 3
    act(3, "A refusal the rail's own words decide")
    state = LedgerState(blocked=500_00)
    v = engine.evaluate(
        Proposal(Action.DEBIT, 340_00, "citycabs", "razorpay_auth_capture"),
        envelope, state)
    print(f"  partial debit of {rs(340_00)} against a {rs(500_00)} block")
    print(f"  → \033[31mREFUSED\033[0m — {v.reason}")
    print(f"    \033[2m{v.citation}: “{v.quote}”\033[0m")
    print("\n  \033[2mThis is the conformance oracle: the boundary is machine-checkable,")
    print("  and the refusal carries the evidence that decided it.\033[0m")

    # ---------------------------------------------------------------- ACT 4
    act(4, "Ceiling too low — and NPCI grants no retry for this decline")
    rail = SimulatedRail("_demo_rail", customer_balance=5_000_00)
    ceiling, actual = 380_00, 470_00
    print(f"  model predicts p50 fare, ceiling set at {rs(ceiling)}")
    print(f"  actual metered fare comes in at {rs(actual)}\n")

    ref = rail.reserve(ceiling, "citycabs")
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"block_id": ref.block_id, "to": "BLOCKED", "ceiling": ceiling})

    try:
        rail.debit(ref, actual)
    except RailError as exc:
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION, {
            "block_id": ref.block_id, "outcome": "failed", "error": str(exc),
            "retries_available": RETRIES_FOR_UNDER_BLOCK,
            "citation": "NPCI OC-228 acquiring obligation 3",
        })
        print(f"    debit \033[31mdeclined\033[0m: {exc}")
        print(f"    retries available: \033[31m{RETRIES_FOR_UNDER_BLOCK}\033[0m")
        print("    \033[2mOC-228 grants retries only for issuer/PSP timeouts —")
        print("    \"no retries for any other declines\". This is an other decline.\033[0m")

        chain.append(Actor.POLICY, EventType.REFUSAL, {
            "rule": "no_retry_budget_for_this_decline",
            "citation": "NPCI/UPI/OC-228/2025-26 acquiring obligation 3",
            "proposed_amount": actual,
        })
        rail.revoke(ref)
        chain.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                     {"block_id": ref.block_id, "to": "REVOKED"})
        print(f"\n  → no retry path. Block torn down. \033[31mSale lost.\033[0m")

    # ---------------------------------------------------------------- ACT 5
    act(5, "The cap is too low — the agent asks, the human re-consents")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from amanat.orchestrator.session import AgentSession

    env5 = Envelope(
        subject="order-cap", max_total=1_000_00, max_per_txn=1_000_00,
        allowed_payees=["citycabs"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        intent_text="Book a cab, cap it at ₹1,000.")
    s = AgentSession(env5, SimulatedRail("_demo_rail", customer_balance=5_000_00))

    fare = 1_200_00
    print(f"  the metered fare comes in at {rs(fare)} — above the {rs(env5.max_total)} cap")
    r = s.reserve(fare, "citycabs", "block the fare")
    print(f"  agent tries to block {rs(fare)} → \033[31mREFUSED\033[0m — {r.detail}")

    prop = s.propose_raise(1_300_00, reason=f"metered fare {rs(fare)} exceeds the cap")
    print(f"\n  \033[2magent proposes\033[0m → {prop.detail}")

    user_key = Ed25519PrivateKey.generate()
    ok = s.approve_raise(1_300_00, user_key, reason="rider approved the higher fare")
    print(f"  \033[32mhuman approves & signs\033[0m → {ok.detail}")

    s.reserve(fare, "citycabs", "block the fare at the raised cap")
    s.debit(fare, "metered fare")
    print(f"\n  → \033[32msettled\033[0m {rs(fare)} against the re-consented cap — "
          "sale saved, not lost.")
    print("  \033[2mThe raise is a human-signed entry in the chain: who raised it, by")
    print("  how much, and the key that signed it. The agent never widened its own")
    print("  grant, and nothing before the signature was retroactively permitted.\033[0m")

    # ---------------------------------------------------------------- ACT 6
    act(6, "Ceiling at p95 — settles, but the remainder does not return itself")
    rail2 = SimulatedRail("_demo_rail", customer_balance=5_000_00)
    ceiling2 = 620_00
    ref2 = rail2.reserve(ceiling2, "citycabs")
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"block_id": ref2.block_id, "to": "BLOCKED", "ceiling": ceiling2})

    rail2.debit(ref2, actual)
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"block_id": ref2.block_id, "event": "DEBIT", "amount": actual})

    stranded_for = ref2.available
    rail2.release(ref2)
    chain.append(Actor.RAIL, EventType.RAIL_TRANSITION,
                 {"block_id": ref2.block_id, "event": "RELEASE", "amount": stranded_for})

    print(f"  ceiling {rs(ceiling2)} · debited {rs(actual)} · released {rs(stranded_for)}")
    print(f"  → \033[32msettled\033[0m. Customer money that would otherwise sit blocked: "
          f"\033[33m{rs(stranded_for)}\033[0m")
    print("\n  \033[2mThat is the tradeoff the ceiling model optimises: a lower ceiling")
    print("  loses sales, a higher one strands the customer's money.\033[0m")

    print("\n  \033[1;33mBut that release is not something the rail does for you.\033[0m")
    print("  \033[2mOC-200: funds stay blocked \"till the time mandate is expired,")
    print("  revoked or the mandate amount is exhausted\". Neither circular imposes")
    print("  any duty to return the remainder, and the word \"release\" appears in")
    print("  neither. Left alone the money sits blocked until the customer-chosen")
    print("  end date — up to 90 days.")
    print("\n  Six merchant-side PSPs were surveyed for an operation that returns")
    print("  the change WITHOUT destroying the block. Exactly one has it (Setu).")
    print("  Cashfree: \"Only the CANCEL action is supported for SBMD subscriptions\".")
    print("  Razorpay unblocks \"all remaining funds under the token\" — never some.")
    print("\n  And the asymmetry runs the wrong way for an agent: revoke is an")
    print("  unattended server call, while modify needs the customer's mPIN. An")
    print("  unattended agent's only money-returning lever is the destructive one.")
    print("  OC-228 allows one block at a time per merchant, so tearing it down")
    print("  means the next purchase needs fresh authentication.\033[0m")
    print("\n  \033[1mThe tradeoff nothing on UPI escapes:\033[0m")
    print("  \033[2mSBMD keeps the mandate and strands the change.")
    print("  OTM returns the change and spends the mandate.")
    print("  Stranding is a design choice, not a rail constraint — so the cost")
    print("  function needs three terms (strand, revoke, modify), not one.\033[0m")

    # ---------------------------------------------------------------- ACT 6
    act(7, "The evidence packet — what the money actually did")
    packet = chain.export_packet()
    print(f"  {len(packet['entries'])} entries · "
          f"{len(chain.rail_transitions())} rail transitions · "
          f"{len(chain.refusals())} refusals")

    EvidenceChain.verify_packet(packet)
    print("  → \033[32mverifies\033[0m standalone, with no access to this system")

    victim = next(i for i, e in enumerate(packet["entries"])
                  if e["event_type"] == EventType.RAIL_TRANSITION.value)
    packet["entries"][victim]["payload"]["ceiling"] = 1
    print(f"\n  tampering with entry {victim} (rewriting the ceiling to {rs(1)})...")
    try:
        EvidenceChain.verify_packet(packet)
        print("  → \033[31mVERIFICATION WRONGLY PASSED\033[0m")
    except Exception as exc:
        print(f"  → \033[32mrejected\033[0m: {exc}")

    # ------------------------------------------------------------- HONESTY
    act(8, "What this build has NOT verified")
    for rail_id, cap, note in unverified_report():
        print(f"  \033[33m{rail_id}.{cap}\033[0m")
        if note:
            print(f"    \033[2m{note.strip()[:150]}\033[0m")
    print("\n  \033[2mUnverified capabilities are refused, never assumed. Absence of")
    print("  evidence is not permission.\033[0m\n")


if __name__ == "__main__":
    from amanat.rails.semantics import (
        Capability, RAILS, RailProfile, SourceTier,
    )

    # Acts 4 and 5 run against a named simulator rail. SBMD itself now permits
    # partial debit on a primary citation — NPCI/UPI/OC-228/2025-26, acquiring
    # obligations 5(d)-(e), read 21 Aug 2026 — so this fixture is no longer
    # standing in for a capability the real table lacks. It keeps the demo
    # narrative separable from the conformance table.
    RAILS.setdefault("_demo_rail", RailProfile(
        rail_id="_demo_rail", display_name="Demo Rail (cited partial debit)",
        capabilities=[Capability(
            name="partial_debit", supported=True, source_tier=SourceTier.PRIMARY,
            citation="demo fixture", url="https://example.test",
            quote="partial debit against a standing block is permitted",
        )],
    ))
    run()
