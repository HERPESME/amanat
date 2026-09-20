"""The registry as one self-contained page: what a hold does on each rail, measured and cited.

    python -m amanat.registry.page          # writes docs/registry/index.html

A comparison matrix (concepts down, rails across) where every cell says what the rail was found to
do and on what evidence, then a limits table, then every row in full. The matrix and every row are
rendered here, on the server, from `docs/registry/registry.json`: the page reads without
JavaScript, and loads nothing from anywhere — no font, no script, no stylesheet. A small script
adds filtering and jumps from a cell to its row.

Every string that came from the registry is escaped. A quote is somebody else's text.

The page is generated, never hand-edited, and CI fails on drift like the other files in
docs/registry. It says what a cell means as well as what it says: a sandbox is not an issuer, a
`?` is a refusal not a maybe, and a row whose source could not be re-read says so.
"""
from __future__ import annotations

import json
from collections import Counter
from html import escape
from pathlib import Path

from amanat.registry import export

OUT = export.OUT_DIR / "index.html"

GROUPS = [
    ("UPI and Indian PSPs", ["sbmd", "upi_otm", "setu_umap", "cashfree_preauth", "razorpay_auth_capture"]),
    ("Card networks and PSPs", ["visa_card_auth", "stripe_card_manual_capture", "adyen_card_auth"]),
    ("Agent-payment protocol: x402", ["x402", "x402_exact", "x402_upto_evm", "x402_upto_svm",
                                     "x402_auth_capture", "x402_batch_settlement"]),
]
SHORT = {
    "sbmd": "UPI Reserve Pay", "upi_otm": "UPI one-time mandate", "setu_umap": "Setu UMAP",
    "cashfree_preauth": "Cashfree pre-auth", "razorpay_auth_capture": "Razorpay capture",
    "visa_card_auth": "Visa", "stripe_card_manual_capture": "Stripe", "adyen_card_auth": "Adyen",
    "x402": "x402 (extensions)", "x402_exact": "x402 exact", "x402_upto_evm": "x402 upto · EVM",
    "x402_upto_svm": "x402 upto · Solana", "x402_auth_capture": "x402 auth-capture",
    "x402_batch_settlement": "x402 batch",
}
_REREAD = ("verified", "verified_fragments", "verified_by_copy")
_VERDICT = {"yes": "✓", "no": "✗", "unk": "?"}
_VERDICT_WORDS = {"yes": "supported", "no": "not supported", "unk": "unverified — refused, not assumed"}


def _e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def _anchor(rail_id: str, kind: str, name: str) -> str:
    return f"{rail_id}--{kind}--{name}".replace("_", "-")


def _verdict(row: dict) -> str:
    if row["tier"] == "unverified" or not row["usable_as_fact"]:
        return "unk"
    return "yes" if row["supported"] else "no"


def _tier_label(row: dict) -> str:
    return row["tier"].upper() + (f" · {row['environment']}" if row["environment"] else "")


def _verification_line(v: dict | None, tier: str = "") -> str:
    if v is None:
        if tier == "observed":
            return "An observation: checked by its probe (below) rather than against a page."
        if tier == "unverified":
            return "Unverified: nothing was quoted, so there is nothing to re-check."
        return "Not yet re-checked against its source."
    words = {
        "verified": "the quote is present verbatim", "verified_fragments": "every piece of the quote is present, in order",
        "verified_by_copy": "the document is byte-identical to the committed copy the quote was transcribed from",
        "not_found": "the source was read and the quote is NOT there",
        "document_changed": "the document at the source has changed",
        "unfetchable": "the source could not be read (" + v["detail"] + ")",
    }
    stale = "" if v["quote_current"] else " The quote was edited after this check."
    changes = "".join(f" {c['on']}: {c['from']} → {c['to']}." for c in v["changes"])
    return f"Re-checked {v['checked_on']}: {words.get(v['result'], v['result'])}.{stale}{changes}"


def _observation_line(o: dict | None) -> str:
    if o is None:
        return ""
    latest = o["latest"]
    verdict = "supported" if latest["supported"] else "not supported"
    agree = "agrees with this row" if latest["agrees"] else "DISAGREES with this row"
    runs = f"{o['runs']} conclusive run" + ("" if o["runs"] == 1 else "s")
    changes = "".join(f" {c['on']}: {c['from']} → {c['to']}." for c in o["changes"])
    return (f"Probe {o['probe_id']}: latest conclusive answer {verdict} ({latest['observed_on']}, "
            f"{latest['environment']}; {runs}) — {agree}.{changes}")


def _stats(doc: dict) -> dict:
    rows = [r for rail in doc["rails"] for r in (*rail["capabilities"], *rail["limits"])]
    caps = [c for rail in doc["rails"] for c in rail["capabilities"]]
    checks = Counter(r["verification"]["result"] for r in rows if r["verification"])
    return {"rails": len(doc["rails"]), "capabilities": len(caps),
            "limits": sum(len(r["limits"]) for r in doc["rails"]),
            "observed": sum(1 for r in rows if r["tier"] == "observed"),
            "unverified": sum(1 for r in caps if r["tier"] == "unverified"),
            "checks": checks,
            "checked": sum(checks.values()),                                 # rows with a source to check
            "reread": sum(checks[k] for k in _REREAD),                       # the quote was found there
            "unreadable": checks["unfetchable"]}                             # the source could not be read


def _matrix(doc: dict) -> str:
    rails = {r["rail_id"]: r for r in doc["rails"]}
    order = [rid for _, ids in GROUPS for rid in ids if rid in rails]
    head1 = "".join(f'<th scope="colgroup" colspan="{sum(1 for i in ids if i in rails)}" class="grp">{_e(g)}</th>'
                    for g, ids in GROUPS if any(i in rails for i in ids))
    head2 = "".join(f'<th scope="col" class="rail"><span title="{_e(rails[rid]["display_name"])}">{_e(SHORT[rid])}</span></th>'
                    for rid in order)
    body = []
    for concept in doc["concepts"]:
        cells = []
        for rid in order:
            row = next((c for c in rails[rid]["capabilities"] if c["name"] == concept["name"]), None)
            if row is None:
                cells.append('<td class="none" aria-label="no row">·</td>')
                continue
            v = _verdict(row)
            label = f'{SHORT[rid]} · {concept["name"].replace("_", " ")}: {_VERDICT_WORDS[v]}, {_tier_label(row).lower()}'
            cells.append(
                f'<td><button type="button" class="cell {v} t-{_e(row["tier"])}" data-target="{_e(_anchor(rid, "capability", row["name"]))}" '
                f'data-tier="{_e(row["tier"])}" data-group="{_e(next(g for g, ids in GROUPS if rid in ids))}" '
                f'aria-label="{_e(label)}" title="{_e(label)}">{_VERDICT[v]}</button></td>')
        body.append(f'<tr data-concept="{_e(concept["name"])}"><th scope="row"><span class="cname">{_e(concept["name"])}</span>'
                    f'<span class="cdef">{_e(concept["definition"])}</span></th>{"".join(cells)}</tr>')
    return ('<div class="scroll"><table class="matrix"><caption>What each rail was found to do, by question. '
            'A ✓ or ✗ rests on evidence usable as fact; a ? is a refusal, not a maybe; a dot means the rail has no row for it.</caption>'
            f'<thead><tr><th class="corner" rowspan="2" scope="col">Question</th>{head1}</tr><tr>{head2}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _limits(doc: dict) -> str:
    rows = []
    for rail in doc["rails"]:
        for lim in rail["limits"]:
            value = f'₹{lim["value"] / 100:,.0f}' if lim["unit"] == "paise" else f'{lim["value"]} {lim["unit"]}'
            rows.append(
                f'<tr><th scope="row">{_e(SHORT[rail["rail_id"]])}</th><td class="mono">{_e(lim["name"])}</td>'
                f'<td class="num">{_e(value)}</td><td><span class="chip t-{_e(lim["tier"])}">{_e(_tier_label(lim))}</span></td>'
                f'<td class="date">{_e(lim["obtained_on"] or "—")}</td>'
                f'<td><a href="#{_e(_anchor(rail["rail_id"], "limit", lim["name"]))}">{_e(lim["citation"])}</a></td></tr>')
    return ('<div class="scroll"><table class="plain"><caption>Numeric bounds a rail states. An unverified limit is still enforced: '
            'thin evidence means refuse more, never less.</caption><thead><tr><th scope="col">Rail</th><th scope="col">Limit</th>'
            '<th scope="col">Value</th><th scope="col">Evidence</th><th scope="col">Obtained</th><th scope="col">Source</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _row_details(rail: dict, kind: str, row: dict) -> str:
    if kind == "capability":
        v = _verdict(row)
        head = f'<span class="verdict {v}">{_VERDICT[v]} {_e(_VERDICT_WORDS[v])}</span>'
    else:
        value = f'₹{row["value"] / 100:,.0f}' if row["unit"] == "paise" else f'{row["value"]} {row["unit"]}'
        head = f'<span class="verdict lim">{_e(value)}</span>'
    quote = f'<blockquote>{_e(row["quote"])}</blockquote>' if row["quote"] else ""
    src = (f'<p class="src">{_e(row["citation"])}' + (f' · <a href="{_e(row["url"])}" rel="noopener noreferrer">{"API reference" if row["tier"] == "observed" else "source"}</a>' if row["url"] else "") + "</p>"
           if row["citation"] or row["url"] else "")
    meta = f'<p class="meta">{_e(_tier_label(row))} · obtained {_e(row["obtained_on"] or "not recorded")}</p>'
    notes = f'<p class="notes">{_e(row["notes"])}</p>' if row["notes"] else ""
    checked = f'<p class="check">{_e(_verification_line(row["verification"], row["tier"]))}</p>'
    probe = f'<p class="check">{_e(_observation_line(row["observation"]))}</p>' if row["observation"] else ""
    return (f'<details class="row" id="{_e(_anchor(rail["rail_id"], kind, row["name"]))}">'
            f'<summary><span class="mono">{_e(row["name"])}</span> {head} <span class="chip t-{_e(row["tier"])}">{_e(_tier_label(row))}</span></summary>'
            f'{quote}{src}{meta}{checked}{probe}{notes}</details>')


def _rails(doc: dict) -> str:
    out = []
    for rail in doc["rails"]:
        rows = [_row_details(rail, "capability", c) for c in rail["capabilities"]]
        rows += [_row_details(rail, "limit", l) for l in rail["limits"]]
        out.append(f'<section class="rail-sec" id="rail-{_e(rail["rail_id"].replace("_", "-"))}"><h3>{_e(rail["display_name"])} '
                   f'<span class="mono dim">{_e(rail["rail_id"])}</span></h3>{"".join(rows)}</section>')
    return "".join(out)


def _evidence(doc: dict, stats: dict) -> str:
    counts = stats["checks"]
    order = ["verified", "verified_fragments", "verified_by_copy", "not_found", "document_changed", "unfetchable"]
    line = ", ".join(f'{counts[k]} {k.replace("_", " ")}' for k in order if counts.get(k)) or "none yet"
    streams = "".join(
        f'<tr><td class="mono">{_e(s["stream"])}</td><td class="num">{s["length"]}</td><td class="mono hash">{_e(s["head"])}</td></tr>'
        for s in doc["stores"])
    sources = "".join(
        f'<tr><td class="mono">{_e(s["path"])}</td><td class="num">{s["bytes"]:,}</td><td class="mono hash">{_e(s["sha256"])}</td></tr>'
        for s in doc["sources"])
    return (f'<p>{stats["checked"]} rows cite a source that can be checked: {_e(line)}. A source that cannot be read is reported as such, '
            'never as a missing quote.</p>'
            '<h3>Evidence streams</h3><p>Append-only, each line carrying the hash of the last. A stream that no longer extends the '
            'checkpoint printed here has been rewritten.</p>'
            f'<div class="scroll"><table class="plain"><thead><tr><th scope="col">Stream</th><th scope="col">Lines</th><th scope="col">Head (SHA-256)</th></tr></thead><tbody>{streams}</tbody></table></div>'
            '<h3>Documents the quotes were transcribed from</h3><p>The regulator’s site refuses scripted clients, so these are committed, '
            'and anyone can download the circular in a browser and compare.</p>'
            f'<div class="scroll"><table class="plain"><thead><tr><th scope="col">File</th><th scope="col">Bytes</th><th scope="col">SHA-256</th></tr></thead><tbody>{sources}</tbody></table></div>')


_CSS = """
:root{color-scheme:light dark;--bg:#f4f6f9;--panel:#fff;--line:#dbe1ea;--ink:#141a23;--dim:#5a6575;--brand:#4338ca;
--ok:#0b7a55;--okbg:#e3f6ee;--bad:#b42323;--badbg:#fbe9e9;--unk:#8a5a00;--unkbg:#fdf3dc;--prim:#dfe7ff;--obs:#e9f7ee;--sec:#f1f3f7;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#0e131b;--panel:#161d28;--line:#2a3443;--ink:#e6ebf3;--dim:#93a0b3;--brand:#8b9bff;
--ok:#4cd6a0;--okbg:#0f2b22;--bad:#ff8a8a;--badbg:#341717;--unk:#f0c060;--unkbg:#33280d;--prim:#1d2a55;--obs:#12291d;--sec:#1b2432}}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans)}
main{max-width:1180px;margin:0 auto;padding:0 16px 64px}
header{padding:32px 0 8px}h1{font-size:28px;line-height:1.2;margin:0 0 8px}h2{font-size:20px;margin:40px 0 8px}h3{font-size:16px;margin:24px 0 8px}
p{margin:8px 0}.lede{max-width:70ch;color:var(--dim)}.mono{font-family:var(--mono);font-size:13px}.dim{color:var(--dim)}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.date{white-space:nowrap}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0;padding:0;list-style:none}
.stats li{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px}.stats b{font-size:18px;display:block}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--dim);margin:12px 0}
.chip{display:inline-block;font:12px var(--mono);border:1px solid var(--line);border-radius:999px;padding:1px 8px;white-space:nowrap}
.t-primary{background:var(--prim)}.t-observed{background:var(--obs)}.t-secondary{background:var(--sec)}.t-unverified{background:var(--unkbg)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
table{border-collapse:collapse;width:100%}caption{caption-side:top;text-align:left;font-size:13px;color:var(--dim);padding:10px 12px}
th,td{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}thead th{border-top:0;font-size:12px;font-weight:600}
.matrix .corner,.matrix tbody th{position:sticky;left:0;background:var(--panel);z-index:1;min-width:190px;max-width:230px}
.matrix .grp{text-align:center;border-left:1px solid var(--line);background:var(--sec)}.matrix .rail{font-size:11px;text-align:center;min-width:60px;writing-mode:horizontal-tb}
.matrix td{text-align:center;padding:3px}.matrix td.none{color:var(--line)}
.cname{display:block;font:600 12px var(--mono);overflow-wrap:anywhere}.cdef{display:block;font-size:11px;color:var(--dim);font-weight:400}
.cell{font:700 15px var(--sans);width:100%;min-width:38px;height:34px;border:1px solid transparent;border-radius:6px;cursor:pointer;color:var(--ink)}
.cell.yes{color:var(--ok);background:var(--okbg)}.cell.no{color:var(--bad);background:var(--badbg)}.cell.unk{color:var(--unk);background:var(--unkbg);background-image:repeating-linear-gradient(135deg,transparent 0 5px,rgba(138,90,0,.14) 5px 7px)}
.cell.t-observed{border-color:var(--ok);box-shadow:inset 0 0 0 1px var(--ok)}.cell.t-primary{border-color:var(--brand)}.cell:hover,.cell:focus-visible{outline:2px solid var(--brand);outline-offset:1px}
.cell[hidden]{visibility:hidden}.dimmed{opacity:.18}
.controls{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:12px 0;font-size:13px}.controls label{display:inline-flex;gap:6px;align-items:center}
.controls input[type=search]{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink)}
.rail-sec{margin:24px 0}details.row{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:6px 0;padding:0 12px}
details.row>summary{cursor:pointer;padding:9px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}details.row[open]{border-color:var(--brand)}
details.row:target{outline:2px solid var(--brand)}
.verdict{font-weight:700;font-size:13px}.verdict.yes{color:var(--ok)}.verdict.no{color:var(--bad)}.verdict.unk{color:var(--unk)}.verdict.lim{color:var(--brand)}
blockquote{margin:6px 0;padding:8px 12px;border-left:3px solid var(--brand);background:var(--sec);border-radius:0 6px 6px 0;font-size:14px;overflow-wrap:anywhere}
.src,.meta,.check,.notes{font-size:13px;color:var(--dim)}.check{color:var(--ink)}.notes{white-space:pre-line}
.hash{font-size:11px;overflow-wrap:anywhere}a{color:var(--brand)}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--dim)}
code{font:13px var(--mono);background:var(--sec);padding:1px 5px;border-radius:4px}
@media (max-width:640px){h1{font-size:23px}.matrix .corner,.matrix tbody th{min-width:120px;max-width:150px}.cname{font-size:11px}.cdef{display:none}}
"""

_JS = """
(function () {
  var cells = Array.prototype.slice.call(document.querySelectorAll('.cell'));
  function openRow(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.open = true;
    el.scrollIntoView({block: 'center'});
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  }
  cells.forEach(function (c) { c.addEventListener('click', function () { openRow(c.getAttribute('data-target')); }); });
  window.addEventListener('hashchange', function () { var el = document.getElementById(location.hash.slice(1)); if (el && el.tagName === 'DETAILS') el.open = true; });
  if (location.hash) { var t = document.getElementById(location.hash.slice(1)); if (t && t.tagName === 'DETAILS') t.open = true; }
  var tiers = Array.prototype.slice.call(document.querySelectorAll('input[data-tier]'));
  var groups = Array.prototype.slice.call(document.querySelectorAll('input[data-group]'));
  var search = document.getElementById('q');
  function apply() {
    var on = {}; tiers.forEach(function (i) { on[i.getAttribute('data-tier')] = i.checked; });
    var gon = {}; groups.forEach(function (i) { gon[i.getAttribute('data-group')] = i.checked; });
    cells.forEach(function (c) {
      var show = on[c.getAttribute('data-tier')] && gon[c.getAttribute('data-group')];
      c.classList.toggle('dimmed', !show);
    });
    var q = (search.value || '').trim().toLowerCase();
    Array.prototype.forEach.call(document.querySelectorAll('.matrix tbody tr'), function (tr) {
      tr.hidden = q && tr.getAttribute('data-concept').toLowerCase().indexOf(q) < 0;
    });
  }
  tiers.concat(groups).forEach(function (i) { i.addEventListener('change', apply); });
  if (search) search.addEventListener('input', apply);
})();
"""


def render(doc: dict | None = None) -> str:
    doc = doc if doc is not None else export.build()
    stats = _stats(doc)
    tier_boxes = "".join(f'<label><input type="checkbox" data-tier="{t}" checked> {t}</label>'
                         for t in ("primary", "observed", "secondary", "unverified"))
    group_boxes = "".join(f'<label><input type="checkbox" data-group="{_e(g)}" checked> {_e(g)}</label>' for g, _ in GROUPS)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Payment-rail semantics</title>
<meta name="description" content="What a hold, a partial capture, a release and a retry do on each payment rail — measured, dated and cited.">
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
<h1>What a payment hold does, rail by rail</h1>
<p class="lede">Can a capture be smaller than the hold? Is the rest released, and by whom? How long does a hold last? Does a retry act once?
Each cell below is one rail’s answer with the sentence it rests on, the date it was read or measured, and whether the source has been
re-read since. Where a source is silent the cell says so and the policy engine refuses, rather than assumes.</p>
<ul class="stats">
<li><b>{stats["rails"]}</b>rails</li><li><b>{stats["capabilities"]}</b>capabilities</li><li><b>{stats["limits"]}</b>limits</li>
<li><b>{stats["observed"]}</b>measured against an API</li><li><b>{stats["reread"]}</b>quotes re-read from their source</li>
<li><b>{stats["unreadable"]}</b>sources that could not be read</li>
<li><b>{stats["unverified"]}</b>unverified (refused)</li><li><b>{_e(doc["as_of"])}</b>as of</li></ul>
</header>

<h2 id="matrix">The matrix</h2>
<div class="legend"><span><b>✓</b> supported</span><span><b>✗</b> not supported</span><span><b>?</b> unverified: refused, never assumed</span>
<span><span class="chip t-primary">PRIMARY</span> a regulation or a protocol’s own spec</span><span><span class="chip t-observed">OBSERVED · sandbox</span> measured; a sandbox is not an issuer</span>
<span><span class="chip t-secondary">SECONDARY</span> a vendor describing itself</span></div>
<div class="controls" role="group" aria-label="Filters">{tier_boxes}<span class="dim">|</span>{group_boxes}
<input type="search" id="q" placeholder="Filter questions" aria-label="Filter questions"></div>
{_matrix(doc)}
<p class="dim">Select a cell to open its row below. Rows are also linked by address: the fragment is the rail, the kind and the name.</p>

<h2 id="limits">Limits</h2>
{_limits(doc)}

<h2 id="rows">Every row, in full</h2>
{_rails(doc)}

<h2 id="evidence">How to check this</h2>
{_evidence(doc, stats)}
<p>Everything on this page is generated from <a href="registry.json"><code>registry.json</code></a>, whose contract is
<a href="registry.schema.json"><code>registry.schema.json</code></a>. Re-check the quotes with <code>python -m amanat.registry.watch</code>;
re-measure the sandbox with <code>python -m amanat.probes run</code>. A row that names a probe fails the test suite if the rail answers differently.</p>

<footer>Apache-2.0. Source: <a href="https://github.com/HERPESME/amanat" rel="noopener noreferrer">github.com/HERPESME/amanat</a>.
A sandbox observation says what the vendor’s sandbox did on the day, not what an issuer would do in production.</footer>
</main>
<script>{_JS}</script>
</body>
</html>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(export.ROOT)}")


if __name__ == "__main__":
    main()
