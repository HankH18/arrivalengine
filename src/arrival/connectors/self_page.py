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
from arrival.connectors.identity import carries_name, choose_one, corroborates
from arrival.contracts import PersonRef, RawDoc
from arrival.http.client import fetch_record

__all__ = ["SelfPageConnector"]

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

#: Wikidata "official website".
OFFICIAL_WEBSITE_PROPERTY = "P856"

#: Wikidata "instance of", and the item id for "human".
INSTANCE_OF = "P31"
HUMAN = "Q5"

#: How many same-name candidates to look at before deciding. `limit=1` was the defect:
#: it makes the search engine's ranking the identity decision, and hides from the
#: connector the very fact -- that there is more than one of her -- that should make it
#: decline.
CANDIDATES = 5

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
        """Wikidata P856 for the entity the ROSTER identifies, or "" when there is not one.

        THE WORST CASE IN THE FAN-OUT, AND THE ONE WITH NO FIXTURE COVERAGE.  This branch
        runs only when `details` names no URL, which no recorded corpus does, so nothing
        watched it.  What it used to do: search Wikidata on the NAME ALONE with `limit=1`,
        take whatever came back first, follow that item's website one hop, and stamp the
        result `self_page` — **the highest-trust `SourceKind` in the system**, the one
        whose whole justification is "they published it, on their own domain, on purpose".
        A same-name stranger at rank 1 got the member's most-trusted document slot, and
        `wbsearchentities` ranks by sitelink count, so the stranger it picks is
        systematically the more famous of the two.

        So: headroom instead of `limit=1` (a stranger at rank 1 must not be able to spend
        the whole allowance), the label has to carry the name, the item has to say it is a
        human, and the roster has to recognise it — `require_corroboration=True`, which is
        the one place in the fan-out that flag is set. A tie declines, and so does a lone
        candidate nothing corroborates.
        """
        found = await self.get_json(
            WIKIDATA_API,
            params={
                "action": "wbsearchentities",
                "search": person.name,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": CANDIDATES,
                "format": "json",
            },
        )
        qids = [
            str(row["id"])
            for row in _rows(found)
            if str(row.get("id", "")).startswith("Q")
            and carries_name(str(row.get("label") or row.get("id") or ""), person.name)
        ][:CANDIDATES]
        if not qids:
            return ""

        entities = await self.get_json(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(qids),
                "props": "claims|labels|descriptions|aliases",
                "languages": "en",
                "format": "json",
            },
        )
        people = [
            (qid, entity)
            for qid in qids
            if _is_a_human(entity := _entity(entities, qid))
        ]
        chosen = choose_one(
            people,
            lambda pair: corroborates(person, _identity_text(pair[1])),
            require_corroboration=True,
        )
        if chosen is None:
            return ""
        return _website_claim(entities, chosen[0])


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("search")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _entity(payload: Any, qid: str) -> dict[str, Any]:
    entities = payload.get("entities") if isinstance(payload, dict) else None
    entity = entities.get(qid) if isinstance(entities, dict) else None
    return entity if isinstance(entity, dict) else {}


def _is_a_human(entity: dict[str, Any]) -> bool:
    """`P31 = Q5`. An item that says it is something else is not a member of the club.

    An item with no `P31` at all is allowed through to the corroboration check rather than
    rejected: Wikidata is incomplete, and "unstated" is not "stated to be a company".
    """
    instances = [
        value
        for claim in (entity.get("claims") or {}).get(INSTANCE_OF, []) or []
        if isinstance(claim, dict)
        for value in [_item_id(claim)]
        if value
    ]
    return not instances or HUMAN in instances


def _item_id(claim: dict[str, Any]) -> str:
    snak = claim.get("mainsnak")
    datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
    value = datavalue.get("value") if isinstance(datavalue, dict) else None
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return ""


def _identity_text(entity: dict[str, Any]) -> str:
    """Everything on an item that a roster detail could match: label, description, aliases.

    Item-valued claims are deliberately NOT resolved here. Turning `P108 -> Q90000001`
    into "Thornfield Loom" costs a third round trip per person, and it is the `wikidata`
    connector's job — this one only needs to decide whether to fetch a website.
    """
    parts: list[str] = []
    for group in ("labels", "descriptions"):
        values = entity.get(group)
        if isinstance(values, dict):
            for value in values.values():
                if isinstance(value, dict) and value.get("value"):
                    parts.append(str(value["value"]))
    aliases = entity.get("aliases")
    if isinstance(aliases, dict):
        for group in aliases.values():
            if isinstance(group, list):
                parts.extend(
                    str(alias["value"])
                    for alias in group
                    if isinstance(alias, dict) and alias.get("value")
                )
    for claim in (entity.get("claims") or {}).get(OFFICIAL_WEBSITE_PROPERTY, []) or []:
        if isinstance(claim, dict):
            snak = claim.get("mainsnak")
            datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
            value = datavalue.get("value") if isinstance(datavalue, dict) else None
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


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
