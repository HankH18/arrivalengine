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

WHAT THE SEARCH RETURNS IS NOT WHAT WAS ASKED FOR (T-020).  Searching a person's name and
their company returns the company's article too, and frequently ranks it first.  Emitted
as a `wikipedia` document it is presented as an encyclopedic biography OF THE PERSON —
R11 displays it that way and T-3 extracts facts about the SUBJECT from whatever text
arrives — so "Thornfield Loom publishes a monthly maintenance almanac" becomes a sentence
about Marisol Quennebeck, correctly cited to a page that never mentions her.  A document
is kept only when the article's own title carries the member's name.

Two smaller traps went with it.  `srlimit=budget` gave the filter nothing to work with:
search rank is not relevance rank, so at `budget=1` the single title returned is often the
company and the connector has no headroom to reach her article three results down.  And
`quote()` leaves `/` safe, while a page title is one path SEGMENT — a title containing a
slash built a REST path that addresses something else and 404s.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from arrival.connectors.base import BaseConnector, affiliations, parse_date, text_block
from arrival.contracts import PersonRef, RawDoc
from arrival.util import normalize_ws

__all__ = ["WikipediaConnector"]

API = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
ARTICLE = "https://en.wikipedia.org/wiki/{title}"

_WORD = re.compile(r"[^0-9a-z]+")


def _tokens(text: str) -> set[str]:
    """Comparable word tokens. Single letters are dropped: initials match anything."""
    return {word for word in _WORD.split(normalize_ws(text)) if len(word) >= 2}


def _is_about(title: str, name: str) -> bool:
    """Is an article with this title about `name`?

    Word containment, so "Pell Marrowby (entrepreneur)" and "Marisol Quennebeck/Archive"
    are hers while "Pelmyre Works" is not. Deliberately judged on the TITLE and not on the
    extract: the company's article names its founder in the first sentence, which is
    exactly how an article about the company gets mistaken for an article about her.
    """
    wanted = _tokens(name)
    return bool(wanted) and wanted <= _tokens(title)


def _path_segment(title: str) -> str:
    """A page title as ONE REST path segment. `safe=""` because `/` occurs in titles."""
    return quote(title.replace(" ", "_"), safe="")


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
                # Headroom: these candidates are about to be filtered down to the ones
                # actually about her, and the company's article often outranks hers.
                "srlimit": max(5, min(budget * 3, 20)),
                "format": "json",
                "formatversion": 2,
            },
        )
        titles = self._titles(payload)
        if not titles:
            return []

        docs: list[RawDoc] = []
        for title in titles[: max(5, min(budget + 3, 10))]:
            if len(docs) >= budget:
                break
            doc = await self._summary(person, title)
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

    async def _summary(self, person: PersonRef, title: str) -> RawDoc | None:
        payload = await self.get_json(SUMMARY.format(title=_path_segment(title)))
        if not isinstance(payload, dict):
            return None
        extract = str(payload.get("extract") or "")
        if not extract.strip():
            return None
        if str(payload.get("type") or "") == "disambiguation":
            return None

        # The article's own title, preferred over the one we searched for, because a
        # redirect lands somewhere else and it is where we landed that we are citing.
        titles = payload.get("titles")
        landed = str(
            (titles.get("normalized") if isinstance(titles, dict) else "")
            or payload.get("title")
            or title
        )
        if not _is_about(landed, person.name):
            return None

        url = ""
        content_urls = payload.get("content_urls")
        if isinstance(content_urls, dict):
            desktop = content_urls.get("desktop")
            if isinstance(desktop, dict):
                url = str(desktop.get("page") or "")
        if not url:
            # `/wiki/` is not the REST API: it takes the title as the rest of the path,
            # so a slash inside a title stays a slash here and the link still resolves.
            url = ARTICLE.format(title=quote(landed.replace(" ", "_")))

        return self.doc(
            url,
            title=str(payload.get("title") or title),
            text=text_block(payload.get("description"), extract),
            published_at=parse_date(payload.get("timestamp")),
        )
