"""Canonical bytes: what every hash and signature in the chain is taken over.

The rules are RFC 8785 (JSON Canonicalization Scheme) restricted to the values a
payment record actually holds — strings, integers, booleans, null, arrays and
objects. Two properties matter more than generality:

* **Two runtimes must agree byte for byte.** The verifier page recomputes every
  hash in the browser. If Python and JavaScript ever disagree, a genuine packet
  renders as tampered. Object keys are therefore sorted by UTF-16 code unit (as
  RFC 8785 requires, and as JavaScript's default sort does), not by code point
  (as Python's `sort_keys=True` does): the two differ for any pair of keys that
  straddle the astral plane, and a model-chosen tool-argument name can force it.
* **Nothing is silently reformatted.** Money is integer paise, and a fraction is
  exactly where runtimes format differently (`470.0` vs `470`). Floats,
  out-of-range integers, lone surrogates, non-string keys and unknown types are
  refused outright rather than stringified, so a bad payload stops at the write
  instead of producing a hash another implementation cannot reproduce.
"""
from __future__ import annotations

import json
from typing import Any

MAX_SAFE_INT = 2**53 - 1          # the largest integer every runtime holds exactly
_SURROGATES = range(0xD800, 0xE000)
_REPLACEMENT = chr(0xFFFD)


class CanonicalError(ValueError):
    """A value that cannot be canonicalised without loss or ambiguity."""


def sanitize_text(text: str) -> str:
    """Replace lone surrogates with U+FFFD.

    Text an untrusted model supplies (a payee, a reason, a tool name) is recorded
    in the chain. A lone surrogate is legal in a Python string and in a JSON
    escape, but it has no UTF-8 form, so it must not be allowed to abort an audit
    write. Clean it at the boundary; `canonicalize` refuses it if it slips past.
    """
    if not any(ord(ch) in _SURROGATES for ch in text):
        return text
    return "".join(_REPLACEMENT if ord(ch) in _SURROGATES else ch for ch in text)


def _string(text: str) -> str:
    for ch in text:
        if ord(ch) in _SURROGATES:
            raise CanonicalError(
                f"lone surrogate U+{ord(ch):04X} in a string has no UTF-8 form")
    # ensure_ascii=False: non-ASCII stays literal UTF-8; control characters get
    # the JSON short forms or lowercase \u00xx — identical to JSON.stringify.
    return json.dumps(text, ensure_ascii=False)


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
            raise CanonicalError(
                f"integer {value} is outside ±{MAX_SAFE_INT}, the range every "
                "runtime holds exactly")
        return str(value)
    if isinstance(value, float):
        raise CanonicalError(
            f"float {value!r} cannot be canonicalised: amounts are integer paise "
            "and a fraction is where runtimes format differently")
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalError(f"object key {key!r} is not a string")
        ordered = sorted(value, key=lambda k: k.encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(f"{_string(k)}:{_encode(value[k])}" for k in ordered) + "}"
    raise CanonicalError(f"unsupported type {type(value).__name__}")


def canonicalize(value: Any) -> bytes:
    """The canonical UTF-8 bytes of `value`."""
    return _encode(value).encode("utf-8")
