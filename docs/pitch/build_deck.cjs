/* Amanat pitch deck — built to match the design system of caliper-deck.pdf:
 * cream + charcoal alternating grounds, terracotta accent, bold mono headlines
 * with an orange eyebrow bar, dark callout bands, big mono stat rows, hairline
 * tables, italic footnotes. 16:9, IBM Plex Mono + Lato.
 *
 *   NODE_PATH=$(npm root -g) node docs/pitch/build_deck.cjs
 */
const pptxgen = require("pptxgenjs");

const P = new pptxgen();
P.layout = "LAYOUT_16x9";              // 10 × 5.625 in
P.author = "Eeshan Singh Pokharia";
P.title = "Amanat — Razorpay AI Buildathon 2026";

/* ---- palette (sampled from the reference) ---- */
const CREAM = "F0EDE4", DARK = "1D1C1A";
const ORANGE = "C2622C", ORANGE_D = "CE7136";          // accent · accent-on-dark
const INK = "1D1C1A", MUTED = "6E6A63", FAINT = "7D7970";
const D_TEXT = "EDEAE2", D_MUTED = "AEAAA1";
const BORDER = "DED8CB", LINE = "D9D3C7";
const GREEN = "1E7B46", RED = "C03A2B";
const PALE_RED = "FBEAE5", PALE_GREEN = "E9F3EB";

const MONO = "IBM Plex Mono", SANS = "Lato";
const ML = 0.75, MR = 0.75, W = 10 - ML - MR;          // margins, content width

/* ---- helpers ---- */
function cream() { const s = P.addSlide(); s.background = { color: CREAM }; return s; }
function dark()  { const s = P.addSlide(); s.background = { color: DARK };  return s; }

function eyebrow(s, tag, headline, opts = {}) {
  const onDark = !!opts.dark;
  const hw = opts.w ?? W;
  s.addShape(P.shapes.RECTANGLE,
    { x: ML, y: 0.50, w: 0.045, h: 0.78, fill: { color: ORANGE } });
  s.addText(tag.toUpperCase(), {
    x: ML + 0.17, y: 0.46, w: hw, h: 0.3, margin: 0,
    fontFace: MONO, bold: true, fontSize: 10.5, color: onDark ? ORANGE_D : ORANGE,
    charSpacing: 3 });
  s.addText(headline, {
    x: ML + 0.17, y: 0.72, w: hw, h: 0.62, margin: 0, valign: "top",
    fontFace: MONO, bold: true, fontSize: 25, color: onDark ? D_TEXT : INK });
}

function footnote(s, text, onDark = false) {
  s.addText(text, { x: ML, y: 5.18, w: W, h: 0.3, margin: 0,
    fontFace: SANS, italic: true, fontSize: 9, color: onDark ? D_MUTED : FAINT });
}

function band(s, y, h, runs) {          // dark callout band on cream slides
  s.addShape(P.shapes.RECTANGLE, { x: ML, y, w: W, h, fill: { color: DARK } });
  s.addText(runs, { x: ML + 0.35, y: y + 0.08, w: W - 0.7, h: h - 0.16,
    margin: 0, valign: "middle", fontFace: SANS, fontSize: 11.5, color: D_TEXT });
}

function card(s, x, y, w, h, fill = "FFFFFF") {
  s.addShape(P.shapes.RECTANGLE,
    { x, y, w, h, fill: { color: fill }, line: { color: BORDER, width: 0.75 } });
}

function stat(s, x, y, big, caption, color = ORANGE, w = 2.55) {
  s.addText(big, { x, y, w, h: 0.62, margin: 0,
    fontFace: MONO, bold: true, fontSize: 34, color });
  s.addText(caption, { x, y: y + 0.66, w, h: 0.55, margin: 0, valign: "top",
    fontFace: SANS, fontSize: 10.5, color: MUTED });
}

/* box for diagrams: title mono + small sub */
function node(s, x, y, w, h, title, sub, opts = {}) {
  s.addShape(P.shapes.RECTANGLE, { x, y, w, h,
    fill: { color: opts.fill ?? "FFFFFF" },
    line: { color: opts.border ?? "3A3833", width: opts.lw ?? 1,
            dashType: opts.dash ? "dash" : "solid" } });
  const tcol = opts.tcol ?? INK, scol = opts.scol ?? MUTED;
  s.addText(title, { x: x + 0.07, y: y + 0.05, w: w - 0.14, h: 0.24, margin: 0,
    fontFace: MONO, bold: true, fontSize: 9.5, color: tcol });
  if (sub) s.addText(sub, { x: x + 0.07, y: y + 0.27, w: w - 0.14, h: h - 0.3,
    margin: 0, valign: "top", fontFace: SANS, fontSize: 7.6, color: scol });
}
function arrow(s, x1, y1, x2, y2, color = "6E6A63") {
  s.addShape(P.shapes.LINE, { x: x1, y: y1, w: x2 - x1, h: y2 - y1,
    line: { color, width: 1.2, endArrowType: "triangle" } });
}

/* ============================== 00 · TITLE ============================== */
{
  const s = dark();
  s.addShape(P.shapes.RECTANGLE, { x: 0, y: 0, w: 0.1, h: 5.625, fill: { color: ORANGE } });
  s.addText("AMANAT", { x: 0.85, y: 1.30, w: 8, h: 1.0, margin: 0,
    fontFace: MONO, bold: true, fontSize: 54, color: D_TEXT, charSpacing: 2 });
  s.addText("Amount-contingent settlement for agent-initiated payments", {
    x: 0.88, y: 2.32, w: 7.6, h: 0.4, margin: 0,
    fontFace: SANS, fontSize: 16, color: D_TEXT });
  s.addText("“We would rather refuse our own mechanism than ship a\ncapability we could not cite.”", {
    x: 0.88, y: 3.10, w: 5.8, h: 0.75, margin: 0, lineSpacing: 20,
    fontFace: SANS, italic: true, fontSize: 13, color: ORANGE_D });
  s.addText([
    { text: "Eeshan Singh Pokharia", options: { bold: true, breakLine: true } },
    { text: "eeshan.singh53@gmail.com  ·  github.com/HERPESME/amanat" },
  ], { x: 0.88, y: 4.08, w: 6.5, h: 0.5, margin: 0,
    fontFace: SANS, fontSize: 10.5, color: D_MUTED });
  s.addText("Razorpay AI Buildathon 2026  ·  Track 01 — AI Growth & Agentic Commerce", {
    x: 0.88, y: 4.82, w: 8, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 10, color: D_MUTED, charSpacing: 1 });
}

/* ============================ 01 · THE PROBLEM =========================== */
{
  const s = cream();
  eyebrow(s, "The problem", "An agent pays before the price exists.");
  s.addShape(P.shapes.RECTANGLE, { x: ML, y: 1.70, w: W, h: 0.62, fill: { color: DARK } });
  s.addText("“Book a cab to the airport — cap it at ₹1,000.”", {
    x: ML + 0.35, y: 1.70, w: W - 0.7, h: 0.62, margin: 0, valign: "middle",
    fontFace: MONO, bold: true, fontSize: 16, color: D_TEXT });
  stat(s, ML,        2.78, "₹620",   "the ceiling the agent must commit — before the meter runs", INK);
  stat(s, ML + 2.95, 2.78, "0 retries", "if the ceiling is low — the debit dies; NPCI grants none for this decline");
  stat(s, ML + 5.90, 2.78, "90 days",   "if it is high — the customer’s money can sit blocked for nothing");
  s.addText("A human watches the meter and pays what it says. An agent is asked to forecast — on rails built for known prices.", {
    x: ML, y: 4.42, w: W, h: 0.4, margin: 0,
    fontFace: SANS, italic: true, fontSize: 12.5, color: INK });
  footnote(s, "UPI Reserve Pay (Single Block Multiple Debit) holds funds in the customer’s own account · NPCI OC-228 / OC-200, read from the scanned circulars.");
}

/* ============================ 02 · THE MARKET ============================ */
{
  const s = cream();
  eyebrow(s, "What the market lacks", "Everyone proves permission. Then stops.");
  const rows = [
    ["Google AP2",                "a signed mandate — the agent may spend",     "authorization"],
    ["OpenAI / Stripe ACP",       "checkout of a known amount",                      "prepay · fixed price"],
    ["Coinbase x402",             "a machine paid per call",                         "prepay · no contingency"],
    ["Visa TAP · MC Agent Pay", "who the agent is",                             "identity only"],
  ];
  const cw = [2.35, 3.75, 2.4], x0 = ML, y0 = 1.86, rh = 0.50;
  ["STANDARD", "WHAT IT PROVES", "WHERE IT STOPS"].forEach((h, i) => {
    s.addText(h, { x: x0 + cw.slice(0, i).reduce((a, b) => a + b, 0) + 0.12,
      y: y0, w: cw[i] - 0.2, h: 0.3, margin: 0,
      fontFace: MONO, fontSize: 8.5, color: FAINT, charSpacing: 2 });
  });
  rows.forEach((r, ri) => {
    const y = y0 + 0.34 + ri * rh;
    s.addShape(P.shapes.LINE, { x: x0, y, w: cw[0] + cw[1] + cw[2], h: 0,
      line: { color: LINE, width: 0.75 } });
    r.forEach((c, ci) => {
      s.addText(c, { x: x0 + cw.slice(0, ci).reduce((a, b) => a + b, 0) + 0.12,
        y: y + 0.06, w: cw[ci] - 0.2, h: rh - 0.08, margin: 0, valign: "middle",
        fontFace: ci === 0 ? MONO : SANS, bold: ci === 0,
        fontSize: ci === 0 ? 10.5 : 11, color: ci === 0 ? INK : MUTED });
    });
  });
  const gy = y0 + 0.34 + rows.length * rh + 0.14;
  s.addShape(P.shapes.LINE, { x: x0, y: gy - 0.06, w: cw[0] + cw[1] + cw[2], h: 0,
    line: { color: LINE, width: 0.75 } });
  s.addText([
    { text: "GAP 1   ", options: { fontFace: MONO, bold: true, color: ORANGE, fontSize: 10.5 } },
    { text: "settle an amount that does not exist yet — ceiling now, actual later.",
      options: { fontFace: SANS, bold: true, color: INK, fontSize: 12 } },
  ], { x: x0, y: gy + 0.08, w: W, h: 0.32, margin: 0 });
  s.addText([
    { text: "GAP 2   ", options: { fontFace: MONO, bold: true, color: ORANGE, fontSize: 10.5 } },
    { text: "“my agent did it” — no post-transaction record to settle a dispute against.",
      options: { fontFace: SANS, bold: true, color: INK, fontSize: 12 } },
  ], { x: x0, y: gy + 0.44, w: W, h: 0.32, margin: 0 });
  footnote(s, "Gap 2 in the industry’s own words — the card networks and dispute processors flag the post-authorization layer as unaddressed.");
}

/* ============================= 03 · THE TRAP ============================= */
{
  const s = dark();
  eyebrow(s, "The trap", "Razorpay already runs agentic payments.", { dark: true });
  s.addText("Reserve Pay pilots are live — Zomato, Swiggy, Zepto.", {
    x: ML + 0.17, y: 1.55, w: W, h: 0.35, margin: 0,
    fontFace: SANS, fontSize: 14, color: D_MUTED });
  s.addShape(P.shapes.RECTANGLE, { x: ML + 0.17, y: 2.25, w: W - 0.34, h: 0.016,
    fill: { color: "44413C" } });
  s.addText("So we did not build another checkout.", {
    x: ML + 0.17, y: 2.55, w: W, h: 0.4, margin: 0,
    fontFace: SANS, bold: true, fontSize: 16, color: ORANGE_D });
  s.addText("Checkout is solved. What is not solved is settling an amount that does not exist yet — and proving afterwards, to someone who does not trust you, what the agent’s money actually did.", {
    x: ML + 0.17, y: 3.15, w: 8.2, h: 0.85, margin: 0, lineSpacing: 22,
    fontFace: SANS, fontSize: 13.5, color: D_TEXT });
  s.addText("The interesting problem is not moving the money. It is the receipt.", {
    x: ML + 0.17, y: 4.35, w: 8.2, h: 0.35, margin: 0,
    fontFace: SANS, italic: true, fontSize: 12, color: "C4C0B7" });
}

/* ============================ 04 · THE THESIS ============================ */
{
  const s = cream();
  eyebrow(s, "The thesis", "The unit of trust: a signed transition.");
  const cy = 1.62, ch = 3.1;
  card(s, ML, cy, 4.1, ch);
  s.addText("A transition carries", { x: ML + 0.28, y: cy + 0.2, w: 3.6, h: 0.28,
    margin: 0, fontFace: MONO, bold: true, fontSize: 11, color: MUTED });
  const items = [
    ["the proposal",       "the model’s ask, verbatim"],
    ["the verdict",        "deterministic — and it cites its reason"],
    ["the rail state",     "block · debit · release · refuse"],
    ["an Ed25519 signature","over the entry’s bytes"],
    ["a hash link",        "to the entry before it"],
  ];
  items.forEach(([t, d], i) => {
    const y = cy + 0.62 + i * 0.47;
    s.addShape(P.shapes.OVAL, { x: ML + 0.28, y: y + 0.06, w: 0.09, h: 0.09,
      fill: { color: ORANGE } });
    s.addText(t, { x: ML + 0.5, y, w: 1.75, h: 0.42, margin: 0, valign: "top",
      fontFace: SANS, bold: true, fontSize: 10.5, color: INK });
    s.addText(d, { x: ML + 2.2, y, w: 1.8, h: 0.45, margin: 0, valign: "top",
      fontFace: SANS, fontSize: 8.8, color: MUTED });
  });
  const rx = ML + 4.4;
  s.addShape(P.shapes.RECTANGLE, { x: rx, y: cy, w: 4.1, h: ch, fill: { color: DARK } });
  s.addText("Three consequences", { x: rx + 0.28, y: cy + 0.2, w: 3.6, h: 0.28,
    margin: 0, fontFace: MONO, bold: true, fontSize: 11, color: ORANGE_D });
  const cons = [
    ["Refusal is evidence.", "The times it said no are in the record, signed — the half ordinary logs leave out."],
    ["The model holds no authority.", "Gemini or Claude, swappable. A wild prompt yields refusals, not payments."],
    ["Anyone can verify.", "The packet re-checks its own hashes and signatures in your browser — zero trust in us."],
  ];
  cons.forEach(([t, d], i) => {
    const y = cy + 0.6 + i * 0.8;
    s.addText(t, { x: rx + 0.28, y, w: 3.55, h: 0.28, margin: 0,
      fontFace: SANS, bold: true, fontSize: 11.5, color: D_TEXT });
    s.addText(d, { x: rx + 0.28, y: y + 0.27, w: 3.55, h: 0.5, margin: 0,
      valign: "top", fontFace: SANS, fontSize: 9.3, color: D_MUTED });
  });
  footnote(s, "A settlement is an assembly of transitions that survived the policy engine. What was refused is never hidden.");
}

/* ============================ 05 · HOW IT WORKS ========================== */
{
  const s = cream();
  eyebrow(s, "How it works", "Propose. Dispose. Enforce. Record.");
  card(s, ML, 1.55, W, 2.55);
  /* row of nodes */
  node(s, 1.02, 2.08, 1.28, 0.78, "ENVELOPE", "budget · payee · expiry — the human’s grant");
  node(s, 2.72, 2.08, 1.28, 0.78, "MODEL", "proposes only — Gemini / Claude", { dash: true });
  node(s, 4.42, 2.00, 1.50, 0.94, "POLICY ENGINE", "deterministic · envelope, then rail legality",
    { fill: DARK, tcol: D_TEXT, scol: D_MUTED, border: DARK });
  node(s, 6.55, 1.74, 1.62, 0.62, "REFUSED", "with the circular quoted",
    { fill: PALE_RED, border: RED, tcol: RED });
  node(s, 6.55, 2.62, 1.62, 0.62, "RAIL", "block → debit → release");
  node(s, 8.40, 2.62, 0.78, 0.62, "SETTLED", "",
    { fill: PALE_GREEN, border: GREEN, tcol: GREEN });
  arrow(s, 2.30, 2.47, 2.72, 2.47);
  arrow(s, 4.00, 2.47, 4.42, 2.47);
  arrow(s, 5.92, 2.28, 6.55, 2.05);
  arrow(s, 5.92, 2.70, 6.55, 2.90);
  arrow(s, 8.17, 2.93, 8.40, 2.93);
  /* the chain strip */
  s.addShape(P.shapes.RECTANGLE, { x: 1.02, y: 3.52, w: 8.12, h: 0.40, fill: { color: DARK } });
  s.addText("EVIDENCE CHAIN — every proposal, verdict, transition and refusal · Ed25519 · hash-linked",
    { x: 1.14, y: 3.52, w: 7.9, h: 0.40, margin: 0, valign: "middle",
      fontFace: MONO, fontSize: 8.8, color: D_TEXT });
  arrow(s, 5.17, 2.94, 5.17, 3.52, "9A958C");
  arrow(s, 7.36, 3.24, 7.36, 3.52, "9A958C");
  s.addText("The LLM proposes. The policy engine disposes. The rail enforces.", {
    x: ML, y: 4.30, w: W, h: 0.34, margin: 0,
    fontFace: MONO, bold: true, fontSize: 14.5, color: INK });
  s.addText("No path reaches money without a verdict — pinned by test, then held over 600+ random action sequences per CI run.", {
    x: ML, y: 4.68, w: W, h: 0.3, margin: 0, fontFace: SANS, fontSize: 11, color: MUTED });
  footnote(s, "Invariants proven property-based, not by example: debited + released ≤ blocked · blocked ≤ budget · available ≥ 0. No counterexample found.");
}

/* ========================== 06 · THE GATE HELD =========================== */
{
  const s = cream();
  eyebrow(s, "Evidence discipline", "The gate held — against us.");
  const cy = 1.58, ch = 1.72;
  card(s, ML, cy, 4.1, ch);
  s.addText("For one day, it refused its own mechanism", {
    x: ML + 0.25, y: cy + 0.16, w: 3.65, h: 0.5, margin: 0,
    fontFace: MONO, bold: true, fontSize: 10.5, color: INK });
  s.addText("Partial debit rested on vendor docs. Tier UNVERIFIED — so permits() returned False, for us too. Absence of evidence is not permission.", {
    x: ML + 0.25, y: cy + 0.68, w: 3.65, h: 0.95, margin: 0, valign: "top",
      fontFace: SANS, fontSize: 10, color: MUTED });
  const rx = ML + 4.4;
  card(s, rx, cy, 4.1, ch);
  s.addText("Then we read the circular", {
    x: rx + 0.25, y: cy + 0.16, w: 3.65, h: 0.3, margin: 0,
    fontFace: MONO, bold: true, fontSize: 10.5, color: GREEN });
  s.addText("NPCI/UPI/OC-228 — image-only scans, read page by page. Partial debit holds by necessary implication; tier PRIMARY, quote attached. The gate opened.", {
    x: rx + 0.25, y: cy + 0.52, w: 3.65, h: 0.72, margin: 0, valign: "top",
    fontFace: SANS, fontSize: 10, color: MUTED });
  s.addText("“The current block limits (unutilised) are always checked before initiating a debit.”", {
    x: rx + 0.25, y: cy + 1.24, w: 3.65, h: 0.44, margin: 0, valign: "top",
    fontFace: SANS, italic: true, fontSize: 8.8, color: FAINT });
  band(s, 3.55, 1.05, [
    { text: "Five evidence tiers, one rule. ", options: { bold: true, color: ORANGE_D } },
    { text: "PRIMARY (circulars) · OBSERVED (the live API’s own response) · SECONDARY (PSP docs) are usable as fact. MARKETING and UNVERIFIED never are — 26 capabilities across 5 rails, every one cited or refused." },
  ]);
  footnote(s, "The day of refusal is preserved in the suite as a record, not deleted — the gate keying off evidence, not convenience, is the design.");
}

/* ============================ 07 · MEASURED ============================= */
{
  const s = cream();
  eyebrow(s, "Measured, not quoted", "A doc says should. A probe says did.");
  const rows = [
    ["Razorpay · partial capture", "HTTP 400", RED,
     "“Capture amount must be equal to the amount authorized” — the live rail and the docs agree word for word."],
    ["Setu UMAP · reachability", "NXDOMAIN", ORANGE,
     "credentials accepted (HTTP 200) — but uatapi.setu.co / api.setu.co resolve on neither Google nor Cloudflare DNS."],
    ["Razorpay · live settlement", "rfnd_TT5K…", GREEN,
     "capture ₹620 → refund ₹150 → merchant nets ₹470 — a real refund id, inside the signed chain."],
    ["NPCI OC-228 · block cap", "₹10,000", INK,
     "read from the scanned circular, enforced in code — a reserve above it is refused with the quote attached."],
  ];
  const y0 = 1.98, rh = 0.74, c1 = 2.55, c2 = 1.55;
  ["PROBE", "RESULT", "WHAT IT MEANS"].forEach((h, i) => {
    const xs = [ML + 0.05, ML + c1 + 0.05, ML + c1 + c2 + 0.05];
    s.addText(h, { x: xs[i], y: y0 - 0.3, w: 3, h: 0.26, margin: 0,
      fontFace: MONO, fontSize: 8.5, color: FAINT, charSpacing: 2 });
  });
  rows.forEach((r, i) => {
    const y = y0 + i * rh;
    s.addShape(P.shapes.LINE, { x: ML, y, w: W, h: 0, line: { color: LINE, width: 0.75 } });
    s.addText(r[0], { x: ML + 0.05, y: y + 0.08, w: c1 - 0.15, h: rh - 0.12,
      margin: 0, valign: "middle", fontFace: SANS, bold: true, fontSize: 10.5, color: INK });
    s.addText(r[1], { x: ML + c1 + 0.05, y: y + 0.08, w: c2 - 0.1, h: rh - 0.12,
      margin: 0, valign: "middle", fontFace: MONO, bold: true, fontSize: 12.5, color: r[2] });
    s.addText(r[3], { x: ML + c1 + c2 + 0.05, y: y + 0.08, w: W - c1 - c2 - 0.1,
      h: rh - 0.12, margin: 0, valign: "middle", fontFace: SANS, fontSize: 9.8, color: MUTED });
  });
  s.addShape(P.shapes.LINE, { x: ML, y: y0 + rows.length * rh, w: W, h: 0,
    line: { color: LINE, width: 0.75 } });
  footnote(s, "Reproduce: python -m amanat.rails.probe · python -m amanat.rails.settle — test mode throughout; no real money moved.");
}

/* ============================ 08 · THE DISPUTE =========================== */
{
  const s = cream();
  eyebrow(s, "The add-on", "“My agent did it.” Check the record.");
  const rows = [
    ["✓", GREEN,  "SUPPORTS MERCHANT",
     "“The ₹470 charged was authorized and within every bound” — cites the mandate at entry #0, the debit at entry #8."],
    ["●", ORANGE, "CHARGE NOT IN CHAIN",
     "“The disputed ₹5,000 was never charged — that attempt was refused at entry #2.” Refusals are evidence."],
    ["✗", RED,    "OUTSIDE EVIDENCE",
     "“The signed record cannot establish delivery.” The honest limit, stated — not papered over."],
  ];
  rows.forEach((r, i) => {
    const y = 1.80 + i * 0.74;
    s.addText(r[0], { x: ML, y, w: 0.3, h: 0.3, margin: 0,
      fontFace: MONO, bold: true, fontSize: 13, color: r[1] });
    s.addText(r[2], { x: ML + 0.38, y: y + 0.01, w: 2.5, h: 0.3, margin: 0,
      fontFace: MONO, bold: true, fontSize: 10.5, color: r[1] });
    s.addText(r[3], { x: ML + 3.0, y, w: W - 3.0, h: 0.66, margin: 0, valign: "top",
      fontFace: SANS, fontSize: 10.3, color: MUTED });
  });
  band(s, 4.12, 0.92, [
    { text: "An evidence finding — not an issuer decision. ", options: { bold: true, color: ORANGE_D } },
    { text: "It states what the signed record shows. No win-rate is claimed — win-rate is the issuer’s call, not the record’s. The export is a one-click representment packet: authorization + evidence + finding." },
  ]);
  footnote(s, "Adjudicated against a real AP2 Open Payment Mandate — vct mandate.payment.open.1, round-tripped through AP2’s own schema, not borrowed field names.");
}

/* ============================ 09 · THE NUMBERS =========================== */
{
  const s = cream();
  eyebrow(s, "The numbers", "Every figure matches the runner.");
  stat(s, ML,        1.72, "205",  "tests — all pass with zero credentials and zero network", INK, 2.0);
  stat(s, ML + 2.15, 1.72, "23",   "adversarial attacks — homoglyphs, overflows, sequence abuse — all refused", ORANGE, 2.0);
  stat(s, ML + 4.30, 1.72, "600+", "random action sequences per CI run — money invariants held after every step", ORANGE, 2.0);
  stat(s, ML + 6.45, 1.72, "26",   "rail capabilities across 5 rails — every one cited, or refused", ORANGE, 2.0);
  band(s, 3.42, 1.30, [
    { text: "The ceiling model, honestly. ", options: { bold: true, color: ORANGE_D } },
    { text: "Conformal quantile regression on 300k real NYC metered fares. Finding: the coverage guarantee is distribution-free but not shift-free — 18 of 20 configs missed nominal under a real temporal split; recency calibration narrows the gap ~10× (−3.55pp → +0.91pp). A random split would have passed — and lied about deployment." },
  ]);
  footnote(s, "No public Indian fare or COD dataset exists — the method transfers, the coefficients do not. Said in the repo, said here.");
}

/* ============================ 10 · RUNNING IT ============================ */
{
  const s = cream();
  eyebrow(s, "Running it", "Try to make it misbehave.");
  s.addShape(P.shapes.RECTANGLE, { x: ML, y: 1.62, w: W, h: 0.60, fill: { color: DARK } });
  s.addText("amanat-demo-699979063196.asia-south1.run.app", {
    x: ML + 0.35, y: 1.62, w: W - 0.7, h: 0.60, margin: 0, valign: "middle",
    fontFace: MONO, bold: true, fontSize: 15, color: D_TEXT });
  s.addText("Attack the envelope → watch the refusal, with its citation → dispute the charge → download the representment packet. Real governed core; bring-your-own-key live Gemini agent; the receipt re-verifies in your browser.", {
    x: ML, y: 2.38, w: W, h: 0.55, margin: 0,
    fontFace: SANS, fontSize: 11, color: MUTED });
  s.addShape(P.shapes.RECTANGLE, { x: ML, y: 3.22, w: W, h: 1.46, fill: { color: DARK } });
  const cmdcol = (x, lines) => s.addText(
    lines.flatMap(([c, d], i) => [
      { text: c, options: { color: D_TEXT, breakLine: true } },
      { text: "  " + d, options: { color: D_MUTED, breakLine: i === 0 } },
    ]),
    { x, y: 3.38, w: 3.95, h: 1.16, margin: 0, fontFace: MONO, fontSize: 10, lineSpacing: 15.5 });
  cmdcol(ML + 0.35, [
    ["python -m amanat.demo", "the seven-act walkthrough"],
    ["python -m amanat.compare", "two rails, side by side"]]);
  cmdcol(ML + 4.55, [
    ["python -m amanat.dispute.demo", "settle on AP2, then contest it"],
    ["python -m amanat.rails.settle", "a real run on Razorpay test APIs"]]);
  footnote(s, "Cloud Run, scales to zero — it costs nothing idle; open it once before judging to skip the cold start.");
}

/* ========================= 11 · LIMITS + CLOSE =========================== */
{
  const s = dark();
  eyebrow(s, "Stated before you ask", "What this is not.", { dark: true });
  const limits = [
    ["The SBMD demo runs on a simulator.", "Every semantic cites the circular it models; the real-rail run is gated on PSP enablement. The real Razorpay path is the different-but-honest capture-then-refund."],
    ["Consent binding is a placeholder.", "Adjudication proves the settlement conformed to the recorded grant — not that a human’s key signed it. AP2’s cnf slot is where that lands."],
    ["No risk model of ours.", "Razorpay ships RTO Shield. Risk enters through a seam, not a rewrite."],
    ["No win-rate, no “first”, no invented data.", "Negative findings are promoted, not buried. The adversarial review that killed our own v1 thesis ships in the repo."],
  ];
  limits.forEach(([t, d], i) => {
    const y = 1.62 + i * 0.62;
    s.addText(t, { x: ML + 0.17, y, w: 3.3, h: 0.56, margin: 0, valign: "top",
      fontFace: SANS, bold: true, fontSize: 11, color: D_TEXT });
    s.addText(d, { x: ML + 3.65, y, w: 5.0, h: 0.58, margin: 0, valign: "top",
      fontFace: SANS, fontSize: 9.5, color: D_MUTED });
  });
  s.addShape(P.shapes.RECTANGLE, { x: ML + 0.17, y: 4.14, w: W - 0.34, h: 0.016,
    fill: { color: "44413C" } });
  s.addText([
    { text: "Agents will transact before trust does.", options: { breakLine: true } },
    { text: "Amanat is the trust part." },
  ], { x: ML + 0.17, y: 4.32, w: 8.4, h: 0.72, margin: 0, lineSpacing: 23,
    fontFace: MONO, bold: true, fontSize: 16.5, color: ORANGE_D });
  s.addText("Eeshan Singh Pokharia  ·  eeshan.singh53@gmail.com  ·  github.com/HERPESME/amanat", {
    x: ML + 0.17, y: 5.12, w: 8.4, h: 0.3, margin: 0,
    fontFace: MONO, fontSize: 9.5, color: D_MUTED });
}

P.writeFile({ fileName: "docs/pitch/amanat-deck.pptx" })
  .then(() => console.log("wrote docs/pitch/amanat-deck.pptx"));
