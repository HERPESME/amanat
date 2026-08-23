"""Render an evidence packet as a self-verifying HTML file.

    uv run --with cryptography python -m amanat.evidence.render        # demo chain
    uv run ... python -m amanat.evidence.render packet.json out.html

The claim the evidence chain makes is that a dispute artifact is *verifiable by a
party who does not trust the orchestrator*. A JSON blob does not make that
tangible; this does. The exported page embeds the packet and, in the viewer's own
browser with no network and no trust in us, recomputes every hash (WebCrypto
SHA-256 over the same canonical bytes Python hashed) and re-checks every Ed25519
signature against the embedded public key.

If a single byte of any entry is altered, the page says so and names the entry.
A Tamper button demonstrates exactly that, live.

Browser-side hashing matches Python because `chain._canonical` uses
`ensure_ascii=False`, so `JSON.stringify` over the same keys, sorted, produces
identical bytes. The page self-tests the pristine packet on load — if that ever
shows BROKEN, the parity is off and it is visible immediately, not in front of an
audience.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from amanat.evidence.chain import EvidenceChain

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amanat — dispute packet</title>
<style>
  :root {
    --bg:#0f1115; --card:#171a21; --line:#262b36; --ink:#e8eaed; --dim:#9aa3b2;
    --ok:#3fb950; --bad:#f85149; --warn:#d29922; --accent:#7c72ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  .wrap { max-width:56rem; margin:0 auto; padding:2.5rem 1.25rem 4rem; }
  h1 { font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }
  .sub { color:var(--dim); margin:0 0 1.75rem; }
  .banner { padding:1rem 1.25rem; border-radius:10px; font-weight:600;
            display:flex; align-items:center; gap:.6rem; margin-bottom:1.5rem; }
  .banner.ok { background:rgba(63,185,80,.12); border:1px solid var(--ok); color:var(--ok); }
  .banner.bad { background:rgba(248,81,73,.12); border:1px solid var(--bad); color:var(--bad); }
  .dot { width:.7rem; height:.7rem; border-radius:50%; background:currentColor; }
  .meta { display:flex; flex-wrap:wrap; gap:.5rem 1.5rem; color:var(--dim);
          font-size:13px; margin-bottom:1.5rem; }
  .meta code { color:var(--ink); }
  .entry { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:1rem 1.15rem; margin-bottom:.75rem; position:relative; }
  .entry.refusal { border-color:var(--bad); }
  .entry.rail { border-color:var(--accent); }
  .row { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
  .seq { color:var(--dim); font-variant-numeric:tabular-nums; font-size:12px;
         min-width:1.5rem; }
  .tag { font-size:11px; text-transform:uppercase; letter-spacing:.04em;
         padding:.1rem .5rem; border-radius:20px; border:1px solid var(--line);
         color:var(--dim); }
  .tag.refusal { color:var(--bad); border-color:var(--bad); }
  .tag.rail_transition { color:var(--accent); border-color:var(--accent); }
  .actor { color:var(--dim); font-size:13px; }
  .chk { margin-left:auto; font-size:12px; font-weight:600; }
  .chk.ok { color:var(--ok); } .chk.bad { color:var(--bad); }
  pre { margin:.6rem 0 0; padding:.6rem .75rem; background:#0b0d11; border-radius:7px;
        overflow-x:auto; font-size:12.5px; color:#c3cad6; white-space:pre-wrap;
        word-break:break-word; }
  .hash { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px;
          color:var(--dim); margin-top:.5rem; word-break:break-all; }
  button { font:inherit; font-size:13px; padding:.5rem 1rem; border-radius:7px;
           border:1px solid var(--line); background:var(--card); color:var(--ink);
           cursor:pointer; }
  button:hover { border-color:var(--accent); }
  .bar { display:flex; gap:.6rem; margin-bottom:1.5rem; flex-wrap:wrap; }
  .note { color:var(--dim); font-size:12.5px; margin-top:2rem; }
  [contenteditable] { outline:none; }
  [contenteditable]:focus { box-shadow:0 0 0 1px var(--accent); border-radius:4px; }
</style></head>
<body><div class="wrap">
  <h1>Amanat — evidence packet</h1>
  <p class="sub">Subject <code>__SUBJECT__</code>. This page verifies itself in your
     browser, with no network and no trust in whoever produced it.</p>

  <div id="banner" class="banner"><span class="dot"></span><span id="banner-text">Verifying…</span></div>

  <div class="meta">
    <span>public key <code id="pk"></code></span>
    <span id="count"></span>
  </div>

  <div class="bar">
    <button onclick="verifyAll()">Re-verify</button>
    <button onclick="tamper()">Tamper with a rail entry</button>
    <button onclick="location.reload()">Reset</button>
  </div>

  <div id="entries"></div>

  <p class="note">Each entry's hash is recomputed here (SHA-256 over the same
     canonical bytes Python hashed) and its Ed25519 signature checked against the
     embedded public key. Edit any payload below — it is editable — and re-verify:
     the entry it belongs to turns red, because the signature no longer matches
     what you are now claiming it said. Refusals are evidence too, and appear in
     the chain like any other entry.</p>

  <script id="packet" type="application/json">__PACKET__</script>
  <script>
  const PACKET = JSON.parse(document.getElementById('packet').textContent);
  const GENESIS = PACKET.genesis_hash;

  // Canonical JSON matching Python json.dumps(sort_keys=True,
  // separators=(',',':'), ensure_ascii=False).
  function canonical(v) {
    if (v === null || typeof v !== 'object') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
    const keys = Object.keys(v).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
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
    const recomputed = await sha256hex(digestInput(e));
    const hashOK = recomputed === e.hash;
    const linkOK = e.prev_hash === expectedPrev;
    let sigOK = true;
    if (ed25519 && pubKey) {
      sigOK = await crypto.subtle.verify({ name: 'Ed25519' }, pubKey,
        hexToBytes(e.signature), hexToBytes(e.hash));
    }
    return { hashOK, linkOK, sigOK, ok: hashOK && linkOK && sigOK };
  }

  function readEntriesFromDOM() {
    // Re-read editable payloads so tampering is reflected.
    return PACKET.entries.map((e, i) => {
      const el = document.querySelector(`#pl-${i}`);
      if (el) { try { e.payload = JSON.parse(el.textContent); } catch (_) {} }
      return e;
    });
  }

  async function verifyAll() {
    const entries = readEntriesFromDOM();
    let prev = GENESIS, allOK = true, brokenAt = null;
    for (let i = 0; i < entries.length; i++) {
      const r = await verifyEntry(entries[i], prev);
      const chk = document.querySelector(`#chk-${i}`);
      chk.textContent = r.ok ? '✓ signed & linked'
        : (!r.hashOK ? '✗ content altered' : !r.linkOK ? '✗ broken link' : '✗ bad signature');
      chk.className = 'chk ' + (r.ok ? 'ok' : 'bad');
      document.querySelector(`#e-${i}`).style.opacity = r.ok ? '1' : '1';
      if (!r.ok && brokenAt === null) { brokenAt = i; allOK = false; }
      prev = entries[i].hash;
    }
    const b = document.getElementById('banner'), t = document.getElementById('banner-text');
    if (allOK) { b.className = 'banner ok';
      t.textContent = ed25519 ? 'VERIFIED — all entries signed, hashed and linked'
        : 'HASH-LINKED — signatures not checked (this browser lacks Ed25519)'; }
    else { b.className = 'banner bad';
      t.textContent = `TAMPERED — entry ${brokenAt} does not verify`; }
  }

  function tamper() {
    const idx = PACKET.entries.findIndex(e => e.event_type === 'rail_transition');
    if (idx < 0) return;
    const el = document.querySelector(`#pl-${idx}`);
    const p = JSON.parse(el.textContent);
    if ('amount' in p) p.amount = 1; else p._tampered = true;
    el.textContent = JSON.stringify(p, null, 2);
    verifyAll();
  }

  function render() {
    document.getElementById('pk').textContent =
      PACKET.public_key.slice(0, 16) + '…';
    document.getElementById('count').textContent =
      PACKET.entries.length + ' entries';
    const host = document.getElementById('entries');
    host.innerHTML = PACKET.entries.map((e, i) => {
      const cls = e.event_type === 'refusal' ? 'refusal'
        : e.event_type === 'rail_transition' ? 'rail' : '';
      return `<div class="entry ${cls}" id="e-${i}">
        <div class="row">
          <span class="seq">#${e.seq}</span>
          <span class="tag ${e.event_type}">${e.event_type.replace(/_/g,' ')}</span>
          <span class="actor">${e.actor}</span>
          <span class="chk" id="chk-${i}">…</span>
        </div>
        <pre id="pl-${i}" contenteditable="true">${
          JSON.stringify(e.payload, null, 2)}</pre>
        <div class="hash">hash ${e.hash}</div>
      </div>`;
    }).join('');
  }

  (async () => { render(); await loadKey(); await verifyAll(); })();
  </script>
</div></body></html>
"""


def render_html(packet: dict) -> str:
    embedded = json.dumps(packet).replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__SUBJECT__", str(packet.get("subject", "")))
            .replace("__PACKET__", embedded))


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
    # A .json arg is an input packet; a .html arg is where to write. Either may
    # be omitted — with neither, render the built-in demo chain.
    in_path = next((a for a in args if a.endswith(".json")), None)
    out = Path(next((a for a in args if a.endswith(".html")), "dispute-packet.html"))
    packet = json.loads(Path(in_path).read_text()) if in_path else _demo_packet()

    # Prove it verifies in Python before shipping an HTML page that claims it does.
    EvidenceChain.verify_packet(packet)
    out.write_text(render_html(packet))
    print(f"  wrote {out}  ({len(packet['entries'])} entries, verified in Python)")
    print(f"  open it: file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
