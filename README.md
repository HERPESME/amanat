<div align="center">

# Amanat

**अमानत** — *a thing held in trust, to be returned to its owner*

### Amount-contingent settlement for agent-initiated payments

*Block a ceiling. Debit the actual. Prove what the money did.*

[![tests](https://img.shields.io/badge/tests-174%20passing-2ea44f?style=flat-square)](#testing)
[![python](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square)](#quick-start)
[![rails](https://img.shields.io/badge/rails-UPI%20SBMD%20%C2%B7%20Razorpay%20%C2%B7%20Setu-6c5ce7?style=flat-square)](#the-evidence-table)
[![credentials](https://img.shields.io/badge/core%20runs%20with-zero%20credentials-e17055?style=flat-square)](#testing)

### ▶ Try it live

**[Interactive demo](https://amanat-demo-699979063196.asia-south1.run.app)** — set a budget, run a settlement or attack it, watch the policy engine refuse and the signed chain verify itself.
&nbsp;·&nbsp;
**[Verify a signed packet](https://claude.ai/code/artifact/6edf0c30-6be8-4f60-961b-285b11af9995)** — recomputes its own hashes and signatures in your browser; press *Tamper* to watch it catch a change.

</div>

---

<table>
<tr><td width="140"><b>Track</b></td><td><b>01 — AI Growth &amp; Agentic Commerce</b></td></tr>
<tr><td><b>Builder</b></td><td>Eeshan Singh Pokharia</td></tr>
<tr><td><b>Email</b></td><td><a href="mailto:eeshan.singh53@gmail.com">eeshan.singh53@gmail.com</a></td></tr>
<tr><td><b>Event</b></td><td>Razorpay AI Buildathon 2026</td></tr>
</table>

> **Track 01's bar:** *every money action explainable, bounded and gated; show the audit
> trail and one failure handled gracefully.*
>
> This is a governance and auditability project. It is deliberately modest in scope, and
> the section it is proudest of is [What building it found](#what-building-it-found) —
> which is entirely negative results.

---

## The problem

A human in a cab **watches the meter and pays what it says.** They never commit to a
number in advance.

An AI agent cannot do that. To spend on your behalf it has to commit to an amount
**before that amount exists** — before the meter has run, before the grocery
substitutions are known, before the charger reports kWh.

So it must choose a ceiling, and both directions of error cost real money:

<div align="center">

| Ceiling too **low** | Ceiling too **high** |
|:---:|:---:|
| debit declined → **sale lost** | customer's money **locked up for nothing** |

</div>

Everything here exists to make that choice safely, and to prove afterwards what actually
happened.

---

## What it does

Instead of prepaying or paying afterwards, the agent blocks a ceiling in **your own bank
account**, draws only the real amount, and returns the difference.

```mermaid
flowchart LR
    A["🗣️ Intent<br/><i>book a cab,<br/>max ₹1,000</i>"] --> B["🔒 Block ₹620<br/><i>ceiling — funds stay<br/>in your account</i>"]
    B --> C["🚕 Trip runs<br/><i>the amount<br/>comes into existence</i>"]
    C --> D["💸 Debit ₹470<br/><i>the actual fare</i>"]
    D --> E["↩️ Release ₹150<br/><i>the difference</i>"]
    E --> F["📜 Signed evidence<br/><i>what the money did</i>"]

    style A fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    style B fill:#fff3e0,stroke:#f57c00,color:#e65100
    style D fill:#e8f5e9,stroke:#388e3c,color:#1b5e20
    style E fill:#e8f5e9,stroke:#388e3c,color:#1b5e20
    style F fill:#f3e5f5,stroke:#7b1fa2,color:#4a148c
```

This runs on **UPI Single Block Multiple Debit** (NPCI's SBMD, branded *Reserve Pay*).
The ₹620 never leaves your account — it is blocked there, visible in your own UPI app,
revocable by you at any moment.

### Same intent, two rails — and the chain shows the difference

The same ceiling settled on two rails nets the merchant the same ₹470. What differs is
what happens to *your* money, and the signed chain records which path actually ran:

| | UPI SBMD (Reserve Pay) | Razorpay (test-mode API, live) |
|---|---|---|
| **Chain** | `BLOCKED → DEBITED → RELEASED` | `AUTHORIZED → CAPTURED → REFUNDED` |
| **Leaves your account** | only ₹470, ever | full ₹620, until the refund settles |
| **How** | block ₹620, debit ₹470, release the rest | capture ₹620 (partial capture is refused), refund ₹150 |

Razorpay cannot block-and-partial-debit — `Capture amount must be equal to the amount
authorized`, measured from the live API. So the same outcome is reached the way that rail
*does* allow, and the honest cost (money out until the refund settles; MDR on the ceiling)
is written into the chain rather than hidden. Run it: `python -m amanat.compare`.

---

## Quick start

No credentials needed for the core. That is a design property, not an oversight.

```bash
git clone https://github.com/HERPESME/amanat && cd amanat

# The seven-act walkthrough — the whole argument in one command
uv run --with cryptography python -m amanat.demo

# 174 tests. No API key, no network.
uv run --with pytest --with cryptography --with numpy \
       --with scikit-learn --with pandas --with pyarrow pytest tests/ -q
```

```bash
# Same intent, two rails, two signed chains — side by side
uv run --with cryptography python -m amanat.compare

# A dispute packet that verifies itself in your browser (open the file it writes)
uv run --with cryptography python -m amanat.evidence.render
```

> **Hosted:** the same demo runs at
> **<https://amanat-demo-699979063196.asia-south1.run.app>** (Cloud Run, scales to zero).

<details>
<summary><b>Optional — live agent, the ML frontier, real-rail settlement</b></summary>

```bash
cp .env.example .env        # add GEMINI_API_KEY or ANTHROPIC_API_KEY

# Which credentials are present, and does each actually work?
uv run --with google-genai --with httpx --with cryptography python -m amanat.doctor

# The LLM agent against a governed session
uv run --with google-genai --with cryptography python -m amanat.orchestrator.cli

# Ceiling frontier on real NYC TLC fares (downloads ~100MB once)
uv run --with numpy --with pandas --with scikit-learn --with pyarrow \
       python -m amanat.ceiling.frontier

# Measure a live rail instead of quoting its docs
uv run --with httpx --with cryptography python -m amanat.rails.probe

# Settle a real Razorpay test-mode payment (capture then refund, real ids)
uv run --with httpx --with cryptography python -m amanat.rails.authorize
uv run --with httpx --with cryptography python -m amanat.rails.settle \
       --payment pay_XXXX --actual 47000
```

Docker:

```bash
docker compose run --rm demo      # walkthrough
docker compose run --rm tests     # containment suite
docker compose run --rm frontier  # ceiling frontier
```

</details>

---

## Architecture

The one sentence that carries the design:

<div align="center">

### The LLM proposes. The policy engine disposes. The rail enforces.

*Three layers of authority, each stricter than the last — and the outermost one is a bank.*

</div>

```mermaid
flowchart TB
    subgraph UNTRUSTED["🤖 UNTRUSTED — proposes only"]
        LLM["Claude / Gemini<br/><i>interchangeable, holds no authority</i>"]
    end

    subgraph GOVERNED["🛡️ GOVERNED — deterministic, no model call"]
        SESSION["AgentSession<br/><i>the single path to money</i>"]
        POLICY["Policy Engine<br/><i>envelope + rail legality</i>"]
        SEM["Rail Semantics<br/><i>what each rail is</i><br/><i>evidenced to permit</i>"]
    end

    subgraph ENFORCED["🏦 ENFORCED — by the bank"]
        RAIL["Payment Rail<br/><i>UPI SBMD · Razorpay · Setu</i>"]
    end

    CHAIN[("📜 Evidence Chain<br/><i>signed · hash-linked</i><br/><i>records refusals too</i>")]

    LLM -->|"proposes"| SESSION
    SESSION -->|"submits for verdict"| POLICY
    POLICY -->|"is this legal here?"| SEM
    SESSION -->|"approved actions only"| RAIL
    SESSION -.->|"every proposal,<br/>verdict + transition"| CHAIN

    style UNTRUSTED fill:#ffebee,stroke:#c62828
    style GOVERNED fill:#e8f5e9,stroke:#2e7d32
    style ENFORCED fill:#e3f2fd,stroke:#1565c0
    style CHAIN fill:#f3e5f5,stroke:#7b1fa2
```

If the model is prompt-injected, compromised, or simply wrong, the worst it can produce
is a **refusal record** — never a payment. That property is proven without a model in
`tests/test_session.py::test_a_refused_proposal_never_reaches_the_rail`, and stress-tested
by 31 adversarial tests.

📐 Full C4 diagrams: [`docs/architecture/`](docs/architecture/) ·
📋 Generated rail semantics: [`docs/RAIL_SEMANTICS.md`](docs/RAIL_SEMANTICS.md)

---

## One transaction, end to end

```mermaid
sequenceDiagram
    autonumber
    participant H as 👤 Human
    participant A as 🤖 Agent
    participant P as 🛡️ Policy
    participant R as 🏦 Rail
    participant E as 📜 Evidence

    H->>P: "Book a cab, max ₹600"
    P->>E: envelope recorded

    A->>P: reserve ₹900
    P-->>A: ❌ REFUSED — exceeds budget
    P->>E: refusal + reason

    A->>P: reserve ₹600
    P->>R: block ₹600
    R->>E: BLOCKED

    Note over R: trip runs — amount<br/>comes into existence

    A->>P: debit ₹470
    P->>R: partial debit
    R->>E: DEBITED

    A->>P: release remainder
    P->>R: return ₹130
    R->>E: RELEASED

    E-->>H: signed packet, verifies standalone
```

Step 3 is the interesting one. The model asked for a statistically sensible ceiling and
the policy engine said no, because **your budget is a harder boundary than the model's
confidence** — and the refusal is signed into the chain, so the artifact later shows not
only what happened but what was prevented.

---

## The two ideas worth explaining

### 1 · Every capability is cited, or it is refused

The code cannot say *"this rail supports X"* without a verbatim quote from the source.
There is no third state.

```python
Capability(
    name="partial_debit", supported=True,
    source_tier=SourceTier.PRIMARY,
    citation="NPCI/UPI/OC-228/2025-26",
    quote="The current block limits (unutilised) are always checked "
          "before initiating a debit...",
)
```

**Absence of evidence is not permission.** An `UNVERIFIED` capability returns `False`
from `permits()` even when `supported=True`. This is why the system spent a day
**refusing its own core mechanism** — partial debit was described only in vendor docs
until the NPCI circular was actually read.

| Tier | Meaning | Usable as fact? |
|---|---|---|
| `PRIMARY` | NPCI circular, RBI directive | ✅ |
| `OBSERVED` | measured against the live API — the quote *is* its response | ✅ |
| `SECONDARY` | PSP docs, for that PSP's own behaviour | ✅ |
| `MARKETING` | blog posts, comparison tables | ❌ |
| `UNVERIFIED` | believed, not confirmed | ❌ |

### 2 · The evidence chain goes below authorization

Every agent-payment evidence standard shipping today — **AP2's mandate chain, Visa
Trusted Agent Protocol, Mastercard Agentic Tokens, Pine Labs Grantex**, and the
offline-verifiable receipt lineage of US 12,671,588 — terminates at authorization: they
prove the agent was *permitted* to spend.

This extends the signed chain downward through the rail's own state transitions — block
placed, partial debit, release, revoke — **including the transitions the system refused
to make** — producing an artifact **verifiable by a party who does not trust the
orchestrator**.

That is an auditability contribution, not a payments one. It is modest, and saying so is
what makes it credible.

The artifact is real, not rhetorical: `python -m amanat.evidence.render` exports the chain
as a **self-contained HTML file that verifies itself in the browser** — recomputing every
hash with WebCrypto and re-checking every Ed25519 signature against the embedded key, with
no network and no trust in whoever produced it. Edit any payload and it names the entry
that no longer verifies.

> **▶ Verify one live in your browser:**
> **[claude.ai/code/artifact/6edf0c30…](https://claude.ai/code/artifact/6edf0c30-6be8-4f60-961b-285b11af9995)**
> — open it, then press **Tamper** and watch it catch the change.
> Source: [`docs/sample/dispute-packet.html`](docs/sample/dispute-packet.html).

---

## What building it found

The most useful outputs of this project are negative results. Each one is cited,
reproducible, and encoded in the capability table.

<table>
<tr>
<td width="30"><b>1</b></td>
<td><b>NPCI forbids delivery-contingent debit for goods</b><br/>
<i>"the delivery of goods and service should only be after the confirmation of successful
debit"</i> — OC-228. Post-delivery debit is carved out only for variable-amount services.
This killed the original thesis outright and forced the pivot to amount-contingency.</td>
</tr>
<tr>
<td><b>2</b></td>
<td><b>The rail never returns the change by itself</b><br/>
OC-200 keeps funds blocked <i>"till the time mandate is expired, revoked or the mandate
amount is exhausted"</i>. The word "release" appears in neither circular. Only 1 of 6
surveyed PSPs (Setu) can return the remainder without destroying the authorization.</td>
</tr>
<tr>
<td><b>3</b></td>
<td><b>Conformal coverage misses under temporal drift — 18 of 20 configurations</b><br/>
The guarantee is distribution-free but <b>not shift-free</b>. Training on January to
deploy in February breaks exchangeability. Calibrating on recent rather than random rows
narrows the gap ~10× (−3.55pp → +0.91pp) without closing it. A random split would have
shown a clean pass, and would have been a lie about deployment.</td>
</tr>
<tr>
<td><b>4</b></td>
<td><b>Setu's own docs name API hosts that do not exist</b><br/>
Credentials are valid and the token endpoint returns 200 — but <code>uatapi.setu.co</code>
and <code>api.setu.co</code> are <b>NXDOMAIN on both Google and Cloudflare</b> public
resolvers. Invisible until you hold credentials and try.</td>
</tr>
<tr>
<td><b>5</b></td>
<td><b>Razorpay refuses partial capture — measured, not quoted</b><br/>
<code>HTTP 400 — Capture amount must be equal to the amount authorized</code>, from the
live API. Reaching that state needed a browser: payment links auto-capture, and S2S
creation is not enabled on a self-serve account.</td>
</tr>
</table>

---

## The evidence table

26 capabilities across 5 rails. What each claim rests on:

| Rail | Capabilities | Evidence |
|---|---|---|
| **UPI SBMD** (Reserve Pay) | 16 | 12 `PRIMARY` · 3 `SECONDARY` · 1 `UNVERIFIED` |
| **Razorpay** manual capture | 3 | 1 `OBSERVED` · 2 `SECONDARY` |
| **Setu UMAP** | 3 | 2 `OBSERVED` · 1 `SECONDARY` |
| **UPI OTM** | 2 | 1 `SECONDARY` · 1 `UNVERIFIED` |
| **Cashfree** pre-auth | 2 | 2 `UNVERIFIED` — application pending |

Both NPCI circulars are committed in [`docs/sources/`](docs/sources/). They are
image-only scans; every quote was read from pages rendered at 220 dpi.

---

## Project structure

```
src/amanat/
├── rails/
│   ├── semantics.py    ← the capability table. Cited or refused.
│   ├── simulator.py    ← enforces the same table the policy engine reads
│   ├── razorpay.py     ← real adapter; refuses what the rail cannot honour
│   ├── settlement.py   ← capture-then-refund on Razorpay's real verbs
│   ├── probe.py        ← measures live rails instead of quoting them
│   └── authorize.py    ← browser harness for an authorized-but-uncaptured payment
├── policy/
│   ├── envelope.py     ← the human's grant. Frozen; widening leaves a trace.
│   └── engine.py       ← deterministic. No model call, ever.
├── evidence/
│   ├── chain.py        ← Ed25519 + SHA-256, append-only, records refusals
│   └── render.py       ← exports a chain as a browser-verifiable HTML packet
├── ceiling/            ← conformalized quantile regression on real NYC TLC fares
├── orchestrator/       ← governed core + swappable Claude/Gemini backends
├── compare.py          ← same intent, two rails, two signed chains
├── demo.py             ← the seven-act walkthrough
└── doctor.py           ← which credentials work, and which do not
```

---

## Testing

```bash
uv run --with pytest --with cryptography --with numpy \
       --with scikit-learn --with pandas --with pyarrow pytest tests/ -q
```

**174 tests, no credential and no network.** If proving the agent is bounded ever
required a live model, the agent would not be bounded.

| Suite | What it pins |
|---|---|
| `test_semantics.py` | every capability cited; unverified never permitted |
| `test_policy.py` | envelope + rail limits enforced, with citations |
| `test_evidence.py` | append-only, hash-linked, tamper detected by entry |
| `test_session.py` | no path to money skips policy |
| `test_adversarial.py` | 31 attacks — amounts, homoglyph payees, sequence, malformed calls |
| `test_ceiling.py` | conformal guarantee holds on *exchangeable* data |
| `test_backends.py` | a second LLM provider added no second route to money |
| `test_settlement.py` | capture-then-refund gated; double-settlement refused |
| `test_compare.py` | the two rails share no transition verbs |
| `test_render.py` | the browser's hashing reproduces Python's, byte for byte |

CI additionally regenerates `RAIL_SEMANTICS.md` and **fails on any diff**, so the prose
cannot claim more than the runtime honours.

---

## Honest limitations

Stated here rather than waiting to be asked.

- **Razorpay's `authorized` state has already debited the customer.** It is not a hold.
  Partial capture is unsupported there — measured, not assumed.
- **The simulator carries the demo.** Real SBMD access needs merchant activation. Every
  semantic it models cites the circular it comes from, and the real Razorpay adapter
  *refuses* rather than degrading quietly.
- **No public Indian COD-RTO or metered-fare dataset exists.** The ceiling model trains
  on NYC TLC fares. The method transfers; the coefficients do not.
- **Razorpay already ships** RTO Shield, risk-tiered COD fees, partial COD, and a live
  Reserve Pay agentic pilot (23 Feb 2026). Risk enters through a seam here rather than
  being re-implemented.
- **Equensworldline `WO2020094875A1` (2018)** describes freeze-settlement,
  settle-at-a-reduced-amount and refund-the-remainder, plus a *"tamper-proof history of
  the operations"*. Neither the settlement mechanism nor tamper-proof payment history is
  claimed as new here.
- **`sbmd.block_amount_reducible_without_revoke` is UNVERIFIED.** No circular or PSP doc
  states whether a modify may *lower* an amount, so it is refused.

---

## References

**Primary** — NPCI/UPI/OC-228/2025-26 · NPCI/UPI/OC.No.200/2024-25 (both in
[`docs/sources/`](docs/sources/)) · RBI e-Mandate Framework, 21 Apr 2026

**Method** — Romano, Patterson &amp; Candès, *Conformalized Quantile Regression*,
NeurIPS 2019, [arXiv:1905.03222](https://arxiv.org/abs/1905.03222) ·
[NYC TLC trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)

**Prior art conceded** — Google AP2 · Visa Trusted Agent Protocol · Mastercard Agentic
Tokens · Pine Labs Grantex · Equensworldline `WO2020094875A1` · US 12,671,588

---

<div align="center">

**Eeshan Singh Pokharia** · [eeshan.singh53@gmail.com](mailto:eeshan.singh53@gmail.com)

*Razorpay AI Buildathon 2026 — Track 01*

<sub><code>.claude/</code> holds the decision record — three adversarial reviewers, five
rounds, ~5,000 lines — and is deliberately not committed.</sub>

</div>
