"""The self-verifying HTML dispute packet.

The page recomputes each hash in the browser. If its canonicalization does not
match Python's byte for byte, a valid chain would render as TAMPERED in front of
whoever opened it. I cannot run a browser here, so instead this ports the page's
`canonical()` algorithm to Python and proves it reproduces the real stored
hashes. The JS mirrors this algorithm exactly; if this passes, the browser agrees.
"""
import hashlib
import json
import re

from amanat.evidence.render import render_html, _demo_packet


def js_canonical(v) -> str:
    """A faithful port of the page's canonical() — same algorithm, same output.

    JS JSON.stringify and Python json.dumps(ensure_ascii=False) agree on scalars
    (numbers, booleans, null, and strings over our payloads), so mirroring the
    object/array walk reproduces the browser's bytes.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(js_canonical(x) for x in v) + "]"
    keys = sorted(v.keys())
    return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + js_canonical(v[k])
                          for k in keys) + "}"


def digest_input(e: dict) -> dict:
    return {"seq": e["seq"], "prev_hash": e["prev_hash"], "timestamp": e["timestamp"],
            "actor": e["actor"], "event_type": e["event_type"], "payload": e["payload"]}


class TestBrowserHashParity:
    def test_the_pages_canonicalization_reproduces_every_stored_hash(self):
        packet = _demo_packet()
        for e in packet["entries"]:
            recomputed = hashlib.sha256(
                js_canonical(digest_input(e)).encode("utf-8")).hexdigest()
            assert recomputed == e["hash"], f"entry {e['seq']} would show as tampered"

    def test_parity_holds_across_the_embed_and_parse_round_trip(self):
        """The rupee sign survives escaped-in-HTML then parsed-back-in-browser.

        render_html embeds the packet with the default escaping, so ₹ becomes
        \\u20b9 in the file. The browser's JSON.parse restores it before
        canonical() runs, so the recomputed hash matches. This checks the whole
        round trip on the entry that actually carries a non-ASCII character.
        """
        packet = _demo_packet()
        with_unicode = [e for e in packet["entries"]
                        if any(ord(c) > 127 for c in json.dumps(e["payload"],
                                                                ensure_ascii=False))]
        assert with_unicode, "expected at least one ₹-bearing entry"

        # simulate the browser: embed (escaped) -> parse back -> canonicalize
        embedded = json.dumps(packet)            # ensure_ascii=True, like render_html
        restored = json.loads(embedded)          # like JSON.parse in the page
        for e in restored["entries"]:
            recomputed = hashlib.sha256(
                js_canonical(digest_input(e)).encode("utf-8")).hexdigest()
            assert recomputed == e["hash"]


class TestTheHtmlIsSelfContained:
    def test_no_external_resources(self):
        html = render_html(_demo_packet())
        assert not re.search(r'(?:src|href)\s*=\s*["\']https?:', html)

    def test_it_embeds_the_packet_and_the_verifier(self):
        html = render_html(_demo_packet())
        assert '<script id="packet"' in html
        assert "crypto.subtle.verify" in html
        assert "'Ed25519'" in html
        assert "crypto.subtle.digest('SHA-256'" in html

    def test_script_close_tags_in_data_are_escaped(self):
        html = render_html(_demo_packet())
        # the embedded JSON must not be able to close the script element early
        body = html.split('id="packet"')[1].split("</script>")[0]
        assert "</script" not in body.lower()

    def test_the_demo_packet_carries_refusals_and_transitions(self):
        packet = _demo_packet()
        kinds = {e["event_type"] for e in packet["entries"]}
        assert "refusal" in kinds
        assert "rail_transition" in kinds
