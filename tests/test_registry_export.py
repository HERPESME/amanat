"""The registry as a published, versioned contract.

`semantics.py` is the truth the policy engine reads. The JSON export is the same table for
everyone else — an MCP proxy, another language, a comparison page — and the schema is what
they can rely on. The point of validating against a schema is that the project's central
discipline (cited-or-unverified; absence of evidence is not permission) is written into the
schema itself, so a consumer that validates inherits it.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from amanat.rails.semantics import RAILS, SourceTier
from amanat.registry import export

ROOT = Path(__file__).resolve().parents[1]


def _validate(doc):
    jsonschema.Draft202012Validator(export.schema()).validate(doc)


def _rows(doc, kind="capabilities"):
    return [(r["rail_id"], row) for r in doc["rails"] for row in r[kind]]


def _first(doc, predicate, kind="capabilities"):
    return next(row for _, row in _rows(doc, kind) if predicate(row))


class TestTheExportIsAContract:
    def test_it_validates_against_its_own_schema(self):
        _validate(export.build())

    def test_the_schema_is_itself_a_valid_schema(self):
        jsonschema.Draft202012Validator.check_schema(export.schema())

    def test_every_registered_row_is_exported(self):
        doc = export.build()
        assert {r["rail_id"] for r in doc["rails"]} == set(RAILS)
        for r in doc["rails"]:
            profile = RAILS[r["rail_id"]]
            assert [c["name"] for c in r["capabilities"]] == list(profile.capabilities)
            assert [l["name"] for l in r["limits"]] == list(profile.limits)

    def test_the_export_says_what_the_engine_will_permit(self):
        """The artefact may not be more generous than the runtime."""
        doc = export.build()
        for rail_id, cap in _rows(doc):
            assert cap["permitted"] is RAILS[rail_id].permits(cap["name"]), (rail_id, cap["name"])

    def test_a_limit_is_exported_with_its_number_and_unit(self):
        lim = _first(export.build(), lambda l: l["name"] == "max_block_amount", "limits")
        assert lim["value"] == 10_000_00 and lim["unit"] == "paise" and lim["tier"] == "primary"

    def test_the_tier_definitions_are_part_of_the_export(self):
        tiers = {t["tier"]: t for t in export.build()["tiers"]}
        assert set(tiers) == {t.value for t in SourceTier}
        assert tiers["observed"]["usable_as_fact"] is True
        assert tiers["unverified"]["usable_as_fact"] is False and tiers["marketing"]["usable_as_fact"] is False

    def test_as_of_is_the_latest_date_any_row_was_obtained(self):
        doc = export.build()
        dates = [row["obtained_on"] for _, row in _rows(doc) + _rows(doc, "limits") if row["obtained_on"]]
        assert dates and doc["as_of"] == max(dates)


class TestTheSchemaCarriesTheDiscipline:
    def test_an_unverified_row_cannot_be_marked_permitted(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "unverified")["permitted"] = True
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_an_unverified_row_cannot_be_marked_usable_as_fact(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "unverified")["usable_as_fact"] = True
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_a_permitted_row_must_be_supported_and_a_fact(self):
        bad = copy.deepcopy(export.build())
        row = _first(bad, lambda c: c["permitted"])
        row["supported"] = False
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_every_row_that_is_not_unverified_must_carry_a_quote(self):
        for kind in ("capabilities", "limits"):
            bad = copy.deepcopy(export.build())
            _first(bad, lambda c: c["tier"] == "primary", kind)["quote"] = ""
            with pytest.raises(jsonschema.ValidationError):
                _validate(bad)

    def test_an_observed_row_must_name_its_environment(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "observed")["environment"] = None
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_only_an_observed_row_may_name_one(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "primary")["environment"] = "sandbox"
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_an_unknown_field_is_refused_so_a_change_to_the_contract_is_deliberate(self):
        bad = copy.deepcopy(export.build())
        bad["rails"][0]["capabilities"][0]["confidence"] = 0.9
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)

    def test_a_date_must_be_iso(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["obtained_on"])["obtained_on"] = "21 Aug 2026"
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)


class TestThePublishedFilesAreCurrent:
    def test_the_export_is_deterministic_across_interpreters(self):
        """No wall clock, no set ordering: two fresh interpreters with different hash seeds agree."""
        code = "from amanat.registry import export; import sys; sys.stdout.write(export.render())"
        outs = []
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, TZ="Pacific/Auckland",
                       PYTHONPATH=str(ROOT / "src"))
            outs.append(subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                                       capture_output=True, text=True, check=True).stdout)
        assert outs[0] == outs[1] == export.render()

    def test_the_committed_export_matches_the_table(self):
        committed = (ROOT / "docs" / "registry" / "registry.json").read_text(encoding="utf-8")
        assert committed == export.render(), "run: python -m amanat.registry.export"

    def test_the_committed_schema_matches_the_packaged_one(self):
        committed = (ROOT / "docs" / "registry" / "registry.schema.json").read_text(encoding="utf-8")
        assert json.loads(committed) == export.schema()

    def test_the_committed_export_validates(self):
        _validate(json.loads((ROOT / "docs" / "registry" / "registry.json").read_text(encoding="utf-8")))


class TestVerificationInTheExport:
    """Each row says when its quote was last re-checked against its source, and what happened.

    The results come from the evidence store, so the export is still deterministic: it reads
    committed files, never a clock or the network.
    """

    @staticmethod
    def _run(path, results, at):
        from amanat.registry import watch
        return watch.record(results, path, at=at)

    @staticmethod
    def _res(name="partial_void", result="verified", quote=None, rail="cashfree_preauth", kind="capability"):
        from amanat.registry import watch
        from amanat.rails.semantics import RAILS
        row = (RAILS[rail].capabilities if kind == "capability" else RAILS[rail].limits)[name]
        return {"rail_id": rail, "kind": kind, "name": name, "url": row.url, "result": result,
                "detail": "d", "http_status": 200, "content_sha256": "a" * 64,
                "quote_sha256": watch.quote_hash(quote if quote is not None else row.quote)}

    def test_a_row_with_no_recorded_check_has_null_verification(self, tmp_path):
        doc = export.build(watch_path=tmp_path / "none.jsonl")
        assert all(row["verification"] is None for _, row in _rows(doc) + _rows(doc, "limits"))
        assert doc["stores"] == []

    def test_the_latest_check_and_its_date_are_exported(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        h = self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        doc = export.build(watch_path=path)
        v = _first(doc, lambda c: c["name"] == "partial_void")["verification"]
        assert v["result"] == "verified" and v["checked_on"] == "2026-09-20"
        assert v["quote_current"] is True and v["checks"] == 1 and v["changes"] == []
        assert v["evidence_hash"] == h

    def test_a_skipped_result_is_not_a_check(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(result="skipped")], "2026-09-20T12:00:00Z")
        doc = export.build(watch_path=path)
        assert _first(doc, lambda c: c["name"] == "partial_void")["verification"] is None

    def test_a_change_of_result_is_recorded_with_its_date(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        self._run(path, [self._res(result="not_found")], "2026-09-25T12:00:00Z")
        v = _first(export.build(watch_path=path), lambda c: c["name"] == "partial_void")["verification"]
        assert v["result"] == "not_found" and v["checks"] == 2
        assert v["changes"] == [{"on": "2026-09-25", "from": "verified", "to": "not_found"}]

    def test_a_quote_edited_after_its_check_is_not_current(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(quote="the quote as it was before someone edited the row")], "2026-09-20T12:00:00Z")
        v = _first(export.build(watch_path=path), lambda c: c["name"] == "partial_void")["verification"]
        assert v["quote_current"] is False

    def test_limits_carry_verification_too(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(name="hold_expiry_days", kind="limit")], "2026-09-20T12:00:00Z")
        v = _first(export.build(watch_path=path), lambda l: l["name"] == "hold_expiry_days", "limits")["verification"]
        assert v["result"] == "verified"

    def test_the_head_of_each_store_is_exported_as_a_checkpoint(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        h = self._run(path, [self._res()], "2026-09-21T12:00:00Z")
        doc = export.build(watch_path=path)
        assert doc["stores"] == [{"stream": "watch", "length": 2, "head": h}]

    def test_the_export_validates_with_verification_present(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        _validate(export.build(watch_path=path))

    def test_as_of_counts_a_recent_check(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2030-01-02T12:00:00Z")
        assert export.build(watch_path=path)["as_of"] == "2030-01-02"

    def test_a_tampered_store_refuses_to_export(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        self._run(path, [self._res()], "2026-09-21T12:00:00Z")
        lines = path.read_bytes().split(b"\n")
        lines[0] = lines[0].replace(b"verified", b"verifyed")
        path.write_bytes(b"\n".join(lines))
        from amanat.registry.store import StoreError
        with pytest.raises(StoreError):
            export.build(watch_path=path)

    def test_the_schema_refuses_a_malformed_verification(self):
        bad = copy.deepcopy(export.build())
        row = _first(bad, lambda c: c["tier"] == "primary")
        row["verification"] = {"result": "looks_fine", "detail": "", "checked_on": "2026-09-20",
                               "quote_current": True, "evidence_hash": "a" * 64, "checks": 1, "changes": []}
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)


class TestTheCommittedSourcesAreExported:
    def test_each_committed_source_document_is_listed_with_its_hash(self):
        import hashlib
        doc = export.build()
        assert doc["sources"], "the NPCI circulars are committed and cited"
        for src in doc["sources"]:
            data = (ROOT / src["path"]).read_bytes()
            assert src["sha256"] == hashlib.sha256(data).hexdigest() and src["bytes"] == len(data)

    def test_every_source_is_cited_by_at_least_one_row(self):
        doc = export.build()
        cited = {row["url"] for _, row in _rows(doc) + _rows(doc, "limits")}
        assert all(src["url"] in cited for src in doc["sources"])
