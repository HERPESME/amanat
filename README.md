# Razorpay AI Buildathon — Decision Workspace

> **New session? Read `.claude/decisions/FINAL-RECOMMENDATION.md` first. Nothing else is load-bearing.**
>
> `.claude/` is deliberately **not committed** — it holds the decision record (three
> adversarial reviewers, four rounds, ~4,100 lines) and the agent definitions that
> produced it. It lives only in the working copy. References to `.claude/...`
> throughout this README point there, not into the repository.

This repo currently contains **no code**. It holds the research and the adversarial review that
decided *what to build*. Building starts from §3 of the final recommendation.

---

## The event

| | |
|---|---|
| **What** | Razorpay AI Buildathon — student-only **intern hiring** event, not a prize hackathon |
| **Offer** | Rs 75,000/month, 6 or 12 months, Bangalore, in-person |
| **Deadline** | Applications close **5 September 2026** |
| **Deliverable** | Public GitHub repo → 5-min pitch → **architecture presentation** → panel interview |
| **Implication** | The artifact is a hiring signal. Depth and defensibility beat demo polish. |

---

## The decision, in one paragraph

Build an agent that blocks a spending **ceiling**, debits the **actual** amount, and releases the
difference — with a machine-readable encoding of what each payment rail legally permits, a
deterministic policy engine that **refuses** illegal transitions, and a signed evidence chain over
the rail's own state transitions. **Track 01.** Claim exactly one thing as novel: that the evidence
chain extends *below* authorization into rail state. Pitch everything else as competent engineering.

**The framing sentence:**
> A human in a cab never sets a ceiling — they observe the fare and pay. An agent must commit to an
> amount *before that amount exists*.

---

## How this was decided

Three adversarial reviewers, two rounds, cross-examined against each other:

| Reviewer | Lens | Round 1 verdict |
|---|---|---|
| `novelty-auditor` | hostile prior-art hunter | **D+** — "assembly, not invention" |
| `payments-architect` | Indian rails, RBI/NPCI, unit economics | **BROKEN** — mechanism unlawful for goods |
| `hiring-panel-skeptic` | would this get the offer? | **NO HIRE** as proposed |

Round 2 put them in the same room. **Two reviewers reversed themselves**, and the panel reviewer
falsified the payments reviewer's headline claim by pulling AP2's JSON schemas from source. The
surviving recommendation is what was left standing.

Agent definitions live in `.claude/agents/` and **auto-register next session** — re-run them on any
new claim before you commit to it. That is the point of this workspace.

---

## Layout

```
README.md                             ← you are here
.claude/
├── settings.json                     5 plugins, project scope
├── agents/                           3 adversarial reviewers (auto-register next session)
│   ├── novelty-auditor.md            hostile prior-art hunter
│   ├── payments-architect.md         Indian rails / regulation / unit economics
│   └── hiring-panel-skeptic.md       would this get the offer?
├── context/
│   ├── 01-proposal.md                ORIGINAL PROPOSAL — REFUTED, kept as history only
│   ├── 02-research-findings.md       sourced research, all URLs
│   └── 03-crossexam.md               round-2 brief that forced the reconciliation
└── decisions/
    ├── FINAL-RECOMMENDATION.md       ★ START HERE
    ├── round1-novelty.md     (475 lines)
    ├── round1-panel.md       (602 lines)
    ├── round1-payments.md  (1,545 lines)   ← primary NPCI circular text, OCR'd
    ├── round2-novelty.md     (345 lines)   ← CPC patent sweep
    ├── round2-panel.md       (569 lines)   ← AP2 schemas pulled from source
    └── round2-payments.md    (606 lines)   ← the amount-contingent pivot
```

## Plugins enabled (project scope — inherited automatically)

`feature-dev` (brings `code-architect`, `code-explorer`) · `pr-review-toolkit` (6 review agents) ·
`agent-sdk-dev` · `mcp-server-dev` · `playground`
Already at user scope: `superpowers`, `frontend-design`, `c4-architecture`.

---

## Rules for whoever builds this

1. **Never write "first" or "nobody has built this."** Every such claim in the original proposal was
   refuted, and one was refuted by a Razorpay product launched six months earlier. Claim rigour, not priority.
2. **Read the primary source, not a summary of it.** Both reviewers who reversed themselves did so
   because they reasoned from a summary. The NPCI circular text is quoted in `round1-payments.md`.
3. **The evidence chain is the centrepiece, not a supporting unit.** It is the only surviving
   novelty claim.
4. **No LLM in the enforcement path.** The LLM proposes; the policy engine disposes; the rail enforces.
5. **Volunteer the weaknesses before being asked** — §6 of the final recommendation. That is the
   documented difference between HIRE and STRONG HIRE.
6. **Do not build:** the negotiation layer, the RTO risk model, the discount economics, or the A/B harness.
   Each was killed for a specific reason recorded in the decisions folder.

## Before pitching — unresolved

- [ ] Read **`US20210241305A1`** (Capital One) and **`WO2020094875A1`** (Equensworldline). Both directly
      relevant, both unread — Google rate-limited the fetch. Live threat.
- [ ] Fire rail-enablement requests at **Cashfree** (pre-auth: UPI + partial capture + 1-yr window,
      **not** self-serve) and **Setu UAT** in hour 1. Assume neither lands; the simulator is a
      first-class implementation regardless.
- [ ] Confirm the **purpose-code** split: SBMD **76** = securities, merchant-revoke-only;
      **77** = goods, customer-revocable. Setu documents 76; e-commerce needs 77.


---

## Build status

**64 tests passing.** Governance core, ceiling model, and orchestrator implemented.

```bash
uv run --with pytest --with cryptography --with numpy --with scikit-learn \
       --with pandas --with pyarrow pytest tests/ -q      # 64 tests
uv run --with cryptography python -m amanat.demo          # 7-act walkthrough
uv run --with numpy --with pandas --with scikit-learn \
       --with pyarrow python -m amanat.ceiling.frontier   # real NYC TLC fares
uv run --with cryptography python -m amanat.rails.docgen  # regenerate rail docs

docker compose run --rm demo | tests | frontier | agent   # needs Docker running
```

| Unit | Status | Where |
|---|---|---|
| Rail semantics + capability table | done | `src/amanat/rails/semantics.py` |
| Policy engine + containment suite | done | `src/amanat/policy/` |
| Signed evidence chain + tamper demo | done | `src/amanat/evidence/chain.py` |
| Rail adapter + simulator | done | `src/amanat/rails/{base,simulator}.py` |
| Ceiling model (conformal) + seam | done | `src/amanat/ceiling/` |
| Orchestrator (governed core + LLM) | done | `src/amanat/orchestrator/` |
| 7-act end-to-end demo | done | `src/amanat/demo.py` |
| Docker (2 targets) + compose | written, **unbuilt** | `Dockerfile`, `compose.yaml` |
| C4 architecture docs | done | `docs/architecture/` |
| Generated rail semantics | done | `docs/RAIL_SEMANTICS.md` |

## Primary sources obtained (21 Aug 2026)

Both governing NPCI circulars — **OC-228/2025-26** and **OC.No.200/2024-25** — were retrieved
and read in full (image-only scans, rendered at 220 dpi, read page by page; local copies in
`docs/sources/`). `sbmd` went from 3 PRIMARY + 5 UNVERIFIED capabilities to **11, all PRIMARY
with verbatim quotes**.

- **Partial debit is permitted** — by *necessary implication*, not an explicit clause. OC-228
  5(d)–(e) distinguishes "original block value" from "remaining balance"; OC-200 issuer 1 says
  the bank "shall allow multiple debits against the block". Say "by implication"; do not say
  "NPCI says you may partially debit."
- **SBMD is a pre-funded drawdown pool**, not authorize-then-capture. Debit < block is the
  *ordinary* case.
- **The rail does not return the remainder.** OC-200: funds stay blocked "till the time mandate
  is expired, revoked or the mandate amount is exhausted". "Release" appears in neither
  circular. Leg three of the mechanism is an action this system must take — and revoke tears
  down the whole block, of which only one per merchant may stand.
- **Rs 10,000 and 90 days are one sentence.** For ceiling selection the Rs 10,000 block cap is
  the binding constraint, not the 90-day window.

Four claims this project was carrying were wrong and are now fixed — see §0 of
`FINAL-RECOMMENDATION.md`. The demo previously retried three times citing a rule that does not
apply to that decline; OC-228 grants retries only for issuer/PSP timeouts.

## Two findings that came out of building it

**1. The system refuses its own core mechanism.** `sbmd.partial_debit` is
`UNVERIFIED` — only vendor docs describe it; the NPCI clause has not been read.
Because unverified capabilities are refused, amount-contingent settlement is
currently blocked on the real rail by our own honesty rule. That is the
conformance oracle working, and it is the most useful thing to put on camera.
Resolving it is a research task, not a code change.
See `test_partial_debit_on_sbmd_is_refused_pending_verification`.

**2. Conformal coverage misses under temporal drift — 18 of 20 configurations.**
Conformal prediction's guarantee is distribution-free but **not shift-free**:
it assumes calibration and test data are exchangeable, and training on January
to deploy in February breaks exactly that. Calibrating on *recent* rather than
random training rows narrows the gap by roughly an order of magnitude
(−3.55pp → +0.91pp at α=0.25) without closing it. A random train/test split
would have shown the guarantee holding cleanly — and would have been a lie about
deployment. `tests/test_ceiling.py` confirms the implementation is correct on
exchangeable data, which isolates shift from bug.

## Patent position (resolved 21 Aug 2026)

Both flagged patents were retrieved and read in full — see
`.claude/decisions/round3-patents.md`.

- **Capital One `US20210241305A1`** — not a threat. It is an issuer auction for
  card selection. But its dependent claims cover "place several holds, keep one,
  cancel the rest", so never claim novelty in bare hold-then-release.
- **Equensworldline `WO2020094875A1` / `EP3671600A1`** — claims are not a threat
  (they require delivery-phase notification, which the amount-contingent pivot
  deleted). Its **description** is the strongest prior art found: freeze
  settlement, settle at a reduced amount, refund the remainder, plus a
  "tamper-proof history of the operations". **Volunteer this citation unprompted.**
- Surviving deltas across both full texts: `hash` 0, `signature` 0, `signed` 0,
  `audit` 0, `evidence` 0, `state machine` 0, `partial` 0.

The claim in `FINAL-RECOMMENDATION.md` §5 was narrowed accordingly. Its two
bolded clauses — *including refused transitions* and *verifiable by a party who
does not trust the orchestrator* — are load-bearing. Do not shorten that sentence.
