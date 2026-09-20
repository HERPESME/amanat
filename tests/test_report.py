"""The report says nothing the data does not.

Every number and list in Rail Semantics Report #1 is computed from the exported registry and the
evidence streams, so a statement in it cannot outlive the row it rests on. What is tested is that
computation — against counts made independently of the generator — plus what a report must never do:
claim priority, present a sandbox as an issuer, or hide what is unknown.
"""
import copy
import json
import re
from pathlib import Path

import pytest

from amanat.probes import runner
from amanat.registry import export, report, store

ROOT = Path(__file__).resolve().parents[1]
DOC = export.build()
MD = report.render(DOC)
ROWS = [(r["rail_id"], row) for r in DOC["rails"] for row in r["capabilities"]]


def _count(concept):
    got = {"yes": 0, "no": 0}
    for _, row in ROWS:
        if row["name"] == concept and row["tier"] != "unverified":
            got["yes" if row["supported"] else "no"] += 1
    return got


class TestTheReportIsComputedFromTheData:
    def test_the_committed_report_is_the_one_the_code_writes(self):
        committed = (ROOT / "docs" / "reports" / "rail-semantics-report-1.md").read_text(encoding="utf-8")
        assert committed == report.render(), "run: python -m amanat.registry.report"

    def test_it_is_deterministic_and_dated_by_the_data_not_the_clock(self):
        assert report.render(DOC) == report.render(copy.deepcopy(DOC))
        assert f"As of {DOC['as_of']}." in MD

    def test_the_registry_counts_are_the_registrys(self):
        caps = sum(len(r["capabilities"]) for r in DOC["rails"])
        limits = sum(len(r["limits"]) for r in DOC["rails"])
        every = [row for r in DOC["rails"] for row in (*r["capabilities"], *r["limits"])]
        reread = sum(1 for x in every if x["verification"] and x["verification"]["result"]
                     in ("verified", "verified_fragments", "verified_by_copy"))
        unread = sum(1 for x in every if x["verification"] and x["verification"]["result"] == "unfetchable")
        observed = sum(1 for x in every if x["tier"] == "observed")
        unverified = sum(1 for _, x in ROWS if x["tier"] == "unverified")
        assert f"{len(DOC['rails'])} rails, {caps} capabilities, {limits} numeric limits" in MD
        assert f"{observed} rows were **measured**" in MD and f"{reread} quotes were **re-read**" in MD
        assert f"{unread} sources could not be read" in MD and f"{unverified} capabilities are **unverified**" in MD

    def test_the_headline_on_partial_capture_counts_what_the_rows_say(self):
        c = _count("partial_debit")
        assert f"is supported on {c['yes']} rails and refused on {c['no']}" in MD

    def test_every_headline_question_has_a_section_with_its_definition_and_its_lists(self):
        defs = {c["name"]: c["definition"] for c in DOC["concepts"]}
        for name in report.HEADLINE:
            assert f"### `{name}`\n\n{defs[name]}" in MD
            c = _count(name)
            assert f"**Supported** ({c['yes']})" in MD and f"**Not supported** ({c['no']})" in MD

    def test_a_rail_appears_under_a_question_only_if_it_has_a_row_for_it(self):
        section = MD[MD.index("### `void_whole_hold`"):MD.index("### `remainder_auto_released`")]
        with_row = {report.page.SHORT[r["rail_id"]] for r in DOC["rails"]
                    if any(c["name"] == "void_whole_hold" for c in r["capabilities"])}
        named = {short for short in report.page.SHORT.values() if f"{short} (" in section}
        assert named == with_row

    def test_the_hold_lifetimes_are_the_registrys(self):
        for r in DOC["rails"]:
            for lim in r["limits"]:
                if lim["name"] == "hold_expiry_days":
                    assert f"{report.page.SHORT[r['rail_id']]} {lim['value']} days" in MD

    def test_every_measured_row_is_in_the_table_with_the_start_of_its_evidence_hash(self):
        for rid, row in ROWS:
            if row["tier"] == "observed" and row["observation"]:
                assert f"`{rid}.{row['name']}`" in MD
                assert f"`{row['observation']['latest']['evidence_hash'][:12]}`" in MD

    def test_the_measured_table_is_a_well_formed_markdown_table(self):
        table = [l for l in MD.splitlines() if l.startswith("| `") and "probes" not in l and "sources" not in l]
        measured = [l for l in table if re.match(r"\| `[a-z0-9_]+\.[a-z_]+` \| (supported|not supported) \|", l)]
        assert measured
        for line in measured:
            assert len(re.split(r"(?<!\\)\|", line.strip())) == 7, line          # 5 cells, two outer bars


class TestWhatIsNotKnownIsSaid:
    def test_every_unverified_row_is_named_and_explained(self):
        unverified = {(rid, row["name"]) for rid, row in ROWS if row["tier"] == "unverified"}
        assert unverified == set(report.UNVERIFIED_WHY)
        for rid, name in unverified:
            assert f"`{rid}.{name}` — {report.UNVERIFIED_WHY[(rid, name)]}" in MD

    def test_a_new_unverified_row_without_a_sentence_stops_the_report(self):
        bad = copy.deepcopy(DOC)
        bad["rails"][0]["capabilities"][0].update(tier="unverified", usable_as_fact=False, permitted=False)
        with pytest.raises(KeyError, match="UNVERIFIED_WHY"):
            report.render(bad)

    def test_a_source_that_could_not_be_read_is_listed_as_unreadable(self):
        assert "cite `www.npci.org.in (HTTP 403)`" in MD

    def test_the_regulator_note_appears_only_while_a_regulator_page_is_unreadable(self):
        assert "The regulator's site refuses scripted clients" in MD
        ok = copy.deepcopy(DOC)
        for r in ok["rails"]:
            for row in (*r["capabilities"], *r["limits"]):
                if row["verification"] and row["verification"]["result"] == "unfetchable":
                    row["verification"]["result"] = "verified"
        assert "The regulator's site refuses scripted clients" not in report.render(ok)

    def test_it_says_it_is_a_snapshot_and_a_sandbox_is_not_an_issuer(self):
        assert "This is a\nsnapshot" in MD or "snapshot" in MD
        assert "A sandbox is not an issuer" in MD

    def test_visas_guide_is_described_as_secondary_and_why(self):
        assert "Visa's guide is treated as secondary because it says the Visa Rules govern" in MD


class TestItClaimsNoPriority:
    BANNED = ["world's first", "the first to", "first-ever", "first ever", "nobody has", "no one has", "no prior art",
              "unprecedented", "novel approach", "never been done", "groundbreaking", "revolutionary",
              "of its kind", "one of a kind", "best-in-class", "cutting-edge", "state-of-the-art"]
    # "first" is allowed only where it means "before" or names a step, never where it claims priority
    ALLOWED_FIRST = ("vendor first", "first result", "first capture", "first attempt", "first run", "first key")

    @pytest.mark.parametrize("phrase", BANNED)
    def test_the_report_never_says(self, phrase):
        assert phrase not in MD.lower()

    def test_the_word_first_is_used_only_for_order_never_for_priority(self):
        for path, text in (("report", MD), ("vendor note", (ROOT / "docs" / "reports" / "VENDOR-NOTIFICATION.md").read_text(encoding="utf-8"))):
            for m in re.finditer(r"\bfirst\b", text, flags=re.I):
                around = text[max(0, m.start() - 12):m.end() + 12].lower()
                assert any(a in around for a in self.ALLOWED_FIRST), (path, text[max(0, m.start() - 40):m.end() + 40])

    def test_nor_does_the_vendor_note(self):
        text = (ROOT / "docs" / "reports" / "VENDOR-NOTIFICATION.md").read_text(encoding="utf-8").lower()
        assert not [p for p in self.BANNED if p in text]


class TestTheProbeFactsComeFromTheStoredRuns:
    @staticmethod
    def _run(tmp_path, n, refs, orders=None):
        path = tmp_path / "probes.cashfree_preauth.jsonl"
        for i in range(n):
            ref = refs[i % len(refs)]
            exchanges = [
                {"step": "hold", "op": "hold", "label": "order_create", "request": {}, "status": 200,
                 "response": {"order_id": f"o{i}" if orders is None else orders[i]}, "error": None, "at": "t"},
                {"step": "capture", "op": "capture", "label": "capture", "request": {}, "status": 200,
                 "response": {"authorization": {"action": "CAPTURE", "action_reference": ref}}, "error": None, "at": "t"}]
            runner.record({"probe_id": f"cashfree_preauth.p{i}", "rail_id": "cashfree_preauth", "environment": "sandbox",
                           "exchanges": exchanges, "findings": [], "finished_at": f"2026-09-2{i}T00:00:00Z"}, path)
        return tmp_path

    def test_the_number_of_probes_and_holds_is_read_from_the_store(self, tmp_path):
        out = report.render(DOC, self._run(tmp_path, 3, ["CAP_1"]))
        assert "3 probes ran against" in out and "3 holds in all" in out

    def test_a_constant_capture_reference_is_reported_with_its_count(self, tmp_path):
        out = report.render(DOC, self._run(tmp_path, 4, ["CAP_777"]))
        assert "every one of the 4 capture responses, across 4 orders, carries the same `action_reference` (CAP_777)" in out

    def test_references_that_vary_are_not_called_constant(self, tmp_path):
        out = report.render(DOC, self._run(tmp_path, 4, ["CAP_1", "CAP_2"]))
        assert "carries the same" not in out

    def test_the_same_order_used_twice_is_one_hold(self, tmp_path):
        out = report.render(DOC, self._run(tmp_path, 3, ["CAP_1"], orders=["a", "a", "b"]))
        assert "2 holds in all" in out

    def test_the_committed_report_says_what_the_committed_store_says(self):
        facts = report._probe_facts(None)
        assert f"{facts['probes']} probes ran" in MD and f"{facts['orders']} holds in all" in MD
        assert facts["refs"].get("CAP_12121", 0) >= 1 and "(CAP_12121)" in MD


class TestTheCheckpointsPinTheEvidence:
    def test_each_stream_head_and_source_hash_is_printed(self):
        for s in DOC["stores"]:
            assert f"| `{s['stream']}` | {s['length']} | `{s['head']}` |" in MD
        for s in DOC["sources"]:
            assert s["sha256"] in MD


class TestTheVendorNote:
    NOTE = (ROOT / "docs" / "reports" / "VENDOR-NOTIFICATION.md").read_text(encoding="utf-8")

    def test_the_report_points_at_it_and_it_exists(self):
        assert "[`VENDOR-NOTIFICATION.md`](VENDOR-NOTIFICATION.md)" in MD

    def test_it_states_the_commitments(self):
        for phrase in ("**before** it is published", "Fourteen days", "What is never done", "Probing production",
                       "Publishing a credential", "security contact"):
            assert phrase in self.NOTE, phrase

    def test_the_stripe_candidate_quotes_the_registrys_own_words(self):
        """The two sentences the note sets side by side are the ones the registry holds, not remembered ones."""
        norm = lambda t: " ".join(t.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').split())
        rows = {(r["rail_id"], row["name"]): row["quote"] for r in DOC["rails"] for row in r["capabilities"]}
        high = rows[("stripe_card_manual_capture", "over_capture")]
        low = rows[("stripe_card_manual_capture", "partial_debit")]
        note = norm(self.NOTE)
        assert norm(high).rstrip(".") in note
        assert "must be less than or equal to the original amount" in norm(low) and \
            "must be less than or equal to the original amount" in note

    def test_the_cashfree_candidate_matches_the_stored_records(self):
        refs = report._probe_facts(None)["refs"]
        assert "CAP_12121" in refs and "VOID_12121" in refs
        assert "CAP_12121" in self.NOTE and "VOID_12121" in self.NOTE
        assert "Duplicate capture_id present" in self.NOTE
        assert f"{report._probe_facts(None)['orders']} different" in self.NOTE.replace("eight", "8") or "eight different" in self.NOTE

    def test_nothing_in_it_claims_a_vendor_has_been_told(self):
        assert "Nothing has been sent." in self.NOTE
