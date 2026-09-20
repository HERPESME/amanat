# Amanat — working notes for Claude

**Read `.claude/decisions/FINAL-RECOMMENDATION.md` before changing anything conceptual.**
The design was rebuilt after a two-round adversarial review refuted the original thesis.
The reasoning is recorded; do not re-derive it from scratch.

## What this is

An agent blocks a spending **ceiling**, debits the **actual** amount, and releases the
difference — with a machine-readable encoding of what each payment rail legally permits,
a deterministic policy engine that refuses illegal transitions, and a signed evidence
chain over rail state transitions.

Framing sentence: *a human in a cab never sets a ceiling — they observe the fare and pay.
An agent must commit to an amount before that amount exists.*

## Non-negotiable design rules

1. **The LLM proposes, the policy engine disposes, the rail enforces.** No model call in
   `policy/`. Ever. It is a pure function.
2. **Unverified capabilities are refused, never assumed.** Absence of evidence is not
   permission. `SourceTier.UNVERIFIED` → `permits()` returns False.
3. **Refusals are evidence.** Every denial is written to the chain with its citation.
4. **The evidence chain is the centrepiece**, not a supporting unit — it is the only
   claim that survived prior-art audit.
5. **Never write "first" / "novel" / "nobody has built this."** See the
   `claim-discipline` skill. The original proposal was refuted by a product that had
   shipped six months earlier.
6. **Integer paise only.** No float touches money.

## Project skills (auto-load)

`rail-semantics` · `evidence-chain` · `claim-discipline` — in `.claude/skills/`.
Invoke the matching one before touching the corresponding module.

## Review agents

`.claude/agents/` holds three adversarial reviewers (`novelty-auditor`,
`payments-architect`, `panel-skeptic`). **Run them against any new claim before
committing to it.** They killed the first design; that is what they are for.

## Commands

```bash
uv run --extra ml --extra web --extra dev pytest tests/ -q   # 419 tests (Node.js runs the browser-verifier tests)
uv run --extra dev python -m amanat.demo                     # end-to-end walkthrough
```

## Open research

`sbmd.partial_debit` is resolved: PRIMARY, by necessary implication from NPCI OC-228 and
OC-200. Still UNVERIFIED (and therefore refused): `sbmd.block_amount_reducible_without_revoke`,
`upi_otm.post_delivery_debit_goods`, and `cashfree_preauth.remainder_auto_released` — the last
one because the earlier "auto-released" result was an inference, not a reading. A dated
measurement is running: `python -m amanat.rails.probe_cashfree_release poll` appends to
`docs/observations/cashfree-release/` (run it at about +24 h and +7 d 1 h after the start).

Round-6 audit and strategy are in `.claude/decisions/round6-*.md` (local): read them before
conceptual changes.
