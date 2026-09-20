"""A registry is cited, so its notes read as findings and not as a pitch.

The rows' `notes` are published in the JSON export, the comparison page and the rail document. Some
were drafted in the voice of a presentation: an instruction to a salesperson ("Volunteer this in the
pitch"), a rail's own state called a "trap", shouted headings, advice to the reader. A vendor engineer
who opens the first row about their product and finds that does not answer the file, whatever the
facts underneath. The facts stay; the voice goes, and this keeps it out.
"""
import re
from pathlib import Path

from amanat.rails.semantics import RAILS

ROOT = Path(__file__).resolve().parents[1]
TIERS = re.compile(r"\b(?:PRIMARY|SECONDARY|OBSERVED|UNVERIFIED|MARKETING)\b")
SHOUT = re.compile(r"\b[A-Z]{3,}(?:[ ,;:'’-]+[A-Z]{3,}){2,}")
FORBIDDEN = re.compile(
    r"\bpitch\b|on camera|out loud|\bvolunteer|\btrap\b|price it that way|worth building on|"
    r"\bdo not cite\b|\bnever (?:quote|cite)\b|this project should|good news|the honest one|walk away|"
    r"most consequential|\bround [0-9]\b|\bskill\b|\.claude", re.IGNORECASE)


def _notes():
    for rail in RAILS.values():
        for kind, group in (("capability", rail.capabilities), ("limit", rail.limits)):
            for name, row in group.items():
                yield rail.rail_id, kind, name, row.notes


def test_no_note_uses_the_language_of_a_presentation_or_addresses_the_reader():
    found = [(rid, name, m.group(0)) for rid, _, name, notes in _notes() for m in FORBIDDEN.finditer(notes)]
    assert found == []


def test_no_note_shouts():
    found = [(rid, name, m.group(0)) for rid, _, name, notes in _notes()
             for m in SHOUT.finditer(TIERS.sub("", notes))]
    assert found == []


def test_the_first_row_about_razorpay_says_what_its_authorized_state_is_and_is_not():
    note = RAILS["razorpay_auth_capture"].capabilities["funds_held_in_customer_account"].notes
    assert note.startswith("Razorpay's 'authorized' state is not a hold")
    assert "settled to the merchant on capture" in note and "auto-refunded" in note
    assert "sbmd.funds_held_in_customer_account" in note and "cashfree_preauth.funds_held_in_customer_account" in note


def test_the_published_files_carry_none_of_it_either():
    for path in ("docs/registry/registry.json", "docs/registry/index.html", "docs/RAIL_SEMANTICS.md"):
        text = (ROOT / path).read_text(encoding="utf-8")
        found = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)})
        assert found == [], (path, found)
