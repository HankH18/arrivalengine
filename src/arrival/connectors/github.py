"""GitHub: what someone actually builds, in their own commit messages.

WHY THIS IS A TASTE SOURCE AND NOT A SURVEILLANCE ONE.  A public repository is a thing a
person chose to publish under their own name, and its README line is the sentence they
wrote about their own work.  That is the far side of the "seen vs. dossiered" line: a host
saying "you pushed a release for the freight scheduler last week" is reading a press
release the member published themselves.

Three calls, each earning its place: `/search/users` maps a display name to a login;
`/users/{login}` returns the profile fields (name, company, blog, location, bio) a resolver
needs to *reject* the wrong Pell Marrowby, and search results deliberately omit; and
`/users/{login}/repos?sort=pushed` returns recent work newest-first, which is the half a
digest can use.

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
        if remaining > 0:
            docs.extend(await self._repositories(login, remaining))
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
                # Headroom for the fork filter below.
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
