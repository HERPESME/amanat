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
