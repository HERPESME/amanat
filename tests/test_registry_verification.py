"""The committed watch log is a ratchet on the registry.

`python -m amanat.registry.watch` needs the network, so the suite does not run it. It does read
the committed results, which is enough to hold three lines: every quote that can be checked has
been; none is known to be missing from its source; and editing a quote means checking it again.
Anything the watcher cannot read must be named, with the reason.
"""
import pytest

from amanat.registry import store, watch

LATEST = watch.latest() if store.stream_path(watch.STREAM).exists() else {}
CHECKABLE = [r for r in watch.rows() if watch._skip_reason(r) is None]


def test_the_rows_offered_to_the_check_are_exactly_the_cited_ones():
    """Computed independently of the watcher's own rule, so a rule that skips everything cannot
    make every per-row test below vanish."""
    from amanat.rails.semantics import RAILS
    cited = {(rid, kind, row.name)
             for rid, rail in RAILS.items()
             for kind, group in (("capability", rail.capabilities), ("limit", rail.limits))
             for row in group.values()
             if row.source_tier.value in ("primary", "secondary", "marketing") and row.url and row.quote.strip()}
    assert {r.key for r in CHECKABLE} == cited and cited


def test_the_watch_log_is_an_unbroken_chain():
    assert store.verify(store.stream_path(watch.STREAM)).length >= 1


@pytest.mark.parametrize("row", CHECKABLE, ids=lambda r: f"{r.rail_id}.{r.name}")
def test_every_checkable_row_has_been_checked(row):
    assert row.key in LATEST, "run: python -m amanat.registry.watch"


@pytest.mark.parametrize("row", CHECKABLE, ids=lambda r: f"{r.rail_id}.{r.name}")
def test_no_quote_is_known_to_be_missing_from_its_source(row):
    res = LATEST.get(row.key)
    assert res is None or res["result"] not in (watch.NOT_FOUND, watch.DOCUMENT_CHANGED), \
        f"{res['result']}: {res['detail']} — fix the row or downgrade it to UNVERIFIED"


@pytest.mark.parametrize("row", CHECKABLE, ids=lambda r: f"{r.rail_id}.{r.name}")
def test_a_quote_edited_after_its_last_check_is_checked_again(row):
    res = LATEST.get(row.key)
    assert res is None or res["quote_sha256"] == watch.quote_hash(row.quote), \
        "the quote changed since it was last checked: run python -m amanat.registry.watch"


@pytest.mark.parametrize("row", CHECKABLE, ids=lambda r: f"{r.rail_id}.{r.name}")
def test_a_url_changed_after_its_last_check_is_checked_again(row):
    """A quote verified on one page says nothing about another."""
    res = LATEST.get(row.key)
    assert res is None or res["url"] == row.url, \
        "the url changed since the quote was last checked: run python -m amanat.registry.watch"


def test_the_only_sources_the_watcher_cannot_read_are_the_regulators_scans():
    """A row that becomes unfetchable elsewhere (a page rewritten as a JavaScript app, a moved
    URL) must be noticed, not absorbed."""
    unread = {k for k, v in LATEST.items() if v["result"] == watch.UNFETCHABLE}
    urls = {r.key: r.url for r in watch.rows()}
    assert {k for k in unread if "npci.org.in" not in urls[k]} == set()
    assert all(LATEST[k]["detail"] for k in unread), "an unfetchable row must say why"
