"""A place to disagree.

The registry says things about other people's products, dated and cited. The vendor protocol says the
project tells a vendor *before* it publishes; the other half is a path for a vendor (or anyone) to say a
row is wrong once it is published, and a promise about what happens then. An adopter review read the
whole repository as a PSP engineer would and found neither the path nor the licence of the data.
"""
import json
import re
from pathlib import Path

import jsonschema

from amanat.registry import export, page

ROOT = Path(__file__).resolve().parents[1]
DOC = export.build()
HTML = page.render(DOC)
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "row-correction.md"
PROMISE = "Corrections are dated and marked as corrections, never silently edited."


def flat(text):
    """Prose is wrapped where it was written; a reader ignores that, and so does this."""
    return " ".join(text.split())


class TestTheIssueTemplate:
    def test_it_exists_and_titles_the_issue_by_the_row(self):
        raw = TEMPLATE.read_text(encoding="utf-8")
        text, head = flat(raw), raw.split("---")[1]
        assert 'title: "row: <rail_id>.<capability>"' in head and "label" in head
        for phrase in ("the sentence you would put instead", "a public page", "recorded beside the observation"):
            assert phrase in text.lower(), phrase
        assert PROMISE in text

    def test_it_says_a_private_source_is_welcome_but_is_not_a_checkable_one(self):
        assert "private" in flat(TEMPLATE.read_text(encoding="utf-8")).lower()


class TestThePageSaysWhereToDisagree:
    def test_the_top_of_the_page_says_it_once_with_the_promise(self):
        assert "Work at one of these rails?" in HTML
        assert re.search(r"open an issue</a> titled <code>row: &lt;rail_id&gt;\.&lt;capability&gt;</code>", HTML)
        assert PROMISE in HTML

    def test_every_rail_section_says_it_with_its_own_rail_id(self):
        for rail in DOC["rails"]:
            sec = re.search(rf'<section class="rail-sec" id="rail-{re.escape(rail["rail_id"].replace("_", "-"))}">.*?</section>',
                            HTML, flags=re.S).group(0)
            assert f"row: {rail['rail_id']}." in sec, rail["rail_id"]
            assert f'title=row%3A%20{rail["rail_id"]}.' in sec

    def test_the_link_is_the_repositorys_issue_form_and_opens_without_the_openers_authority(self):
        tags = re.findall(r'<a [^>]*href="https://github\.com/HERPESME/amanat/issues/new[^"]*"[^>]*>', HTML)
        assert len(tags) == len(DOC["rails"]) + 1
        assert all('rel="noopener noreferrer"' in t and "template=row-correction.md" in t for t in tags)


class TestTheDataSaysWhatItMayBeUsedFor:
    def test_the_export_carries_its_licence_and_says_a_quote_is_its_sources(self):
        assert DOC["license"]["spdx"] == "Apache-2.0"
        assert "the source's own terms" in DOC["license"]["quotes"] or "source's own terms" in DOC["license"]["quotes"]
        assert DOC["canonical_url"] == "https://github.com/HERPESME/amanat/blob/main/docs/registry/registry.json"
        assert DOC["corrections"].endswith("template=row-correction.md")

    def test_the_schema_requires_them_so_a_consumer_cannot_drop_them(self):
        bad = json.loads(json.dumps(DOC))
        del bad["license"]
        try:
            jsonschema.Draft202012Validator(export.schema()).validate(bad)
        except jsonschema.ValidationError:
            pass
        else:
            raise AssertionError("an export without a licence validated")

    def test_the_licence_matches_the_repositorys(self):
        assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")[:400]


class TestTheReadersFindThePath:
    def test_contributing_has_a_section_for_a_row_about_your_product(self):
        text = flat((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))
        assert "## Correcting a row about your product" in text and "row: <rail_id>.<capability>" in text
        assert PROMISE in text

    def test_the_readme_gives_a_vendor_their_rows_in_one_command_and_says_how_to_correct_one(self):
        text = flat((ROOT / "README.md").read_text(encoding="utf-8"))
        assert "### If you work at a rail in the registry" in text
        assert "jq '.rails[] | select(.rail_id==" in text and "row: <rail_id>.<capability>" in text
        assert PROMISE in text
        assert "(#if-you-work-at-a-rail-in-the-registry)" in text, "linked from the top of the page"
