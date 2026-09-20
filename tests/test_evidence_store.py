"""The evidence store: what makes a dated observation hard to quietly rewrite.

A probe run or a quote check is recorded as one line of JSON. Each line carries the SHA-256
of the previous line's exact bytes, so the file is a hash chain that anyone can check with
nothing but a hash function — no canonicalisation, no library, no trust in this code. Editing
an earlier line breaks the next line's link; the last line, and the length of the file, are
pinned by a checkpoint (length + hash of the last line) held by someone else — an older
registry export, a report, a counterparty.

What a green `verify` means is spelled out in the tests, including what it does not: on its
own it accepts a shorter valid chain.
"""
import hashlib
import json
import threading

import pytest

from amanat.registry import store
from amanat.registry.store import GENESIS, Head, StoreError


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "stream.jsonl"


def _fill(path, n=4):
    return [store.append(path, "probe_run", {"i": i}, at=f"2026-09-2{i}T00:00:00Z") for i in range(n)]


class TestTheChain:
    def test_a_record_is_hashed_as_stored(self, path):
        h = store.append(path, "probe_run", {"a": 1}, at="2026-09-20T10:00:00Z")
        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        assert h == _sha(raw[:-1])

    def test_the_first_record_links_to_genesis_and_each_next_to_its_predecessor(self, path):
        hashes = _fill(path)
        recs = [json.loads(l) for l in path.read_text().splitlines()]
        assert recs[0]["prev"] == GENESIS
        assert [r["prev"] for r in recs[1:]] == hashes[:-1]
        assert [r["seq"] for r in recs] == [1, 2, 3, 4]

    def test_two_identical_payloads_have_different_hashes(self, path):
        a = store.append(path, "k", {"x": 1}, at="2026-09-20T10:00:00Z")
        b = store.append(path, "k", {"x": 1}, at="2026-09-20T10:00:00Z")
        assert a != b

    def test_floats_and_unicode_survive_because_the_bytes_are_what_is_hashed(self, path):
        """API bodies carry floats (`620.0`) and non-ASCII text. No canonicalisation is needed:
        a verifier hashes the stored bytes and never re-serialises."""
        h = store.append(path, "probe_run", {"payment_amount": 620.0, "note": "₹150 — “held”"},
                         at="2026-09-20T10:00:00Z")
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["data"] == {"payment_amount": 620.0, "note": "₹150 — “held”"}
        assert store.verify(path).hash == h

    def test_lines_are_compact_and_key_sorted(self, path):
        store.append(path, "k", {"b": 1, "a": 2}, at="2026-09-20T10:00:00Z")
        line = path.read_text().splitlines()[0]
        assert " " not in line.replace("“", "")
        assert line.index('"at"') < line.index('"data"') < line.index('"kind"') < line.index('"prev"')

    def test_a_record_can_be_found_by_its_hash(self, path):
        hashes = _fill(path)
        assert store.find(path, hashes[2])["data"] == {"i": 2}
        assert store.find(path, "0" * 64) is None


class TestVerify:
    def test_a_good_chain_verifies_and_reports_its_head(self, path):
        hashes = _fill(path)
        head = store.verify(path)
        assert head == Head(length=4, hash=hashes[-1])

    def test_an_empty_or_missing_store_is_a_valid_empty_chain(self, path):
        assert store.verify(path) == Head(length=0, hash=GENESIS)

    def test_altering_an_earlier_line_breaks_the_link_after_it(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        lines[1] = lines[1].replace(b'"i":1', b'"i":9')
        path.write_bytes(b"\n".join(lines))
        with pytest.raises(StoreError, match=r"line 3.*earlier line"):
            store.verify(path)

    def test_removing_a_middle_line_is_caught(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        del lines[1]
        path.write_bytes(b"\n".join(lines))
        with pytest.raises(StoreError):
            store.verify(path)

    def test_swapping_two_lines_is_caught(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        lines[1], lines[2] = lines[2], lines[1]
        path.write_bytes(b"\n".join(lines))
        with pytest.raises(StoreError):
            store.verify(path)

    def test_a_torn_final_line_is_reported(self, path):
        _fill(path)
        with open(path, "ab") as f:
            f.write(b'{"at":"2026-09-2')
        with pytest.raises(StoreError, match="torn"):
            store.verify(path)

    def test_a_line_that_is_not_an_object_is_reported(self, path):
        _fill(path, 1)
        with open(path, "ab") as f:
            f.write(b"[1,2,3]\n")
        with pytest.raises(StoreError):
            store.verify(path)


class TestVerifyReadsWhatIsOnDisk:
    """Each rule on its own, with lines forged so the *other* rules are satisfied."""

    @staticmethod
    def _write(path, recs):
        lines, prev = [], GENESIS
        for r in recs:
            r = {"at": "2026-09-20T10:00:00Z", "kind": "k", "prev": prev, **r}
            line = json.dumps(r, sort_keys=True, separators=(",", ":")).encode()
            lines.append(line)
            prev = _sha(line)
        path.write_bytes(b"\n".join(lines) + b"\n")

    def test_a_wrong_position_is_caught_even_when_the_hash_link_is_right(self, path):
        self._write(path, [{"seq": 1, "data": {}}, {"seq": 3, "data": {}}])
        with pytest.raises(StoreError, match="seq"):
            store.verify(path)

    def test_a_forged_but_well_formed_chain_verifies_which_is_why_a_checkpoint_exists(self, path):
        self._write(path, [{"seq": 1, "data": {"v": 1}}, {"seq": 2, "data": {"v": 2}}])
        assert store.verify(path).length == 2

    def test_a_bare_nan_is_not_json_and_is_refused_on_read(self, path):
        self._write(path, [{"seq": 1, "data": {"v": float("nan")}}])
        assert b"NaN" in path.read_bytes()
        with pytest.raises(StoreError, match="not valid JSON"):
            store.verify(path)


class TestWhatOnlyACheckpointCanCatch:
    """Without an outside reference a chain cannot see its own truncation or a rewritten tail.
    Stated as a test so nobody reads a green `verify` as more than it is."""

    def test_a_truncated_chain_still_verifies_on_its_own(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        path.write_bytes(b"\n".join(lines[:2]) + b"\n")
        assert store.verify(path).length == 2                  # valid — and shorter

    def test_a_checkpoint_catches_the_truncation(self, path):
        _fill(path)
        cp = store.verify(path)
        lines = path.read_bytes().split(b"\n")
        path.write_bytes(b"\n".join(lines[:2]) + b"\n")
        with pytest.raises(StoreError, match="shorter"):
            store.verify(path, head=cp)

    def test_a_checkpoint_catches_a_rewritten_last_line(self, path):
        _fill(path)
        cp = store.verify(path)
        lines = path.read_bytes().split(b"\n")
        lines[3] = lines[3].replace(b'"i":3', b'"i":7')
        path.write_bytes(b"\n".join(lines))
        assert store.verify(path).length == 4                  # internally consistent
        with pytest.raises(StoreError, match="checkpoint"):
            store.verify(path, head=cp)

    def test_a_checkpoint_is_satisfied_by_a_chain_that_only_grew(self, path):
        _fill(path, 3)
        cp = store.verify(path)
        store.append(path, "probe_run", {"i": 3}, at="2026-09-23T00:00:00Z")
        assert store.verify(path, head=cp).length == 4

    def test_the_genesis_checkpoint_accepts_anything(self, path):
        _fill(path)
        assert store.verify(path, head=Head(0, GENESIS)).length == 4


class TestAppendIsCareful:
    def test_append_refuses_to_extend_a_broken_chain(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        lines[1] = lines[1].replace(b'"i":1', b'"i":9')
        path.write_bytes(b"\n".join(lines))
        with pytest.raises(StoreError):
            store.append(path, "probe_run", {"i": 4}, at="2026-09-24T00:00:00Z")

    def test_concurrent_appends_keep_one_unbroken_chain(self, path):
        errors = []

        def work(k):
            try:
                for j in range(5):
                    store.append(path, "probe_run", {"who": k, "j": j}, at="2026-09-20T10:00:00Z")
            except Exception as exc:                            # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert store.verify(path).length == 30

    def test_the_kind_and_time_are_required_text(self, path):
        with pytest.raises(StoreError):
            store.append(path, "", {"x": 1}, at="2026-09-20T10:00:00Z")
        with pytest.raises(StoreError):
            store.append(path, "k", {"x": 1}, at="")

    def test_a_payload_that_is_not_json_is_refused_before_anything_is_written(self, path):
        with pytest.raises(StoreError):
            store.append(path, "k", {"x": object()}, at="2026-09-20T10:00:00Z")
        assert not path.exists() or path.read_bytes() == b""

    def test_nan_is_refused(self, path):
        """NaN is not JSON; another implementation would not read the line."""
        with pytest.raises(StoreError):
            store.append(path, "k", {"x": float("nan")}, at="2026-09-20T10:00:00Z")


class TestReading:
    def test_records_yield_each_hash_with_its_record_in_order(self, path):
        hashes = _fill(path)
        got = list(store.records(path))
        assert [h for h, _ in got] == hashes
        assert [r["data"]["i"] for _, r in got] == [0, 1, 2, 3]

    def test_records_of_a_missing_file_are_empty(self, path):
        assert list(store.records(path)) == []

    def test_reading_verifies_first(self, path):
        _fill(path)
        lines = path.read_bytes().split(b"\n")
        lines[0] = lines[0].replace(b'"i":0', b'"i":5')
        path.write_bytes(b"\n".join(lines))
        with pytest.raises(StoreError):
            list(store.records(path))
