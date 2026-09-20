"""Rows and their evidence: a measured row must agree with the latest measurement.

A row that names a `probe_id` is checked continuously. The probe is run (by a person, or by a
scheduled job with the sandbox credentials), and each run is appended to the evidence store.
This suite reads the committed store, offline. If the latest *conclusive* observation disagrees
with the row, a rail has changed its behaviour — or the row was wrong — and the suite fails until a
person edits the row, citing the new observation. An inconclusive run (an outage, a bad credential)
never counts against a row.
"""
import json

import pytest

from amanat.probes import catalogue, runner
from amanat.rails.semantics import RAILS, SourceTier
from amanat.registry import store

ROWS = [(rid, kind, row) for rid, rail in RAILS.items()
        for kind, group in (("capability", rail.capabilities), ("limit", rail.limits))
        for row in group.values()]
PROBED = [(rid, row) for rid, kind, row in ROWS if kind == "capability" and row.probe_id]


def _latest(rail_id):
    path = store.stream_path(runner.stream_for(rail_id))
    return runner.latest_findings(path) if path.exists() else {}


def _records(rail_id):
    return [(h, r) for h, r in store.records(store.stream_path(runner.stream_for(rail_id)))
            if r["kind"] == "probe_run"]


# Measured before probes existed: only a quote was kept, so no run backs them. The set may only
# shrink — as each is re-measured by a probe, it leaves this list.
LEGACY_OBSERVATIONS = {
    ("setu_umap", "credentials_self_serve"),         # a login call; no harness yet
    ("setu_umap", "documented_api_hosts_resolve"),   # DNS; no harness yet
}


class TestEveryObservedRowIsBackedOrAdmittedToBeNot:
    def test_an_observed_row_names_a_probe_unless_it_is_a_known_legacy_measurement(self):
        unbacked = {(rid, row.name) for rid, kind, row in ROWS
                    if kind == "capability" and row.source_tier is SourceTier.OBSERVED and not row.probe_id}
        assert unbacked == LEGACY_OBSERVATIONS

    def test_the_legacy_list_only_holds_rows_that_exist_and_lack_a_probe(self):
        for rid, name in LEGACY_OBSERVATIONS:
            row = RAILS[rid].capabilities[name]
            assert row.source_tier is SourceTier.OBSERVED and not row.probe_id


class TestRowsAndProbesPointAtEachOther:
    @pytest.mark.parametrize("rid,row", PROBED, ids=lambda x: getattr(x, "name", x))
    def test_the_probe_exists_belongs_to_the_rail_and_speaks_to_the_row(self, rid, row):
        probe = catalogue.PROBES[row.probe_id]
        assert probe.rail_id == rid
        assert row.name in {r.capability for r in probe.rules}

    def test_every_capability_a_probe_reports_is_a_row_of_its_rail(self):
        for probe in catalogue.PROBES.values():
            for cap in {r.capability for r in probe.rules}:
                assert cap in RAILS[probe.rail_id].capabilities, (probe.probe_id, cap)

    def test_every_capability_a_probe_reports_is_backed_by_a_probe_that_reports_it(self):
        for probe in catalogue.PROBES.values():
            for cap in {r.capability for r in probe.rules}:
                row = RAILS[probe.rail_id].capabilities[cap]
                assert row.probe_id and row.probe_id in catalogue.PROBES
                assert cap in {r.capability for r in catalogue.PROBES[row.probe_id].rules}


class TestTheDriftAlarm:
    @pytest.mark.parametrize("rid,row", PROBED, ids=lambda x: getattr(x, "name", x))
    def test_the_latest_conclusive_observation_agrees_with_the_row(self, rid, row):
        latest = _latest(rid).get((row.probe_id, row.name))
        assert latest is not None, f"never observed: run python -m amanat.probes run {row.probe_id}"
        assert latest["supported"] is row.supported, (
            f"the rail now answers {latest['supported']} where the row says {row.supported} "
            f"(observation {latest['evidence_hash'][:12]}, {latest['observed_at']}): edit the row and cite it")

    @pytest.mark.parametrize("rid,row", [(r, x) for r, x in PROBED if x.source_tier is SourceTier.OBSERVED],
                             ids=lambda x: getattr(x, "name", x))
    def test_an_observed_row_says_the_environment_the_probe_ran_on(self, rid, row):
        latest = _latest(rid)[(row.probe_id, row.name)]
        assert latest["environment"] == row.environment.value


class TestAnObservedQuoteIsBackedByTheRecord:
    """The row's words are the rail's words, found in the stored run rather than remembered."""

    @staticmethod
    def _nodes(obj):
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from TestAnObservedQuoteIsBackedByTheRecord._nodes(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from TestAnObservedQuoteIsBackedByTheRecord._nodes(v)

    @staticmethod
    def _contains(node, snippet):
        def same(a, b):
            if isinstance(a, bool) or isinstance(b, bool):
                return a is b
            return a == b
        return all(k in node and same(node[k], v) for k, v in snippet.items())

    @pytest.mark.parametrize("rid,row", [(r, x) for r, x in PROBED if x.source_tier is SourceTier.OBSERVED],
                             ids=lambda x: getattr(x, "name", x))
    def test_the_messages_and_fields_the_row_quotes_are_in_the_recorded_exchanges(self, rid, row):
        import re
        runs = [r["data"] for h, r in _records(rid) if r["data"]["probe_id"] == row.probe_id]
        responses = [e["response"] for run in runs for e in run["exchanges"]]
        haystack = json.dumps(responses, ensure_ascii=False)
        snippets = [json.loads(m) for m in re.findall(r"\{[^{}]*\}", row.quote)]
        # JSON strings in order, so `{"a":"b"}; "message"` pairs its quotes correctly
        messages = [q for q in re.findall(r'"((?:[^"\\]|\\.)*)"', row.quote)
                    if len(q) >= 8 and not any(q in json.dumps(sn) for sn in snippets)]
        assert snippets or messages, "an observed quote must carry the rail's own words or fields"
        for q in messages:
            assert q in haystack, f"{q!r} is not in any recorded run of {row.probe_id}"
        nodes = [n for r in responses for n in self._nodes(r)]
        for sn in snippets:
            assert any(self._contains(n, sn) for n in nodes), f"{sn} is not in any recorded run of {row.probe_id}"


def test_the_committed_probe_streams_are_unbroken_chains():
    for name in store.streams():
        if name.startswith("probes."):
            assert store.verify(store.stream_path(name)).length >= 1


class TestTheGeneratedDocSaysWhenARowAndItsProbeDisagree:
    def _obs(self, agrees):
        return {"probe_id": "p.q", "runs": 2,
                "latest": {"supported": True, "observed_on": "2026-09-20", "evidence_hash": "a" * 64,
                           "environment": "sandbox", "agrees": agrees}, "changes": []}

    def test_agreement_is_stated_plainly(self):
        from amanat.rails import docgen
        line = docgen._probe_line(self._obs(True))
        assert "latest conclusive answer supported" in line and "2 conclusive runs" in line
        assert "disagrees" not in line

    def test_a_disagreement_is_flagged_in_bold(self):
        from amanat.rails import docgen
        assert "**disagrees with this row**" in docgen._probe_line(self._obs(False))

    def test_the_committed_doc_carries_a_probe_line_for_every_probed_row_with_a_run(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "docs" / "RAIL_SEMANTICS.md").read_text(encoding="utf-8")
        for rid, row in PROBED:
            if (row.probe_id, row.name) in _latest(rid):
                assert f"Probe `{row.probe_id}`" in text
