"""T-039: an XML `<title>` emptied a feed's extracted text.

THE MECHANISM.  `extract._TextExtractor` routes `<title>` character data to `RawDoc.title`
and deliberately keeps it OUT of the text, so a quote can never be citable to a tab label.
That is exactly right for HTML, where there is one `<title>` and it is chrome.

An Atom or RSS document has one `<title>` per ENTRY plus one for the channel, and they are
the headlines -- the body copy of the document.  Routing every one of them to
`RawDoc.title` extracted a feed whose entries carry only headlines to the EMPTY STRING, and
`fetch_text` answers an empty document with `None`.  A perfectly readable page became no
page, and `client._remember_non_text`'s sibling path then cached the nothing.

WHY IT IS STILL REACHABLE.  `connectors/self_page.py:254` reads the member's feed through
`fetch_record` + `feed.parse_feed(record.body)`, on the RAW body -- so the connector whose
job is feeds does not go through here.  Two other routes do, and neither is guarded:

* `connectors/wayback.py:115` fetches an archived capture through `get_page` ->
  `fetch_text`.  Its CDX query is `{host}/*` with no mimetype filter, so `/feed`,
  `/rss.xml` and `/atom.xml` captures are enumerated and fetched like any other page.
* `connectors/self_page.py:297` fetches seed URLs from `person.details` and feed-entry
  target URLs.  The `is_feed_url` guard is applied only to crawled page links
  (`self_page.py:156`), not to either of those.

THE FIX, and its boundary.  Only the FIRST `<title>` is the document's own.  For HTML that
is the same one title as before and nothing changes -- which the last section here pins.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_text
from arrival.http.extract import html_title, html_to_text

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://feed.example.com/atom.xml"

_ATOM = (
    "<?xml version='1.0' encoding='utf-8'?>"
    "<feed xmlns='http://www.w3.org/2005/Atom'>"
    "<title>Thornfield Loom</title>"
    "<entry><title>The maintenance almanac ships on Friday</title></entry>"
    "<entry><title>Warp tension, measured over eleven years</title></entry>"
    "<entry><title>Why we still buy Aubusson fibre</title></entry>"
    "</feed>"
)

_RSS = (
    "<?xml version='1.0'?><rss version='2.0'><channel>"
    "<title>Thornfield Loom</title>"
    "<item><title>The maintenance almanac ships on Friday</title></item>"
    "<item><title>Warp tension, measured over eleven years</title></item>"
    "</channel></rss>"
)


def _serve(monkeypatch, content_type: str, body: str):
    async def handle(self, request, **_):
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            content=body.encode(),
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()


# --- 1. a feed of headlines is a document ---------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/atom+xml", _ATOM),
        ("application/rss+xml", _RSS),
        ("text/xml", _ATOM),
        # A feed served as text/html is the common misconfiguration, and it took the same
        # branch: `client._extract` merges html and xml into one call.
        ("text/html", _ATOM),
    ],
    ids=["atom", "rss", "text-xml", "atom-as-html"],
)
def test_a_feed_whose_entries_are_only_headlines_is_still_a_document(
    monkeypatch, tmp_path, content_type, body
):
    _serve(monkeypatch, content_type, body)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None, (
        f"a {content_type} feed carrying three readable headlines extracted to the empty "
        "string and became no document at all"
    )
    assert "The maintenance almanac ships on Friday" in doc.text, (
        f"the entry headlines are the document's body copy: {doc.text!r}"
    )


def test_the_channel_title_is_still_the_document_title_and_not_body_copy(
    monkeypatch, tmp_path
):
    """The half of the old behaviour that was right, kept: the FIRST title names the
    document, so nothing can be quoted back as if the channel name were a sentence."""
    _serve(monkeypatch, "application/atom+xml", _ATOM)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert doc.title == "Thornfield Loom"
    assert doc.text.count("Thornfield Loom") == 0, (
        f"the channel title leaked into the quotable text: {doc.text!r}"
    )


def test_every_entry_headline_survives_extraction():
    """Directly on the extractor, so the count is unambiguous."""
    text = html_to_text(_ATOM)

    for headline in (
        "The maintenance almanac ships on Friday",
        "Warp tension, measured over eleven years",
        "Why we still buy Aubusson fibre",
    ):
        assert headline in text, f"{headline!r} was dropped; got {text!r}"


def test_the_document_title_is_the_first_one_not_all_of_them_concatenated():
    """`html_title` used to accumulate every `<title>` in the document, so a three-entry
    feed produced a `RawDoc.title` that was four headlines run together with no spaces."""
    assert html_title(_ATOM) == "Thornfield Loom"
    assert html_title(_RSS) == "Thornfield Loom"


# --- 2. HTML behaviour is unchanged, which is the constraint on the fix ---------------


def test_an_html_page_still_keeps_its_title_out_of_the_quotable_text(
    monkeypatch, tmp_path
):
    """The property T-039 may not cost: a host must never be handed a quote that is really
    the browser tab's label."""
    page = (
        "<html><head><title>Thornfield Loom | Est. 2014</title></head><body>"
        "<p>Thornfield Loom publishes a monthly maintenance almanac.</p>"
        "</body></html>"
    )
    _serve(monkeypatch, "text/html", page)

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert doc.title == "Thornfield Loom | Est. 2014"
    assert "Est. 2014" not in doc.text, (
        f"the tab label became quotable body copy: {doc.text!r}"
    )
    assert "monthly maintenance almanac" in doc.text


def test_an_inline_svg_title_neither_becomes_the_page_title_nor_leaks_into_the_text():
    """An icon in a page header is the common case, and `<svg>` is a drop-content element.

    This is the edge the "first title wins" rule could plausibly have broken: if a
    suppressed `<title>` counted as the document's, a page with an icon above its `<head>`
    would lose its real title to an accessibility label.
    """
    page = (
        "<html><body><header><svg><title>Home icon</title></svg></header>"
        "<title>Thornfield Loom | Est. 2014</title>"
        "<p>The loom publishes a monthly maintenance almanac.</p></body></html>"
    )

    assert html_title(page) == "Thornfield Loom | Est. 2014", (
        "a suppressed <svg><title> must not be mistaken for the document's own title"
    )
    text = html_to_text(page)
    assert "Home icon" not in text, "an accessibility label is not body copy"
    assert "Est. 2014" not in text, "the real title is still kept out of the quotable text"
    assert "monthly maintenance almanac" in text
