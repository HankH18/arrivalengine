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

AND THE FEED, WHICH IS THE THIRD DOCUMENTED SOURCE (T-021).  Acceptance 2 ends "plus
`/feed` RSS if present", and the module used to do the exact opposite: `/feed` and `/rss`
sat on the skip list, so the one address on the member's own site that carries DATED prose
was the one address deliberately never read.  That mattered more than it sounds.
`fetch_text` cannot date a page — there is nothing in HTML that reliably says when it was
written — so before this, every `self_page` document arrived with `published_at=None` and
a digest could say what the member writes but never when.  A feed entry carries its date as
a field.

The skip list survives, with its job made explicit: a feed is never emitted AS A PAGE (a
reader does not want the XML), it is handed to the feed reader instead.  Feeds are found
three ways, cheapest first — the `<link rel="alternate">` the site already advertises, an
anchor whose path says "feed", and finally the single conventional guess at `{origin}/feed`
that acceptance 2 names.

WEB SPACE, NOT HOST, FOR EVERYTHING FOLLOWED.  On a domain the member owns, one host check
is the whole story.  On a shared platform it is not, and this connector reaches one: a
roster line reading `linkedin.com/in/marisol-quennebeck` makes `linkedin.com/in/anybody`
a same-host link and `linkedin.com/feed` the platform's own timeline, and both would be
stamped `self_page` — the highest-trust `SourceKind` in the system.  So a link on a
`SHARED_HOSTS` host is followed only under the path the roster actually named, and the
conventional feed guess is not made on one at all.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from arrival.connectors.base import BaseConnector, text_block, urls_in
from arrival.connectors.feed import (
    advertised_feeds,
    conventional_feed,
    is_feed_url,
    parse_feed,
)
from arrival.connectors.identity import (
    carries_name,
    choose_one,
    corroborates,
    is_shared_host,
)
from arrival.contracts import PersonRef, RawDoc
from arrival.http.client import fetch_record
from arrival.http.extract import html_to_text

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
#: `feed.FEED_NAMES` are excluded from PAGE following for a different reason and handled
#: separately: a feed is not prose, it is a list of prose, and it is read as one.
_SKIP_SEGMENTS = ("/login", "/signup", "/cart", "/privacy", "/terms")

#: A feed entry shorter than this is a headline with no body — a link list, a podcast
#: stub, a "new post" ping. It is not evidence of anything, so the page behind it is
#: fetched instead of cited from the feed.
MIN_ENTRY_CHARS = 40


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


def in_web_space(url: str, base_url: str, seed: str) -> bool:
    """Is `url` inside the web space the roster's `seed` vouches for?

    The same rule `identity.on_own_host` applies to a roster URL, applied here to a SEED —
    which may have come from Wikidata's P856 rather than from `details`, and so is not in
    `person.details` for `on_own_host` to find. The host is compared against `base_url`,
    the address the seed actually RESOLVED to, so a site that redirects to its `www.` form
    still has its own links followed; the shared-platform path check is anchored on the
    seed, because that is the part the roster named.
    """
    target = urlsplit(url)
    host = (target.hostname or "").lower()
    if target.scheme not in ("http", "https") or not host:
        return False
    if host != (urlsplit(base_url).hostname or "").lower():
        return False
    if not is_shared_host(host):
        return True
    prefix = urlsplit(seed).path.rstrip("/")
    if not prefix:
        # The roster named a platform's ROOT. That names nobody, so it vouches for nobody.
        return False
    return target.path == prefix or target.path.startswith(prefix + "/")


def _hrefs(base_url: str, markup: str) -> list[str]:
    parser = _Links()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a page we cannot parse simply has no links
        return []
    found: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href.strip()).split("#", 1)[0]
        if absolute and absolute not in found:
            found.append(absolute)
    return found


def _page_links(base_url: str, seed: str, markup: str) -> list[str]:
    """Same-web-space links worth following as prose, in document order."""
    return [
        url
        for url in _hrefs(base_url, markup)
        if in_web_space(url, base_url, seed)
        and not is_feed_url(url)
        and not any(segment in urlsplit(url).path.lower() for segment in _SKIP_SEGMENTS)
    ]


def _feed_urls(base_url: str, seed: str, markup: str) -> list[str]:
    """Feeds this page offers, cheapest discovery first, then the one guess.

    The conventional `{origin}/feed` is skipped on a shared host: `linkedin.com/feed` is
    the platform's timeline and belongs to nobody, and `medium.com/feed` is Medium's.
    """
    found: list[str] = []
    for url in advertised_feeds(base_url, markup):
        if in_web_space(url, base_url, seed) and url not in found:
            found.append(url)
    for url in _hrefs(base_url, markup):
        if is_feed_url(url) and in_web_space(url, base_url, seed) and url not in found:
            found.append(url)
    host = (urlsplit(base_url).hostname or "").lower()
    if not is_shared_host(host):
        guess = conventional_feed(base_url)
        if guess and guess not in found:
            found.append(guess)
    return found


def _entry_text(title: str, summary: str) -> str:
    """The entry's own prose, with any markup its summary carried removed.

    Atom permits `type="html"` content, so a `<summary>` routinely arrives as escaped
    markup; leaving it in would put `<p>` into `RawDoc.text`, which T-3 quotes verbatim.
    """
    body = html_to_text(summary) if "<" in summary else summary
    return text_block(title, body)


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
        feeds: list[tuple[str, str]] = []
        visited: set[str] = set()

        for url in seeds:
            if len(docs) >= budget:
                break
            if is_feed_url(url):
                # T-061's other half. The skip list stopped a CRAWLED link that is a feed
                # from being read as a page; a seed the ROSTER wrote went straight to the
                # page fetcher, so `https://site.example/feed` was extracted as prose and
                # emitted as one `self_page` document of flattened XML — no entries, no
                # dates, and the one thing this connector reads a feed for lost. A feed is
                # a feed whoever named it.
                feeds.append((url, url))
                continue
            doc, markup = await self._page(url, visited)
            if doc is None:
                continue
            docs.append(doc)
            markup = markup or ""
            for link in _page_links(doc.url, url, markup):
                if link not in followed:
                    followed.append(link)
            for candidate in _feed_urls(doc.url, url, markup):
                if (candidate, url) not in feeds:
                    feeds.append((candidate, url))

        # Feeds before the remaining page links: a feed entry is the only `self_page`
        # document that arrives with a date on it, and an undated extraction of the same
        # page is the thing a digest can do least with.
        for feed_url, seed in feeds:
            if len(docs) >= budget:
                break
            docs.extend(await self._feed(feed_url, seed, visited, budget - len(docs)))

        for url in followed:
            if len(docs) >= budget:
                break
            doc, _ = await self._page(url, visited)
            if doc is not None:
                docs.append(doc)
        return docs

    async def _feed(
        self, feed_url: str, seed: str, visited: set[str], limit: int
    ) -> list[RawDoc]:
        """Up to `limit` documents from one RSS/Atom feed. `[]` when there is not one.

        An address that 404s, a body that is not a feed and a feed with no entries are all
        the same answer here, which is what "if present" means: the guess at `{origin}/feed`
        costs one request on a site that has none and nothing else.
        """
        if limit <= 0 or feed_url in visited:
            return []
        visited.add(feed_url)
        record = await fetch_record(feed_url, settings=self.settings)
        if record is None:
            return []
        visited.add(record.url)

        docs: list[RawDoc] = []
        for entry in parse_feed(record.body, record.url):
            if len(docs) >= limit:
                break
            if entry.url in visited or is_feed_url(entry.url):
                # `is_feed_url` here for the same reason it guards `_page_links`: an entry
                # whose target is itself a feed is not prose, and the fallback branch below
                # would hand it to the page fetcher and emit flattened XML as the member's
                # own writing.
                continue
            # A feed may syndicate somebody else's writing. An entry pointing off the
            # member's own web space is exactly the outward crawl this module refuses.
            if not in_web_space(entry.url, record.url, seed):
                continue
            body = _entry_text(entry.title, entry.summary)
            if len(body.strip()) >= MIN_ENTRY_CHARS:
                visited.add(entry.url)
                doc = self.doc(
                    entry.url,
                    title=entry.title,
                    text=body,
                    published_at=entry.published_at,
                )
            else:
                # A headline with no body: fetch the page it points at instead. The date
                # from the feed is still the best one anybody has for it.
                doc, _ = await self._page(entry.url, visited)
                if doc is not None and entry.published_at is not None:
                    doc = doc.model_copy(update={"published_at": entry.published_at})
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
