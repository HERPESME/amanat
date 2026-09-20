"""The registry page: a comparison that reads without JavaScript and loads nothing.

The page is generated from the exported registry. What is checked here is what a reader relies on:
a cell never says more than its row (a `✓` only where the engine would permit), every row is
reachable, every string from the registry is escaped (a quote is somebody else's text), nothing is
fetched from anywhere, and the committed file is the one the code would write today.
"""
import copy
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from amanat.registry import export, page

ROOT = Path(__file__).resolve().parents[1]
DOC = export.build()
HTML = page.render(DOC)
VOID = {"meta", "link", "input", "br", "hr", "img"}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids, self.classes = [], [], [], []
        self.cells = []            # (class list, data-target, aria-label, text)
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "button" and "cell" in (a.get("class") or "").split():
            self._cur = [a["class"].split(), a.get("data-target"), a.get("aria-label"), ""]
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, self.stack[-3:]))
            return
        self.stack.pop()
        if tag == "button" and self._cur:
            self.cells.append(tuple(self._cur))
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur[3] += data


def parsed(html=HTML):
    p = _Tags()
    p.feed(html)
    p.close()
    return p


class TestTheDocumentIsWellFormed:
    def test_every_tag_is_closed_in_order(self):
        p = parsed()
        assert p.errors == [] and p.stack == []

    def test_ids_are_unique(self):
        ids = parsed().ids
        assert len(ids) == len(set(ids))

    def test_it_declares_a_language_a_title_and_a_viewport(self):
        assert '<html lang="en">' in HTML and "<title>Payment-rail semantics</title>" in HTML
        assert 'name="viewport"' in HTML

    def test_it_has_a_dark_scheme(self):
        assert "prefers-color-scheme:dark" in HTML

    def test_it_is_deterministic(self):
        assert page.render(DOC) == page.render(copy.deepcopy(DOC))


class TestNothingIsFetched:
    def test_no_external_script_stylesheet_font_or_image(self):
        for banned in ("<script src", "<link", "@import", "url(", "<img", "<iframe", "<object"):
            assert banned not in HTML, banned

    def test_the_script_never_reaches_for_the_network(self):
        js = HTML[HTML.index("<script>") + 8:HTML.index("</script>")]
        for banned in ("fetch(", "XMLHttpRequest", "import(", "WebSocket", "sendBeacon", "eval(", "innerHTML", "document.write"):
            assert banned not in js, banned

    def test_a_link_to_the_outside_never_opens_with_the_openers_authority(self):
        for tag in re.findall(r'<a [^>]*href="https?://[^"]+"[^>]*>', HTML):
            assert 'rel="noopener noreferrer"' in tag, tag

    def test_the_script_parses(self, tmp_path):
        from tests.support_node import require_node
        js = HTML[HTML.index("<script>") + 8:HTML.index("</script>")]
        f = tmp_path / "page.js"
        f.write_text(js, encoding="utf-8")
        out = subprocess.run([require_node(), "--check", str(f)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


class TestEveryStringFromTheRegistryIsEscaped:
    HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)>"\'&'

    def _hostile(self):
        bad = copy.deepcopy(DOC)
        rail = bad["rails"][0]
        rail["display_name"] = self.HOSTILE
        for row in (*rail["capabilities"], *rail["limits"]):
            row["quote"] = row["notes"] = row["citation"] = self.HOSTILE
            row["url"] = 'https://x.test/"onmouseover="alert(3)'
        bad["concepts"][0]["definition"] = self.HOSTILE
        bad["concepts"][0]["name"] = self.HOSTILE
        bad["as_of"] = self.HOSTILE
        bad["stores"][0]["stream"] = self.HOSTILE
        return page.render(bad)

    def test_nothing_hostile_survives_as_markup(self):
        out = self._hostile()
        assert "<script>alert" not in out and "<img src=x" not in out and 'onmouseover="alert' not in out

    def test_it_appears_as_escaped_text_instead(self):
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in self._hostile()

    def test_the_hostile_page_is_still_well_formed(self):
        p = parsed(self._hostile())
        assert p.errors == [] and p.stack == []


class TestWhatACellSays:
    @staticmethod
    def _lookup():
        cells = {}
        for cell in parsed().cells:
            cells[cell[1]] = cell
        return cells

    def test_a_tick_appears_only_where_the_engine_would_permit(self):
        cells = self._lookup()
        for rail in DOC["rails"]:
            for cap in rail["capabilities"]:
                target = page._anchor(rail["rail_id"], "capability", cap["name"])
                if target not in cells:
                    continue                       # a rail-specific name is not a matrix row
                classes, _, _, text = cells[target]
                assert ("yes" in classes) == cap["permitted"], (rail["rail_id"], cap["name"])
                assert (text == "✓") == cap["permitted"]

    def test_an_unverified_row_is_a_question_mark_never_a_tick_or_a_cross(self):
        cells = self._lookup()
        seen = 0
        for rail in DOC["rails"]:
            for cap in rail["capabilities"]:
                target = page._anchor(rail["rail_id"], "capability", cap["name"])
                if cap["tier"] == "unverified" and target in cells:
                    seen += 1
                    assert cells[target][3] == "?" and "unk" in cells[target][0]
        assert seen >= 1

    def test_a_refusal_backed_by_evidence_is_a_cross(self):
        cells = self._lookup()
        target = page._anchor("razorpay_auth_capture", "capability", "over_capture")
        assert cells[target][3] == "✗" and "no" in cells[target][0]

    def test_every_cell_says_in_words_what_it_means(self):
        for classes, _, label, _ in parsed().cells:
            assert re.search(r"(supported|not supported|unverified)", label), label

    def test_every_cell_leads_to_a_row_that_exists(self):
        p = parsed()
        ids = set(p.ids)
        assert p.cells and all(target in ids for _, target, _, _ in p.cells)

    def test_a_capability_and_a_limit_of_the_same_name_do_not_share_an_address(self):
        assert page._anchor("r", "capability", "x_y") != page._anchor("r", "limit", "x_y")
        bad = copy.deepcopy(DOC)
        rail = bad["rails"][0]
        clash = copy.deepcopy(rail["limits"][0] if rail["limits"] else next(l for r in bad["rails"] for l in r["limits"]))
        clash["name"] = rail["capabilities"][0]["name"]
        rail["limits"].append(clash)
        ids = parsed(page.render(bad)).ids
        assert len(ids) == len(set(ids))

    def test_every_capability_and_limit_has_its_full_row(self):
        ids = set(parsed().ids)
        for rail in DOC["rails"]:
            for cap in rail["capabilities"]:
                assert page._anchor(rail["rail_id"], "capability", cap["name"]) in ids
            for lim in rail["limits"]:
                assert page._anchor(rail["rail_id"], "limit", lim["name"]) in ids

    def test_every_quote_is_on_the_page_without_running_a_script(self):
        from html import escape
        for rail in DOC["rails"]:
            for row in (*rail["capabilities"], *rail["limits"]):
                if row["quote"]:
                    assert escape(row["quote"], quote=True) in HTML, (rail["rail_id"], row["name"])


class TestTheMatrixCoversTheRegistry:
    def test_every_rail_is_in_exactly_one_group_and_has_a_short_name(self):
        grouped = [rid for _, ids in page.GROUPS for rid in ids]
        assert sorted(grouped) == sorted(r["rail_id"] for r in DOC["rails"]) and len(grouped) == len(set(grouped))
        assert set(page.SHORT) == {r["rail_id"] for r in DOC["rails"]}

    def test_every_rail_is_a_column_and_every_concept_a_row(self):
        for rail in DOC["rails"]:
            assert page._e(page.SHORT[rail["rail_id"]]) in HTML
        for concept in DOC["concepts"]:
            assert f'data-concept="{concept["name"]}"' in HTML

    def test_a_rail_with_no_row_for_a_question_shows_a_dot_not_an_answer(self):
        blanks = re.findall(r'<td class="none"[^>]*>([^<]*)</td>', HTML)
        assert blanks and set(blanks) == {"·"}

    def test_the_grid_is_complete_one_cell_per_rail_per_question(self):
        rails, concepts = len(DOC["rails"]), len(DOC["concepts"])
        assert len(re.findall(r'<th scope="col" class="rail">', HTML)) == rails
        cells = len(parsed().cells)
        blanks = len(re.findall(r'<td class="none"', HTML))
        assert cells + blanks == rails * concepts
        for row in re.findall(r'<tr data-concept="[^"]*">.*?</tr>', HTML, flags=re.S):
            assert row.count("<td") == rails

    def test_the_number_of_answers_is_the_number_of_matrix_rows_the_registry_holds(self):
        shared = {c["name"] for c in DOC["concepts"]}
        expected = sum(1 for r in DOC["rails"] for c in r["capabilities"] if c["name"] in shared)
        assert len(parsed().cells) == expected


class TestWhatItSaysAboutItsOwnEvidence:
    def test_an_unreadable_source_is_reported_as_unreadable_not_as_a_missing_quote(self):
        assert "could not be read (HTTP 403)" in HTML
        assert "NOT there" not in HTML                      # nothing in the committed registry is missing

    def test_a_row_with_no_recheck_says_why_by_its_kind(self):
        assert "checked by its probe (below) rather than against a page" in HTML          # an observation
        assert "nothing was quoted, so there is nothing to re-check" in HTML              # an unverified row

    def test_a_link_from_a_measurement_is_to_the_api_reference_not_a_source_of_the_words(self):
        for rail in DOC["rails"]:
            for row in rail["capabilities"]:
                if not row["url"]:
                    continue
                anchor = page._anchor(rail["rail_id"], "capability", row["name"])
                block = re.search(rf'<details class="row" id="{re.escape(anchor)}">.*?</details>', HTML, flags=re.S).group(0)
                want = "API reference</a>" if row["tier"] == "observed" else "source</a>"
                assert want in block, (rail["rail_id"], row["name"], row["tier"])

    def test_a_missing_quote_would_be_said_plainly(self):
        bad = copy.deepcopy(DOC)
        row = next(c for r in bad["rails"] for c in r["capabilities"] if c["verification"])
        row["verification"]["result"] = "not_found"
        assert "the quote is NOT there" in page.render(bad)

    def test_a_probe_that_disagrees_with_its_row_is_flagged(self):
        bad = copy.deepcopy(DOC)
        row = next(c for r in bad["rails"] for c in r["capabilities"] if c["observation"])
        row["observation"]["latest"]["agrees"] = False
        assert "DISAGREES with this row" in page.render(bad)

    def test_a_quote_edited_after_its_check_is_said_to_be(self):
        bad = copy.deepcopy(DOC)
        row = next(c for r in bad["rails"] for c in r["capabilities"] if c["verification"])
        row["verification"]["quote_current"] = False
        assert "The quote was edited after this check." in page.render(bad)

    def test_it_says_a_sandbox_is_not_an_issuer(self):
        assert "sandbox is not an issuer" in HTML

    def test_the_headline_numbers_come_from_the_registry(self):
        caps = sum(len(r["capabilities"]) for r in DOC["rails"])
        limits = sum(len(r["limits"]) for r in DOC["rails"])
        every = [row for r in DOC["rails"] for row in (*r["capabilities"], *r["limits"])]
        reread = sum(1 for row in every if row["verification"] and row["verification"]["result"]
                     in ("verified", "verified_fragments", "verified_by_copy"))
        unread = sum(1 for row in every if row["verification"] and row["verification"]["result"] == "unfetchable")
        observed = sum(1 for row in every if row["tier"] == "observed")
        assert f'<b>{len(DOC["rails"])}</b>rails' in HTML
        assert f'<b>{caps}</b>capabilities' in HTML and f'<b>{limits}</b>limits' in HTML
        assert reread > 0 and f'<b>{reread}</b>quotes re-read from their source' in HTML
        assert unread > 0 and f'<b>{unread}</b>sources that could not be read' in HTML
        assert f'<b>{observed}</b>measured against an API' in HTML

    def test_a_source_that_could_not_be_read_is_never_counted_as_re_read(self):
        """The headline once said 97 quotes were re-read; 15 of them could not even be fetched."""
        bad = copy.deepcopy(DOC)
        for r in bad["rails"]:
            for row in (*r["capabilities"], *r["limits"]):
                if row["verification"]:
                    row["verification"]["result"] = "unfetchable"
        out = page.render(bad)
        assert "<b>0</b>quotes re-read from their source" in out
        checked = sum(1 for r in bad["rails"] for row in (*r["capabilities"], *r["limits"]) if row["verification"])
        assert f"<b>{checked}</b>sources that could not be read" in out

    def test_the_evidence_streams_and_source_hashes_are_printed(self):
        for s in DOC["stores"]:
            assert s["head"] in HTML
        for s in DOC["sources"]:
            assert s["sha256"] in HTML


class TestThePublishedFileIsCurrent:
    def test_the_committed_page_is_the_one_the_code_writes(self):
        committed = (ROOT / "docs" / "registry" / "index.html").read_text(encoding="utf-8")
        assert committed == page.render(), "run: python -m amanat.registry.page"
