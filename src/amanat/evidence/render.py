"""Render an evidence packet as a self-verifying HTML page.

    uv run --with cryptography python -m amanat.evidence.render            # standalone file
    uv run --with cryptography python -m amanat.evidence.render --artifact page.html

The claim the chain makes is that its artifact is *verifiable by a party who does
not trust the orchestrator*. A JSON blob does not make that tangible; this does.
The page embeds the packet and, in the viewer's own browser with no network,
recomputes every hash (WebCrypto SHA-256 over the same canonical bytes Python
hashed) and re-checks every Ed25519 signature against the embedded public key.
Edit any payload — they are editable — and it names the entry that no longer
verifies.

WebCrypto needs a secure context, so the standalone file only verifies fully when
served over https; the `--artifact` form is meant to be published and opened over
https, where it works.

Browser/Python hash parity holds because `chain._canonical` uses
`ensure_ascii=False`; `test_render` ports the page's `canonical()` to Python and
proves it reproduces every stored hash.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from amanat.evidence.chain import EvidenceChain

# --------------------------------------------------------------------------
# The verification logic, shared verbatim between both renderings. It reads and
# writes a fixed set of element ids — pl-{i}, chk-{i}, e-{i}, #banner,
# #banner-text, #pk, #count — so any DOM that provides them can reuse it.
# --------------------------------------------------------------------------
_VERIFY_JS = r"""
const PACKET = JSON.parse(document.getElementById('packet').textContent);
const GENESIS = PACKET.genesis_hash;

// Canonical JSON matching Python json.dumps(sort_keys=True,
// separators=(',',':'), ensure_ascii=False).
function canonical(v) {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  return '{' + Object.keys(v).sort()
    .map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
}
function hexToBytes(h) {
  const a = new Uint8Array(h.length / 2);
  for (let i = 0; i < a.length; i++) a[i] = parseInt(h.substr(i * 2, 2), 16);
  return a;
}
async function sha256hex(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}
let pubKey = null, ed25519 = true;
async function loadKey() {
  try {
    pubKey = await crypto.subtle.importKey('raw', hexToBytes(PACKET.public_key),
      { name: 'Ed25519' }, false, ['verify']);
  } catch (e) { ed25519 = false; }
}
function digestInput(e) {
  return canonical({ seq: e.seq, prev_hash: e.prev_hash, timestamp: e.timestamp,
    actor: e.actor, event_type: e.event_type, payload: e.payload });
}
async function verifyEntry(e, expectedPrev) {
  const hashOK = (await sha256hex(digestInput(e))) === e.hash;
  const linkOK = e.prev_hash === expectedPrev;
  let sigOK = true;
  if (ed25519 && pubKey)
    sigOK = await crypto.subtle.verify({ name: 'Ed25519' }, pubKey,
      hexToBytes(e.signature), hexToBytes(e.hash));
  return { hashOK, linkOK, sigOK, ok: hashOK && linkOK && sigOK };
}
function readEntriesFromDOM() {
  return PACKET.entries.map((e, i) => {
    const el = document.querySelector('#pl-' + i);
    if (el) { try { e.payload = JSON.parse(el.textContent); } catch (_) {} }
    return e;
  });
}
async function verifyAll() {
  const entries = readEntriesFromDOM();
  let prev = GENESIS, allOK = true, brokenAt = null;
  for (let i = 0; i < entries.length; i++) {
    const r = await verifyEntry(entries[i], prev);
    const chk = document.querySelector('#chk-' + i);
    chk.textContent = r.ok ? 'signed & linked'
      : (!r.hashOK ? 'content altered' : !r.linkOK ? 'broken link' : 'bad signature');
    chk.className = 'chk ' + (r.ok ? 'ok' : 'bad');
    document.querySelector('#e-' + i).classList.toggle('broken', !r.ok);
    if (!r.ok && brokenAt === null) { brokenAt = i; allOK = false; }
    prev = entries[i].hash;
  }
  const b = document.getElementById('banner'), t = document.getElementById('banner-text');
  b.className = 'banner ' + (allOK ? 'ok' : 'bad');
  t.textContent = allOK
    ? (ed25519 ? 'Verified — every entry signed, hashed and linked'
               : 'Hash-linked — signatures unchecked (this browser lacks Ed25519)')
    : 'Tampered — entry ' + brokenAt + ' no longer matches its signature';
}
function tamper() {
  const idx = PACKET.entries.findIndex(e => e.event_type === 'rail_transition');
  if (idx < 0) return;
  const el = document.querySelector('#pl-' + idx);
  const p = JSON.parse(el.textContent);
  if ('amount' in p) p.amount = 1; else p._tampered = true;
  el.textContent = JSON.stringify(p, null, 2);
  verifyAll();
}
"""

_INIT_JS = "(async () => { render(); await loadKey(); await verifyAll(); })();"


# --------------------------------------------------------------------------
# Standalone file — a plain, dependable page for local reference.
# --------------------------------------------------------------------------
_STANDALONE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amanat — dispute packet</title>
<style>
  body{margin:0;background:#0f1115;color:#e8eaed;font:15px/1.6 system-ui,sans-serif}
  .wrap{max-width:52rem;margin:0 auto;padding:2rem 1rem 4rem}
  .banner{padding:1rem;border-radius:8px;font-weight:600;margin:1rem 0}
  .banner.ok{background:rgba(63,185,80,.12);border:1px solid #3fb950;color:#3fb950}
  .banner.bad{background:rgba(248,81,73,.12);border:1px solid #f85149;color:#f85149}
  .entry{background:#171a21;border:1px solid #262b36;border-radius:8px;padding:1rem;margin:.5rem 0}
  .entry.broken{border-color:#f85149}
  .chk.ok{color:#3fb950}.chk.bad{color:#f85149}
  pre{background:#0b0d11;padding:.5rem;border-radius:6px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
  .hash{font-family:ui-monospace,monospace;font-size:11px;color:#9aa3b2;word-break:break-all}
  button{font:inherit;padding:.5rem 1rem;border-radius:6px;border:1px solid #262b36;background:#171a21;color:#e8eaed;cursor:pointer;margin-right:.5rem}
</style></head><body><div class="wrap">
  <h1>Evidence packet — <code>__SUBJECT__</code></h1>
  <div id="banner" class="banner"><span id="banner-text">Verifying…</span></div>
  <p>public key <code id="pk"></code> · <span id="count"></span></p>
  <div><button onclick="verifyAll()">Re-verify</button>
       <button onclick="tamper()">Tamper</button>
       <button onclick="location.reload()">Reset</button></div>
  <div id="entries"></div>
  <script id="packet" type="application/json">__PACKET__</script>
  <script>
  __VERIFY__
  function render(){
    document.getElementById('pk').textContent=PACKET.public_key.slice(0,16)+'…';
    document.getElementById('count').textContent=PACKET.entries.length+' entries';
    document.getElementById('entries').innerHTML=PACKET.entries.map((e,i)=>
      `<div class="entry" id="e-${i}"><b>#${e.seq}</b> ${e.event_type} · ${e.actor}
       <span class="chk" id="chk-${i}">…</span>
       <pre id="pl-${i}" contenteditable="true">${JSON.stringify(e.payload,null,2)}</pre>
       <div class="hash">${e.hash}</div></div>`).join('');
  }
  __INIT__
  </script></div></body></html>
"""


# --------------------------------------------------------------------------
# Artifact — the published, forensic-instrument treatment.
#
# Palette: cool slate neutrals (a considered blue-grey, not flat grey), an
# indigo brand for cryptographic structure, and green / red reserved strictly
# for verification state. Type: IBM Plex Sans for prose, IBM Plex Mono for the
# ledger material — a coherent technical superfamily, apt for hashes and chains.
# --------------------------------------------------------------------------
_ARTIFACT = """<title>Amanat Evidence Packet</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root{
  --bg:#eef1f5; --panel:#ffffff; --line:#dde2ea; --ink:#161b24; --dim:#5c6675;
  --brand:#4f46e5; --brand-soft:rgba(79,70,229,.10);
  --ok:#0e9f6e; --ok-soft:rgba(14,159,110,.10);
  --bad:#e02424; --bad-soft:rgba(224,36,36,.09);
  --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:'IBM Plex Sans',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
}
:root:not([data-theme="light"]){
  @media (prefers-color-scheme:dark){
    --bg:#0c0f16; --panel:#141925; --line:#232b3a; --ink:#e7ebf2; --dim:#8b97a9;
    --brand:#8b85f5; --brand-soft:rgba(139,133,245,.14);
    --ok:#34d399; --ok-soft:rgba(52,211,153,.13);
    --bad:#f87171; --bad-soft:rgba(248,113,113,.13);
  }
}
:root[data-theme="dark"]{
  --bg:#0c0f16; --panel:#141925; --line:#232b3a; --ink:#e7ebf2; --dim:#8b97a9;
  --brand:#8b85f5; --brand-soft:rgba(139,133,245,.14);
  --ok:#34d399; --ok-soft:rgba(52,211,153,.13);
  --bad:#f87171; --bad-soft:rgba(248,113,113,.13);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:60rem;margin:0 auto;padding:3rem 1.25rem 5rem}
.eyebrow{font-family:var(--mono);font-size:12px;font-weight:500;letter-spacing:.12em;
  text-transform:uppercase;color:var(--brand);margin:0 0 .6rem}
h1{font-size:2rem;font-weight:600;letter-spacing:-.02em;margin:0 0 .4rem;text-wrap:balance}
.lede{color:var(--dim);max-width:46ch;margin:0 0 2rem}
.sample{display:inline-block;font-family:var(--mono);font-size:11px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--dim);border:1px solid var(--line);
  border-radius:20px;padding:.15rem .7rem;margin-bottom:2rem}

/* the hero: the live verification verdict */
.banner{display:flex;align-items:center;gap:.85rem;padding:1.15rem 1.35rem;
  border-radius:14px;font-weight:600;font-size:1.05rem;margin-bottom:1.5rem;
  border:1px solid var(--line);background:var(--panel);transition:all .25s ease}
.banner .seal{width:2.4rem;height:2.4rem;flex:none;border-radius:50%;
  display:grid;place-items:center;border:2px solid currentColor}
.banner .seal svg{width:1.3rem;height:1.3rem}
.banner.ok{color:var(--ok);background:var(--ok-soft);border-color:var(--ok)}
.banner.bad{color:var(--bad);background:var(--bad-soft);border-color:var(--bad)}
.banner.pending{color:var(--dim)}

.meta{display:flex;flex-wrap:wrap;gap:.4rem 2rem;font-size:13px;color:var(--dim);
  margin-bottom:1.5rem}
.meta code{font-family:var(--mono);color:var(--ink)}
.controls{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:2.25rem}
button{font-family:var(--sans);font-size:13.5px;font-weight:500;padding:.6rem 1.1rem;
  border-radius:9px;border:1px solid var(--line);background:var(--panel);
  color:var(--ink);cursor:pointer;transition:border-color .15s,color .15s}
button:hover{border-color:var(--brand);color:var(--brand)}
button.warn:hover{border-color:var(--bad);color:var(--bad)}
button:focus-visible{outline:2px solid var(--brand);outline-offset:2px}

.rail{position:relative;padding-left:1.5rem}
.rail::before{content:"";position:absolute;left:5px;top:.4rem;bottom:.4rem;
  width:2px;background:var(--line)}
.entry{position:relative;background:var(--panel);border:1px solid var(--line);
  border-radius:12px;padding:1.05rem 1.2rem;margin-bottom:.7rem;transition:border-color .2s}
.entry::before{content:"";position:absolute;left:-1.5rem;top:1.35rem;width:12px;height:12px;
  border-radius:50%;background:var(--panel);border:2px solid var(--line);transform:translateX(-1px)}
.entry.rail-t::before{border-color:var(--brand)}
.entry.refusal::before{border-color:var(--bad)}
.entry.broken{border-color:var(--bad)}
.entry.broken::before{border-color:var(--bad);background:var(--bad)}
.head{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap}
.seq{font-family:var(--mono);font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
.chip{font-family:var(--mono);font-size:10.5px;font-weight:500;letter-spacing:.05em;
  text-transform:uppercase;padding:.15rem .55rem;border-radius:6px;
  border:1px solid var(--line);color:var(--dim)}
.chip.rail_transition{color:var(--brand);border-color:var(--brand);background:var(--brand-soft)}
.chip.refusal{color:var(--bad);border-color:var(--bad);background:var(--bad-soft)}
.chip.policy_decision,.chip.intent,.chip.envelope,.chip.proposal{color:var(--dim)}
.actor{font-size:12.5px;color:var(--dim)}
.chk{margin-left:auto;font-family:var(--mono);font-size:11.5px;font-weight:500;
  display:flex;align-items:center;gap:.35rem}
.chk::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.chk.ok{color:var(--ok)}.chk.bad{color:var(--bad)}
pre{font-family:var(--mono);font-size:12.5px;line-height:1.55;margin:.7rem 0 0;
  padding:.7rem .85rem;background:var(--bg);border:1px solid var(--line);border-radius:8px;
  overflow-x:auto;white-space:pre-wrap;word-break:break-word;color:var(--ink)}
pre:focus{outline:none;border-color:var(--brand)}
.hash{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:.55rem;
  word-break:break-all;letter-spacing:.01em}
.hash b{color:var(--dim);font-weight:500}
.note{color:var(--dim);font-size:13px;line-height:1.7;margin-top:2.5rem;
  border-top:1px solid var(--line);padding-top:1.5rem;max-width:60ch}
.note code{font-family:var(--mono);font-size:.92em;color:var(--ink)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <p class="eyebrow">Amanat · agent-payment evidence</p>
  <h1>This packet verifies itself</h1>
  <p class="lede">Every hash is recomputed and every signature re-checked in your
    browser — no network, and no trust in whoever produced it.</p>
  <span class="sample">Sample chain · demonstration data</span>

  <div id="banner" class="banner pending">
    <span class="seal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
      <path d="M20 6 9 17l-5-5"/></svg></span>
    <span id="banner-text">Verifying in your browser…</span>
  </div>

  <div class="meta">
    <span>public key <code id="pk"></code></span>
    <span id="count"></span>
    <span>signed with Ed25519 · hash-linked with SHA-256</span>
  </div>

  <div class="controls">
    <button onclick="verifyAll()">Re-verify</button>
    <button class="warn" onclick="tamper()">Tamper with a rail entry</button>
    <button onclick="render();verifyAll()">Reset</button>
  </div>

  <div class="rail" id="entries"></div>

  <p class="note">Each entry's hash is recomputed here from its contents and its
    Ed25519 signature checked against the embedded public key; each entry also
    links to the one before it, so no entry can be removed or reordered. The
    payloads below are editable — change one and re-verify: the entry turns red,
    because the signature no longer matches what you are now claiming it said.
    <b>Refusals are recorded too</b> — the times the system declined to move
    money are in the chain like any other entry, which is the half that ordinary
    authorization logs leave out.</p>

  <script id="packet" type="application/json">__PACKET__</script>
  <script>
  __VERIFY__
  function label(t){return t.replace(/_/g,' ');}
  function render(){
    document.getElementById('pk').textContent = PACKET.public_key.slice(0,20)+'…';
    document.getElementById('count').textContent = PACKET.entries.length+' entries';
    document.getElementById('entries').innerHTML = PACKET.entries.map((e,i)=>{
      const cls = e.event_type==='refusal' ? 'refusal'
        : e.event_type==='rail_transition' ? 'rail-t' : '';
      return `<div class="entry ${cls}" id="e-${i}">
        <div class="head">
          <span class="seq">#${e.seq}</span>
          <span class="chip ${e.event_type}">${label(e.event_type)}</span>
          <span class="actor">${e.actor}</span>
          <span class="chk" id="chk-${i}">checking</span>
        </div>
        <pre id="pl-${i}" contenteditable="true" spellcheck="false">${
          JSON.stringify(e.payload,null,2)}</pre>
        <div class="hash"><b>hash</b> ${e.hash}</div>
      </div>`;
    }).join('');
  }
  __INIT__
  </script>
</div>
"""


def _fill(template: str, packet: dict) -> str:
    embedded = json.dumps(packet).replace("</", "<\\/")
    return (template
            .replace("__SUBJECT__", str(packet.get("subject", "")))
            .replace("__PACKET__", embedded)
            .replace("__VERIFY__", _VERIFY_JS)
            .replace("__INIT__", _INIT_JS))


def render_html(packet: dict) -> str:
    """A standalone, self-contained HTML document."""
    return _fill(_STANDALONE, packet)


def render_artifact(packet: dict) -> str:
    """Artifact-body form: a <title>, styles and content, no outer document tags."""
    return _fill(_ARTIFACT, packet)


def _demo_packet() -> dict:
    """A small, varied chain so the page has refusals and rail transitions."""
    from datetime import datetime, timedelta, timezone

    from amanat.orchestrator.session import AgentSession
    from amanat.policy.envelope import Envelope
    from amanat.rails.simulator import SimulatedRail

    env = Envelope(subject="cab-7742", max_total=1_000_00, max_per_txn=800_00,
                   allowed_payees=["citycabs"],
                   expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                   intent_text="Book a cab to the airport, cap it at ₹1,000.")
    s = AgentSession(env, SimulatedRail("sbmd", customer_balance=5_000_00))
    s.reserve(5_000_00, "citycabs", "greedy over-block")     # refused: budget
    s.reserve(100_00, "randomcab", "wrong payee")            # refused: payee
    s.reserve(620_00, "citycabs", "p95 of the fare distribution")
    s.debit(470_00, "metered fare")
    s.release(reason="trip complete")
    return s.evidence_packet()


def main() -> int:
    args = list(sys.argv[1:])
    artifact = "--artifact" in args
    args = [a for a in args if a != "--artifact"]
    in_path = next((a for a in args if a.endswith(".json")), None)
    default = "dispute-packet.artifact.html" if artifact else "dispute-packet.html"
    out = Path(next((a for a in args if a.endswith(".html")), default))
    packet = json.loads(Path(in_path).read_text()) if in_path else _demo_packet()

    # Prove it verifies in Python before shipping a page that claims it does.
    EvidenceChain.verify_packet(packet)
    out.write_text(render_artifact(packet) if artifact else render_html(packet))
    print(f"  wrote {out}  ({len(packet['entries'])} entries, verified in Python)")
    if not artifact:
        print(f"  open over https to verify signatures: file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
