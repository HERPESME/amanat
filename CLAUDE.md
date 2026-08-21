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
   `claim-discipline` skill. The original proposal was refuted by a product the hiring
   company shipped six months earlier.
6. **Integer paise only.** No float touches money.

## Project skills (auto-load)

`rail-semantics` · `evidence-chain` · `claim-discipline` — in `.claude/skills/`.
Invoke the matching one before touching the corresponding module.

## Review agents

`.claude/agents/` holds three adversarial reviewers (`novelty-auditor`,
`payments-architect`, `hiring-panel-skeptic`). **Run them against any new claim before
committing to it.** They killed the first design; that is what they are for.

## Commands

```bash
uv run --with pytest --with cryptography pytest tests/   # 37 tests
uv run --with cryptography python -m amanat.demo         # end-to-end walkthrough
```

## The open research task that blocks the thesis

`sbmd.partial_debit` is UNVERIFIED, so the system currently **refuses its own core
mechanism** on the real rail. Only vendor docs describe partial debit; the NPCI circular
clause has not been read. Resolving this is *research*, not code. See
`tests/test_policy.py::test_partial_debit_on_sbmd_is_refused_pending_verification`.
