"""OpenAlex: scholarly work, co-authors, and the institution that produced both.

WHY IT EARNS A SLOT.  An academic paper is a hub with people already attached to it — a
co-author is a `person` hub and an institution is a `school` hub, and both are far rarer
than "works in tech", which is what makes them worth points in T-5's IDF-weighted score.
It is also one of the few sources where a *non-obvious* fact is routine rather than lucky:
that the person who runs a logistics company wrote the scheduling paper the field still
cites is exactly R7's slot.

Free, no key, no card.  OpenAlex asks callers to identify themselves with a `mailto`, which
costs nothing and buys the polite pool; the address is `CONTACT_EMAIL`, the same one the
User-Agent already advertises.

ABSTRACTS ARE INVERTED.  OpenAlex ships `abstract_inverted_index` — `{word: [positions]}` —
and no plain abstract, for licensing reasons.  Reconstructing it is four lines and is the
difference between a citable paragraph and a title.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, parse_date, text_block
from arrival.contracts import PersonRef, RawDoc

__all__ = ["OpenAlexConnector", "deinvert_abstract"]

AUTHORS = "https://api.openalex.org/authors"
WORKS = "https://api.openalex.org/works"


def deinvert_abstract(index: Any) -> str:
    """Rebuild prose from OpenAlex's `{word: [positions]}` inverted index."""
    if not isinstance(index, dict) or not index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                slots[position] = str(word)
    if not slots:
        return ""
    return " ".join(slots[position] for position in sorted(slots))


def _openalex_id(value: Any) -> str:
    """`https://openalex.org/A123` -> `A123`. Ids arrive as URLs in every response."""
    text = str(value or "")
    return text.rsplit("/", 1)[-1] if text else ""


class OpenAlexConnector(BaseConnector):
    """`kind="openalex"` — the author profile plus their most-cited recent works."""

    kind = "openalex"

    def _polite(self) -> dict[str, str]:
        return {"mailto": self.settings.contact_email}

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        author = await self._author(person)
        if author is None:
            return []

        docs: list[RawDoc] = []
        profile = self._profile_document(author)
        if profile is not None:
            docs.append(profile)

        remaining = budget - len(docs)
        if remaining > 0:
            docs.extend(await self._works(_openalex_id(author.get("id")), remaining))
        return docs

    async def _author(self, person: PersonRef) -> dict[str, Any] | None:
        payload = await self.get_json(
            AUTHORS,
            params={"search": person.name, "per-page": 5, **self._polite()},
        )
        if not isinstance(payload, dict):
            return None
        results = payload.get("results")
        if not isinstance(results, list):
            return None
        for result in results:
            if isinstance(result, dict) and result.get("display_name"):
                return result
        return None

    def _profile_document(self, author: dict[str, Any]) -> RawDoc | None:
        identifier = _openalex_id(author.get("id"))
        if not identifier:
            return None
        institutions = author.get("last_known_institutions")
        if not isinstance(institutions, list) or not institutions:
            single = author.get("last_known_institution")
            institutions = [single] if isinstance(single, dict) else []
        places = [
            str(entry.get("display_name"))
            for entry in institutions
            if isinstance(entry, dict) and entry.get("display_name")
        ]
        concepts = [
            str(entry.get("display_name"))
            for entry in (author.get("x_concepts") or [])
            if isinstance(entry, dict) and entry.get("display_name")
        ]
        return self.doc(
            f"https://openalex.org/{identifier}",
            title=f"{author.get('display_name')} — OpenAlex author profile",
            text=text_block(
                str(author.get("display_name") or ""),
                f"Affiliation: {', '.join(places)}" if places else None,
                f"Research areas: {', '.join(concepts[:6])}" if concepts else None,
                f"{author.get('works_count', 0)} works, "
                f"{author.get('cited_by_count', 0)} citations.",
                f"ORCID: {author['orcid']}" if author.get("orcid") else None,
            ),
        )

    async def _works(self, author_id: str, limit: int) -> list[RawDoc]:
        if not author_id:
            return []
        payload = await self.get_json(
            WORKS,
            params={
                "filter": f"author.id:{author_id}",
                "sort": "publication_date:desc",
                "per-page": max(1, min(limit, 25)),
                **self._polite(),
            },
        )
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        docs: list[RawDoc] = []
        for work in results[:limit]:
            if not isinstance(work, dict):
                continue
            doc = self._work_document(work)
            if doc is not None:
                docs.append(doc)
        return docs

    def _work_document(self, work: dict[str, Any]) -> RawDoc | None:
        title = str(work.get("display_name") or work.get("title") or "")
        abstract = str(work.get("abstract") or "") or deinvert_abstract(
            work.get("abstract_inverted_index")
        )
        url = str(work.get("doi") or "")
        location = work.get("primary_location")
        if not url and isinstance(location, dict):
            url = str(location.get("landing_page_url") or "")
        if not url:
            identifier = _openalex_id(work.get("id"))
            url = f"https://openalex.org/{identifier}" if identifier else ""

        venue = ""
        if isinstance(location, dict) and isinstance(location.get("source"), dict):
            venue = str(location["source"].get("display_name") or "")

        coauthors = [
            str(entry["author"].get("display_name"))
            for entry in (work.get("authorships") or [])
            if isinstance(entry, dict) and isinstance(entry.get("author"), dict)
        ]
        return self.doc(
            url,
            title=title,
            text=text_block(
                title,
                f"Published {work.get('publication_year')}" + (f" in {venue}" if venue else ""),
                f"Authors: {', '.join(coauthors)}" if coauthors else None,
                f"Cited {work.get('cited_by_count', 0)} times.",
                abstract,
            ),
            published_at=parse_date(work.get("publication_date")),
        )
