"""T-024: HN is searched BY AUTHOR as well as by name, and comments come back.

WHAT WAS WRONG.  TASKS T-1 acceptance 2 spells this connector "Algolia by author/name".
Only the name half existed: one `tags=story` query for the member's name.  That finds
things written ABOUT her and misses everything she wrote under a handle, which on Hacker
News is nearly all of it — and it misses comments entirely, which is where the sentence a
host would actually want to quote usually is.

WHY THE MISSING HALF WAS HARD, AND WHY "JUST SEARCH THE HANDLE" IS NOT THE FIX.  A handle
is not a name.  `carries_name("mquennebeck", "Marisol Quennebeck")` is False, so the shared
identity contract cannot accept a comment on the strength of its author field, and
accepting it anyway would be the exact defect `identity.py` exists to prevent: every
comment by every `mquennebeck` on the site, filed under the member's name, in the hub graph
where T-5 joins the whole roster onto it.

SO THE HANDLE IS VERIFIED FIRST, ONCE, AGAINST THE PROFILE ITS OWNER WROTE — the same shape
as GitHub's `/users/{login}` check.  A profile whose `about` names the member in full and
echoes a roster detail (or simply links to the member's own domain) identifies its handle;
every other handle is somebody else's.  After that the tag filter is scoped to her by
construction and the question is not re-asked per comment, because a comment does not
contain its author's legal name and demanding that it does would reject every correct hit.

THE CORPUS BELOW IS A WORLD, NOT AN ANSWER KEY.  Two handles post about looms in Providence
under the same display name.  One profile corroborates the roster and one does not, and
which one is which is decided by `PersonRef.details` — not by anything served over the
wire, which is identical for both.  `test_the_verification_follows_the_roster_and_not_the_
corpus` runs the identical bytes against a roster describing the OTHER person and requires
the answer to invert; a connector that passed the first tests by preferring the first hit,
or by hardcoding a handle, fails that one.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import MEMBER, parts, search

from arrival.contracts import PersonRef

pytestmark = pytest.mark.ticket("T-1")

HER_HANDLE = "mquennebeck"
IMPOSTOR = "mq_freight"
ITEM = "https://news.ycombinator.com/item?id={}"

HER_LINE = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)
HIS_LINE = (
    "Marisol Quennebeck runs dock scheduling at Halvard Freight Systems in Tucson and has "
    "never set foot in New England."
)
HER_COMMENT = (
    "We tried a queue per loom before a queue per mill, and the second one is the only "
    "version the supervisors ever actually opened."
)
HIS_COMMENT = (
    "Dock doors are not looms, and the scheduling literature keeps pretending the two "
    "problems are the same shape."
)

PROFILES = {
    HER_HANDLE: {"username": HER_HANDLE, "about": HER_LINE, "karma": 812},
    IMPOSTOR: {"username": IMPOSTOR, "about": HIS_LINE, "karma": 2140},
}


def _story(object_id: str, title: str, author: str, url: str, text: str) -> dict:
    return {
        "objectID": object_id,
        "title": title,
        "author": author,
        "points": 96,
        "num_comments": 28,
        "url": url,
        "story_text": text,
        "created_at": "2024-04-02T15:11:00.000Z",
        "_tags": ["story", f"author_{author}", f"story_{object_id}"],
    }


def _comment(object_id: str, author: str, story_id: str, story_title: str, text: str) -> dict:
    return {
        "objectID": object_id,
        "author": author,
        "comment_text": text,
        "story_id": story_id,
        "story_title": story_title,
        "story_url": "https://example.org/threads",
        "created_at": "2024-05-20T10:02:00.000Z",
        "_tags": ["comment", f"author_{author}", f"story_{story_id}"],
    }


#: The name query. The IMPOSTOR is first, because "take the top hit" is the defect.
BY_NAME = [
    _story("55500011", "Halvard Freight Systems rebuilt dock scheduling", IMPOSTOR,
           "https://halvardfreight.example.net/notes/dock", HIS_LINE),
    _story("40112233", "Thornfield Loom: scheduling looms without a mainframe", HER_HANDLE,
           "https://thornfieldloom.example.com/notes/2024-scheduling", HER_LINE),
]

BY_AUTHOR = {
    HER_HANDLE: [
        _story("40112234", "The maintenance almanac, four years in", HER_HANDLE,
               "https://thornfieldloom.example.com/almanac", HER_LINE),
        _comment("40998801", HER_HANDLE, "40998800", "Ask HN: scheduling for small shops",
                 HER_COMMENT),
        _comment("40998802", HER_HANDLE, "40998700", "Show HN: a loom queue", HER_COMMENT),
        _comment("40998803", HER_HANDLE, "40998600", "A third thread entirely", HER_COMMENT),
    ],
    IMPOSTOR: [
        _comment("55599901", IMPOSTOR, "55599900", "Dock scheduling at scale", HIS_COMMENT),
    ],
}


def router(request):
    path, query = parts(request)
    if path.startswith("/api/v1/users/"):
        return PROFILES.get(path.rsplit("/", 1)[-1])
    if path == "/api/v1/search":
        tags = query.get("tags", "")
        for handle, hits in BY_AUTHOR.items():
            if f"author_{handle}" in tags:
                return {"nbHits": len(hits), "hits": hits}
        return {"nbHits": len(BY_NAME), "hits": BY_NAME}
    return None


def test_hn_verifies_a_handle_against_its_own_profile_before_using_it(monkeypatch, tmp_path):
    """The reproduction, half one. Before the fix no profile was ever fetched."""
    _, requested = search("hn", router, monkeypatch, tmp_path)

    profiles = [url for url in requested if "/api/v1/users/" in url]
    assert profiles, (
        "TASKS T-1 acceptance 2 says 'Algolia by author/name' and the connector asked only "
        f"{requested!r}. A handle cannot be accepted on the strength of the handle, so the "
        "author half needs the profile behind it — which was never fetched."
    )


def test_hn_returns_a_comment_the_member_actually_wrote(monkeypatch, tmp_path):
    """The reproduction, half two. `tags=story` cannot return a comment by construction."""
    docs, requested = search("hn", router, monkeypatch, tmp_path)

    assert any("author_" in url for url in requested), (
        f"no author-scoped query was made; asked {requested!r}"
    )
    comments = [doc for doc in docs if "Comment on" in doc.title]
    assert comments, (
        "no comment came back. A comment is where the sentence a host would want to quote "
        f"usually is; got {[d.title for d in docs]!r}"
    )
    comment = comments[0]
    assert HER_COMMENT in comment.text
    assert comment.url == ITEM.format("40998801"), (
        f"a comment is cited to its OWN permalink, not the story's: {comment.url!r}. "
        "Citing the story would attribute somebody else's whole thread to the member."
    )
    assert comment.source_kind == "hn"
    assert comment.published_at is not None and comment.published_at.year == 2024


def test_stories_still_lead_and_comments_are_capped(monkeypatch, tmp_path):
    """Taste: a comment out of context is the easiest way to embarrass a member."""
    docs, _ = search("hn", router, monkeypatch, tmp_path)

    kinds = ["comment" if "Comment on" in doc.title else "story" for doc in docs]
    assert kinds[0] == "story", f"a comment led the dossier: {[d.title for d in docs]!r}"
    assert kinds.count("comment") <= 2, (
        f"comments were not capped: {[d.title for d in docs]!r}. Four were available."
    )
    assert all(doc.url.startswith("https://news.ycombinator.com/item?id=") for doc in docs)
    assert len({doc.doc_id for doc in docs}) == len(docs)


def test_hn_never_uses_a_handle_whose_profile_the_roster_does_not_recognise(
    monkeypatch, tmp_path
):
    """The impostor posts about the same subject under the same display name.

    Nothing served distinguishes him from the member except which of them
    `PersonRef.details` corroborates, and he is ranked first everywhere.
    """
    docs, _ = search("hn", router, monkeypatch, tmp_path)

    corpus = "\n".join(f"{doc.url}\n{doc.title}\n{doc.text}" for doc in docs)
    assert IMPOSTOR not in corpus, (
        f"documents by {IMPOSTOR!r} were attributed to the member: {corpus!r}"
    )
    assert "Halvard Freight" not in corpus, f"got {corpus!r}"
    assert HIS_COMMENT not in corpus


def test_the_verification_follows_the_roster_and_not_the_corpus(monkeypatch, tmp_path):
    """The anti-cheat: identical bytes, the other roster, and every answer inverts."""
    mirror = PersonRef(
        person_id="marisol-quennebeck",
        name=MEMBER.name,
        details=[
            "dock scheduling, Halvard Freight Systems",
            "Tucson, Arizona",
            "https://halvardfreight.example.net/",
        ],
    )
    docs, _ = search("hn", router, monkeypatch, tmp_path, person=mirror)

    corpus = "\n".join(f"{doc.url}\n{doc.title}\n{doc.text}" for doc in docs)
    assert HER_COMMENT not in corpus, (
        "with the roster naming Halvard Freight Systems in Tucson, the connector still "
        f"returned the Thornfield person's writing: {corpus!r}. Nothing served changed; "
        "the roster did."
    )
    assert HIS_COMMENT in corpus, (
        "and the person the roster actually describes should now be found by exactly the "
        f"same mechanism: {corpus!r}"
    )


def test_a_roster_declared_hn_profile_is_the_first_handle_checked(monkeypatch, tmp_path):
    """The club writing the handle down is the strongest identification available here."""
    declared = PersonRef(
        person_id="marisol-quennebeck",
        name=MEMBER.name,
        details=[
            "co-founder, Thornfield Loom",
            "Providence, Rhode Island",
            f"https://news.ycombinator.com/user?id={HER_HANDLE}",
        ],
    )
    _, requested = search("hn", router, monkeypatch, tmp_path, person=declared)

    profiles = [url for url in requested if "/api/v1/users/" in url]
    assert profiles, f"asked {requested!r}"
    assert profiles[0].endswith(f"/users/{HER_HANDLE}"), (
        f"the roster named a handle and the connector checked something else first: "
        f"{profiles!r}"
    )


def test_a_missing_profile_endpoint_costs_the_name_half_nothing(monkeypatch, tmp_path):
    """Unverifiable is a no, and a no is `[]` for that half — never an exception."""

    def no_profiles(request):
        path, _ = parts(request)
        if path.startswith("/api/v1/users/"):
            return None
        return router(request)

    docs, requested = search("hn", no_profiles, monkeypatch, tmp_path)

    assert any("/api/v1/users/" in url for url in requested), f"asked {requested!r}"
    assert docs, "the name half must still produce its documents"
    assert not any("Comment on" in doc.title for doc in docs), (
        f"a handle nothing corroborated was used anyway: {[d.title for d in docs]!r}"
    )
