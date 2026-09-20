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

    def test_an_unverified_row_exports_no_answer_and_every_other_row_exports_one(self):
        """`supported: null` is the export's word for "not established"."""
        for rail_id, cap in _rows(export.build()):
            if cap["tier"] == "unverified":
                assert cap["supported"] is None, (rail_id, cap["name"])
            else:
                assert isinstance(cap["supported"], bool), (rail_id, cap["name"])

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

    def test_an_unverified_row_cannot_claim_support_either_way(self):
        for claim in (True, False):
            bad = copy.deepcopy(export.build())
            _first(bad, lambda c: c["tier"] == "unverified")["supported"] = claim
            with pytest.raises(jsonschema.ValidationError):
                _validate(bad)

    def test_a_row_with_evidence_cannot_be_unknown(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "primary")["supported"] = None
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

    def test_an_observed_row_must_say_when_it_was_obtained(self):
        bad = copy.deepcopy(export.build())
        _first(bad, lambda c: c["tier"] == "observed")["obtained_on"] = None
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
        doc = export.build(store_dir=tmp_path)
        assert all(row["verification"] is None for _, row in _rows(doc) + _rows(doc, "limits"))
        assert doc["stores"] == []

    def test_the_latest_check_and_its_date_are_exported(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        h = self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        doc = export.build(store_dir=tmp_path)
        v = _first(doc, lambda c: c["name"] == "partial_void")["verification"]
        assert v["result"] == "verified" and v["checked_on"] == "2026-09-20"
        assert v["quote_current"] is True and v["checks"] == 1 and v["changes"] == []
        assert v["evidence_hash"] == h

    def test_a_skipped_result_is_not_a_check(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(result="skipped")], "2026-09-20T12:00:00Z")
        doc = export.build(store_dir=tmp_path)
        assert _first(doc, lambda c: c["name"] == "partial_void")["verification"] is None

    def test_a_change_of_result_is_recorded_with_its_date(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        self._run(path, [self._res(result="not_found")], "2026-09-25T12:00:00Z")
        v = _first(export.build(store_dir=tmp_path), lambda c: c["name"] == "partial_void")["verification"]
        assert v["result"] == "not_found" and v["checks"] == 2
        assert v["changes"] == [{"on": "2026-09-25", "from": "verified", "to": "not_found"}]

    def test_a_quote_edited_after_its_check_is_not_current(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(quote="the quote as it was before someone edited the row")], "2026-09-20T12:00:00Z")
        v = _first(export.build(store_dir=tmp_path), lambda c: c["name"] == "partial_void")["verification"]
        assert v["quote_current"] is False

    def test_a_row_demoted_to_unverified_shows_no_verification_from_its_old_checks(self, tmp_path):
        """The store keeps the checks; the export must not say a quote was re-read for a row that
        no longer has one. `merchant_revocable` was PRIMARY, then unverified after review."""
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(name="merchant_revocable", rail="sbmd", result="verified")],
                  "2026-09-20T12:00:00Z")
        row = _first(export.build(store_dir=tmp_path), lambda c: c["name"] == "merchant_revocable")
        assert row["tier"] == "unverified" and row["verification"] is None

    def test_limits_carry_verification_too(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res(name="hold_expiry_days", kind="limit")], "2026-09-20T12:00:00Z")
        doc = export.build(store_dir=tmp_path)
        cashfree = next(r for r in doc["rails"] if r["rail_id"] == "cashfree_preauth")   # several rails have this limit
        v = next(l for l in cashfree["limits"] if l["name"] == "hold_expiry_days")["verification"]
        assert v["result"] == "verified"

    def test_the_head_of_each_store_is_exported_as_a_checkpoint(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        h = self._run(path, [self._res()], "2026-09-21T12:00:00Z")
        doc = export.build(store_dir=tmp_path)
        assert doc["stores"] == [{"stream": "watch", "length": 2, "head": h}]

    def test_the_export_validates_with_verification_present(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        _validate(export.build(store_dir=tmp_path))

    def test_as_of_counts_a_recent_check(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2030-01-02T12:00:00Z")
        assert export.build(store_dir=tmp_path)["as_of"] == "2030-01-02"

    def test_a_tampered_store_refuses_to_export(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        self._run(path, [self._res()], "2026-09-20T12:00:00Z")
        self._run(path, [self._res()], "2026-09-21T12:00:00Z")
        lines = path.read_bytes().split(b"\n")
        lines[0] = lines[0].replace(b"verified", b"verifyed")
        path.write_bytes(b"\n".join(lines))
        from amanat.registry.store import StoreError
        with pytest.raises(StoreError):
            export.build(store_dir=tmp_path)

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


class TestProbeObservationsInTheExport:
    """A row that names a probe says what the latest conclusive run found and whether it agrees."""

    ROW = ("cashfree_preauth", "void_whole_hold")          # OBSERVED, supported=True, probe_id set

    @staticmethod
    def _obs(supported, at="2026-09-20T12:00:00Z", **kw):
        probe_id = kw.get("probe_id", "cashfree_preauth.void_whole_hold")
        return {"probe_id": probe_id, "rail_id": "cashfree_preauth", "environment": "sandbox",
                "findings": [{"capability": kw.get("cap", "void_whole_hold"), "supported": supported,
                              "basis": "b", "step": "s"}]}

    def _record(self, tmp_path, supported, at):
        from amanat.probes import runner
        return runner.record(self._obs(supported), tmp_path / "probes.cashfree_preauth.jsonl", at=at)

    def _row(self, doc):
        return _first(doc, lambda c: c["name"] == "void_whole_hold")

    def test_a_probed_row_with_no_recorded_run_has_no_observation(self, tmp_path):
        assert self._row(export.build(store_dir=tmp_path))["observation"] is None

    def test_a_row_without_a_probe_never_has_one(self, tmp_path):
        self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        doc = export.build(store_dir=tmp_path)
        assert _first(doc, lambda c: c["name"] == "self_serve_enablement")["observation"] is None

    def test_the_latest_conclusive_answer_is_exported_with_whether_it_agrees(self, tmp_path):
        h = self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        o = self._row(export.build(store_dir=tmp_path))["observation"]
        assert o["probe_id"] == "cashfree_preauth.void_whole_hold" and o["runs"] == 1
        assert o["latest"] == {"supported": True, "observed_on": "2026-09-20", "evidence_hash": h,
                               "environment": "sandbox", "agrees": True}
        assert o["changes"] == []

    def test_a_run_that_disagrees_with_the_row_is_flagged(self, tmp_path):
        self._record(tmp_path, False, "2026-09-20T12:00:00Z")
        assert self._row(export.build(store_dir=tmp_path))["observation"]["latest"]["agrees"] is False

    def test_an_inconclusive_run_is_not_an_observation(self, tmp_path):
        self._record(tmp_path, None, "2026-09-20T12:00:00Z")
        assert self._row(export.build(store_dir=tmp_path))["observation"] is None

    def test_a_change_of_answer_is_listed_with_its_day(self, tmp_path):
        self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        self._record(tmp_path, True, "2026-09-21T12:00:00Z")
        self._record(tmp_path, False, "2026-09-22T12:00:00Z")
        o = self._row(export.build(store_dir=tmp_path))["observation"]
        assert o["runs"] == 3 and o["changes"] == [{"on": "2026-09-22", "from": True, "to": False}]

    def test_the_export_validates_with_observations_present(self, tmp_path):
        self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        _validate(export.build(store_dir=tmp_path))

    def test_as_of_counts_a_recent_observation(self, tmp_path):
        self._record(tmp_path, True, "2031-03-04T12:00:00Z")
        assert export.build(store_dir=tmp_path)["as_of"] == "2031-03-04"

    def test_the_probe_stream_is_checkpointed(self, tmp_path):
        h = self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        assert {"stream": "probes.cashfree_preauth", "length": 1, "head": h} in export.build(store_dir=tmp_path)["stores"]

    def test_a_tampered_probe_stream_refuses_to_export(self, tmp_path):
        self._record(tmp_path, True, "2026-09-20T12:00:00Z")
        self._record(tmp_path, True, "2026-09-21T12:00:00Z")
        path = tmp_path / "probes.cashfree_preauth.jsonl"
        lines = path.read_bytes().split(b"\n")
        lines[0] = lines[0].replace(b'"supported":true', b'"supported":false')
        path.write_bytes(b"\n".join(lines))
        from amanat.registry.store import StoreError
        with pytest.raises(StoreError):
            export.build(store_dir=tmp_path)

    def test_the_schema_refuses_a_malformed_observation(self):
        bad = copy.deepcopy(export.build())
        row = _first(bad, lambda c: c["name"] == "void_whole_hold")
        row["observation"] = {"probe_id": "x", "runs": 0, "latest": {}, "changes": []}
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)


class TestTheVocabularyIsPartOfTheExport:
    def test_each_concept_lists_the_rails_that_use_it(self):
        doc = export.build()
        by_name = {c["name"]: c for c in doc["concepts"]}
        assert by_name["partial_debit"]["rails"] == [
            rid for rid, r in RAILS.items() if "partial_debit" in r.capabilities]
        assert len(by_name["partial_debit"]["rails"]) >= 8, "the comparison the vocabulary exists for"

    def test_every_shared_capability_name_in_the_export_is_a_concept(self):
        doc = export.build()
        names = {c["name"] for c in doc["concepts"]}
        from collections import Counter
        used = Counter(c["name"] for r in doc["rails"] for c in r["capabilities"])
        assert {n for n, k in used.items() if k > 1} <= names

    def test_a_concept_needs_a_real_definition_and_a_rail(self):
        bad = copy.deepcopy(export.build())
        bad["concepts"][0]["rails"] = []
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)
        bad = copy.deepcopy(export.build())
        bad["concepts"][0]["definition"] = "short"
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)


class TestNothingPrivateIsPublished:
    """A vendor's private identifier is not this project's to publish."""

    def test_a_support_ticket_number_appears_in_no_published_file(self):
        files = [ROOT / "README.md", ROOT / "CLAUDE.md"]
        files += sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "docs" / "reports").glob("*.md"))
        files += sorted((ROOT / "docs" / "registry").glob("*")) + sorted((ROOT / "src").rglob("*.py"))
        offenders = [str(f.relative_to(ROOT)) for f in files
                     if f.is_file() and "8266875" in f.read_text(encoding="utf-8", errors="ignore")]
        assert offenders == []
