"""The registry is keyed to Hyperswitch's connector names, so its rows can be joined to their matrix.

Hyperswitch (Apache-2.0) publishes a per-connector capability flag with no provenance. A registry
row that says *which* connector it is about lets an operator of Hyperswitch ask "what does the
Stripe connector's underlying rail actually do to a hold?" and get a quote, a date and a probe.

The mapping is checked offline against a committed snapshot of Hyperswitch's `Connector` enum at a
pinned commit: a mapped name must be a variant there, and a rail that is a network, a regulator's
scheme or a protocol — not a connector — must map to nothing. Cashfree and Setu are not connectors
at that commit, and the registry says so instead of guessing a name.
"""
import hashlib
import re
from pathlib import Path

import jsonschema
import pytest

from amanat.rails.semantics import HYPERSWITCH, RAILS
from amanat.registry import export

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / HYPERSWITCH["snapshot_path"]


def _variants():
    """The variant names inside `pub enum Connector { ... }`."""
    text = SNAPSHOT.read_text(encoding="utf-8")
    body = text[text.index("pub enum Connector {"):]
    body = body[:body.index("\n}\n")]
    return {m.group(1) for m in re.finditer(r"^\s{4}([A-Z][A-Za-z0-9_]*),\s*$", body, flags=re.M)}


class TestTheMappingIsChecked:
    def test_the_snapshot_is_the_pinned_file_and_its_hash_is_recorded(self):
        assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == HYPERSWITCH["snapshot_sha256"]
        assert re.fullmatch(r"[0-9a-f]{40}", HYPERSWITCH["commit"])
        assert HYPERSWITCH["commit"][:7] in HYPERSWITCH["snapshot_path"]

    def test_every_mapped_name_is_a_variant_of_the_connector_enum_at_that_commit(self):
        variants = _variants()
        assert {"Stripe", "Adyen", "Razorpay"} <= variants, "the snapshot parses"
        mapped = {r.rail_id: r.hyperswitch_connector for r in RAILS.values() if r.hyperswitch_connector}
        assert mapped == {"razorpay_auth_capture": "razorpay", "stripe_card_manual_capture": "stripe",
                          "adyen_card_auth": "adyen"}
        for name in mapped.values():
            assert name.capitalize() in variants, name

    def test_cashfree_and_setu_are_not_connectors_at_that_commit_and_are_not_mapped(self):
        variants = {v.lower() for v in _variants()}
        assert "cashfree" not in variants and "setu" not in variants
        assert RAILS["cashfree_preauth"].hyperswitch_connector is None
        assert RAILS["setu_umap"].hyperswitch_connector is None

    def test_networks_schemes_and_protocols_are_not_connectors(self):
        for rid in ("sbmd", "upi_otm", "visa_card_auth", "x402", "x402_exact", "x402_upto_evm", "x402_upto_svm",
                    "x402_auth_capture", "x402_batch_settlement"):
            assert RAILS[rid].hyperswitch_connector is None, rid

    def test_a_connector_name_is_lowercase_letters_the_way_hyperswitch_serialises_it(self):
        for r in RAILS.values():
            if r.hyperswitch_connector:
                assert re.fullmatch(r"[a-z]+", r.hyperswitch_connector)


class TestItIsExported:
    DOC = export.build()

    def test_each_rail_carries_its_connector_or_null(self):
        got = {r["rail_id"]: r["hyperswitch_connector"] for r in self.DOC["rails"]}
        assert got["stripe_card_manual_capture"] == "stripe" and got["cashfree_preauth"] is None
        assert set(got) == set(RAILS)

    def test_the_pin_the_mapping_was_checked_against_is_exported(self):
        o = self.DOC["overlays"]["hyperswitch"]
        assert o["commit"] == HYPERSWITCH["commit"] and o["snapshot_sha256"] == HYPERSWITCH["snapshot_sha256"]
        assert o["repo"] == "juspay/hyperswitch" and o["checked_on"]

    def test_it_validates(self):
        jsonschema.Draft202012Validator(export.schema()).validate(self.DOC)

    def test_the_schema_refuses_a_malformed_connector_name(self):
        import copy
        bad = copy.deepcopy(self.DOC)
        bad["rails"][0]["hyperswitch_connector"] = "Not A Name"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(export.schema()).validate(bad)

    def test_the_committed_export_matches(self):
        assert (ROOT / "docs" / "registry" / "registry.json").read_text(encoding="utf-8") == export.render()
