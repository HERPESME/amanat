# Amanat — Demo Video Script

**Runtime:** ~4:00 (a 3:00 trim is marked at the end)
**Format:** 1080p, 16:9, screen recording + voiceover
**Builder:** Eeshan Singh Pokharia · Razorpay AI Buildathon 2026 · Track 01
**Live URL:** https://amanat-demo-699979063196.asia-south1.run.app

The script is two columns per scene: **VOICEOVER** (read at ~150 wpm — the word
counts are timed to fit) and **ON SCREEN** (exactly what to show and do at that
moment). Timestamps are cumulative. Record the terminal and browser at the times
shown; don't type live — see *Pre-flight*.

---

## Pre-flight checklist (do before recording)

- [ ] **Warm the live URL** — open it once so Cloud Run is not cold-starting on camera.
- [ ] **`.env` has sandbox keys** — `CASHFREE_CLIENT_ID`, `CASHFREE_CLIENT_SECRET`. Dry-run
      `python -m amanat.rails.probe_cashfree` once so the sandbox is warm; the recorded run then returns in ~10s.
- [ ] **Terminal**: dark theme, font 16–18pt, window ~110 cols, clear scrollback before each command.
- [ ] **Browser**: 100% zoom, no extra tabs/bookmarks bar, use the deployed URL (https — so signatures verify).
- [ ] **Deck open** to the title + architecture slides (`docs/pitch/amanat-deck.pdf`) for the intro cutaways.
- [ ] **Two takes of the terminal commands** pre-run so you can screen-record clean output without typos/waits.
- [ ] Mic check; keep pace calm — the content is dense, let it breathe.

**Commands used, in order (copy-ready):**
```bash
uv run --with cryptography python -m amanat.demo
uv run --with httpx --with cryptography python -m amanat.rails.probe_cashfree
uv run --with cryptography python -m amanat.compare
uv run --with cryptography python -m amanat.dispute.demo
```

---

## SCENE 1 — HOOK  ·  0:00–0:18

> **VOICEOVER**
> "A human in a cab watches the meter, then pays what it says. They never commit to a
> number in advance. An AI agent has to — it must promise an amount *before that amount
> exists*. Set it too low, the payment dies. Too high, your money sits locked for nothing."

**ON SCREEN**
- Open on the **deck title slide** (AMANAT, black, orange bar) — hold 3s.
- Cut to the **problem slide** (₹620 · 0 retries · 90 days) as you say "too low / too high".
- Keep it slow and cinematic; this is the only non-product moment.

---

## SCENE 2 — WHAT IT IS  ·  0:18–0:42

> **VOICEOVER**
> "Amanat solves this three ways. It blocks a *ceiling* in your own bank account, debits
> the *actual* amount once it's known, and returns the difference — then signs a
> tamper-evident record of exactly what the money did. Block the ceiling, debit the actual,
> prove the rest."

**ON SCREEN**
- Cut to the **"What it does" flow** (the mermaid: Intent → Block ₹620 → Trip → Debit ₹470 →
  Release ₹150 → Signed evidence). Either the README render or a deck slide.
- Let each node highlight as you name it (block → debit → release → prove).

---

## SCENE 3 — ARCHITECTURE  ·  0:42–1:18

> **VOICEOVER**
> "The design is one sentence: the LLM proposes, a policy engine disposes, the rail enforces.
> The model is untrusted — it can only *ask*. Every request hits a deterministic engine with
> no model call in it, that either permits the action with a citation, or refuses it. And
> every proposal, every verdict, every refusal is signed into a hash-linked evidence chain.
> If the model is prompt-injected, the worst it can produce is a signed *refusal* — never a
> payment."

**ON SCREEN**
- Show the **architecture diagram** (deck slide 5, or README architecture mermaid):
  UNTRUSTED → GOVERNED → ENFORCED, with the Evidence Chain strip.
- As you say "proposes / disposes / enforces", trace the arrows left to right.
- On "signed refusal", highlight the REFUSED (red) node.

---

## SCENE 4 — TERMINAL: THE GOVERNED CORE  ·  1:18–1:58

> **VOICEOVER**
> "Here's the whole argument in one command — no API key, no network. Watch: the agent tries
> to over-block five thousand rupees, and it's refused, because your budget is a harder
> boundary than the model's confidence. Then a sensible ceiling is blocked, the real fare of
> four-seventy is debited, the rest released — and it all lands in a signed chain. Two
> hundred and thirty-three tests hold this, with zero credentials."

**ON SCREEN**
- Terminal, clean. Run:
  `uv run --with cryptography python -m amanat.demo`
- Let the seven-act walkthrough scroll. **Pause/zoom** on:
  - the **REFUSED** line (the ₹5,000 over-block),
  - the **block ₹620 → debit ₹470 → release ₹150** sequence,
  - the closing **evidence summary** (entries · rail transitions · refusals).
- Optional lower-third caption: `233 tests · 0 credentials · no network`.

---

## SCENE 5 — LIVE ON A REAL RAIL  ·  1:58–2:45  ★ the money shot

> **VOICEOVER**
> "But does the mechanism actually *run* on a real rail? This is live, right now, against
> Cashfree's UPI pre-authorization sandbox. It holds six-twenty. Then it debits four-seventy
> — a debit *smaller* than the hold — and the rail says HTTP two hundred. The hundred-and-fifty
> difference comes back on its own. That exact shape? Razorpay refuses it — HTTP four hundred,
> 'capture must equal the amount authorized.' Two real rails, measured: one refuses
> amount-contingent settlement, one accepts it. Not quoted from a doc — measured."

**ON SCREEN**
- Terminal. Run:
  `uv run --with httpx --with cryptography python -m amanat.rails.probe_cashfree`
- Hold on the three legs as they print:
  - **Leg 1** — hold ₹620 (order → simulate → status PAID),
  - **Leg 2** — **✓ ACCEPTED (HTTP 200) — captured ₹470** — *zoom in here, this is the headline*,
  - **Leg 3** — the ₹150 auto-released,
  - the closing contrast block: **✗ Razorpay HTTP 400  vs  ✓ Cashfree HTTP 200**.
- Lower-third caption on Leg 2: `Cashfree UPI pre-auth · ₹470 of ₹620 · HTTP 200 · live sandbox`.
- (Optional, 5s) cut to `python -m amanat.compare` showing the two rails' chains side by side.

---

## SCENE 6 — THE FRONTEND  ·  2:45–3:28

> **VOICEOVER**
> "All of this is on a public console — no stored keys, the real governed core. Set a budget,
> then try to break it. Over budget — refused, with the reason. Now the honest path: block,
> debit, release — settled. And here's the receipt. It re-computes every hash and re-checks
> every signature *in your browser*. Press Tamper — change one signed value — and it names the
> exact entry that no longer verifies. Then load the receipt from the real Cashfree run, and
> verify *that* the same way."

**ON SCREEN**
- Browser at the **live URL**. Manual tab is pre-filled (budget 1000, ceiling 620, actual 470).
- Click **"Over budget"** preset → point at the red **refused** decision + citation.
- Click **"Reset"**, then **"Run through the policy engine"** → the green **Settled** verdict + tiles
  (Blocked ₹620 · Debited ₹470 · Returned ₹150).
- Scroll to **"The signed receipt"** → the green **"Verified — every entry signed, hashed and linked"**.
- Click **Tamper** → banner flips red: **"Tampered — entry N no longer matches its signature"**. Let it land.
- Scroll up to the green **"Live rail · measured"** card → click **"Load the signed real-rail receipt"**
  → the green note appears and the **real Cashfree packet** renders and verifies. Open one
  **"inspect signed bytes"** to reveal `http_status: 200`, `cf_payment_id`, `PRE_AUTH|Transaction Success`.

---

## SCENE 7 — THE DISPUTE ADD-ON  ·  3:28–3:48

> **VOICEOVER**
> "One more thing the market has no answer for. Every agent-payment standard proves the agent
> was *permitted* and stops. When a cardholder says 'my agent did it,' there's no record to
> settle it against. Amanat adjudicates the signed chain against a real AP2 mandate and states,
> with cited entries, what the evidence shows — an evidence finding, never a claim about who wins."

**ON SCREEN** — *the cleanest, most reliable take is the terminal here; it scripts all three findings*
- Run: `uv run --with cryptography python -m amanat.dispute.demo`
- Hold on the three findings as they print:
  - **"I never authorized this"** → *SUPPORTS MERCHANT* — the ₹470 was authorized and within every
    bound, citing the AP2 mandate entry and the debit entry.
  - **"I was charged ₹5,000 I never approved"** → *CHARGE NOT IN CHAIN* — that attempt was **refused**;
    it never became a debit.
  - **"The cab never came"** → *OUTSIDE EVIDENCE* — the honest limit: the record can't establish delivery.
- End on the disclaimer line: **"This is an evidence finding, not an issuer decision."**
- *(Alternative, if staying in the browser: in Scene 6 first click the **"Over budget"** preset so the
  packet carries a ₹5,000 refusal, then in "Now dispute it" click **"I never authorized this"** → it
  cites the refused ₹5,000 as never charged; **"Goods never arrived"** → outside evidence.)*

---

## SCENE 8 — CLOSE  ·  3:48–4:05

> **VOICEOVER**
> "Two hundred and thirty-three tests, twenty-eight rail capabilities across five rails —
> every one cited or refused — and the core mechanism measured live on a real rail. Agents
> will transact before trust does. Amanat is the trust part."

**ON SCREEN**
- Cut to the deck **closing slide**: *"Agents will transact before trust does. Amanat is the trust part."*
- End card holds 4s with: **Eeshan Singh Pokharia · Track 01 · github.com/HERPESME/amanat · the live URL**.
- Fade out.

---

## The 3:00 trim (if a hard cap applies)

Keep Scenes 1, 3, 5, 6, 8. Specifically:
- **0:00–0:15** Hook (Scene 1, shortened).
- **0:15–0:45** Architecture (Scene 3).
- **0:45–1:35** Live real rail (Scene 5 — never cut this; it's the differentiator).
- **1:35–2:35** Frontend (Scene 6 — keep the Tamper moment and the real-rail receipt).
- **2:35–3:00** Close (Scene 8).
Drop the standalone governed-core terminal (Scene 4) and the dispute add-on (Scene 7);
both are implied by the frontend.

---

## Capture notes & assets

| Asset | Where | Use in |
|---|---|---|
| Title / problem / architecture / closing slides | `docs/pitch/amanat-deck.pdf` | Scenes 1, 2, 3, 8 |
| "What it does" flow diagram | README (renders on GitHub) or deck | Scene 2 |
| Governed-core walkthrough | `python -m amanat.demo` | Scene 4 |
| **Live Cashfree pre-auth run** | `python -m amanat.rails.probe_cashfree` | Scene 5 ★ |
| Two-rail chain contrast | `python -m amanat.compare` | Scene 5 (optional) |
| Live console (attack · verify · tamper · real-rail receipt) | the run.app URL | Scene 6 |
| Dispute adjudication | console "Now dispute it" or `python -m amanat.dispute.demo` | Scene 7 |

**Editing tips**
- Zoom/punch-in on the two numbers that carry the story: **HTTP 200** (Scene 5) and the red
  **Tampered** banner (Scene 6). Hold each an extra beat.
- Keep terminal output legible — if a command scrolls fast, slow it in the edit or re-run with the
  window taller.
- Lower-thirds only where marked; don't clutter. One caption per scene, max.
- Music: low, neutral, no drops. The content is the show.

**One-line summary to end the description box:**
*Amanat — an agent can spend your money but physically cannot spend it wrong, and it signs a receipt that proves it. Measured live on a real UPI rail.*
