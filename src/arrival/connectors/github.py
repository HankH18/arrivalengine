"""GitHub: what someone actually builds, in their own commit messages.

WHY THIS IS A TASTE SOURCE AND NOT A SURVEILLANCE ONE.  A public repository is a thing a
person chose to publish under their own name, and its README line is the sentence they
wrote about their own work.  That is the far side of the "seen vs. dossiered" line: a host
saying "you pushed a release for the freight scheduler last week" is reading a press
release the member published themselves.

Four calls, each earning its place: `/search/users` maps a display name to a login;
`/users/{login}` returns the profile fields (name, company, blog, location, bio) a resolver
needs to *reject* the wrong Pell Marrowby, and search results deliberately omit;
`/users/{login}/repos?sort=pushed` returns recent work newest-first; and
`/users/{login}/events/public` returns what actually HAPPENED lately — TASKS T-1 acceptance
2 names "recent public events/repos" and only the second half was ever built (T-022).

WHY EVENTS AND NOT JUST REPOS.  A repository is a standing fact: it exists, it has a
description, it was last pushed at some point.  An event is dated and specific — "cut the
0.9 release of the freight scheduler on Tuesday" — and a dated specific is the entire
difference between a host who sounds briefed and a host who sounds like they read a bio.
The two are complementary rather than redundant, so the budget is split: recent repos
first, then a reserved slot or two for what happened.

WHERE THE IDENTITY DECISION IS MADE, FOR ALL THREE DOCUMENT SOURCES.  Once and only once,
on the ACCOUNT, by `identifies` + `choose_one` in `_find_account`.  `/users/{login}/repos`
and `/users/{login}/events/public` are both scoped to that verified login, so every item
they return is that account's; a document from either inherits the account's identity
rather than re-deriving it.  Re-deriving would in fact be wrong: a commit message does not
name its author and a login does not carry a person's name, so demanding `identifies` per
item would reject every correct event.  What each item IS checked for is that its actor is
the verified login — an events payload naming somebody else (an organisation's feed, a
push by a collaborator) is not this member's work and is dropped.

`GITHUB_TOKEN` is optional, per `Settings`.  Without it the API allows 60 requests an hour
by IP, which is enough for a small roster and useless for a large one — so the token is
sent when present and its absence is not an error.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, parse_date, text_block
from arrival.connectors.identity import (
    best_affiliation,
    choose_one,
    corroborates,
    identifies,
    on_own_host,
)
from arrival.contracts import PersonRef, RawDoc

__all__ = ["GithubConnector"]

API = "https://api.github.com"

#: Logins to look at before deciding. `/search/users` ranks by follower count, so the
#: first hit is the most FAMOUS person with the name, not the member.
CANDIDATE_LOGINS = 3

#: Documents the recent-activity feed may claim out of one person's allowance. Repos come
#: first because they are the standing picture; the reservation exists so a prolific
#: account's repository list cannot spend the whole budget and leave the digest with
#: nothing dated. Never larger than "budget minus the profile minus one repo".
EVENT_SLOTS = 2

#: Event types that are the member PUBLISHING THEIR OWN WORK, and — by their absence — the
#: ones deliberately left out. `WatchEvent` (a star) and `ForkEvent` are things a person
#: did TO somebody else's repository; `FollowEvent` and `MemberEvent` are a social graph;
#: `IssueCommentEvent` is an argument in somebody else's thread. Repeating any of those to
#: a member is reading them their browsing history, which is the wrong side of the
#: "seen vs. dossiered" line this product is scored on. What is left is what they shipped.
PUBLISHING_EVENTS = frozenset(
    {"PushEvent", "PullRequestEvent", "ReleaseEvent", "CreateEvent", "PublicEvent"}
)

#: What each event type is called in a sentence, and the page a reader should land on.
_EVENT_PROSE: dict[str, tuple[str, str]] = {
    "PushEvent": ("Pushed commits to", "commits"),
    "PullRequestEvent": ("Opened a pull request on", "pulls"),
    "ReleaseEvent": ("Published a release of", "releases"),
    "CreateEvent": ("Created", ""),
    "PublicEvent": ("Made public", ""),
}


def _account_fields(account: dict[str, Any]) -> dict[str, list[str]]:
    """The profile fields, sorted by what KIND of evidence each one is."""
    return {
        "names": [str(account.get("name") or ""), str(account.get("login") or "")],
        "prose": [str(account.get("bio") or "")],
        "urls": [str(account.get("blog") or ""), str(account.get("html_url") or "")],
        "context": [
            str(account.get("company") or ""),
            str(account.get("location") or ""),
            str(account.get("blog") or ""),
            str(account.get("bio") or ""),
        ],
    }


def _is_the_member(person: PersonRef, account: dict[str, Any]) -> bool:
    return identifies(person, **_account_fields(account))


def _account_score(person: PersonRef, account: dict[str, Any]) -> int:
    """How loudly this profile agrees with the roster. Ties between profiles decline."""
    fields = _account_fields(account)
    score = corroborates(person, *fields["context"], *fields["names"], *fields["prose"])
    # A blog on a domain the roster named is worth more than any single field echo: the
    # member wrote that URL into their own GitHub profile.
    return score + 2 * sum(1 for url in fields["urls"] if on_own_host(url, person))


class GithubConnector(BaseConnector):
    """`kind="github"` — one profile document plus the most recently pushed repositories."""

    kind = "github"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.settings.github_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        account = await self._find_account(person)
        if account is None:
            return []
        login = str(account.get("login") or "")

        docs: list[RawDoc] = []
        profile = self._profile(login, account)
        if profile is not None:
            docs.append(profile)

        remaining = budget - len(docs)
        if remaining <= 0:
            return docs

        # Events are asked for FIRST and returned LAST, and both halves of that matter.
        # Reserved rather than leftover, because "whatever repos did not use" is zero for
        # every account with more repositories than budget — which is most of them — and a
        # capability nothing ever reaches is the defect T-022 recorded. Asked first so the
        # reservation can be handed back: a source that is empty, dead or rate-limited
        # must not cost the repositories their slots. Returned last because display order
        # is standing-picture-then-latest-news, not request order.
        events = await self._events(login, min(EVENT_SLOTS, max(0, remaining - 1)))
        docs.extend(await self._repositories(login, remaining - len(events)))
        docs.extend(events)
        return docs

    async def _find_account(self, person: PersonRef) -> dict[str, Any] | None:
        """The member's account, or `None`. The search HIT can never answer this.

        `/search/users` returns `{login, id, avatar_url, html_url, type}` and nothing
        else — no name, no company, no location — so "the first hit with a login wins" was
        not a weak identity check, it was the ABSENCE of one dressed as a lookup. The
        fields a resolver needs to reject the wrong Marisol Quennebeck live one call
        further in, on `/users/{login}`, which this connector was already fetching and
        then never comparing against `details`.

        So: fetch the profiles of the top few candidates and let the roster choose. A tie
        declines (`choose_one`), because two accounts the roster corroborates equally are
        two accounts it cannot tell apart.
        """
        query = f'"{person.name}" {best_affiliation(person)}'.strip()
        payload = await self.get_json(
            f"{API}/search/users",
            params={"q": query, "per_page": 5},
            headers=self._headers(),
        )
        items: Any = None
        if isinstance(payload, dict):
            items = payload.get("items")
        elif isinstance(payload, list):
            items = payload
        if not isinstance(items, list):
            return None

        logins = [
            str(item["login"])
            for item in items
            if isinstance(item, dict) and item.get("login")
        ][:CANDIDATE_LOGINS]

        accounts: list[dict[str, Any]] = []
        for login in logins:
            account = await self.get_json(f"{API}/users/{login}", headers=self._headers())
            if isinstance(account, dict) and _is_the_member(person, account):
                accounts.append(account)
        return choose_one(accounts, lambda account: _account_score(person, account))

    def _profile(self, login: str, payload: dict[str, Any]) -> RawDoc | None:
        url = str(payload.get("html_url") or f"https://github.com/{login}")
        return self.doc(
            url,
            title=f"{payload.get('name') or login} ({login}) on GitHub",
            text=text_block(
                f"GitHub profile: {login}",
                payload.get("name"),
                f"Company: {payload['company']}" if payload.get("company") else None,
                f"Location: {payload['location']}" if payload.get("location") else None,
                f"Website: {payload['blog']}" if payload.get("blog") else None,
                payload.get("bio"),
                f"{payload.get('public_repos', 0)} public repositories, "
                f"{payload.get('followers', 0)} followers.",
            ),
            published_at=parse_date(payload.get("created_at")),
        )

    async def _repositories(self, login: str, limit: int) -> list[RawDoc]:
        payload = await self.get_json(
            f"{API}/users/{login}/repos",
            params={
                "sort": "pushed",
                "direction": "desc",
                # Headroom for the fork filter below, for the same reason `_events` asks
                # for more rows than it will keep.
                "per_page": max(1, min(limit * 2, 30)),
            },
            headers=self._headers(),
        )
        rows: Any = payload
        if isinstance(payload, dict):
            rows = payload.get("items") or payload.get("repositories")
        if not isinstance(rows, list):
            return []

        docs: list[RawDoc] = []
        for repo in rows:
            if len(docs) >= limit:
                break
            if not isinstance(repo, dict) or repo.get("fork"):
                # A fork is somebody else's work sitting in this account; citing it as
                # "what they are building" is the kind of wrong a host says out loud.
                # Filtered BEFORE the cap, not after: `rows[:limit]` spent a slot on every
                # fork it happened to truncate against, so an account whose two most
                # recently pushed repositories are forks returned nothing at budget 2.
                continue
            doc = self.doc(
                str(repo.get("html_url") or ""),
                title=str(repo.get("full_name") or repo.get("name") or ""),
                text=text_block(
                    repo.get("full_name") or repo.get("name"),
                    repo.get("description"),
                    f"Language: {repo['language']}" if repo.get("language") else None,
                    f"{repo.get('stargazers_count', 0)} stars, "
                    f"last pushed {repo.get('pushed_at', 'unknown')}.",
                ),
                published_at=parse_date(repo.get("pushed_at")),
            )
            if doc is not None:
                docs.append(doc)
        return docs

    async def _events(self, login: str, limit: int) -> list[RawDoc]:
        """Recent public activity by `login` (TASKS T-1 acceptance 2, "recent public events").

        `/users/{login}/events/public` is the public half of the activity feed — the same
        thing the profile page shows a logged-out visitor — so nothing here is visible to
        this process that is not already visible to anyone who types the member's login
        into a browser. That is the line: the connector reads a published feed, it does
        not assemble one.
        """
        if limit <= 0:
            return []
        payload = await self.get_json(
            f"{API}/users/{login}/events/public",
            # Headroom: most of a busy account's feed is stars and forks, which
            # `PUBLISHING_EVENTS` drops, so asking for exactly `limit` rows routinely
            # returns `limit` rows of noise and no documents.
            params={"per_page": max(1, min(limit * 10, 30))},
            headers=self._headers(),
        )
        rows: Any = payload
        if isinstance(payload, dict):
            rows = payload.get("items") or payload.get("events")
        if not isinstance(rows, list):
            return []

        docs: list[RawDoc] = []
        for event in rows:
            if len(docs) >= limit:
                break
            doc = self._event(login, event)
            if doc is not None:
                docs.append(doc)
        return docs

    def _event(self, login: str, event: Any) -> RawDoc | None:
        """One activity item as a citation, or `None` when it is not the member's own work."""
        if not isinstance(event, dict):
            return None
        if event.get("public") is False:
            return None
        actor = event.get("actor")
        actor_login = str(actor.get("login") or "") if isinstance(actor, dict) else ""
        # The identity check for this endpoint: the account was verified once, in
        # `_find_account`, and an item whose actor is somebody else did not come from it.
        if actor_login.lower() != login.lower():
            return None

        kind = str(event.get("type") or "")
        if kind not in PUBLISHING_EVENTS:
            return None
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "CreateEvent" and str(payload.get("ref_type") or "") != "repository":
            # A branch or a tag is bookkeeping; a new repository is news.
            return None

        repo = event.get("repo") if isinstance(event.get("repo"), dict) else {}
        full_name = str(repo.get("name") or "")
        if not full_name:
            return None

        verb, page = _EVENT_PROSE.get(kind, ("Public activity on", ""))
        url = _payload_url(payload) or f"https://github.com/{full_name}" + (
            f"/{page}" if page else ""
        )
        return self.doc(
            url,
            title=f"{verb} {full_name}",
            text=text_block(
                f"{verb} {full_name} on GitHub.",
                _event_detail(kind, payload),
                f"Repository: https://github.com/{full_name}",
                f"Public activity by {actor_login}.",
            ),
            published_at=parse_date(event.get("created_at")),
        )


def _payload_url(payload: dict[str, Any]) -> str:
    """The event's own landing page when the payload carries one.

    Preferred over a repository path because it cites the exact thing that happened — the
    release, the pull request — rather than the list it appears in.
    """
    for key in ("release", "pull_request", "issue"):
        item = payload.get(key)
        if isinstance(item, dict):
            url = str(item.get("html_url") or "")
            if url.startswith(("http://", "https://")):
                return url
    return ""


def _event_detail(kind: str, payload: dict[str, Any]) -> str:
    """The one line of this event worth repeating out loud."""
    if kind == "PushEvent":
        commits = payload.get("commits")
        messages = [
            str(commit["message"]).strip()
            for commit in (commits if isinstance(commits, list) else [])
            if isinstance(commit, dict) and commit.get("message")
        ]
        if messages:
            return "Latest commit message: " + messages[0].splitlines()[0]
        size = payload.get("size")
        return f"{size} commits." if isinstance(size, int) else ""
    for key, label in (("release", "Release"), ("pull_request", "Pull request")):
        item = payload.get(key)
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or item.get("tag_name") or "")
            body = str(item.get("body") or "").strip().splitlines()
            headline = f"{label}: {name}".strip(": ")
            return text_block(headline, body[0] if body else None)
    description = payload.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return ""
