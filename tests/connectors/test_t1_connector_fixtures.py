"""TASKS T-1 acceptance 2: one test per connector against `tests/fixtures/http/{kind}_*`.

Each of the ten connectors gets the same contract test — at least one `RawDoc`, the right
`source_kind`, a fetchable url, non-empty text, and the budget respected — plus a test
that grades what it actually *extracted*, because those five properties are all satisfied
by a connector that returns a cookie banner.

A green contract test is evidence of shape, not of judgement. `RawDoc.text` is what T-2
resolves against, what T-3 quotes verbatim, and what a host eventually reads out loud, so
the per-kind tests below assert the specific thing each source is *for*: that the Wayback
document is the site's older prose, that OpenAlex's inverted abstract came back as
sentences, that GitHub did not cite somebody else's forked repository as the member's own
work.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from t1_recorded import KINDS, install_transport, load, no_real_sleep, settings_for

from arrival.connectors import all_connectors
from arrival.contracts import RawDoc

pytestmark = pytest.mark.ticket("T-1")

GENEROUS = 5

#: The three sentences the whole recorded corpus is written around. Every connector's
#: fixture carries at least one of them, so "did this parse?" has a checkable answer.
S1 = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)
S2 = (
    "Thornfield Loom publishes a monthly maintenance almanac that mill-floor supervisors "
    "read before every shift."
)
S3 = (
    "Quennebeck chaired the Narragansett Mill Archive restoration committee before "
    "Thornfield Loom existed."
)

COOKIE_BANNER = "We use cookies to improve your experience."

#: PROVIDENCE RIVER CONSERVANCY, whose 990 the propublica recording carries deliberately —
#: its own `note` says the row "is what a CITY-name query returns, and the subject is NOT on
#: its board". Spelled once here because two checks below now depend on it (T-062).
CONSERVANCY_EIN = "50912344"


def _connector(kind, settings):
    found = [c for c in all_connectors(settings) if getattr(c, "kind", None) == kind]
    assert found, f"all_connectors() returned no connector with kind {kind!r}"
    return found[0]


def run(kind, monkeypatch, tmp_path, budget, *, fail=None):
    """Drive one connector against its own recorded corpus. Returns (docs, urls asked for)."""
    recording = load(kind)
    requested = install_transport(monkeypatch, recording, fail=fail)
    no_real_sleep(monkeypatch)
    connector = _connector(kind, settings_for(tmp_path))
    docs = asyncio.run(connector.search(recording.person, budget))
    return docs, requested


# --- the contract every connector owes, graded ten times ---------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_connector_returns_cited_rawdocs_from_its_recorded_fixture(kind, monkeypatch, tmp_path):
    """>=1 RawDoc with correct source_kind, url, non-empty text -- TASKS T-1 acceptance 2."""
    docs, requested = run(kind, monkeypatch, tmp_path, GENEROUS)

    assert requested, (
        f"the {kind} connector produced {len(docs)} document(s) without making a single "
        "HTTP request. A connector that never reads its source is a fixture wearing a "
        "connector's name."
    )
    assert docs, (
        f"the {kind} connector parsed nothing out of "
        f"tests/fixtures/http/{kind}_*.json. It asked for {sorted(set(requested))!r}; "
        "every one of those urls is answered by the recording, so an empty list means "
        "the response shape was not understood."
    )

    for doc in docs:
        assert isinstance(doc, RawDoc), f"{kind} returned a {type(doc).__name__}, not a RawDoc"
        assert doc.source_kind == kind, (
            f"{kind} stamped source_kind={doc.source_kind!r} on {doc.url!r}. A citation "
            "naming the wrong source is worse than no citation: T-3's non-obvious rule "
            "and R11's display rules both key off source_kind."
        )
        assert doc.url.startswith(("http://", "https://")), (
            f"{kind} returned url={doc.url!r}; every RawDoc is a citation and a citation "
            "needs a fetchable address"
        )
        assert doc.text.strip(), f"{kind} returned empty text for {doc.url!r}"
        assert len(doc.text) <= 20_000, f"{kind} returned {len(doc.text)} chars for {doc.url!r}"
        assert doc.doc_id == hashlib.sha1(doc.url.encode()).hexdigest()[:16], (
            f"{kind}'s doc_id for {doc.url!r} is {doc.doc_id!r}, not sha1(url)[:16]; the "
            "whole corpus is addressed by that id, so a private scheme breaks dedup"
        )

    assert len({doc.doc_id for doc in docs}) == len(docs), (
        f"{kind} returned the same document twice: {[d.url for d in docs]}"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_connector_respects_its_budget(kind, monkeypatch, tmp_path):
    """Budget is what stops one talkative source eating a person's whole allowance."""
    generous, _ = run(kind, monkeypatch, tmp_path, GENEROUS)
    limited, _ = run(kind, monkeypatch, tmp_path, 1)
    none_at_all, _ = run(kind, monkeypatch, tmp_path, 0)

    assert len(generous) <= GENEROUS, (
        f"{kind} returned {len(generous)} documents for budget={GENEROUS}; DESIGN "
        "§Interfaces defines budget as the maximum number of docs to return"
    )
    assert len(limited) <= 1, f"{kind} returned {len(limited)} documents for budget=1"
    assert len(limited) == 1, (
        f"{kind} returned nothing for budget=1 while returning {len(generous)} for "
        f"budget={GENEROUS}. A budget is a cap, not an off switch."
    )
    assert none_at_all == [], f"{kind} returned {len(none_at_all)} documents for budget=0"


@pytest.mark.parametrize("kind", KINDS)
def test_connector_text_is_prose_and_not_page_furniture(kind, monkeypatch, tmp_path):
    """Non-empty is not the bar. Every doc has to carry something a reader could use.

    The recorded pages deliberately contain a navigation menu, a cookie banner, a footer
    and an inline <script>, because "the text is a cookie banner" satisfies every
    assertion in the contract test above and is worthless to T-2 and T-3.
    """
    docs, _ = run(kind, monkeypatch, tmp_path, GENEROUS)

    for doc in docs:
        assert len(doc.text.strip()) >= 40, (
            f"{kind} returned {doc.text!r} for {doc.url!r} -- too short to be evidence "
            "of anything"
        )
        assert "<" not in doc.text or ">" not in doc.text.split("<", 1)[1][:80], (
            f"{kind} left markup in RawDoc.text for {doc.url!r}: {doc.text[:120]!r}. "
            "T-3 quotes this verbatim."
        )
        assert "function(" not in doc.text and "var " not in doc.text, (
            f"{kind} kept inline JavaScript in the text of {doc.url!r}"
        )

    corpus = "\n".join(doc.text for doc in docs)
    # Not "every doc quotes a signature sentence": wikidata and propublica legitimately
    # return identity and roster records rather than narrative prose. The bar is that the
    # connector's output is connected to its input at all -- a template rendered from an
    # empty response mentions neither the subject nor anything the recording said.
    assert "Marisol Quennebeck" in corpus or "Thornfield Loom" in corpus, (
        f"none of the {len(docs)} document(s) the {kind} connector returned names the "
        "subject or their company. Something was fetched and something was emitted, but "
        f"the two are not connected. Got: {[d.text[:90] for d in docs]!r}"
    )


@pytest.mark.parametrize("kind", ("self_page", "wayback"))
def test_html_sourced_docs_do_not_cite_the_cookie_banner_or_the_nav_menu(
    kind, monkeypatch, tmp_path
):
    """The two connectors that read real HTML pages must not hand chrome downstream.

    T-3 verifies a citation with `normalize_ws(quote) in normalize_ws(doc.text)`, so
    anything the extractor keeps becomes quotable. A digest that says "we noticed you use
    cookies to improve your experience" is the failure this exists to prevent, and it is
    reachable today from any page with a consent banner -- which is most of them.
    """
    docs, _ = run(kind, monkeypatch, tmp_path, GENEROUS)
    assert docs

    for doc in docs:
        assert COOKIE_BANNER not in doc.text, (
            f"the {kind} document for {doc.url!r} carries the consent banner into "
            f"RawDoc.text: {doc.text[:140]!r}"
        )
        assert "All rights reserved" not in doc.text, (
            f"the {kind} document for {doc.url!r} carries the site footer into RawDoc.text"
        )


@pytest.mark.parametrize("kind", ("self_page",))
def test_navigation_labels_are_not_part_of_the_document(kind, monkeypatch, tmp_path):
    docs, _ = run(kind, monkeypatch, tmp_path, GENEROUS)
    home = next(d for d in docs if d.url.endswith("thornfieldloom.example.com/"))

    for label in ("Subscribe", "Press"):
        assert label not in home.text, (
            f"the navigation label {label!r} is in RawDoc.text for {home.url!r}: "
            f"{home.text[:140]!r}. A nav menu is not prose about the member."
        )


# --- what each source is actually FOR ------------------------------------------------


def test_self_page_reads_the_members_own_site_and_one_hop_inside_it(monkeypatch, tmp_path):
    docs, requested = run("self_page", monkeypatch, tmp_path, GENEROUS)

    urls = [doc.url for doc in docs]
    assert "https://thornfieldloom.example.com/" in urls, "the seed url in details is the site"
    assert "https://thornfieldloom.example.com/team/marisol-quennebeck" in urls, (
        "a same-host link on the fetched page is followed while budget remains, so "
        "/team/{name} is reachable from a bare domain"
    )
    assert not any("news.example.org" in url for url in requested), (
        "an OFF-host link was followed. Crawling outward from a personal site is how a "
        "'research the member' tool quietly becomes a 'crawl the internet' tool."
    )
    assert S1 in "\n".join(doc.text for doc in docs)


def test_wikipedia_returns_the_lead_section_and_dates_it(monkeypatch, tmp_path):
    docs, _ = run("wikipedia", monkeypatch, tmp_path, GENEROUS)

    about_her = next(d for d in docs if d.url.endswith("/wiki/Marisol_Quennebeck"))
    assert S1 in about_her.text and S3 in about_her.text, (
        "the REST summary's `extract` is the lead section already stripped of wikitext; "
        "that is the whole reason this endpoint was chosen over action=query&prop=extracts"
    )
    assert "American textile-software co-founder" in about_her.text, (
        "the short description is what a resolver disambiguates on, so it belongs in the text"
    )
    assert about_her.published_at is not None, "the summary carries a timestamp; use it"


def test_wikidata_returns_the_qid_and_the_identifiers_hanging_off_it(monkeypatch, tmp_path):
    docs, _ = run("wikidata", monkeypatch, tmp_path, GENEROUS)
    doc = docs[0]

    assert doc.url == "https://www.wikidata.org/wiki/Q104882317"
    assert "Q104882317" in doc.text, (
        "the QID is a strong key and the canonical hub_id prefix; it has to be quotable "
        "out of the text, not merely present in the url"
    )
    assert "https://thornfieldloom.example.com/" in doc.text, (
        "P856 (official website) is the claim self_page falls back to; it must survive "
        "into the document"
    )
    assert "English Wikipedia: Marisol Quennebeck" in doc.text, (
        "the enwiki sitelink is a strong key T-2 resolves on"
    )


def test_github_cites_published_work_and_never_a_fork(monkeypatch, tmp_path):
    docs, _ = run("github", monkeypatch, tmp_path, GENEROUS)

    urls = [doc.url for doc in docs]
    assert "https://github.com/mquennebeck" in urls, "the profile carries the resolver's fields"
    assert "https://github.com/mquennebeck/loom-scheduler" in urls

    assert not any(url.endswith("/cpython") for url in urls), (
        "a FORK was cited as the member's own work. A fork is somebody else's repository "
        "sitting in this account, and 'you have been working on CPython' is exactly the "
        "kind of wrong a host says out loud."
    )

    profile = next(d for d in docs if d.url == "https://github.com/mquennebeck")
    assert "Thornfield Loom" in profile.text and "Providence" in profile.text, (
        "company and location are the fields a resolver needs to REJECT the wrong "
        "Marisol Quennebeck; search results omit them, which is why the profile is fetched"
    )
    repo = next(d for d in docs if d.url.endswith("/loom-scheduler"))
    assert repo.published_at is not None and repo.published_at.year == 2024, (
        "pushed_at is what makes a repo 'recent'; without it the digest cannot say 'last week'"
    )


def test_openalex_reconstructs_the_inverted_abstract_into_sentences(monkeypatch, tmp_path):
    docs, _ = run("openalex", monkeypatch, tmp_path, GENEROUS)

    work = next(d for d in docs if d.url == "https://doi.org/10.5555/thornfield.2024.1")
    assert S1 in work.text, (
        "OpenAlex ships abstracts ONLY as an inverted index ({word: [positions]}); a "
        "connector that does not de-invert it emits a citation with no abstract, and one "
        f"that de-inverts it wrongly emits word salad. Got: {work.text!r}"
    )
    assert "Teodora Ilves" in work.text, "co-authors are what T-5 joins people on"

    profile = next(d for d in docs if d.url == "https://openalex.org/A5099184423")
    assert "Narragansett Institute of Technology" in profile.text


def test_edgar_reads_the_filing_index_and_dates_the_filing(monkeypatch, tmp_path):
    docs, _ = run("edgar", monkeypatch, tmp_path, GENEROUS)

    form4 = next(d for d in docs if "0001742119-24-000012" in d.url)
    assert form4.url.startswith("https://www.sec.gov/Archives/edgar/data/1742119/"), (
        "the citation must be the real EDGAR archive path a reader can open"
    )
    assert "form 4" in form4.text
    assert "Quennebeck Marisol" in form4.text, "display_names is who the filing names"
    assert form4.published_at is not None and form4.published_at.year == 2024
    assert S1 in form4.text, "file_description is the only prose EDGAR gives; keep it"


def test_propublica_says_plainly_whether_the_member_is_on_the_board(monkeypatch, tmp_path):
    """The 990 roster is the point: "they chair this" vs "this exists near them"."""
    docs, _ = run("propublica", monkeypatch, tmp_path, GENEROUS)

    archive = next(d for d in docs if d.url.endswith("/814402257"))
    assert "Marisol Quennebeck" in archive.text and "Board chair" in archive.text, (
        "the org where the member IS an officer has to say so, or a downstream fact "
        "reads as 'they are involved with X' when the truth is weaker"
    )
    assert "Teodora Ilves (Treasurer)" in archive.text, "the rest of the board is the hub"
    assert "compensation" not in archive.text.lower(), (
        "R11 keeps wealth out of the product; officer compensation is in the 990 and must "
        "not be carried into a document a host reads"
    )

    # --- T-062: this block used to be `if conservancy is not None:` and NEVER RAN. ------
    #
    # WHY THE OLD ASSERTION WAS WRONG INDEPENDENTLY OF ANY CHANGE MADE FOR T-062. It was
    # written in fc4d343, when `organisation_queries` still sent every affiliation term and
    # so asked `q=Providence`; the recording answers that query and the 990 behind it, the
    # branch executed, and it guarded the weaker rule "if we emit the conservancy at all, it
    # must at least not claim she has a role there". Thirty minutes later 36f89e3
    # ("fix(T-018): a charity near the member is not a charity she is part of") added
    # `_is_an_address` to that function, and propublica.py's own docstring replaced the rule
    # the branch guarded with a STRONGER one: "She is emitted only if the 990 names her. No
    # match on the officer list means no document -- NOT a document that merely declines to
    # claim she is on the board." So the branch encodes a contract the product deliberately
    # no longer offers, and since T-018 it also cannot execute: measured at that commit's
    # descendant, `organisation_queries` returns ['Marisol Quennebeck', 'Thornfield Loom'],
    # the connector makes 3 of the recording's 5 requests, and `conservancy` is None. A
    # correct product change orphaned the recording and silently disabled the assertion.
    #
    # It is replaced by the current, stronger rule, asserted unconditionally, plus a
    # positive control so that "absent" is a decision the CONNECTOR made and not a property
    # of a fixture that stopped offering the row.
    recording = load("propublica")
    offered = [
        response["url"]
        for response in recording.responses
        if CONSERVANCY_EIN in response.get("url", "")
    ]
    assert offered, (
        f"the recording no longer carries the ein {CONSERVANCY_EIN} row (PROVIDENCE RIVER "
        "CONSERVANCY, per its own note), so the absence asserted below would be a property "
        "of the fixture rather than of the connector"
    )

    conservancy = next((d for d in docs if d.url.endswith(f"/{CONSERVANCY_EIN}")), None)
    assert conservancy is None, (
        "the connector emitted a document for an organisation whose 990 roster does not "
        "name the member. propublica.py's rule is 'no match on the officer list means no "
        f"document': {conservancy.text[:200] if conservancy else ''!r}"
    )

    # ...and the clause the dead branch was guarding, stated over every document that IS
    # emitted, so it executes whatever the connector decides to ask for. `archive` above is
    # the positive control that `docs` is not empty.
    for doc in docs:
        assert "Marisol Quennebeck" in doc.text, (
            f"{doc.url} carries no mention of the member, so it is an organisation that "
            "merely exists near her being presented as a document about her"
        )


def test_hn_cites_the_item_rather_than_the_article_it_links_to(monkeypatch, tmp_path):
    docs, _ = run("hn", monkeypatch, tmp_path, GENEROUS)

    for doc in docs:
        assert doc.url.startswith("https://news.ycombinator.com/item?id="), (
            f"{doc.url!r} is not an HN item. The item is what is durable and what carries "
            "the discussion; the linked article may move or die."
        )
    top = docs[0]
    assert "Submitted by mquennebeck" in top.text, "who posted it is half the signal"
    assert "214 points" in top.text
    assert top.published_at is not None and top.published_at.year == 2024


def test_wayback_returns_what_the_site_used_to_say_at_the_date_it_said_it(monkeypatch, tmp_path):
    docs, _ = run("wayback", monkeypatch, tmp_path, GENEROUS)

    assert len(docs) >= 2, "one capture is not a history"
    years = sorted(doc.published_at.year for doc in docs if doc.published_at)
    assert years == sorted(set(years)) and len(years) == len(docs), (
        f"every capture must carry the date it was taken; got {years}. Recency scoring "
        "and the whole 'what this used to say' angle depend on it."
    )
    assert 2018 in years, "the CDX timestamp, not today's date, dates an archived capture"

    oldest = min(docs, key=lambda d: d.published_at)
    assert "borrowed jacquard loom" in oldest.text, (
        "the 2018 capture has to carry the 2018 prose; if every capture came back with "
        "the same text the connector is citing the live site, not the archive"
    )
    assert len({doc.text for doc in docs}) == len(docs), (
        "two captures returned byte-identical text, so a budget slot bought nothing"
    )


def test_search_uses_the_engines_own_snippet_and_cites_the_real_page(monkeypatch, tmp_path):
    docs, requested = run("search", monkeypatch, tmp_path, GENEROUS)

    assert requested == ["https://api.tavily.com/search"], (
        f"with a key configured the search connector should ask Tavily once; it asked "
        f"{requested!r}"
    )
    top = docs[0]
    assert top.url == "https://thornfieldloom.example.com/team/marisol-quennebeck", (
        "the result's own url is the citation, so a fact drawn from here still points a "
        "reader at the real page rather than at a search-engine result list"
    )
    assert S1 in top.text
    assert top.published_at is not None


def test_search_falls_back_to_duckduckgo_when_no_api_key_is_configured(monkeypatch, tmp_path):
    """SPEC C1 / Settings: "a missing key disables a capability, never crashes"."""
    recording = load("search")
    requested = install_transport(monkeypatch, recording)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path, tavily_api_key=None)

    connector = _connector("search", settings)
    docs = asyncio.run(connector.search(recording.person, GENEROUS))

    assert not any("tavily" in url for url in requested), "no key means no Tavily call"
    assert any("duckduckgo" in url for url in requested), (
        f"the connector should degrade to the no-account HTML endpoint; it asked {requested!r}"
    )
    assert docs, "the fallback must actually return documents, not merely be attempted"
    assert docs[0].url == "https://thornfieldloom.example.com/team/marisol-quennebeck", (
        "DuckDuckGo wraps results in /l/?uddg=<encoded>; the citation has to be the real "
        "destination, not the redirector"
    )
    assert S1 in docs[0].text
