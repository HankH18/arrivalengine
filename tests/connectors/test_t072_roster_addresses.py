"""T-072: a roster writes an address the way a person says it, and it has to count.

WHAT WAS WRONG, MEASURED ON THE FIRST LIVE RUN (2026-09-04, ten real people).  `self_page`
is the highest-trust `SourceKind` in the system and it appeared in the zero-result list of
ALL TEN people.  The cause was not the crawler, the cache, the encoding detector or the
feed filter — every one of which was a suspect — and the way to tell was to record the
requests the connector actually made rather than to reason about them.  It made two, both
to Wikidata, for every person.  `urls_in(person.details)` had returned `[]` ten times out
of ten, because `_URL_IN_TEXT` requires a scheme and the roster says

    "writes the AVC blog (avc.com)"      "feld.com"      "essays at nabeelqu.co"

That one predicate is also the input to `wayback`'s CDX patterns, `hn`'s declared-site
match and `identity.on_own_host`, so a scheme-less roster silently disabled four sources
and the single most-trusted one among them.

WHY THE ASSERTIONS BELOW ARE SHAPED LIKE THIS.  A bare domain is a guess about prose, and
the cost of guessing wrong is an outbound request to a host nobody named, carrying the
`self_page` stamp on whatever answers.  So the tests come in pairs: the roster spellings
that MUST be read, and the ordinary English that must NOT be — `Ph.D.`, `e.g.`, `U.S.A.`,
`Washington, D.C.`, a sentence that happens to end before a two-letter word.  A predicate
that only ever says yes would pass the first half of this module and fail the product.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search

from arrival.connectors.base import affiliations, bare_domains_in, hosts_in, urls_in
from arrival.connectors.identity import best_affiliation, on_own_host
from arrival.contracts import PersonRef

pytestmark = pytest.mark.ticket("T-1")

#: The three real roster spellings, transposed onto the synthetic subject this suite uses
#: everywhere else. The shapes are copied from `data/roster.yaml`; the words are not.
BARE = PersonRef(
    person_id="marisol-quennebeck",
    name="Marisol Quennebeck",
    details=[
        "co-founder, Thornfield Loom",
        "Providence, Rhode Island",
        "writes the mill-floor notes (thornfieldloom.example.com)",
    ],
)

SITE = "https://thornfieldloom.example.com"
HOME = f"{SITE}/"
ABOUT = f"{SITE}/about"

LINE = (
    "We rewrote the loom scheduler in Providence this spring and the mills now file "
    "their own maintenance windows."
)


def _page(title: str, body: str, extra: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title>{extra}</head>"
        f"<body><p>{body}</p><p><a href='{ABOUT}'>About</a></p></body></html>"
    )


# --- the predicate itself ----------------------------------------------------------


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("writes the AVC blog (avc.com)", ["https://avc.com"]),
        ("feld.com", ["https://feld.com"]),
        ("formerly Palantir; essays at nabeelqu.co", ["https://nabeelqu.co"]),
        ("notes at example.org/marisol", ["https://example.org/marisol"]),
        ("her site is thornfieldloom.example.com.", ["https://thornfieldloom.example.com"]),
    ],
)
def test_a_bare_domain_in_prose_is_an_address(detail, expected):
    assert urls_in([detail]) == expected


@pytest.mark.parametrize(
    "detail",
    [
        "Ph.D. in textile engineering",
        "e.g. the maintenance almanac",
        "born in the U.S.A.",
        "Washington, D.C.",
        "St. Louis, Missouri",
        "co-founder, Thornfield Loom",
        "Providence, Rhode Island",
        "briefly interim CEO of Quarrystone (Nov 2023)",
        "reach her at marisol@thornfieldloom.example.com",
        "version 2.11 of the almanac",
    ],
)
def test_ordinary_prose_is_not_an_address(detail):
    assert urls_in([detail]) == [], (
        "a false positive here is an outbound request to a host the roster never named, "
        "and the highest-trust SourceKind in the system stamped on whatever answers"
    )


def test_a_scheme_url_is_read_once_and_not_again_as_its_own_host():
    details = ["site: https://thornfieldloom.example.com/, blog at https://notes.example.org/m."]

    assert urls_in(details) == [
        "https://thornfieldloom.example.com/",
        "https://notes.example.org/m",
    ]
    assert bare_domains_in(details[0]) == [], (
        "the hosts inside those two urls must not be re-read as bare domains"
    )


def test_hosts_and_the_own_host_predicate_follow_the_bare_spelling():
    assert hosts_in(BARE.details) == ["thornfieldloom.example.com"]
    assert on_own_host(f"{SITE}/notes/3", BARE), (
        "a roster that wrote the address without a scheme still vouches for its own pages"
    )
    assert not on_own_host("https://elsewhere.example/notes/3", BARE)


# --- what the roster's own words are FOR -------------------------------------------


def test_an_address_detail_is_not_offered_as_an_employer():
    found = affiliations(BARE.details)

    assert "Thornfield Loom" in found
    assert not any("thornfieldloom.example.com" in entry for entry in found), (
        "the clause naming the member's website is an address, not an organisation, and a "
        f"search engine handed it looks for a company called that. Got {found!r}"
    )
    assert best_affiliation(BARE) == "Thornfield Loom"


def test_a_semicolon_clause_carrying_a_url_does_not_take_the_company_with_it():
    # The real shape: `"formerly Palantir; essays at nabeelqu.co"`. Skipping the whole
    # detail because it mentions a site threw the previous employer away with it.
    person = PersonRef(
        person_id="marisol-quennebeck",
        name="Marisol Quennebeck",
        details=["writer and researcher", "formerly Thornfield Loom; notes at example.org"],
    )

    assert affiliations(person.details) == ["Thornfield Loom"]
    assert best_affiliation(person) == "Thornfield Loom"


# --- the connectors that live off it -----------------------------------------------


def test_self_page_fetches_the_site_a_roster_named_without_a_scheme(monkeypatch, tmp_path):
    def router(request):
        path, _ = parts(request)
        if "wikidata" in str(request.url):
            pytest.fail(
                "self_page fell through to the Wikidata P856 lookup: the roster NAMED a "
                "site and the fallback is for rosters that do not. That fallthrough is "
                "exactly what the live run did for all ten people."
            )
        if path in ("", "/"):
            return _page("Thornfield Loom", LINE)
        if path == "/about":
            return _page("About", "Marisol Quennebeck founded Thornfield Loom in Providence.")
        return None

    docs, requested = search("self_page", router, monkeypatch, tmp_path, person=BARE)

    assert any(url.startswith(SITE) for url in requested), (
        f"self_page never requested the member's own site at all; asked {requested!r}"
    )
    assert docs, "the member's own site answered and produced no document"
    assert all(doc.url.startswith(SITE) for doc in docs)
    assert all(doc.source_kind == "self_page" for doc in docs)


def test_wayback_enumerates_the_site_a_roster_named_without_a_scheme(monkeypatch, tmp_path):
    rows = [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["a", "20190412104500", f"{SITE}/about", "text/html", "200", "AAA", "900"],
    ]

    def router(request):
        path, query = parts(request)
        if path.startswith("/cdx/"):
            assert query.get("url") == "thornfieldloom.example.com/*", (
                f"wayback asked the archive for {query.get('url')!r}, which is not the "
                "web space the roster named"
            )
            return rows
        return _page("Thornfield Loom (archived)", LINE)

    docs, requested = search("wayback", router, monkeypatch, tmp_path, person=BARE)

    assert any("/cdx/" in url for url in requested), f"no CDX request at all: {requested!r}"
    assert docs, "an archived capture of the member's own site produced no document"


def test_wayback_never_spends_a_fetch_on_a_capture_that_is_not_a_document(
    monkeypatch, tmp_path
):
    # Measured on the first live run that reached a real personal domain: `feld.com/*`
    # returns captures of `1x1.gif` and `049b31d0.gif` among its first rows, and each one
    # cost a fetch, a max_docs_total slot and an LLM verdict to establish that a spacer
    # GIF is not a biography.
    rows = [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["a", "20190412104500", f"{SITE}/1x1.gif", "image/gif", "200", "AAA", "43"],
        ["b", "20190412104501", f"{SITE}/style.css", "text/css", "200", "BBB", "900"],
        ["c", "20190412104502", f"{SITE}/report.pdf", "application/pdf", "200", "CCC", "9000"],
        ["d", "20190412104503", f"{SITE}/about", "text/html", "200", "DDD", "900"],
        ["e", "20190412104504", f"{SITE}/notes", "warc/revisit", "200", "EEE", "900"],
    ]

    def router(request):
        path, _ = parts(request)
        if path.startswith("/cdx/"):
            return rows
        return _page("Thornfield Loom (archived)", LINE)

    docs, requested = search("wayback", router, monkeypatch, tmp_path, person=BARE)

    replays = [url for url in requested if "/web/" in url]
    assert replays, f"wayback fetched no capture at all: {requested!r}"
    for asset in ("1x1.gif", "style.css", "report.pdf"):
        assert not any(asset in url for url in replays), (
            f"wayback replayed {asset}, whose own CDX row says it is not a document; "
            f"asked {replays!r}"
        )
    assert any("/about" in url for url in replays), "the real page must still be fetched"
    assert any("/notes" in url for url in replays), (
        "`warc/revisit` is the archive saying it does not know the type, not saying it is "
        "an image; dropping those loses good pages"
    )
    assert docs
