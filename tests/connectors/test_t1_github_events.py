"""T-022: the `github` connector reads recent public EVENTS, not only repositories.

WHAT WAS WRONG.  TASKS T-1 acceptance 2 spells the GitHub connector "user search + recent
public events/repos".  Only the second half existed — `grep -n events
src/arrival/connectors/github.py` matched nothing — so the connector could say what a
member has built and never what they did last week.  Measured before the fix, against the
corpus below: `/users/{login}/events/public` was never requested, and the emitted documents
were the profile and the repository list.

WHY THE MISSING HALF IS THE HALF A DIGEST WANTS.  A repository is a standing fact with a
description somebody wrote once.  An event is dated and specific: "published a release of
the freight scheduler on Tuesday".  R7 asks the host for something that sounds like they
were paying attention, and a standing fact does not sound like that.

TASTE IS PART OF THE CRITERION, NOT AN ADDITION TO IT.  An events feed also carries stars,
forks and follows — things the member did TO other people's work.  Reading those back to
them is reading them their browsing history, which is the wrong side of the line this whole
product is scored on, so the feed is filtered to what they PUBLISHED.  The corpus below
contains one of each so the filter is graded rather than asserted.

WHERE IDENTITY IS DECIDED.  Once, on the account, by `identifies` + `choose_one` — the
events endpoint is scoped to a login that has already been verified.  What is checked per
item is that its actor IS that login: GitHub's feeds are not always homogeneous (an
account that was converted to an organisation, a shared bot), and an item by somebody else
is not the member's work.  The corpus contains one of those too.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import MEMBER, parts, search

pytestmark = pytest.mark.ticket("T-1")

LOGIN = "mquennebeck"
OTHER = "ilves_t"
EVENTS_PATH = f"/users/{LOGIN}/events/public"

HER_LINE = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)
COMMIT = "Let a mill file its own maintenance window without calling the office first"
RELEASE_NOTE = "The scheduler now reads maintenance windows straight from the mill's own calendar."


def _account() -> dict:
    return {
        "login": LOGIN,
        "id": 90210331,
        "type": "User",
        "html_url": f"https://github.com/{LOGIN}",
        "name": "Marisol Quennebeck",
        "company": "Thornfield Loom",
        "blog": "https://thornfieldloom.example.com/",
        "location": "Providence, Rhode Island",
        "bio": HER_LINE,
        "public_repos": 17,
        "followers": 240,
        "created_at": "2016-08-19T11:04:12Z",
    }


def _repo(name: str) -> dict:
    return {
        "name": name,
        "full_name": f"{LOGIN}/{name}",
        "html_url": f"https://github.com/{LOGIN}/{name}",
        "description": HER_LINE,
        "language": "Python",
        "stargazers_count": 312,
        "pushed_at": "2024-06-11T08:30:00Z",
        "fork": False,
    }


def _event(kind: str, repo: str, actor: str, payload: dict) -> dict:
    return {
        "id": f"382950120{abs(hash((kind, repo))) % 100}",
        "type": kind,
        "actor": {"login": actor, "display_login": actor},
        "repo": {"id": 812345600, "name": f"{actor}/{repo}",
                 "url": f"https://api.github.com/repos/{actor}/{repo}"},
        "payload": payload,
        "public": True,
        "created_at": "2024-06-11T08:30:00Z",
    }


#: Stars and forks first, because "take the top of the feed" is the lazy implementation and
#: a corpus that puts the publishing events first would grade it green.
EVENTS = [
    _event("WatchEvent", "someone-elses-tool", LOGIN, {"action": "started"}),
    _event("ForkEvent", "cpython", LOGIN, {"forkee": {"name": "cpython"}}),
    _event("PushEvent", "loom-scheduler", LOGIN,
           {"size": 3, "commits": [{"sha": "deadbeef", "message": COMMIT}]}),
    _event("ReleaseEvent", "loom-scheduler", LOGIN,
           {"action": "published",
            "release": {"name": "0.9 — self-service maintenance windows",
                        "tag_name": "v0.9",
                        "body": RELEASE_NOTE,
                        "html_url": f"https://github.com/{LOGIN}/loom-scheduler/releases/tag/v0.9"}}),
    _event("PushEvent", "mill-archive-index", OTHER,
           {"size": 1, "commits": [{"sha": "cafe", "message": "Somebody else's commit"}]}),
    _event("CreateEvent", "loom-scheduler", LOGIN, {"ref_type": "branch", "ref": "wip"}),
]


def router(request):
    path, _ = parts(request)
    if path == "/search/users":
        return {"total_count": 1, "incomplete_results": False,
                "items": [{"login": LOGIN, "id": 1, "type": "User",
                           "html_url": f"https://github.com/{LOGIN}"}]}
    if path == EVENTS_PATH:
        return EVENTS
    if path == f"/users/{LOGIN}/repos":
        return [_repo("loom-scheduler"), _repo("maintenance-almanac")]
    if path == f"/users/{LOGIN}":
        return _account()
    if path.startswith("/"):
        return None
    return None


def _drive(monkeypatch, tmp_path, budget: int = 5):
    return search("github", router, monkeypatch, tmp_path, budget=budget)


def test_github_asks_for_recent_public_events(monkeypatch, tmp_path):
    """The reproduction. Before the fix this endpoint was never requested at all."""
    _, requested = _drive(monkeypatch, tmp_path)

    assert any(url.endswith(EVENTS_PATH) or EVENTS_PATH + "?" in url for url in requested), (
        "TASKS T-1 acceptance 2 names 'recent public events/repos' and the connector asked "
        f"only for {requested!r}. `/users/{{login}}/events/public` is the events half."
    )


def test_github_emits_a_dated_document_for_what_the_member_actually_shipped(
    monkeypatch, tmp_path
):
    docs, _ = _drive(monkeypatch, tmp_path)

    activity = [doc for doc in docs if "/releases/" in doc.url or doc.url.endswith("/commits")]
    assert activity, (
        f"no document describes any recent activity; got {[d.url for d in docs]!r}"
    )
    corpus = "\n".join(doc.text for doc in docs)
    assert COMMIT in corpus or RELEASE_NOTE in corpus, (
        "an event document that repeats neither the commit message nor the release note "
        f"has told a host nothing they could say out loud. Got {corpus!r}"
    )
    for doc in activity:
        assert doc.source_kind == "github"
        assert doc.published_at is not None, (
            f"{doc.url} carries no date. A dated specific is the entire reason to read an "
            "events feed rather than a repository list."
        )
        assert len(doc.text.strip()) >= 40, f"{doc.url}: {doc.text!r}"


def test_github_does_not_read_the_member_their_own_browsing_history(monkeypatch, tmp_path):
    """Stars and forks are things done TO other people's work. They are not a dossier."""
    docs, _ = _drive(monkeypatch, tmp_path)

    corpus = "\n".join(f"{doc.url}\n{doc.title}\n{doc.text}" for doc in docs)
    assert "someone-elses-tool" not in corpus, (
        f"a starred repository was reported as the member's own activity: {corpus!r}"
    )
    assert "/cpython" not in corpus, f"a fork was reported as their work: {corpus!r}"
    assert "wip" not in [doc.title for doc in docs], "a branch creation is bookkeeping"


def test_github_drops_an_event_whose_actor_is_somebody_else(monkeypatch, tmp_path):
    """The per-item identity check. The account was verified; the item has to match it."""
    docs, _ = _drive(monkeypatch, tmp_path)

    corpus = "\n".join(f"{doc.url}\n{doc.text}" for doc in docs)
    assert OTHER not in corpus, (
        f"an event whose actor is {OTHER!r} was attributed to {LOGIN!r}: {corpus!r}. "
        "The events endpoint is scoped to a verified login; an item naming a different "
        "actor did not come from that person."
    )


def test_events_do_not_starve_the_repositories_and_neither_starves_the_profile(
    monkeypatch, tmp_path
):
    """Both halves of 'events/repos', inside one budget, with the profile still first."""
    docs, _ = _drive(monkeypatch, tmp_path)
    urls = [doc.url for doc in docs]

    assert len(docs) <= 5
    assert urls[0] == f"https://github.com/{LOGIN}", f"the profile leads; got {urls!r}"
    assert any(url == f"https://github.com/{LOGIN}/loom-scheduler" for url in urls), (
        f"the repositories half was crowded out by events: {urls!r}"
    )
    assert any("/releases/" in url or url.endswith("/commits") for url in urls), (
        f"the events half was crowded out by repositories: {urls!r}"
    )
    assert len({doc.doc_id for doc in docs}) == len(docs), f"duplicate documents: {urls!r}"


def test_a_dead_events_endpoint_costs_the_repositories_nothing(monkeypatch, tmp_path):
    """The reservation is handed back. A rate-limited feed must not shrink the dossier."""

    def no_events(request):
        path, _ = parts(request)
        if path == EVENTS_PATH:
            return None
        return router(request)

    docs, _ = search("github", no_events, monkeypatch, tmp_path, budget=5)
    urls = [doc.url for doc in docs]

    assert f"https://github.com/{LOGIN}/maintenance-almanac" in urls, (
        "with events unavailable, both recorded repositories should still be reachable "
        f"inside a budget of 5; got {urls!r}"
    )


def test_budget_one_is_still_the_profile(monkeypatch, tmp_path):
    docs, _ = _drive(monkeypatch, tmp_path, budget=1)
    assert [doc.url for doc in docs] == [f"https://github.com/{LOGIN}"]
    assert MEMBER.name == "Marisol Quennebeck", "roster shape assumption"
