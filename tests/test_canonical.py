"""Canonical bytes: the one serialisation every hash and signature is taken over.

Two implementations (Python here, JavaScript in the verifier page) must produce
identical bytes for every payload the chain can hold, or a genuine packet renders
as tampered. The rules are RFC 8785 (JCS) restricted to integers: money is
integer paise, and formatting a fraction is where runtimes disagree.
"""
import hashlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from amanat.evidence.canonical import CanonicalError, canonicalize, sanitize_text
from tests.support_node import run_page_js

# RFC 8785 section 3.2.3. UTF-16 order puts U+1F600 (surrogates D83D DE00)
# BEFORE U+FB33; code-point order (Python's sort_keys) puts it after.
RFC_KEYS = ["€", "\r", "דּ", "1", "\U0001F600", "\u0080", "ö"]
RFC_ORDER = ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"]


def _key_order(canonical_bytes: bytes) -> list[str]:
    import json
    return list(json.loads(canonical_bytes.decode("utf-8")).keys())


class TestSerialisation:
    def test_object_keys_sort_by_utf16_code_unit_not_code_point(self):
        out = canonicalize({k: "v" for k in RFC_KEYS})
        assert _key_order(out) == RFC_ORDER
        # a code-point sort (Python's sort_keys) would put the emoji last
        assert RFC_ORDER != sorted(RFC_KEYS)

    def test_output_is_compact_utf8_with_no_ascii_escaping(self):
        assert canonicalize({"note": "₹470", "amount": 47000}) == \
            b'{"amount":47000,"note":"\xe2\x82\xb9470"}'

    def test_control_characters_use_json_escapes(self):
        assert canonicalize("a\n\x1f\"\\") == b'"a\\n\\u001f\\"\\\\"'

    def test_nesting_booleans_null_and_negative_integers(self):
        assert canonicalize({"b": [True, False, None, -7], "a": {}}) == \
            b'{"a":{},"b":[true,false,null,-7]}'

    def test_a_boolean_is_not_serialised_as_an_integer(self):
        assert canonicalize(True) == b"true"


class TestWhatIsRefused:
    """Each of these used to be silently formatted; now it stops the write."""

    @pytest.mark.parametrize("value", [470.0, 0.1, float("nan"), float("inf")])
    def test_floats_are_refused(self, value):
        with pytest.raises(CanonicalError, match="float"):
            canonicalize({"amount": value})

    @pytest.mark.parametrize("value", [2**53, -(2**53), 10**30])
    def test_integers_a_browser_cannot_hold_exactly_are_refused(self, value):
        with pytest.raises(CanonicalError, match="integer"):
            canonicalize({"amount": value})

    def test_the_largest_safe_integer_is_accepted(self):
        assert canonicalize(2**53 - 1) == b"9007199254740991"

    def test_a_lone_surrogate_is_refused(self):
        with pytest.raises(CanonicalError, match="surrogate"):
            canonicalize({"reason": "ok \ud800 not ok"})

    @pytest.mark.parametrize("value", [
        datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("1"), {"a"}, b"x", object(),
    ])
    def test_unknown_types_are_refused_not_stringified(self, value):
        with pytest.raises(CanonicalError, match="unsupported"):
            canonicalize({"x": value})

    def test_non_string_keys_are_refused(self):
        with pytest.raises(CanonicalError, match="key"):
            canonicalize({1: "a"})


class TestSanitiseText:
    def test_a_lone_surrogate_becomes_the_replacement_character(self):
        assert sanitize_text("a\ud800b") == "a�b"

    def test_well_formed_text_is_returned_unchanged(self):
        assert sanitize_text("₹470 😀") == "₹470 😀"


CORPUS = [
    {k: i for i, k in enumerate(RFC_KEYS)},
    {"￮": 1, "\U0001F600": 2},                       # the case sort_keys got wrong
    {"amount": 47000, "payee": "citycabs", "ok": True, "n": None},
    {"nested": {"z": [1, {"b": 2, "a": 1}, []], "a": ""}},
    {"text": "₹470 — \"quoted\" \\ back\nline\ttab \x1f ctrl  "},
    {"neg": -47000, "max": 2**53 - 1, "zero": 0},
    [],
    {},
]


class TestBrowserAgreesWithPython:
    def test_the_pages_canonical_matches_python_byte_for_byte(self):
        results = run_page_js("input => page.canonical(input)", CORPUS)
        for value, got in zip(CORPUS, results):
            assert "error" not in got, got
            assert got["ok"].encode("utf-8") == canonicalize(value), value

    def test_the_page_refuses_what_python_refuses(self):
        # JSON.parse cannot carry a lone surrogate or a float distinct from an
        # integer, so the page-side guard is on non-safe integers and strings.
        out = run_page_js("input => page.canonical(input)", [{"a": 2**53}, {"a": 1.5}])
        assert all("error" in r for r in out)

    def test_digest_input_hashes_a_literal_entry_to_a_hand_computed_value(self):
        entry = {"seq": 0, "prev_hash": "0" * 64, "timestamp": "2026-01-01T00:00:00+00:00",
                 "actor": "human", "event_type": "intent", "payload": {"a": 1}}
        literal = ('{"actor":"human","event_type":"intent","payload":{"a":1},'
                   '"prev_hash":"' + "0" * 64 + '","seq":0,'
                   '"timestamp":"2026-01-01T00:00:00+00:00"}')
        want = hashlib.sha256(literal.encode("utf-8")).hexdigest()
        got = run_page_js(
            "e => sha256(page.digestInputFor(e, page.canonical))", [entry])[0]["ok"]
        assert got == want
