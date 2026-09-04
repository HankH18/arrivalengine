"""The person's own pages: the highest-trust source in the fan-out.

WHY IT IS LAST IN THE MODULE LIST AND FIRST IN DISPLAY PRIORITY.  Everything else here is
somebody's index *of* a person.  This is the page the person wrote, which makes it the only
source where "is this really them?" and "may we say this out loud?" are both answered by
construction: they published it, under their own domain, on purpose.  R7's conversation
opener is at its best when it quotes this.

WHERE THE URLS COME FROM.  Two documented places (TASKS T-1 acceptance 2): a URL sitting in
`PersonRef.details`, and — when the roster gives none — Wikidata's official-website
property (P856), which is the same identifier spine the `wikidata` connector keys on.  The
second is a fallback, not a routine second lookup: a roster that already names the site
should not cost two extra API calls per person.

Same-host links on a fetched page are followed while budget remains, so `/about` and
`/team/{name}` are reachable from a bare domain.  Off-host links are not followed at all —
crawling outward from a personal site is how a "research the member" tool quietly becomes a
"crawl the internet" tool.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from arrival.connectors.base import BaseConnector, urls_in
from arrival.contracts import PersonRef, RawDoc
from arrival.http.client import fetch_record

__all__ = ["SelfPageConnector"]

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

#: Wikidata "official website".
OFFICIAL_WEBSITE_PROPERTY = "P856"

#: Paths that are never a person's own prose, so following them wastes the budget.
_SKIP_SEGMENTS = ("/login", "/signup", "/cart", "/privacy", "/terms", "/rss", "/feed")


class _Links(HTMLParser):
    """Collect `href` values in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _same_host_links(base_url: str, markup: str) -> list[str]:
    parser = _Links()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a page we cannot parse simply has no links
        return []

    base_host = (urlsplit(base_url).hostname or "").lower()
    found: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href.strip())
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        if (parts.hostname or "").lower() != base_host:
            continue
        if any(segment in parts.path.lower() for segment in _SKIP_SEGMENTS):
            continue
        clean = absolute.split("#", 1)[0]
        if clean and clean not in found:
            found.append(clean)
    return found


class SelfPageConnector(BaseConnector):
    """`kind="self_page"` — the member's own site, and pages one hop inside it."""

    kind = "self_page"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        seeds = urls_in(person.details)
        if not seeds:
            website = await self._official_website(person)
            if website:
                seeds = [website]
        if not seeds:
            return []

        docs: list[RawDoc] = []
        followed: list[str] = []
        visited: set[str] = set()

        for url in seeds:
            if len(docs) >= budget:
                break
            doc, markup = await self._page(url, visited)
            if doc is None:
                continue
            docs.append(doc)
            for link in _same_host_links(doc.url, markup or ""):
                if link not in followed:
                    followed.append(link)

        for url in followed:
            if len(docs) >= budget:
                break
            doc, _ = await self._page(url, visited)
            if doc is not None:
                docs.append(doc)
        return docs

    async def _page(self, url: str, visited: set[str]) -> tuple[RawDoc | None, str | None]:
        """Fetch once and return both the citation and the markup its links live in."""
        if url in visited:
            return None, None
        visited.add(url)
        record = await fetch_record(url, settings=self.settings)
        if record is None:
            return None, None
        visited.add(record.url)
        doc = await self.get_page(url)
        return doc, record.body

    async def _official_website(self, person: PersonRef) -> str:
        """Wikidata P856 for the best-matching entity, or "" when there is not one."""
        found = await self.get_json(
            WIKIDATA_API,
            params={
                "action": "wbsearchentities",
                "search": person.name,
                "language": "en",
                "type": "item",
                "limit": 1,
                "format": "json",
            },
        )
        qid = ""
        if isinstance(found, dict) and isinstance(found.get("search"), list):
            for row in found["search"]:
                if isinstance(row, dict) and str(row.get("id", "")).startswith("Q"):
                    qid = str(row["id"])
                    break
        if not qid:
            return ""

        entities = await self.get_json(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims",
                "format": "json",
            },
        )
        return _website_claim(entities, qid)


def _website_claim(payload: Any, qid: str) -> str:
    if not isinstance(payload, dict):
        return ""
    entities = payload.get("entities")
    entity = entities.get(qid) if isinstance(entities, dict) else None
    claims = entity.get("claims") if isinstance(entity, dict) else None
    if not isinstance(claims, dict):
        return ""
    for claim in claims.get(OFFICIAL_WEBSITE_PROPERTY, []) or []:
        if not isinstance(claim, dict):
            continue
        snak = claim.get("mainsnak")
        datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
        value = datavalue.get("value") if isinstance(datavalue, dict) else None
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return ""
