"""The self-verifying HTML dispute packet.

The page recomputes each hash in the browser. If its canonicalisation does not
match Python's byte for byte, a valid chain would render as TAMPERED in front of
whoever opened it. These tests execute the page's real JavaScript under Node
(`tests/support_node.py`) instead of testing a Python port of it, which could
agree with Python while the page did not.
"""
import hashlib
import json
import re

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.evidence.chain import Actor, EventType, EvidenceChain
from amanat.evidence.render import render_html, _demo_packet
from tests.support_node import run_page_js


def _hashes_recomputed_by_the_page(packet: dict) -> list[str]:
    canon = "page.canonical" if packet.get("canonicalization") == "jcs-int" \
        else "page.canonicalV1"
    out = run_page_js(f"e => sha256(page.digestInputFor(e, {canon}))", packet["entries"])
    assert all("error" not in r for r in out), out
    return [r["ok"] for r in out]


class TestBrowserHashParity:
    def test_the_page_reproduces_every_stored_hash_of_a_real_session(self):
        packet = _demo_packet()
        assert _hashes_recomputed_by_the_page(packet) == [e["hash"] for e in packet["entries"]]

    def test_parity_holds_across_the_embed_and_parse_round_trip(self):
        """The rupee sign survives escaped-in-HTML then parsed-back-in-browser.

        render_html embeds the packet with default escaping, so ₹ becomes an
        escape in the file; the browser's JSON.parse restores it before the page
        canonicalises. Round-trip through that, then hash in the page.
        """
        packet = _demo_packet()
        assert any(ord(c) > 127 for e in packet["entries"]
                   for c in json.dumps(e["payload"], ensure_ascii=False)), \
            "expected at least one non-ASCII (₹) entry"
        restored = json.loads(json.dumps(packet))          # like JSON.parse in the page
        assert _hashes_recomputed_by_the_page(restored) == [e["hash"] for e in packet["entries"]]

    def test_a_model_chosen_argument_name_cannot_make_a_genuine_packet_fail(self):
        """Astral vs high-BMP keys sorted differently in Python and in the page."""
        c = EvidenceChain.new("s")
        c.append(Actor.POLICY, EventType.REFUSAL,
                 {"rule": "malformed_tool_call",
                  "arguments": {"￮": "1", "\U0001F600": "2"}})
        packet = c.export_packet()
        assert _hashes_recomputed_by_the_page(packet) == [e["hash"] for e in packet["entries"]]

    def test_a_packet_hashed_by_the_legacy_algorithm_still_reproduces_in_the_page(self):
        """Legacy parity holds for integer/string payloads only.

        A legacy packet holding a float (`470.0`) can never be reproduced in a
        browser: JSON.parse turns it into `470`, so the page hashes different
        bytes. That is why the current format refuses floats outright, and why
        the Cashfree packet that carried one had to be re-exported.
        """
        payload = {"amount": 47000, "captured_paise": 47000, "payee": "citycabs"}
        legacy = json.dumps({"seq": 0, "prev_hash": "0" * 64, "timestamp": "t",
                             "actor": "rail", "event_type": "rail_transition",
                             "payload": payload}, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, default=str)
        want = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
        entry = {"seq": 0, "prev_hash": "0" * 64, "timestamp": "t", "actor": "rail",
                 "event_type": "rail_transition", "payload": payload}
        got = run_page_js(
            "e => sha256(page.digestInputFor(e, page.canonicalV1))", [entry])[0]["ok"]
        assert got == want


class TestWhatTheBannerMayClaim:
    """`assessTrust` decides what the page says a green result means."""

    KEY = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    PACKET = {"public_key": KEY, "entries": [{"hash": "aa"}, {"hash": "bb"}, {"hash": "cc"}]}

    def _assess(self, fragment: str) -> dict:
        return run_page_js("i => page.assessTrust(i.packet, i.fragment)",
                           [{"packet": self.PACKET, "fragment": fragment}])[0]["ok"]

    def test_with_nothing_pinned_it_says_so_and_does_not_call_the_packet_verified(self):
        a = self._assess("")
        assert a["ok"] is True and a["keyPinned"] is False and a["checkpointPinned"] is False
        text = " ".join(a["lines"])
        assert "not pinned" in text and "anyone can produce" in text

    def test_a_matching_pinned_key_is_reported(self):
        a = self._assess(f"#key={self.KEY.upper()}")           # hex case must not matter
        assert a["ok"] is True and a["keyPinned"] is True

    def test_a_different_pinned_key_fails(self):
        a = self._assess("#key=" + "00" * 32)
        assert a["ok"] is False
        assert "does NOT match" in " ".join(a["lines"])

    def test_a_checkpoint_the_packet_extends_is_reported(self):
        a = self._assess("#len=2&head=bb")
        assert a["ok"] is True and a["checkpointPinned"] is True

    def test_a_packet_shorter_than_the_checkpoint_is_truncated(self):
        a = self._assess("#len=4&head=dd")
        assert a["ok"] is False and "Truncated" in " ".join(a["lines"])

    def test_a_packet_that_diverges_from_the_checkpoint_fails(self):
        a = self._assess("#len=2&head=zz")
        assert a["ok"] is False and "does not extend" in " ".join(a["lines"])

    def test_a_checkpoint_missing_its_length_fails_rather_than_being_ignored(self):
        assert self._assess("#head=bb")["ok"] is False


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
