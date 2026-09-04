"""Open web search: Tavily when a key is configured, DuckDuckGo-lite when it is not.

WHY TWO BACKENDS.  SPEC C1 wants free/no-card sources and `Settings` documents every
credential as optional ("a missing key disables a capability, never crashes").  Tavily
returns clean snippets and a relevance score for a search API key; the HTML endpoint at
`html.duckduckgo.com` needs no account at all and returns the same three things — title,
url, snippet — for a bit of parsing.  So the connector degrades from "good" to "adequate"
rather than from "good" to "absent", which is the difference between a person who gets a
digest and a person who does not.

WHY THE SNIPPET IS THE DOCUMENT.  A search result already carries prose about the person,
and it is prose the search engine chose *because* it matched the query.  Fetching each
landing page instead would cost N more round trips against N unknown hosts to obtain text
that `self_page` and the specific-source connectors are already responsible for.  The
result's own url is kept as the citation, so a fact drawn from here still points a reader
at the real page.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from arrival.connectors.base import BaseConnector, affiliations, parse_date, text_block
from arrival.contracts import PersonRef, RawDoc
from arrival.http.client import fetch_record

__all__ = ["SearchConnector"]

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DDG_ENDPOINT = "https://html.duckduckgo.com/html/"

_WHITESPACE = re.compile(r"\s+")


class _DuckDuckGoResults(HTMLParser):
    """Pull (url, title, snippet) triples out of the no-JavaScript DuckDuckGo page.

    The lite endpoint is stable HTML: results are `a.result__a` and snippets are
    `.result__snippet`.  Parsed with the stdlib rather than a regex over the whole page so
    a change in attribute order or extra classes does not silently return nothing.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._collecting: str | None = None
        self._buffer: list[str] = []
        self._href: str = ""

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for name, value in attrs:
            if name == "class" and value:
                return set(value.split())
        return set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "a" and {"result__a", "result-link"} & classes:
            self._collecting = "title"
            self._buffer = []
            self._href = dict(attrs).get("href") or ""
        elif {"result__snippet", "result-snippet"} & classes:
            self._collecting = "snippet"
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._collecting is None:
            return
        text = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
        if self._collecting == "title":
            url = _unwrap_redirect(self._href)
            if url:
                self.results.append({"url": url, "title": text, "snippet": ""})
        elif self._collecting == "snippet" and self.results:
            self.results[-1]["snippet"] = text
        self._collecting = None
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._collecting is not None:
            self._buffer.append(data)


def _unwrap_redirect(href: str) -> str:
    """DuckDuckGo wraps results in `/l/?uddg=<encoded>`; return the real destination.

    The wrapper appears in three spellings and all three are live on the HTML endpoint:
    absolute (`https://duckduckgo.com/l/?uddg=`), protocol-relative (`//duckduckgo.com/l/?`)
    and **root-relative** (`/l/?uddg=`).  The last one has no hostname to match on, so a
    host-only test drops it silently -- and dropping it does not fail loudly, it just
    returns fewer results from the fallback that exists precisely for the case where there
    is no API key.  Matching the `/l/` path covers all three.
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urlsplit(href)
    except ValueError:  # a malformed href is not a result, it is not a crash
        return ""
    host = (parts.hostname or "").lower()
    is_wrapper = parts.path.startswith("/l/") and (not host or "duckduckgo.com" in host)
    if is_wrapper:
        target = parse_qs(parts.query).get("uddg", [""])[0]
        target = unquote(target)
        return target if target.startswith(("http://", "https://")) else ""
    return href if href.startswith(("http://", "https://")) else ""


class SearchConnector(BaseConnector):
    """`kind="search"` — the widest, least trusted net, and usually the first lead."""

    kind = "search"

    def query_for(self, person: PersonRef) -> str:
        """Name plus the strongest affiliation. A bare name matches too many people."""
        affiliation = next(iter(affiliations(person.details)), "")
        return f"{person.name} {affiliation}".strip()

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        query = self.query_for(person)
        docs = await self._tavily(query, budget)
        if docs:
            return docs
        return await self._duckduckgo(query, budget)

    async def _tavily(self, query: str, budget: int) -> list[RawDoc]:
        api_key = self.settings.tavily_api_key
        if not api_key:
            return []
        payload = await self.get_json(
            TAVILY_ENDPOINT,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
            json_body={
                "query": query,
                "max_results": max(1, budget),
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        if not isinstance(payload, dict):
            return []
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        docs: list[RawDoc] = []
        for result in results[:budget]:
            if not isinstance(result, dict):
                continue
            body = result.get("raw_content") or result.get("content") or ""
            doc = self.doc(
                str(result.get("url") or ""),
                title=str(result.get("title") or ""),
                text=text_block(result.get("title"), body),
                published_at=parse_date(result.get("published_date")),
            )
            if doc is not None:
                docs.append(doc)
        return docs

    async def _duckduckgo(self, query: str, budget: int) -> list[RawDoc]:
        record = await self._html(query)
        if record is None:
            return []
        parser = _DuckDuckGoResults()
        try:
            parser.feed(record)
            parser.close()
        except Exception:  # noqa: BLE001 - a mangled results page is [] , not a crash
            return []

        docs: list[RawDoc] = []
        for result in parser.results[:budget]:
            doc = self.doc(
                result["url"],
                title=result["title"],
                text=text_block(result["title"], result["snippet"]),
            )
            if doc is not None:
                docs.append(doc)
        return docs

    async def _html(self, query: str) -> str | None:
        record = await fetch_record(
            DDG_ENDPOINT,
            params={"q": query, "kl": "us-en"},
            settings=self.settings,
        )
        return record.body if record is not None else None
