"""Hacker News via Algolia: what a technical person said in public, in their own voice.

WHY THIS IS A CONVERSATION SOURCE.  Most of this fan-out returns facts *about* a person.
HN returns things the person wrote — a "Show HN" for the thing they built, or the comment
where they explained why they chose the boring database.  R7's opener is supposed to sound
like the host read something, not like they read a bio, and a sentence someone published
under their own handle is the closest a machine gets to that.

The citation is the HN item, not the linked article: the item is where the person's words
are, it never rots, and it makes the source of a quote unambiguous when the article behind
it says something different.  Stories are preferred over comments (`tags=story`) because a
comment out of context is the easiest way to embarrass a member.

The Algolia endpoint is free and needs no key.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, affiliations, parse_date, text_block
from arrival.contracts import PersonRef, RawDoc

__all__ = ["HackerNewsConnector"]

SEARCH = "https://hn.algolia.com/api/v1/search"
ITEM_URL = "https://news.ycombinator.com/item?id={item_id}"


class HackerNewsConnector(BaseConnector):
    """`kind="hn"` — stories by or about this person, cited to the HN item."""

    kind = "hn"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        affiliation = next(iter(affiliations(person.details)), "")
        payload = await self.get_json(
            SEARCH,
            params={
                # The quoted name is the whole disambiguation budget here: HN search is
                # a full-text index with no notion of people.
                "query": f'"{person.name}" {affiliation}'.strip(),
                "tags": "story",
                "hitsPerPage": max(1, min(budget * 2, 20)),
            },
        )
        hits = self._hits(payload)

        docs: list[RawDoc] = []
        for hit in hits:
            if len(docs) >= budget:
                break
            doc = self._document(hit)
            if doc is not None:
                docs.append(doc)
        return docs

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
        title = str(hit.get("title") or hit.get("story_title") or "")
        body = hit.get("story_text") or hit.get("comment_text") or ""
        linked = hit.get("url") or hit.get("story_url")

        return self.doc(
            ITEM_URL.format(item_id=item_id),
            title=title,
            text=text_block(
                title,
                f"Submitted by {hit['author']}" if hit.get("author") else None,
                f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments.",
                f"Links to {linked}" if linked else None,
                body,
            ),
            published_at=parse_date(hit.get("created_at") or hit.get("created_at_i")),
        )
