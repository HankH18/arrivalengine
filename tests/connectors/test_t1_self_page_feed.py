"""T-021: `self_page` reads the member's feed, and reads nothing else through it.

WHAT WAS WRONG.  TASKS T-1 acceptance 2 ends "plus `/feed` RSS if present".  The connector
did the exact opposite: `/feed` and `/rss` were entries on `_SKIP_SEGMENTS`, so the one
address on the member's own site that carries DATED prose was the one address the crawler
deliberately refused, and `grep -ri rss src/` matched nothing at all — there was no parser
to have called.  Measured before the fix, against the corpus below: zero requests to any
feed url, and every `self_page` document arriving with `published_at is None`.

WHY IT IS WORTH A CONNECTOR CHANGE RATHER THAN A SHRUG.  `fetch_text` cannot date a page —
HTML carries no reliable "written on" — so `self_page`, the source whose whole value is
that the member wrote it, was also the source a digest could never say "last month" about.
A feed entry carries its date as a field.  That is the capability, and it is why the two
tests below assert on `published_at` and not merely on document count.

WHAT THIS CORPUS IS AND IS NOT.  It is a WORLD, not an answer key: there is no stored
expected output anywhere in this file, only responses, and the assertions are about
properties (a date is present; an off-host entry is absent; a platform's own timeline is
never requested) rather than about strings copied out of a run.  The same corpus is served
to a member whose roster names a private domain and to one whose roster names a page on a
shared platform, and the required behaviour differs between them because the ROSTER
differs — which is the property a fixture written to match an implementation cannot have.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import MEMBER, parts, search
from t1_decoy import PERSON_SHARED_SITE, SHARED_PROFILE

from arrival.connectors.feed import advertised_feeds, conventional_feed, parse_feed

pytestmark = pytest.mark.ticket("T-1")

HOST = "https://thornfieldloom.example.com"
HOME = f"{HOST}/"
TEAM = f"{HOST}/team/marisol-quennebeck"
NOTE_A = f"{HOST}/notes/2024-05-scheduling"
NOTE_B = f"{HOST}/notes/2023-11-almanac"
OFF_HOST = "https://news.example.org/thornfield-profile"

LINE_A = (
    "We rewrote the loom scheduler in Providence this spring and the mills now file "
    "their own maintenance windows."
)
LINE_B = (
    "Four years of the maintenance almanac, and the thing mill-floor supervisors still "
    "read first is the weather column."
)
LINE_OFF = (
    "A trade weekly profiled Thornfield Loom and got the founding date wrong in the "
    "first paragraph."
)


def _item(title: str, url: str, line: str, when: str) -> str:
    return (
        f"<item><title>{title}</title><link>{url}</link>"
        f"<pubDate>{when}</pubDate><description>{line}</description></item>"
    )


#: A feed carrying two of the member's own posts and one syndicated from somebody else's
#: publication. A feed is allowed to contain anything; the connector is not.
RSS = (
    '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
    "<title>Thornfield Loom</title><link>" + HOME + "</link>"
    "<description>Notes from the mill floor.</description>"
    + _item("Scheduling looms without a mainframe", NOTE_A, LINE_A, "Thu, 02 May 2024 09:14:00 GMT")
    + _item(
        "The maintenance almanac, four years in", NOTE_B, LINE_B, "Tue, 07 Nov 2023 11:40:00 GMT"
    )
    + _item("Thornfield Loom, profiled", OFF_HOST, LINE_OFF, "Mon, 03 Jun 2024 08:00:00 GMT")
    + "</channel></rss>"
)

ATOM = (
    '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
    "<title>Thornfield Loom</title>"
    "<entry><title>Scheduling looms without a mainframe</title>"
    f'<link rel="edit" href="{NOTE_A}/edit"/><link rel="alternate" href="{NOTE_A}"/>'
    "<updated>2024-05-02T09:14:00Z</updated>"
    f'<summary type="html">&lt;p&gt;{LINE_A}&lt;/p&gt;</summary></entry>'
    "</feed>"
)


def _page(title: str, body: str, extra: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title>{extra}</head><body>"
        f'<nav><ul><li><a href="/team/marisol-quennebeck">Team</a></li>'
        f'<li><a href="/feed">Subscribe</a></li></ul></nav>'
        f"<main><h1>{title}</h1><p>{body}</p></main></body></html>"
    )


HOME_BODY = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)


def advertising_router(feed_path: str, feed_body: str):
    """A site that DECLARES its feed in `<head>`, which is the free way to find one."""

    def route(request):
        path, _ = parts(request)
        if path == feed_path:
            return feed_body
        if path == "/":
            return _page(
                "Marisol Quennebeck — Thornfield Loom",
                HOME_BODY,
                extra=(
                    '<link rel="alternate" type="application/rss+xml" '
                    f'title="Notes" href="{feed_path}">'
                ),
            )
        if path == "/team/marisol-quennebeck":
            return _page("Marisol Quennebeck — Team", HOME_BODY)
        return None

    return route


def conventional_router(request):
    """A site that declares nothing, so the ONE guess acceptance 2 names has to be made."""
    path, _ = parts(request)
    if path == "/feed":
        return RSS
    if path == "/":
        return _page("Marisol Quennebeck — Thornfield Loom", HOME_BODY)
    if path == "/team/marisol-quennebeck":
        return _page("Marisol Quennebeck — Team", HOME_BODY)
    return None


# -- the parser, on its own ---------------------------------------------------------------


def test_the_feed_parser_reads_rss_and_atom_and_refuses_everything_else():
    """Both dialects, one code path, and no exception on junk (DESIGN Decision 8)."""
    rss = parse_feed(RSS, f"{HOST}/feed")
    assert [entry.url for entry in rss] == [NOTE_A, NOTE_B, OFF_HOST]
    assert rss[0].published_at is not None and rss[0].published_at.isoformat() == "2024-05-02"
    assert LINE_A in rss[0].summary

    atom = parse_feed(ATOM, f"{HOST}/atom.xml")
    assert [entry.url for entry in atom] == [NOTE_A], (
        "Atom puts the address in `<link href>` and may carry several with different "
        f"`rel`s; `rel=alternate` is the readable one. Got {[e.url for e in atom]!r}"
    )
    assert atom[0].published_at is not None and atom[0].published_at.isoformat() == "2024-05-02"

    for junk in ("", "<html><body>not a feed</body></html>", "<rss><channel", "{}"):
        assert parse_feed(junk, HOME) == [], f"parse_feed({junk!r}) must degrade, not raise"


def test_feed_discovery_prefers_what_the_page_declares_and_falls_back_to_one_guess():
    declared = advertised_feeds(
        HOME,
        '<html><head><link rel="alternate" type="application/rss+xml" href="/blog/feed.xml">'
        '<link rel="alternate" type="text/html" href="/print"></head></html>',
    )
    assert declared == [f"{HOST}/blog/feed.xml"], (
        f"only the FEED alternate is a feed; got {declared!r}"
    )
    assert conventional_feed(f"{HOST}/team/marisol-quennebeck") == f"{HOST}/feed", (
        "the conventional guess is anchored at the site root, not beside the page"
    )


# -- the connector ------------------------------------------------------------------------


@pytest.mark.parametrize("feed_path", ["/feed.xml", "/notes/index.xml"])
def test_self_page_reads_the_feed_the_site_advertises(feed_path, monkeypatch, tmp_path):
    """The capability itself. Before the fix this requested no feed url at all."""
    docs, requested = search(
        "self_page", advertising_router(feed_path, RSS), monkeypatch, tmp_path
    )

    assert any(url.endswith(feed_path) for url in requested), (
        f"the page declared its feed at {feed_path} in `<link rel=alternate>` and the "
        f"connector never asked for it. Requested: {requested!r}. TASKS T-1 acceptance 2: "
        "'plus /feed RSS if present'."
    )
    urls = [doc.url for doc in docs]
    assert NOTE_A in urls and NOTE_B in urls, (
        f"the feed's own entries are the documents it exists to produce; got {urls!r}"
    )
    dated = [doc for doc in docs if doc.published_at is not None]
    assert dated, (
        "every document this connector produced is undated. A feed entry carries its date "
        "as a field, and it is the ONLY dated self_page document there can be: `fetch_text` "
        "cannot date a page."
    )
    note = next(doc for doc in docs if doc.url == NOTE_A)
    assert note.published_at is not None and note.published_at.isoformat() == "2024-05-02"
    assert LINE_A in note.text
    assert note.source_kind == "self_page"


def test_self_page_guesses_the_conventional_feed_when_the_site_declares_none(
    monkeypatch, tmp_path
):
    """`{origin}/feed` is the one guess the acceptance criterion names, and the only one."""
    docs, requested = search("self_page", conventional_router, monkeypatch, tmp_path)

    assert f"{HOST}/feed" in requested, f"asked {requested!r}"
    assert NOTE_A in [doc.url for doc in docs], f"got {[d.url for d in docs]!r}"
    assert not any("/rss" in url or "atom.xml" in url for url in requested), (
        f"one conventional guess, not a sweep of every path a feed might live at: "
        f"{requested!r}"
    )


def test_self_page_does_not_emit_a_feed_entry_that_points_off_the_members_own_site(
    monkeypatch, tmp_path
):
    """A feed may syndicate anybody. The `self_page` stamp may not follow it out.

    This is the same rule that already stops off-host ANCHORS being followed, applied to
    the other way a URL can arrive. It matters more here, not less: an anchor is a link on
    a page, while a feed entry arrives already looking like a document — with a title, a
    body and a date — and is one step away from being emitted as prose the member wrote.
    """
    docs, requested = search(
        "self_page", advertising_router("/feed.xml", RSS), monkeypatch, tmp_path
    )

    assert OFF_HOST not in [doc.url for doc in docs], (
        f"a syndicated entry on news.example.org was emitted with source_kind='self_page', "
        "the highest-trust kind in the system, for a page the member did not publish: "
        f"{[d.url for d in docs]!r}"
    )
    assert not any("news.example.org" in url for url in requested), (
        f"and it was not merely dropped after being fetched: {requested!r}"
    )


def test_self_page_never_asks_a_shared_platform_for_its_own_timeline(monkeypatch, tmp_path):
    """`linkedin.com/feed` is nine hundred million people's feed, and belongs to none of them.

    The roster line here names a PAGE on a shared platform. Guessing `{origin}/feed` from
    it addresses the platform's global timeline, and any document built from that would be
    stamped `self_page` and attributed to the member. The conventional guess is therefore
    made only on a host the member can plausibly own.
    """
    seen: list[str] = []

    def route(request):
        path, _ = parts(request)
        seen.append(path)
        if path == "/in/marisol-quennebeck-thornfield":
            return _page("Marisol Quennebeck", HOME_BODY)
        if path == "/feed":
            return RSS
        return None

    docs, requested = search(
        "self_page", route, monkeypatch, tmp_path, person=PERSON_SHARED_SITE
    )

    assert SHARED_PROFILE.startswith("https://www.linkedin.com/in/"), "roster shape assumption"
    assert "https://www.linkedin.com/feed" not in requested, (
        f"the connector asked a shared platform for its own timeline: {requested!r}"
    )
    assert not any(doc.url.rstrip("/").endswith("/feed") for doc in docs), (
        f"got {[d.url for d in docs]!r}"
    )


def test_a_feed_does_not_cost_the_page_documents_their_place(monkeypatch, tmp_path):
    """Budget is a cap on documents, and the seed page is still the first of them."""
    docs, _ = search(
        "self_page", advertising_router("/feed.xml", RSS), monkeypatch, tmp_path, budget=2
    )

    assert len(docs) <= 2
    assert docs[0].url == HOME, (
        f"the address the roster actually named must still lead; got {[d.url for d in docs]!r}"
    )
    assert MEMBER.details[-1] == HOME, "roster shape assumption"


def test_a_feed_url_is_a_whole_path_segment_and_not_a_substring(monkeypatch, tmp_path):
    """`"/feed" in path` is also true of `/feedback`, and would take a real page off the crawl."""
    from arrival.connectors.feed import is_feed_url

    for url in (f"{HOST}/feed", f"{HOST}/blog/feed/", f"{HOST}/notes/index.xml"):
        assert is_feed_url(url), url
    for url in (f"{HOST}/feedback", f"{HOST}/feeding-the-mill", f"{HOST}/sitemap.xml"):
        assert not is_feed_url(url), (
            f"{url} was classified as a feed. It would be skipped by the page crawler and "
            "handed to an XML parser that finds nothing in it, so the page is simply lost."
        )

    def route(request):
        path, _ = parts(request)
        if path == "/":
            return _page("Marisol Quennebeck — Thornfield Loom", HOME_BODY).replace(
                '<a href="/team/marisol-quennebeck">Team</a>',
                '<a href="/feedback">Feedback</a>',
            )
        if path == "/feedback":
            return _page("Reader feedback — Thornfield Loom", HOME_BODY)
        return None

    _, requested = search("self_page", route, monkeypatch, tmp_path)
    assert f"{HOST}/feedback" in requested, (
        f"a page whose path merely starts with the letters 'feed' was skipped: {requested!r}"
    )


def test_a_headline_only_entry_falls_back_to_the_page_and_keeps_the_feeds_date(
    monkeypatch, tmp_path
):
    """A link-blog entry is a title and nothing else. The title is not evidence; the page is.

    The date is still the feed's, because the page it points at does not carry one — which
    is the whole reason to have read a feed in the first place.
    """
    thin = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Thornfield Loom</title><link>" + HOME + "</link>"
        "<description>Links.</description>"
        f"<item><title>New post</title><link>{NOTE_A}</link>"
        "<pubDate>Thu, 02 May 2024 09:14:00 GMT</pubDate></item>"
        "</channel></rss>"
    )

    def route(request):
        path, _ = parts(request)
        if path == "/feed":
            return thin
        if path == "/":
            return _page("Marisol Quennebeck — Thornfield Loom", HOME_BODY)
        if path == "/notes/2024-05-scheduling":
            return _page("Scheduling looms without a mainframe", LINE_A)
        return None

    docs, requested = search("self_page", route, monkeypatch, tmp_path)

    assert NOTE_A in requested, (
        f"an entry with no body should be resolved by fetching the page: {requested!r}"
    )
    note = next(doc for doc in docs if doc.url == NOTE_A)
    assert LINE_A in note.text, "the page's own prose is what makes this a document"
    assert note.published_at is not None and note.published_at.isoformat() == "2024-05-02", (
        f"the feed's date is the only one anybody has for this page; got {note.published_at!r}"
    )
