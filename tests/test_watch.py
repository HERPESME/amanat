"""The quote watcher: every cited quote is re-checked against its source.

The rule of the registry is that a row carries text *actually read*. A row can stay honest
only if the page it cites keeps saying it, so the watcher re-fetches the source and looks for
the quote, verbatim. An agent (or a person) may propose a row; this check, not a model, admits
it. Nothing here touches the network: pages come from a fake fetcher.
"""
import hashlib

import pytest

from amanat.registry import store, watch
from amanat.registry.watch import FetchError, Fetched, Row

FILLER = "<p>" + "Unrelated documentation text that keeps the page long enough to be real. " * 12 + "</p>"


def page(body: str, *, status=200, ctype="text/html; charset=utf-8", url="https://docs.example/p"):
    return Fetched(status, ctype, ("<html><body>" + body + FILLER + "</body></html>").encode(), url)


class Pages:
    """A fetcher that serves canned pages and remembers what it was asked for."""

    def __init__(self, **by_url):
        self.by_url, self.calls = by_url, []

    def __call__(self, url):
        self.calls.append(url)
        v = self.by_url[url]
        if isinstance(v, Exception):
            raise v
        return v


def row(quote="Capture amount must be equal to the amount authorized.", *, tier="secondary",
        url="https://docs.example/p", name="partial_debit", rail="r", kind="capability"):
    return Row(rail, kind, name, tier, url, quote)


def one(fetch, r, **kw):
    (res,) = watch.run(fetch, rows=[r], **kw)
    return res


class TestMatching:
    @pytest.mark.parametrize("text", [
        "Capture&nbsp;amount   must be\n equal to the amount authorized.",
        "<b>Capture</b> amount <i>must be</i> equal to the amount&#32;authorized.",
        "Capture amount must be equal to the amount authorized.",
    ])
    def test_whitespace_entities_and_inline_markup_do_not_defeat_a_match(self, text):
        assert one(Pages(**{"https://docs.example/p": page(f"<p>{text}</p>")}), row())["result"] == "verified"

    def test_typographic_quotes_and_dashes_are_the_same_text(self):
        q = "Don't capture more - it's refused."
        served = page("<p>Don’t capture more — it’s refused.</p>")
        assert one(Pages(**{"https://docs.example/p": served}), row(q))["result"] == "verified"

    def test_a_different_word_is_not_a_match(self):
        served = page("<p>Capture amount must be less than the amount authorized.</p>")
        r = one(Pages(**{"https://docs.example/p": served}), row())
        assert r["result"] == "not_found" and "not present" in r["detail"]

    def test_case_still_matters(self):
        served = page("<p>capture amount must be equal to the amount authorized.</p>")
        assert one(Pages(**{"https://docs.example/p": served}), row())["result"] == "not_found"

    def test_text_that_only_appears_in_a_script_or_style_is_not_page_text(self):
        served = page('<script>var s = "Capture amount must be equal to the amount authorized.";</script>'
                      "<style>.x:before{content:'Capture amount must be equal to the amount authorized.'}</style>")
        assert one(Pages(**{"https://docs.example/p": served}), row())["result"] == "not_found"

    def test_block_elements_separate_words_but_inline_ones_do_not(self):
        served = page("<p>Capture amount</p><p>must be equal</p><div>to the amount authorized.</div>")
        assert one(Pages(**{"https://docs.example/p": served}), row())["result"] == "verified"
        joined = page("<p>Capture amount must be equal to the amount author<b>ized</b>.</p>")
        assert one(Pages(**{"https://docs.example/p": joined}), row())["result"] == "verified"

    def test_text_either_side_of_a_block_tag_is_a_separate_word(self):
        after = page("<div>Capture amount</div>must be equal to the amount authorized.")
        before = page("Capture amount<div>must be equal to the amount authorized.</div>")
        for served in (after, before):
            assert one(Pages(**{"https://docs.example/p": served}), row())["result"] == "verified"

    def test_ordered_fragments_are_accepted_when_the_quote_marks_its_gaps(self):
        q = "Once captured … cannot be voided"
        served = page("<p>Once captured, a transaction cannot be voided.</p>")
        assert one(Pages(**{"https://docs.example/p": served}), row(q))["result"] == "verified_fragments"

    def test_fragments_out_of_order_or_missing_are_not_found(self):
        q = "cannot be voided … Once captured"
        served = page("<p>Once captured, a transaction cannot be voided.</p>")
        assert one(Pages(**{"https://docs.example/p": served}), row(q))["result"] == "not_found"
        q2 = "Once captured … refunds are instant"
        assert one(Pages(**{"https://docs.example/p": served}), row(q2))["result"] == "not_found"

    def test_a_short_fragment_cannot_smuggle_a_match(self):
        """`a … b` would match any page containing the letters a and b."""
        served = page("<p>Something entirely different.</p>")
        assert one(Pages(**{"https://docs.example/p": served}), row("a … b"))["result"] == "not_found"

    def test_markdown_syntax_is_not_part_of_the_sentence(self):
        md = Fetched(200, "text/markdown",
                     b"# Capture\n\n**Capture amount** must be [equal](https://x.test) to the "
                     b"`amount` authorized.\n" + b"Padding sentence for length. " * 30, "https://docs.example/p.md")
        r = one(Pages(**{"https://docs.example/p.md": md}),
                row("Capture amount must be equal to the amount authorized.", url="https://docs.example/p.md"))
        assert r["result"] == "verified"


class TestQuotesFromMarkdownSources:
    """A spec written in Markdown is quoted as it is read, or as it is written: either way the
    words are the same. Backticks, emphasis, links and table pipes are not part of the sentence."""

    MD = (b"# Scheme\n\n| Phase | Effect | Repeat |\n|---|---|---|\n"
          b"| `authorize` | Reserves the client's funds, where they are held. | No \xe2\x80\x94 once per payment. |\n\n"
          b"The **facilitator** verifies the signature against `permitted.amount` and [transfers](https://x.test) it.\n"
          + b"Padding sentence for length. " * 30)

    def _one(self, quote):
        f = Fetched(200, "text/markdown", self.MD, "https://raw.example/spec.md")
        return one(Pages(**{"https://raw.example/spec.md": f}),
                   row(quote, url="https://raw.example/spec.md"))["result"]

    def test_a_quote_that_keeps_the_backticks_still_matches(self):
        assert self._one("The facilitator verifies the signature against `permitted.amount`") == "verified"

    def test_a_quote_that_drops_them_matches_too(self):
        assert self._one("The facilitator verifies the signature against permitted.amount") == "verified"

    def test_a_table_row_quoted_with_its_pipes_matches(self):
        assert self._one("`authorize` | Reserves the client's funds, where they are held. | No - once per payment.") == "verified"

    def test_a_link_quoted_with_its_markup_matches(self):
        assert self._one("verifies the signature against permitted.amount and [transfers](https://x.test) it") == "verified"

    def test_a_different_word_still_does_not(self):
        assert self._one("The facilitator refuses the signature against `permitted.amount`") == "not_found"


class TestWhatIsChecked:
    def test_an_observed_row_is_skipped_because_a_probe_checks_it_not_a_page(self):
        f = Pages()
        r = one(f, row(tier="observed"))
        assert r["result"] == "skipped" and "probe" in r["detail"] and f.calls == []

    def test_an_unverified_row_and_a_row_without_a_url_or_quote_are_skipped(self):
        f = Pages()
        assert one(f, row(tier="unverified", quote=""))["result"] == "skipped"
        assert one(f, row(url=""))["result"] == "skipped"
        assert one(f, row(quote=""))["result"] == "skipped"
        assert f.calls == []

    def test_a_url_shared_by_several_rows_is_fetched_once(self):
        f = Pages(**{"https://docs.example/p": page("<p>Capture amount must be equal to the amount authorized.</p>")})
        res = watch.run(f, rows=[row(name="a"), row(name="b"), row(name="c")])
        assert [r["result"] for r in res] == ["verified"] * 3 and f.calls == ["https://docs.example/p"]


class TestWhenThePageCannotBeRead:
    def test_an_http_error_is_unfetchable_not_a_missing_quote(self):
        r = one(Pages(**{"https://docs.example/p": Fetched(404, "text/html", b"nope", "u")}), row())
        assert r["result"] == "unfetchable" and r["http_status"] == 404

    def test_an_error_page_that_repeats_the_phrase_is_not_a_verification(self):
        """A soft 404 often echoes the words it was asked for. Only a 200 is a source."""
        echo = page("<p>Not found: Capture amount must be equal to the amount authorized.</p>", status=404)
        echo = Fetched(404, echo.content_type, echo.body, echo.final_url)
        r = one(Pages(**{"https://docs.example/p": echo}), row())
        assert r["result"] == "unfetchable" and r["detail"] == "HTTP 404"

    def test_a_transport_failure_is_unfetchable(self):
        r = one(Pages(**{"https://docs.example/p": FetchError("timed out")}), row())
        assert r["result"] == "unfetchable" and "timed out" in r["detail"]

    def test_a_fetcher_that_blows_up_does_not_stop_the_run(self):
        f = Pages(**{"https://docs.example/p": RuntimeError("boom")})
        assert one(f, row())["result"] == "unfetchable"

    def test_a_page_with_no_readable_text_is_reported_as_such_not_as_a_missing_quote(self):
        shell = Fetched(200, "text/html", b"<html><body><div id=root></div><script>x()</script></body></html>", "u")
        r = one(Pages(**{"https://docs.example/p": shell}), row())
        assert r["result"] == "unfetchable" and "no readable text" in r["detail"]

    def test_a_pdf_with_a_text_layer_is_searched(self):
        pdf = Fetched(200, "application/pdf", b"%PDF-1.7 fake", "u")
        text = "Padding. " * 100 + "Capture amount must be equal to the amount authorized."
        r = one(Pages(**{"https://docs.example/p": pdf}), row(), pdf_to_text=lambda b: text)
        assert r["result"] == "verified"


class TestImageOnlyScans:
    """The NPCI circulars are image-only PDFs. Their text cannot be searched, but the committed
    copy the quotes were transcribed from can be compared, byte for byte, with what the
    regulator serves today."""

    def _pdf(self, body=b"%PDF-1.4 scanned"):
        return Fetched(200, "application/pdf", body, "https://npci.example/oc.pdf")

    def test_a_document_identical_to_a_committed_copy_is_verified_by_that_copy(self):
        pdf = self._pdf()
        copies = {hashlib.sha256(pdf.body).hexdigest(): "docs/sources/NPCI-OC-228.pdf"}
        r = one(Pages(**{"https://npci.example/oc.pdf": pdf}), row(url="https://npci.example/oc.pdf"),
                pdf_to_text=lambda b: "", copies=copies)
        assert r["result"] == "verified_by_copy" and "docs/sources/NPCI-OC-228.pdf" in r["detail"]

    def test_a_document_that_differs_from_every_committed_copy_is_flagged_as_changed(self):
        r = one(Pages(**{"https://npci.example/oc.pdf": self._pdf(b"%PDF-1.4 amended")}),
                row(url="https://npci.example/oc.pdf"), pdf_to_text=lambda b: "",
                copies={"0" * 64: "docs/sources/other.pdf"})
        assert r["result"] == "document_changed"

    def test_without_pdftotext_a_pdf_is_unfetchable_unless_a_copy_matches(self):
        r = one(Pages(**{"https://npci.example/oc.pdf": self._pdf()}), row(url="https://npci.example/oc.pdf"),
                pdf_to_text=lambda b: None, copies={})
        assert r["result"] == "unfetchable" and "pdftotext" in r["detail"]


class TestTheResultRecord:
    def test_it_carries_the_hash_of_what_was_fetched_and_of_the_quote_checked(self):
        served = page("<p>Capture amount must be equal to the amount authorized.</p>")
        r = one(Pages(**{"https://docs.example/p": served}), row())
        assert r["content_sha256"] == hashlib.sha256(served.body).hexdigest()
        assert r["quote_sha256"] == watch.quote_hash("Capture amount must be equal to the amount authorized.")
        assert set(r) >= {"rail_id", "kind", "name", "url", "result", "detail", "http_status"}

    def test_the_quote_hash_ignores_only_what_the_match_ignores(self):
        assert watch.quote_hash("A  b’s") == watch.quote_hash("A b's")
        assert watch.quote_hash("A b") != watch.quote_hash("A c")


class TestRecording:
    def _results(self):
        f = Pages(**{"https://docs.example/p": page("<p>Capture amount must be equal to the amount authorized.</p>")})
        return watch.run(f, rows=[row(name="a"), row(name="b", tier="observed")])

    def test_a_run_is_one_record_in_the_chained_store(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        h = watch.record(self._results(), path, at="2026-09-20T12:00:00Z")
        rec = store.find(path, h)
        assert rec["kind"] == "watch_run" and rec["at"] == "2026-09-20T12:00:00Z"
        assert rec["data"]["counts"] == {"verified": 1, "skipped": 1}
        assert store.verify(path).length == 1

    def test_the_latest_result_per_row_wins(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        good = self._results()
        bad = [dict(r, result="not_found") if r["name"] == "a" else r for r in good]
        watch.record(good, path, at="2026-09-20T12:00:00Z")
        watch.record(bad, path, at="2026-09-21T12:00:00Z")
        latest = watch.latest(path)
        assert latest[("r", "capability", "a")]["result"] == "not_found"
        assert latest[("r", "capability", "a")]["checked_at"] == "2026-09-21T12:00:00Z"

    def test_history_lists_each_check_of_a_row_oldest_first(self, tmp_path):
        path = tmp_path / "watch.jsonl"
        watch.record(self._results(), path, at="2026-09-20T12:00:00Z")
        watch.record(self._results(), path, at="2026-09-21T12:00:00Z")
        hist = watch.history(path)[("r", "capability", "a")]
        assert [h["checked_at"] for h in hist] == ["2026-09-20T12:00:00Z", "2026-09-21T12:00:00Z"]

    def test_latest_of_a_missing_store_is_empty(self, tmp_path):
        assert watch.latest(tmp_path / "none.jsonl") == {}


class TestTheRegistryRows:
    def test_every_cited_row_of_the_registry_is_offered_to_the_watcher(self):
        from amanat.rails.semantics import RAILS
        got = {(r.rail_id, r.kind, r.name) for r in watch.rows()}
        expected = {(rid, kind, row_.name)
                    for rid, rail in RAILS.items()
                    for kind, rows in (("capability", rail.capabilities), ("limit", rail.limits))
                    for row_ in rows.values()}
        assert got == expected


class TestTheFetcherAsksForOneLocale:
    """Vendors localise their documentation, so a quote is checked against one rendering.

    docs.stripe.com serves "Authorising a payment guarantees the amount" to one reader and
    "Authorizing ..." to another, by Accept-Language and by where the request comes from. A
    scheduled job on a US runner would have reported six quotes missing that a person in India
    could read. The watcher asks for one locale; the registry's quotes are transcribed in it.
    """

    def _requests_made(self):
        import httpx

        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, text="<p>Authorizing a payment</p>",
                                  headers={"content-type": "text/html"})

        fetch = watch.http_fetcher(delay=0, transport=httpx.MockTransport(handler))
        fetch("https://docs.example/p")
        return seen

    def test_the_request_names_a_locale(self):
        (request,) = self._requests_made()
        assert request.headers["accept-language"] == watch.LOCALE
        assert watch.LOCALE.startswith("en-US")

    def test_the_request_still_says_who_is_asking(self):
        (request,) = self._requests_made()
        assert request.headers["user-agent"] == watch.USER_AGENT

    def test_a_quote_carries_the_punctuation_the_pinned_locale_serves_not_only_its_spelling(self):
        """Stripe's lifecycle page reads "...releases any held funds and can't be undone" to an en-GB
        reader and "...held funds, and can't be undone" (a serial comma) to an en-US one: found by the
        first live run with the locale pinned, not by the reviewer who named the spelling."""
        from amanat.rails.semantics import RAILS

        quote = RAILS["stripe_card_manual_capture"].capabilities["void_whole_hold"].quote
        assert "held funds, and can\u2019t be undone" in quote

    def test_no_quote_is_transcribed_in_a_spelling_the_pinned_locale_does_not_serve(self):
        """A quote on a vendor page that localises must be in the en-US rendering."""
        from amanat.rails.semantics import RAILS

        british = ("authoris", "Authoris")
        for rail_id in ("stripe_card_manual_capture",):
            for group in (RAILS[rail_id].capabilities, RAILS[rail_id].limits):
                for row_ in group.values():
                    assert not any(b in row_.quote for b in british), (rail_id, row_.name)
