"""T-038: two captures of one archived page are one document, not two.

`BaseConnector._finalise` de-duplicates on `doc_id`, which is `sha1(url)[:16]`.  For nine
of the ten connectors that is the right rule, because their url identifies their content.
`wayback` is the exception: its citation is a REPLAY address,
`https://web.archive.org/web/{timestamp}/{original}`, so the same page captured twice has
two urls, two `doc_id`s and both survive — two of `max_docs_total` spent, two LLM verdicts
paid, and one sentence quotable twice as if two sources had said it.

`collapse=urlkey` does not prevent this.  It collapses by URL KEY, and `http://site/`,
`https://site/`, `site/about` and `site/about/` are four keys over the same bytes; CDX also
collapses only ADJACENT rows, so a non-adjacent repeat comes back whatever the parameter
says.  What identifies an archived page is `digest`, Wayback's own hash of the archived
bytes, which the connector already asks for in `fl=`.

WHAT THIS MODULE GRADES AGAINST.  Literal CDX payloads written here, `arrival.contracts`
and `arrival.util.doc_id` — never a fixture in `tests/fixtures/http/`, which is in this
ticket's own write scope.  The CDX shape (a list of lists whose first row is the header)
is the real API's, and the failure it is written around was reproduced before the fix:
two rows for `thornfieldloom.example.com/about` four years apart with one digest produced
3 HTTP requests, 2 documents, 2 `doc_id`s and 1 distinct `RawDoc.text`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from t1_recorded import no_real_sleep, settings_for

from arrival.connectors.wayback import DIGEST_FIELD, WaybackConnector, dedupe_by_digest
from arrival.contracts import PersonRef, RawDoc
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("T-1")

HOST = "thornfieldloom.example.com"
SITE = f"https://{HOST}/"

PERSON = PersonRef(
    person_id="marisol-quennebeck",
    name="Marisol Quennebeck",
    details=["co-founder, Thornfield Loom", "Providence, Rhode Island", SITE],
)

HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]

#: Distinct, obviously-synthetic content hashes. Wayback's are base32 SHA-1s.
D_ABOUT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
D_ALMANAC = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
D_NOTES = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


def row(path: str, timestamp: str, digest: str, *, status: str = "200", host: str = HOST) -> list:
    return [
        f"{'.'.join(reversed(host.split('.')))})/{path.lstrip('/')}",
        timestamp,
        f"https://{host}/{path.lstrip('/')}",
        "text/html",
        status,
        digest,
        "5120",
    ]


def page(headline: str, sentence: str) -> str:
    return (
        f"<!doctype html><html><head><title>{headline}</title></head><body>"
        f"<main><h1>{headline}</h1><p>{sentence}</p></main></body></html>"
    )


PROSE = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)


def drive(
    monkeypatch,
    tmp_path,
    cdx: list[list[Any]] | dict[str, list[list[Any]]],
    budget: int = 5,
    person=PERSON,
):
    """Run the connector against a literal CDX payload. Returns (docs, urls requested).

    `cdx` is either one payload for every index request, or a mapping from the `url=`
    pattern the connector sends to the payload that pattern should get back — which is how
    a test can give two roster urls two genuinely different capture lists.
    """
    requested: list[str] = []

    async def handle(self: Any, request: httpx.Request, **_: Any) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if "/cdx/search/cdx" in url:
            pattern = dict(parse_qsl(urlsplit(url).query)).get("url", "")
            payload = cdx[pattern] if isinstance(cdx, dict) else cdx
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(payload).encode(),
                request=request,
            )
        # Every replay address answers with prose naming the capture, so a document that
        # comes back can always be traced to the row that produced it.
        stamp = url.split("/web/", 1)[1].split("/", 1)[0] if "/web/" in url else "?"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=page(f"Thornfield Loom ({stamp})", PROSE).encode(),
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    no_real_sleep(monkeypatch)
    connector = WaybackConnector(settings_for(tmp_path))
    return asyncio.run(connector.search(person, budget)), requested


def replays(requested: list[str]) -> list[str]:
    return [url for url in requested if "/web/" in url]


# --- the defect ------------------------------------------------------------------------


def test_two_captures_of_one_page_are_one_document(monkeypatch, tmp_path):
    """The reproduction, as a permanent test. Before the fix: 2 docs, 1 distinct text."""
    cdx = [
        HEADER,
        row("about", "20180614101500", D_ABOUT),
        row("about", "20220803153000", D_ABOUT),
    ]
    docs, requested = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 1, (
        "two captures of one page came back as two documents. Their replay urls differ, "
        "so sha1(url) differs, so `_finalise` cannot tell them apart -- but `digest` "
        f"says they are one page. Got: {[d.url for d in docs]}"
    )
    assert len({d.text for d in docs}) == len(docs)
    assert isinstance(docs[0], RawDoc) and docs[0].source_kind == "wayback"
    assert docs[0].doc_id == doc_id(docs[0].url)


def test_the_duplicate_is_dropped_before_it_costs_a_request(monkeypatch, tmp_path):
    """A budget slot is not the only thing a duplicate spends: each capture is fetched."""
    cdx = [
        HEADER,
        row("about", "20180614101500", D_ABOUT),
        row("about", "20220803153000", D_ABOUT),
    ]
    _, requested = drive(monkeypatch, tmp_path, cdx)

    assert len(replays(requested)) == 1, (
        "the connector fetched both captures and then discarded one. De-duplicating on "
        f"the CDX row costs no request at all. Asked for: {replays(requested)}"
    )


def test_the_latest_capture_is_the_one_kept(monkeypatch, tmp_path):
    """`published_at` feeds `extract.recency_for` and therefore every edge weight.

    The pair already contributes the newest capture's recency today, because `extract`
    takes `max(recency_for(...))` over a hub's evidence -- so keeping the latest removes
    the duplicate and changes nothing else, while keeping the earliest would silently age
    every de-duplicated capture. The prose is identical either way; that is what a shared
    digest means.
    """
    cdx = [
        HEADER,
        row("about", "20180614101500", D_ABOUT),
        row("about", "20220803153000", D_ABOUT),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert docs[0].published_at is not None
    assert docs[0].published_at.year == 2022, (
        f"kept the {docs[0].published_at} capture of two identical ones; the archive last "
        "observed this text in 2022 and that is the date the recency scoring should see"
    )
    assert "20220803153000" in docs[0].url


def test_one_page_under_two_addresses_is_still_one_page(monkeypatch, tmp_path):
    """The commonest real shape: `collapse=urlkey` keeps both `/about` and `/about/`."""
    cdx = [
        HEADER,
        row("about", "20200211084500", D_ABOUT),
        row("about/", "20210105120000", D_ABOUT),
        row("almanac", "20220803153000", D_ALMANAC),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 2, (
        "`/about` and `/about/` are two url keys over one page, and the archive says so "
        f"with one digest. Got {[d.url for d in docs]}"
    )
    assert any("almanac" in d.url for d in docs), "a genuinely different page must survive"


def test_a_200_capture_beats_an_error_capture_of_the_same_content(monkeypatch, tmp_path):
    """`filter=statuscode:200` is a server-side hint CDX does not always honour."""
    cdx = [
        HEADER,
        row("about", "20180614101500", D_ABOUT, status="200"),
        row("about", "20220803153000", D_ABOUT, status="404"),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 1
    assert "20180614101500" in docs[0].url, (
        "a capture the archive recorded as 404 displaced a good one just by being newer; "
        "a replay of an error page is not what the site used to say"
    )


def test_distinct_captures_are_all_kept(monkeypatch, tmp_path):
    """The other direction. Over-collapsing would gut the source this connector is for."""
    cdx = [
        HEADER,
        row("", "20180614101500", D_ABOUT),
        row("about", "20200211084500", D_ALMANAC),
        row("notes/2023-11", "20220803153000", D_NOTES),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 3, f"three different pages became {len(docs)} documents"
    assert len({d.published_at for d in docs}) == 3


# --- what happens when the archive does not tell us -------------------------------------


def test_rows_without_a_digest_are_never_merged_with_each_other(monkeypatch, tmp_path):
    """Absence of the key means "unknown", not "the same as the other unknowns".

    A `fl=` change or an older CDX deployment can leave the column out, and collapsing on
    a missing value would drop every capture but one the moment that happened.
    """
    header = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "length"]
    cdx = [
        header,
        ["com,example,thornfieldloom)/", "20180614101500", SITE, "text/html", "200", "5120"],
        [
            "com,example,thornfieldloom)/about",
            "20200211084500",
            f"{SITE}about",
            "text/html",
            "200",
            "6144",
        ],
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 2, (
        "two captures with no digest column collapsed into one. Without the archive's own "
        "hash there is no evidence they are the same page, and `_finalise`'s url dedupe "
        "is still the backstop for the case where they are literally the same address."
    )


def test_an_empty_digest_string_is_treated_as_absent(monkeypatch, tmp_path):
    cdx = [
        HEADER,
        row("about", "20180614101500", ""),
        row("almanac", "20220803153000", ""),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)
    assert len(docs) == 2


def test_a_cdx_response_with_no_header_row_still_de_duplicates(monkeypatch, tmp_path):
    """`_rows` falls back to the documented default column order; dedupe must follow it."""
    cdx = [
        row("about", "20180614101500", D_ABOUT),
        row("about", "20220803153000", D_ABOUT),
    ]
    docs, _ = drive(monkeypatch, tmp_path, cdx)

    assert len(docs) == 1, (
        "a header-less CDX response is positional, and the default field order names "
        f"{DIGEST_FIELD!r}; the dedupe has to work off it too"
    )
    assert "20220803153000" in docs[0].url


def test_the_dedupe_spans_the_several_patterns_one_roster_line_set_produces(
    monkeypatch, tmp_path
):
    """A roster naming a company site and a personal one archives the same page twice."""
    other = "quennebeck.example.org"
    person = PersonRef(
        person_id="marisol-quennebeck",
        name="Marisol Quennebeck",
        details=["co-founder, Thornfield Loom", SITE, f"https://{other}/"],
    )
    # Two patterns, two DIFFERENT capture lists, one digest. The replay urls differ, so
    # `_finalise` cannot help and the per-call dedupe never sees the pair: only a digest
    # set carried across the patterns can catch this.
    cdx = {
        f"{HOST}/*": [HEADER, row("about", "20180614101500", D_ABOUT)],
        f"{other}/*": [HEADER, row("about", "20220803153000", D_ABOUT, host=other)],
    }
    docs, requested = drive(monkeypatch, tmp_path, cdx, person=person)

    assert len(docs) == 1, (
        "the same archived bytes were cited once per roster url. A digest seen under one "
        f"pattern is seen. Got {[d.url for d in docs]}"
    )
    assert len(replays(requested)) == 1, (
        f"the second copy was fetched before being discarded: {replays(requested)}"
    )


# --- the rule on its own ----------------------------------------------------------------


def test_dedupe_by_digest_keeps_first_seen_order():
    """CDX returns captures oldest-first and a tight budget takes the head of the list, so
    the winner takes the loser's PLACE rather than being appended."""
    rows = [
        {"timestamp": "20180101000000", "digest": D_ABOUT, "statuscode": "200"},
        {"timestamp": "20190101000000", "digest": D_ALMANAC, "statuscode": "200"},
        {"timestamp": "20200101000000", "digest": D_ABOUT, "statuscode": "200"},
    ]
    kept = dedupe_by_digest(rows)

    assert [r["digest"] for r in kept] == [D_ABOUT, D_ALMANAC]
    assert kept[0]["timestamp"] == "20200101000000", "the later capture wins its slot"


def test_dedupe_by_digest_does_not_mutate_its_input():
    rows = [
        {"timestamp": "20180101000000", "digest": D_ABOUT},
        {"timestamp": "20200101000000", "digest": D_ABOUT},
    ]
    before = json.dumps(rows, sort_keys=True)
    dedupe_by_digest(rows)
    assert json.dumps(rows, sort_keys=True) == before


def test_dedupe_by_digest_on_an_empty_list_is_empty():
    assert dedupe_by_digest([]) == []
