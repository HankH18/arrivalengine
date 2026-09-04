"""Wikipedia: the encyclopedic summary, when there is one.

Most members of a private club are not in Wikipedia, and that is fine — a connector that
returns `[]` for nine people out of ten and one excellent paragraph for the tenth is still
worth its request budget, because that paragraph is the highest-signal biography this
pipeline can get for free.

Two calls: the search API to turn a name into candidate page titles, then the REST
`page/summary` endpoint per candidate — `summary` rather than `action=query&prop=extracts`
because it returns the lead section already stripped of wikitext, which is exactly the
"extracted plain text" `RawDoc` wants and saves a second cleaning pass.  The canonical
desktop URL from the response is preferred over one we build from the title, so a redirect
("Pell Marrowby (entrepreneur)") cites where the reader will actually land.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from arrival.connectors.base import BaseConnector, affiliations, parse_date, text_block
from arrival.contracts import PersonRef, RawDoc

__all__ = ["WikipediaConnector"]

API = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
ARTICLE = "https://en.wikipedia.org/wiki/{title}"


class WikipediaConnector(BaseConnector):
    """`kind="wikipedia"` — lead sections for the pages that mention this person."""

    kind = "wikipedia"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        affiliation = next(iter(affiliations(person.details)), "")
        payload = await self.get_json(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": f"{person.name} {affiliation}".strip(),
                "srlimit": max(1, min(budget, 10)),
                "format": "json",
                "formatversion": 2,
            },
        )
        titles = self._titles(payload)
        if not titles:
            return []

        docs: list[RawDoc] = []
        for title in titles:
            if len(docs) >= budget:
                break
            doc = await self._summary(title)
            if doc is not None:
                docs.append(doc)
        return docs

    @staticmethod
    def _titles(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        query = payload.get("query")
        if not isinstance(query, dict):
            return []
        rows = query.get("search")
        titles: list[str] = []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("title"):
                    titles.append(str(row["title"]))
        # `formatversion=2` returns `pages` as a list, version 1 as a dict keyed by pageid;
        # both are accepted so a server default cannot silently empty this connector.
        pages = query.get("pages")
        entries: list[Any] = []
        if isinstance(pages, dict):
            entries = list(pages.values())
        elif isinstance(pages, list):
            entries = pages
        for page in entries:
            if isinstance(page, dict) and page.get("title"):
                title = str(page["title"])
                if title not in titles:
                    titles.append(title)
        return titles

    async def _summary(self, title: str) -> RawDoc | None:
        payload = await self.get_json(SUMMARY.format(title=quote(title.replace(" ", "_"))))
        if not isinstance(payload, dict):
            return None
        extract = str(payload.get("extract") or "")
        if not extract.strip():
            return None
        url = ""
        content_urls = payload.get("content_urls")
        if isinstance(content_urls, dict):
            desktop = content_urls.get("desktop")
            if isinstance(desktop, dict):
                url = str(desktop.get("page") or "")
        if not url:
            url = ARTICLE.format(title=quote(title.replace(" ", "_")))

        return self.doc(
            url,
            title=str(payload.get("title") or title),
            text=text_block(payload.get("description"), extract),
            published_at=parse_date(payload.get("timestamp")),
        )
