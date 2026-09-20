"""The generated rail-semantics document says what the table says, and no more."""
import re

from amanat.rails import docgen
from amanat.rails.semantics import RAILS, SourceTier


def _permitted_cell(doc: str, rail_id: str, name: str) -> str:
    section = doc.split(f"## `{rail_id}` —", 1)[1].split("\n## ", 1)[0]
    line = next(l for l in section.splitlines() if l.startswith(f"| `{name}` |"))
    return line.split("|")[2].strip()


class TestThePermittedColumn:
    """`no` (the source says the rail does not permit it) is not `?` (nothing was established)."""

    def test_a_row_the_source_forbids_is_no(self):
        assert _permitted_cell(docgen.render(), "razorpay_auth_capture", "over_capture") == "**no**"

    def test_a_row_nothing_was_established_about_is_a_question_mark(self):
        assert _permitted_cell(docgen.render(), "sbmd", "block_amount_reducible_without_revoke") == "?"

    def test_a_permitted_row_is_yes(self):
        assert _permitted_cell(docgen.render(), "sbmd", "partial_debit") == "yes"

    def test_every_cell_agrees_with_the_engine(self):
        doc = docgen.render()
        for rail in RAILS.values():
            for cap in rail.capabilities.values():
                cell = _permitted_cell(doc, rail.rail_id, cap.name)
                if rail.permits(cap.name):
                    assert cell == "yes", (rail.rail_id, cap.name)
                elif cap.is_fact:
                    assert cell == "**no**", (rail.rail_id, cap.name)
                else:
                    assert cell == "?", (rail.rail_id, cap.name)


class TestTheTierLegend:
    def test_a_protocol_specification_is_a_primary_source_and_the_legend_says_so(self):
        doc = docgen.render()
        legend = doc.split("## Source tiers", 1)[1].split("\n## ", 1)[0]
        assert "protocol" in SourceTier.PRIMARY.meaning
        assert re.search(r"`PRIMARY`.*protocol", legend)
        assert "*is* the rail" in legend and "*binds* the rail" in legend

    def test_the_unverified_tier_is_described_as_unknown_not_as_believed(self):
        assert "neither yes nor no" in SourceTier.UNVERIFIED.meaning
        assert "Believed" not in SourceTier.UNVERIFIED.meaning
