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
`payments-architect`, `adopter-skeptic`). **Run them against any new claim before
committing to it.** They killed the first design; that is what they are for.

## Commands

```bash
uv run --extra ml --extra web --extra dev pytest tests/ -q   # 1359 tests (Node.js runs the browser-verifier tests)
uv run --extra dev python -m amanat.demo                     # end-to-end walkthrough
uv run --extra dev python -m amanat.registry.watch           # re-check every cited quote against its source (network)
uv run --extra dev python -m amanat.probes run               # re-measure the Cashfree sandbox (network, sandbox credentials in .env)
# regenerate what CI diffs: python -m amanat.rails.docgen, then amanat.registry.export, .page and .report
```

## The registry (Phase 1)

`rails/semantics.py` (Cashfree, Razorpay, SBMD, OTM, Setu) and `rails/reference.py` (Visa, Stripe, Adyen,
x402) hold rows; `docs/registry/`, `docs/RAIL_SEMANTICS.md` and `docs/reports/` are generated from them.
A row needs a verbatim `quote`, the `url` of the page that carries it, and `obtained_on`; an OBSERVED row
needs an `environment` and, unless it is on the legacy list in `tests/test_registry_probes.py`, a `probe_id`.
**A model may propose a row; only the watcher admits it** (`python -m amanat.registry.watch` must find the
quote on the page). A row that names a probe fails the suite if the rail's latest conclusive run disagrees.
`supported` is `None` exactly when a row is UNVERIFIED (a row with no answer says neither yes nor no), and an
OBSERVED row needs its date. Editing or adding a quote fails a ratchet test until the watcher has run again,
so a row change needs the network. **A quote check admits words, not meaning**: two rows the watcher had
admitted were later found not to say what the row said, so read the sentence in its context and run
`payments-architect` on new rows. A passed obligation deadline is *overdue* only where the registry evidences
that the rail keeps the remainder, and *unresolved* otherwise: the chain's remainder is arithmetic, and the
rail was never asked.
Evidence lives in `docs/observations/store/` as append-only hash-chained JSONL: never edit a line. Anything
that reads as a bug in a vendor's product goes to the vendor first (`docs/reports/VENDOR-NOTIFICATION.md`),
so do not push or publish the report or the raw probe data without the owner's say-so.

## Open research

`sbmd.partial_debit` is resolved: PRIMARY, by necessary implication from NPCI OC-228 and
OC-200. Seven capabilities are still UNVERIFIED (and therefore refused); `docs/RAIL_SEMANTICS.md`
lists them under "Outstanding verification", and `cashfree_preauth.remainder_auto_released` is
one because the earlier "auto-released" result was an inference, not a reading. Two of the eight
(`sbmd.merchant_revocable`, `stripe_card_manual_capture.payment_guarantee`) were cited rows until a
review read their sentences in context. A dated
measurement is running: `python -m amanat.rails.probe_cashfree_release poll` appends to
`docs/observations/cashfree-release/` (run it at about +24 h and +7 d 1 h after the start,
18:23 IST on 20 Sep 2026; reads so far show no change).

Round-6 audit and strategy are in `.claude/decisions/round6-*.md` (local): read them before
conceptual changes.
