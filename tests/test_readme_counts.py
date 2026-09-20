"""The README's evidence table may not drift from the registry.

The counts by rail and by tier are written by hand into the README, and Phase 1 keeps adding
rows. A number that is wrong is exactly the failure this project exists to prevent, so the
table is checked against the code that the engine reads.
"""
import re
from collections import Counter
from pathlib import Path

from amanat.rails.semantics import RAILS

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
_TIER = re.compile(r"(\d+) `(PRIMARY|OBSERVED|SECONDARY|MARKETING|UNVERIFIED)`")


def _table_rows():
    """(capability count, {tier: n}) for each row of the evidence table."""
    section = README.split("## The evidence table", 1)[1].split("\n---", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[1].isdigit():
            rows.append((int(cells[1]), {t.lower(): int(n) for n, t in _TIER.findall(cells[2])}))
    return rows


def _registry_rows():
    return [(len(r.capabilities), dict(Counter(c.source_tier.value for c in r.capabilities.values())))
            for r in RAILS.values()]


def _canon(rows):
    return sorted((n, tuple(sorted(t.items()))) for n, t in rows)


def test_the_headline_count_matches_the_registry():
    total = sum(len(r.capabilities) for r in RAILS.values())
    assert f"{total} capabilities across {len(RAILS)} rails" in README


def test_each_row_of_the_evidence_table_matches_a_rail_in_the_registry():
    assert _canon(_table_rows()) == _canon(_registry_rows())


def test_the_readme_names_every_unverified_capability():
    unverified = [f"{r.rail_id}.{c.name}" for r in RAILS.values()
                  for c in r.capabilities.values() if c.source_tier.value == "unverified"]
    section = README.split("## The evidence table", 1)[1].split("\n---", 1)[0]
    for name in unverified:
        assert name in section, f"{name} is UNVERIFIED in the registry but not named in the README"
    assert f"{['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight'][len(unverified)]} capabilit" in section


def test_the_readme_and_project_notes_state_the_number_of_tests_the_suite_collects():
    """The badge was once wrong by 178 tests. Collect the suite and compare, so it cannot drift."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts=",
         "-p", "no:warnings", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, timeout=300).stdout
    n = int(re.search(r"(\d+) tests? collected", out).group(1))
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"tests-{n}%20passing" in README, f"README badge: the suite collects {n} tests"
    assert f"# {n} tests." in README and f"**{n} tests, no credential and no network.**" in README
    assert f"# {n} tests (" in claude, f"CLAUDE.md: the suite collects {n} tests"
