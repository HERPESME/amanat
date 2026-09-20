"""The quote watcher: re-check every cited quote against the page it cites.

    python -m amanat.registry.watch                 # fetch every source, append one run to the store
    python -m amanat.registry.watch --dry-run       # print the results, record nothing
    python -m amanat.registry.watch --rail cashfree_preauth

The registry's rule is that a row carries text actually read. A row stays honest only while
the source keeps saying it, so this fetches each row's `url` and looks for the `quote`,
verbatim (after normalising whitespace, entities, typographic quotes and dashes on both sides;
case and words still matter). A quote that marks its gaps with `…` is accepted as ordered
fragments, each of which must appear in order.

An agent — or a person — may *propose* a row. This check, not a model, admits it: model as
extractor, never enforcer. It is also the "Dependabot for APIs" half of the registry: when a
vendor rewrites a page, the quote stops being found and the next run says so.

Results, per row: `verified`, `verified_fragments`, `verified_by_copy` (an image-only PDF whose
bytes equal a committed copy the quote was transcribed from), `not_found`, `document_changed`
(a PDF whose bytes match no committed copy), `unfetchable` (HTTP error, timeout, no readable
text — never conflated with a missing quote) and `skipped` (OBSERVED rows are checked by
probes; UNVERIFIED rows and rows without a url or quote have nothing to check).

The network is used only here, on demand or on a schedule; the test suite never touches it.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from amanat.registry import store

TOOL = "amanat.registry.watch/1"
STREAM = "watch"
USER_AGENT = "amanat-registry-watch/1 (+https://github.com/HERPESME/amanat)"

# Vendors localise their documentation: docs.stripe.com serves "authorise" to one reader and
# "authorize" to another, by Accept-Language and by where the request comes from. The watcher asks
# for one locale, so a quote is checked against one rendering wherever the job runs, and every
# quote in the registry is transcribed in it. A quote checked from another locale can fail
# legitimately; that is a property of the source, not a change to it.
LOCALE = "en-US,en;q=0.9"
MIN_TEXT = 400            # below this a page has no readable text (a JavaScript shell)
MIN_FRAGMENT = 8          # a fragment shorter than this could match anything
MAX_BYTES = 8_000_000
ROOT = Path(__file__).resolve().parents[3]
SOURCES = ROOT / "docs" / "sources"

VERIFIED = "verified"
VERIFIED_FRAGMENTS = "verified_fragments"
VERIFIED_BY_COPY = "verified_by_copy"
NOT_FOUND = "not_found"
DOCUMENT_CHANGED = "document_changed"
UNFETCHABLE = "unfetchable"
SKIPPED = "skipped"
CURRENT = (VERIFIED, VERIFIED_FRAGMENTS, VERIFIED_BY_COPY)


class FetchError(Exception):
    """The source could not be fetched (transport failure, oversize response)."""


@dataclass(frozen=True)
class Fetched:
    status: int
    content_type: str
    body: bytes
    final_url: str


@dataclass(frozen=True)
class Row:
    rail_id: str
    kind: str          # "capability" | "limit"
    name: str
    tier: str
    url: str
    quote: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rail_id, self.kind, self.name)


Fetcher = Callable[[str], Fetched]


# ------------------------------------------------------------------------------ text

_TYPO = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", "​": "", "­": "",
})
_GAP = re.compile(r"\s*(?:…|\.{3})\s*")


def normalise(text: str) -> str:
    """The comparison form: the same words, whatever the typography."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPO)
    return re.sub(r"\s+", " ", text).strip()


def quote_hash(quote: str) -> str:
    """Hash of the quote as it is compared, so a later edit of a row's quote is detectable."""
    return hashlib.sha256(normalise(quote).encode("utf-8")).hexdigest()


_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "aside", "header", "footer", "nav", "main", "li", "ul", "ol",
    "dl", "dt", "dd", "br", "hr", "tr", "td", "th", "table", "thead", "tbody", "h1", "h2", "h3",
    "h4", "h5", "h6", "pre", "blockquote", "figure", "figcaption", "details", "summary", "form",
    "fieldset", "option", "caption",
}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return "".join(parser.parts)


def markdown_to_text(md: str) -> str:
    """Just enough to read prose out of Markdown: links, emphasis, code ticks, headings, lists."""
    md = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"`{1,3}", "", md)
    md = re.sub(r"(\*\*|__)", "", md)
    md = re.sub(r"(?m)^\s{0,3}(#{1,6}|[-*+]|\d+\.)\s+", "", md)
    return md.replace("|", " ")


def pdf_text(data: bytes) -> str | None:
    """The text layer of a PDF via `pdftotext`, or None if the tool is not installed."""
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    try:
        # Reading order, not `-layout`: layout mode keeps physical columns, which interleaves the
        # lines of a two-column page and splits sentences that the quote holds whole.
        out = subprocess.run([exe, "-", "-"], input=data, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None


# --------------------------------------------------------------------------- matching

def match_quote(page_text: str, quote: str) -> str:
    """`verified`, `verified_fragments` or `not_found` — case-sensitive, words matter."""
    haystack = normalise(page_text)
    whole = normalise(quote)
    if whole and whole in haystack:
        return VERIFIED
    fragments = [normalise(f) for f in _GAP.split(quote) if f.strip()]
    if len(fragments) < 2 or any(len(f) < MIN_FRAGMENT for f in fragments):
        return NOT_FOUND
    pos = 0
    for frag in fragments:
        i = haystack.find(frag, pos)
        if i < 0:
            return NOT_FOUND
        pos = i + len(frag)
    return VERIFIED_FRAGMENTS


# ------------------------------------------------------------------------------ rows

def rows() -> list[Row]:
    from amanat.rails.semantics import RAILS

    out = []
    for rail in RAILS.values():
        for kind, group in (("capability", rail.capabilities), ("limit", rail.limits)):
            for r in group.values():
                out.append(Row(rail.rail_id, kind, r.name, r.source_tier.value, r.url, r.quote))
    return out


def committed_copies() -> dict[str, str]:
    """sha256 -> repo path of every source document committed under docs/sources."""
    if not SOURCES.exists():
        return {}
    return {hashlib.sha256(p.read_bytes()).hexdigest(): str(p.relative_to(ROOT))
            for p in sorted(SOURCES.glob("*.pdf"))}


def _result(r: Row, result: str, detail: str, *, http_status=None, content_sha256=None) -> dict:
    return {
        "rail_id": r.rail_id, "kind": r.kind, "name": r.name, "url": r.url,
        "result": result, "detail": detail, "http_status": http_status,
        "content_sha256": content_sha256,
        "quote_sha256": quote_hash(r.quote) if r.quote else None,
    }


def _skip_reason(r: Row) -> str | None:
    if r.tier == "observed":
        return "observed: what was measured is checked by a probe, not by a page"
    if r.tier == "unverified" or not r.quote.strip():
        return "no quote to check"
    if not r.url:
        return "no url to check against"
    return None


def _kind(f: Fetched, url: str) -> str:
    ctype = f.content_type.lower()
    if "pdf" in ctype or f.body[:5] == b"%PDF-":
        return "pdf"
    if "markdown" in ctype or url.lower().endswith((".md", ".txt")) or ctype.startswith("text/plain"):
        return "markdown"
    return "html"


def _judge(r: Row, f: Fetched, copies: dict[str, str], to_text) -> dict:
    digest = hashlib.sha256(f.body).hexdigest()
    quote = r.quote

    def out(result, detail):
        return _result(r, result, detail, http_status=f.status, content_sha256=digest)

    if f.status != 200:
        return out(UNFETCHABLE, f"HTTP {f.status}")
    kind = _kind(f, r.url)
    if kind == "pdf":
        text = to_text(f.body)
        if text is None or len(normalise(text)) < MIN_TEXT:
            if digest in copies:
                return out(VERIFIED_BY_COPY, f"the document is byte-identical to the committed copy "
                                              f"{copies[digest]}, from which the quote was transcribed")
            if text is None:
                return out(UNFETCHABLE, "the PDF cannot be read here: pdftotext is not installed")
            if copies:
                return out(DOCUMENT_CHANGED, "the PDF has no text layer and matches no committed copy: "
                                             "the document at this URL has changed")
            return out(UNFETCHABLE, "the PDF has no text layer and there is no committed copy to compare")
    elif kind == "markdown":
        text = markdown_to_text(f.body.decode("utf-8", errors="replace"))
        quote = markdown_to_text(r.quote)        # marks are not part of the sentence, on either side
    else:
        text = html_to_text(f.body.decode("utf-8", errors="replace"))
    size = len(normalise(text))
    if size < MIN_TEXT:
        return out(UNFETCHABLE, f"the page has no readable text ({size} characters): "
                                "probably rendered by JavaScript")
    found = match_quote(text, quote)
    if found == NOT_FOUND:
        return out(NOT_FOUND, f"fetched (HTTP 200, {size:,} characters of text); the quote is not present")
    note = "the quote is present verbatim" if found == VERIFIED else "every fragment is present, in order"
    return out(found, note)


def run(fetch: Fetcher, *, rows: list[Row] | None = None, copies: dict[str, str] | None = None,
        pdf_to_text=pdf_text) -> list[dict]:
    """Check each row once. Never raises for a source that cannot be read."""
    rows_ = globals()["rows"]() if rows is None else rows
    copies = committed_copies() if copies is None else copies
    cache: dict[str, Fetched | str] = {}
    results = []
    for r in rows_:
        why = _skip_reason(r)
        if why:
            results.append(_result(r, SKIPPED, why))
            continue
        if r.url not in cache:
            try:
                cache[r.url] = fetch(r.url)
            except FetchError as exc:
                cache[r.url] = str(exc)
            except Exception as exc:                       # noqa: BLE001 - a bad source must not stop the run
                cache[r.url] = f"{type(exc).__name__}: {exc}"
        got = cache[r.url]
        if isinstance(got, str):
            results.append(_result(r, UNFETCHABLE, got))
        else:
            results.append(_judge(r, got, copies, pdf_to_text))
    return results


# --------------------------------------------------------------------------- the store

def record(results: list[dict], path: Path | None = None, *, at: str | None = None) -> str:
    counts = dict(Counter(r["result"] for r in results))
    return store.append(path or store.stream_path(STREAM), "watch_run",
                        {"tool": TOOL, "counts": counts, "results": results}, at=at)


def history(path: Path | None = None) -> dict[tuple[str, str, str], list[dict]]:
    """Every recorded check of every row, oldest first."""
    out: dict[tuple[str, str, str], list[dict]] = {}
    for h, rec in store.records(path or store.stream_path(STREAM)):
        if rec["kind"] != "watch_run":
            continue
        for res in rec["data"]["results"]:
            out.setdefault((res["rail_id"], res["kind"], res["name"]), []).append(
                dict(res, checked_at=rec["at"], evidence_hash=h))
    return out


def latest(path: Path | None = None) -> dict[tuple[str, str, str], dict]:
    return {k: v[-1] for k, v in history(path).items()}


# ---------------------------------------------------------------------------- fetching

def http_fetcher(*, timeout: float = 30.0, delay: float = 1.0, transport=None) -> Fetcher:
    """A polite fetcher: an honest User-Agent, one locale, one request a second per host, a size cap.

    `transport` lets a test see the request without a network.
    """
    import httpx

    client = httpx.Client(follow_redirects=True, timeout=timeout, transport=transport, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/markdown,application/pdf;q=0.9,*/*;q=0.5",
        "Accept-Language": LOCALE,
    })
    last: dict[str, float] = {}

    def fetch(url: str) -> Fetched:
        host = urlparse(url).netloc
        wait = last.get(host, 0.0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with client.stream("GET", url) as resp:
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"response larger than {MAX_BYTES:,} bytes")
                last[host] = time.monotonic()
                return Fetched(resp.status_code, resp.headers.get("content-type", ""), body, str(resp.url))
        except httpx.HTTPError as exc:
            last[host] = time.monotonic()
            raise FetchError(f"{type(exc).__name__}: {exc}") from None

    return fetch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-check every cited quote against its source.")
    ap.add_argument("--rail", help="only this rail_id")
    ap.add_argument("--dry-run", action="store_true", help="print results, record nothing")
    args = ap.parse_args(argv)

    selected = [r for r in rows() if not args.rail or r.rail_id == args.rail]
    results = run(http_fetcher(), rows=selected)
    for res in results:
        if res["result"] != SKIPPED:
            print(f"  {res['result']:19s} {res['rail_id']}.{res['name']:40s} {res['detail']}")
    counts = Counter(r["result"] for r in results)
    print("\n  " + " · ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    if not args.dry_run:
        h = record(results)
        print(f"  recorded as {h[:12]} in {store.stream_path(STREAM).relative_to(ROOT)}")
    return 1 if counts.get(NOT_FOUND) or counts.get(DOCUMENT_CHANGED) else 0


if __name__ == "__main__":
    sys.exit(main())
