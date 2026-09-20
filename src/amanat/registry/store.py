"""The evidence store: an append-only, hash-chained JSONL file.

A probe run or a quote check is recorded as one line of JSON. Every line carries the SHA-256
of the previous line's exact bytes (`prev`) and its own position (`seq`), so the file is a
hash chain that anyone can check with a hash function and nothing else. Hashes are taken over
the bytes as stored, never over a re-serialisation, so there is no canonicalisation to agree
on: API bodies carry floats (`620.0`) and non-ASCII text, and a verifier in any language just
hashes the line it reads.

What a green `verify` proves — and what it does not. It proves the lines link, in order,
without a gap. It does NOT prove nothing was removed from the end or that the last line was
not rewritten: a shorter valid chain is still valid. Those two are caught by a *checkpoint* —
`Head(length, hash-of-last-line)` — held by someone other than the writer (an older registry
export, a published report, a counterparty). Signing and witnessing checkpoints is a later
phase; this is the part that needs no keys.

Records are `{"at", "data", "kind", "prev", "seq"}` with sorted keys and compact separators.
`data` is whatever the writer recorded; it must be JSON (no NaN, no non-JSON types).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

GENESIS = "0" * 64
ROOT = Path(__file__).resolve().parents[3]
STORE_DIR = ROOT / "docs" / "observations" / "store"


class StoreError(Exception):
    """The store is not a valid chain, or a record cannot be written to it."""


@dataclass(frozen=True)
class Head:
    """A checkpoint: how many records the chain held and the hash of its last line."""

    length: int
    hash: str


def line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def stream_path(stream: str, store_dir: Path | None = None) -> Path:
    return (store_dir or STORE_DIR) / f"{stream}.jsonl"


def streams(store_dir: Path | None = None) -> list[str]:
    d = store_dir or STORE_DIR
    return sorted(p.stem for p in d.glob("*.jsonl")) if d.exists() else []


def _no_constants(name: str):
    raise ValueError(f"{name} is not JSON")


def _lines(path: Path) -> list[bytes]:
    """The exact bytes of every complete line, without its newline."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return []
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise StoreError("torn final line: the last record was not completely written")
    return raw[:-1].split(b"\n")


def verify(path: Path, *, head: Head | None = None) -> Head:
    """Check the chain, and optionally that it extends `head`. Returns the chain's head."""
    lines = _lines(path)
    prev = GENESIS
    for n, line in enumerate(lines, 1):
        try:
            rec = json.loads(line, parse_constant=_no_constants)
        except ValueError as exc:
            raise StoreError(f"line {n}: not valid JSON ({exc})") from None
        if not isinstance(rec, dict):
            raise StoreError(f"line {n}: not a JSON object")
        if rec.get("seq") != n:
            raise StoreError(f"line {n}: seq is {rec.get('seq')!r}, expected {n} "
                             "(a line was removed, added or reordered)")
        if rec.get("prev") != prev:
            where = "genesis" if n == 1 else f"the hash of line {n - 1}"
            raise StoreError(f"line {n}: prev does not match {where} "
                             "(an earlier line was altered or removed)")
        prev = line_hash(line)
    actual = Head(len(lines), prev)
    if head is not None and head.length > 0:
        if actual.length < head.length:
            raise StoreError(f"the chain is shorter than the checkpoint: {actual.length} lines, "
                             f"the checkpoint pinned {head.length}")
        if line_hash(lines[head.length - 1]) != head.hash:
            raise StoreError(f"checkpoint mismatch: line {head.length} is not the line the "
                             "checkpoint pinned (the history was rewritten)")
    return actual


def checkpoint(path: Path) -> Head:
    return verify(path)


def append(path: Path, kind: str, data: dict, *, at: str | None = None) -> str:
    """Add one record and return its evidence hash (the hash of its line).

    The chain is verified first, under an exclusive lock, so a broken store is never extended
    and two writers cannot both take the same `seq`. The line is fsynced before this returns.
    """
    if not isinstance(kind, str) or not kind:
        raise StoreError("a record needs a kind")
    if at is None:
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(at, str) or not at:
        raise StoreError("a record needs a time")
    try:                                   # refuse a bad payload before a file is even created
        json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StoreError(f"the record is not JSON: {exc}") from None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            head = verify(path)
            record = {"at": at, "data": data, "kind": kind, "prev": head.hash, "seq": head.length + 1}
            try:
                line = json.dumps(record, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise StoreError(f"the record is not JSON: {exc}") from None
            f.write(line + b"\n")
            f.flush()
            os.fsync(f.fileno())
            return line_hash(line)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def records(path: Path) -> Iterator[tuple[str, dict]]:
    """Every record with its evidence hash, oldest first. The chain is verified before any is
    yielded, so a caller never reasons from a store that has been altered."""
    verify(path)
    for line in _lines(path):
        yield line_hash(line), json.loads(line)


def find(path: Path, evidence_hash: str) -> dict | None:
    for h, rec in records(path):
        if h == evidence_hash:
            return rec
    return None
