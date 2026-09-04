"""Hacker News via Algolia: what a technical person said in public, in their own voice.

WHY THIS IS A CONVERSATION SOURCE.  Most of this fan-out returns facts *about* a person.
HN returns things the person wrote — a "Show HN" for the thing they built, or the comment
where they explained why they chose the boring database.  R7's opener is supposed to sound
like the host read something, not like they read a bio, and a sentence someone published
under their own handle is the closest a machine gets to that.

The citation is the HN item, not the linked article: the item is where the person's words
are, it never rots, and it makes the source of a quote unambiguous when the article behind
it says something different.  Stories are preferred over comments because a comment out of
context is the easiest way to embarrass a member — so stories come first and comments spend
only what is left, capped.

BY NAME **AND BY AUTHOR** (T-024).  TASKS T-1 acceptance 2 says "Algolia by author/name",
and only the name half was built: one `tags=story` query for the member's name, which finds
things written ABOUT her and misses everything she wrote under a handle.  That is most of
what HN has.  The obstacle is real, and it is why the author half was skipped rather than
merely forgotten: a handle is not a name.  `carries_name("mquennebeck", "Marisol
Quennebeck")` is False, so the shared identity contract cannot accept a comment by a handle
on the strength of the handle, and accepting it anyway would be the exact defect
`identity.py` exists to prevent — every comment by every `mquennebeck` on the site,
attributed to the member.

So the handle is VERIFIED FIRST, once, against the profile its owner wrote:
`/api/v1/users/{handle}` returns `about`, a free-text bio, and that goes through
`identifies` exactly as GitHub's `/users/{login}` does.  A handle whose profile names the
member in full and echoes something the roster supplied — or simply links to the member's
own domain — is hers; every other handle is somebody else's and is dropped before a single
comment is read.  Once the handle is verified, `tags=author_{handle}` is scoped to her by
construction, and the identity question is not re-asked per item for the same reason it is
not re-asked per repository on GitHub: a comment does not contain its author's legal name,
so demanding that it does would reject every correct hit.

Candidate handles come from the roster (`news.ycombinator.com/user?id=…` in `details`, the
strongest source, since the club wrote it down) and from the authors of hits the name
search already tied to her.  Both are only candidates: the profile check is the gate.

The Algolia endpoint is free and needs no key.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from arrival.connectors.base import BaseConnector, parse_date, text_block, urls_in
from arrival.connectors.identity import best_affiliation, identifies
from arrival.contracts import PersonRef, RawDoc

__all__ = ["HackerNewsConnector"]

SEARCH = "https://hn.algolia.com/api/v1/search"
USER = "https://hn.algolia.com/api/v1/users/{handle}"
ITEM_URL = "https://news.ycombinator.com/item?id={item_id}"

#: Handles to check profiles for before giving up. One request each, and the list is short
#: because it exists to survive a wrong guess, not to enumerate a name.
CANDIDATE_HANDLES = 2

#: The most comments one person's dossier may draw. A comment is the highest-risk document
#: this connector can emit — it is a remark made in a thread, and the thread is not in the
#: citation — so it is capped rather than merely ranked below stories.
MAX_COMMENTS = 2


def _is_hers(person: PersonRef, hit: dict[str, Any]) -> bool:
    """Is this story by or about the member?

    `hit["author"]` was displayed in the document text (`Submitted by ...`) and never
    checked against anything — and it is a HANDLE, so checking it alone would reject every
    correct hit: `mquennebeck` does not contain "marisol". It is one naming field among
    several, never the gate.

    What actually ties a story to a person here is where it POINTS. A submission linking
    to the member's own domain is hers whoever posted it; otherwise the story's own words
    have to name her in full and echo something the roster supplied.
    """
    linked = [str(hit.get("url") or ""), str(hit.get("story_url") or "")]
    prose = [
        str(hit.get("title") or ""),
        str(hit.get("story_title") or ""),
        str(hit.get("story_text") or ""),
        str(hit.get("comment_text") or ""),
    ]
    return identifies(
        person,
        names=[str(hit.get("author") or "")],
        prose=prose,
        urls=linked,
        context=[*prose, *linked],
    )


class HackerNewsConnector(BaseConnector):
    """`kind="hn"` — stories by or about this person, cited to the HN item."""

    kind = "hn"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        docs, hits = await self._by_name(person, budget)
        if len(docs) >= budget:
            return docs
        docs.extend(await self._by_author(person, hits, budget - len(docs)))
        return docs

    async def _by_name(
        self, person: PersonRef, budget: int
    ) -> tuple[list[RawDoc], list[dict[str, Any]]]:
        """The name half. Returns the documents AND the raw hits, which name the handles."""
        payload = await self.get_json(
            SEARCH,
            params={
                # NOT a phrase query, whatever the quotes suggest. Algolia is typo-tolerant
                # and does not honour `"` as a strict phrase operator, so this string is a
                # bag of words and the hits come back fuzzy-matched: "the search returned
                # it" carries no information about who it is about. The quotes are kept
                # because they cost nothing and help the ranker; the DECIDING is done below,
                # on the hit itself.
                "query": f'"{person.name}" {best_affiliation(person)}'.strip(),
                "tags": "story",
                "hitsPerPage": max(1, min(budget * 2, 20)),
            },
        )
        hits = self._hits(payload)

        docs: list[RawDoc] = []
        hers: list[dict[str, Any]] = []
        for hit in hits:
            if not _is_hers(person, hit):
                continue
            hers.append(hit)
            if len(docs) >= budget:
                continue
            doc = self._document(hit)
            if doc is not None:
                docs.append(doc)
        return docs, hers

    async def _by_author(
        self, person: PersonRef, hits: list[dict[str, Any]], limit: int
    ) -> list[RawDoc]:
        """The author half: everything a VERIFIED handle wrote, comments included."""
        if limit <= 0:
            return []
        handle = await self._verified_handle(person, hits)
        if not handle:
            return []

        payload = await self.get_json(
            SEARCH,
            params={
                # `author_{handle}` AND `(story,comment)`: Algolia ANDs comma-separated
                # tags and ORs parenthesised ones, so this is "written by her, of either
                # shape". No `query`, because the handle IS the query.
                "tags": f"author_{handle},(story,comment)",
                "hitsPerPage": max(1, min(limit * 4, 20)),
            },
        )
        stories: list[dict[str, Any]] = []
        comments: list[dict[str, Any]] = []
        for hit in self._hits(payload):
            if str(hit.get("author") or "") != handle:
                # A tag filter is a request, not a guarantee; the author field is the
                # answer.
                continue
            (comments if _is_a_comment(hit) else stories).append(hit)

        docs: list[RawDoc] = []
        for hit in [*stories, *comments[:MAX_COMMENTS]]:
            if len(docs) >= limit:
                break
            doc = self._document(hit)
            if doc is not None:
                docs.append(doc)
        return docs

    async def _verified_handle(
        self, person: PersonRef, hits: list[dict[str, Any]]
    ) -> str:
        """A handle whose own PROFILE says it belongs to the member, or `""`.

        This is the whole identity decision for the author half, made once. It is the same
        shape as GitHub's: the search result cannot answer it (an Algolia hit carries a
        handle and nothing that could identify a person), so the profile behind the handle
        is fetched and put through the shared contract.
        """
        for handle in _candidate_handles(person, hits)[:CANDIDATE_HANDLES]:
            profile = await self.get_json(USER.format(handle=handle))
            if not isinstance(profile, dict):
                continue
            username = str(profile.get("username") or profile.get("id") or "")
            about = str(profile.get("about") or "")
            if identifies(
                person,
                names=[username],
                prose=[about],
                urls=urls_in([about]),
                context=[about, username],
            ):
                return handle
        return ""

    @staticmethod
    def _hits(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        hits = payload.get("hits")
        if not isinstance(hits, list):
            return []
        return [hit for hit in hits if isinstance(hit, dict)]

    def _document(self, hit: dict[str, Any]) -> RawDoc | None:
        item_id = str(hit.get("objectID") or hit.get("story_id") or "").strip()
        if not item_id:
            return None
        author = str(hit.get("author") or "")
        published_at = parse_date(hit.get("created_at") or hit.get("created_at_i"))

        if _is_a_comment(hit):
            # A comment's citation is the COMMENT's permalink, not the story's: the
            # story's item id belongs to somebody else's submission, and citing it would
            # attribute the whole thread to the member.
            story = str(hit.get("story_title") or hit.get("title") or "")
            story_id = str(hit.get("story_id") or "").strip()
            return self.doc(
                ITEM_URL.format(item_id=item_id),
                title=f"Comment on “{story}”" if story else "Comment on Hacker News",
                text=text_block(
                    f"Hacker News comment by {author}." if author else "Hacker News comment.",
                    f"In the discussion of “{story}”." if story else None,
                    f"Story: {ITEM_URL.format(item_id=story_id)}" if story_id else None,
                    str(hit.get("comment_text") or ""),
                ),
                published_at=published_at,
            )

        title = str(hit.get("title") or hit.get("story_title") or "")
        body = hit.get("story_text") or hit.get("comment_text") or ""
        linked = hit.get("url") or hit.get("story_url")

        return self.doc(
            ITEM_URL.format(item_id=item_id),
            title=title,
            text=text_block(
                title,
                f"Submitted by {author}" if author else None,
                f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments.",
                f"Links to {linked}" if linked else None,
                body,
            ),
            published_at=published_at,
        )


def _is_a_comment(hit: dict[str, Any]) -> bool:
    """Algolia returns both shapes from one index; `_tags` is where it says which."""
    tags = hit.get("_tags")
    if isinstance(tags, list):
        labels = {str(tag) for tag in tags}
        if "comment" in labels:
            return True
        if "story" in labels:
            return False
    return bool(hit.get("comment_text")) and not hit.get("title")


def _candidate_handles(person: PersonRef, hits: list[dict[str, Any]]) -> list[str]:
    """Handles worth checking a profile for, strongest first.

    The roster comes first because it is the only source here that is not a guess: a club
    that wrote `news.ycombinator.com/user?id=mquennebeck` next to a member's name has
    already made the identification this connector otherwise has to earn.
    """
    found: list[str] = []
    for url in urls_in(person.details):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if host != "news.ycombinator.com":
            continue
        for handle in parse_qs(parts.query).get("id", []):
            if handle.strip() and handle.strip() not in found:
                found.append(handle.strip())
    for hit in hits:
        handle = str(hit.get("author") or "").strip()
        if handle and handle not in found:
            found.append(handle)
    return found
