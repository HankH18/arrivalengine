"""RSS 2.0 and Atom, parsed with the standard library. No new dependency (T-021).

WHY A FEED AT ALL.  TASKS T-1 acceptance 2 gives `self_page` one clause beyond "fetch the
URLs in `details`": *"plus `/feed` RSS if present"*.  It is not decoration.  Everything
else `self_page` emits is a page with no date on it — `fetch_text` has no way to know when
a page was written — and a digest that wants to say "she shipped the scheduler rewrite
last month" needs the month.  A feed entry carries `pubDate`/`updated` as a first-class
field, so the one connector whose documents are the member's own words is also the only
one that can date them.

WHY NOT `feedparser`.  It is the obvious dependency and this ticket may not add one.  It
also is not needed: the two formats agree on the shape (a list of entries, each with a
title, a link, a summary and a timestamp), and everything below that is namespace
bookkeeping `xml.etree` already does.  What a real parser buys over this one is tolerance
of malformed XML, and a malformed feed here is correctly answered with `[]` — `self_page`
already has a page-fetch path for anything a feed cannot supply.

WHY NAMESPACES ARE STRIPPED RATHER THAN MATCHED.  Atom is served under
`{http://www.w3.org/2005/Atom}`, RSS 2.0 under no namespace at all, RDF/RSS 1.0 under
`{http://purl.org/rss/1.0/}`, and a real-world feed mixes in Dublin Core and content
modules besides.  Matching the namespace exactly means one branch per dialect and a silent
`[]` for the dialect nobody thought of; matching the LOCAL NAME reads all of them.  The
cost is that a `<title>` from an unrelated namespace would be read as a title, which for a
document whose text is about to be identity-checked anyway is not a risk worth a branch.

THE SIZE CAP IS NOT TIDINESS.  `xml.etree` is expat, and expat expands internal entities,
so a feed on a host we do not control is an unbounded allocation on request ("billion
laughs").  A feed larger than `MAX_FEED_BYTES` is refused unparsed.  That is the same
answer this module gives to every other malformed input, so it needs no special handling
upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from arrival.connectors.base import parse_date

__all__ = [
    "FEED_CONTENT_TYPES",
    "FEED_NAMES",
    "MAX_FEED_BYTES",
    "FeedEntry",
    "advertised_feeds",
    "conventional_feed",
    "is_feed_url",
    "parse_feed",
]

#: LAST PATH SEGMENTS that mean "this URL is a feed, not a page". Used in both directions:
#: a link matching one is never followed as prose, and is offered to the feed reader
#: instead. Matched as a whole segment rather than as a substring, because `"/feed" in
#: path` is also true of `/feedback`, `/feeding-the-mill` and `/rssmith` — which would take
#: an ordinary page off the crawl and send it to an XML parser that finds nothing in it.
FEED_NAMES = frozenset(
    {
        "feed", "feeds", "feed.xml", "feed.atom", "feed.json",
        "rss", "rss.xml", "atom", "atom.xml", "index.xml", "index.rss",
    }
)

#: `type=` values on a `<link rel="alternate">` that name a feed.
FEED_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/feed+json",
    "text/xml",
)

#: See the module docstring: expat expands internal entities, and the feed is fetched from
#: a host this process does not control.
MAX_FEED_BYTES = 2_000_000


@dataclass(frozen=True)
class FeedEntry:
    """One item of a feed, in the only four fields a `RawDoc` needs."""

    title: str
    url: str
    summary: str
    published_at: date | None


def _local(tag: object) -> str:
    """`{http://www.w3.org/2005/Atom}entry` -> `entry`. See the namespace note above."""
    name = str(tag)
    return name.rsplit("}", 1)[-1].lower()


def _text(element: ElementTree.Element) -> str:
    """All character data under `element`, including the tags a summary may contain.

    Atom permits `type="html"` and `type="xhtml"` content, so a summary can arrive either
    as escaped markup in `.text` (which `itertext` returns as-is) or as real child
    elements (which it flattens). Both end up as words, which is what a `RawDoc` wants.
    """
    return " ".join(part.strip() for part in element.itertext() if part and part.strip())


def _child(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    for child in element:
        if _local(child.tag) in names:
            return child
    return None


def _entry_link(entry: ElementTree.Element, base_url: str) -> str:
    """The entry's own address, absolute.

    RSS puts it in `<link>`'s text; Atom puts it in `<link href=...>` and may carry several
    with different `rel`s, of which `alternate` (or an absent `rel`, which defaults to
    `alternate`) is the human-readable one. `<enclosure>` and `rel="replies"` are neither.
    """
    fallback = ""
    for child in entry:
        name = _local(child.tag)
        if name == "link":
            href = (child.attrib.get("href") or child.text or "").strip()
            if not href:
                continue
            rel = (child.attrib.get("rel") or "alternate").strip().lower()
            if rel == "alternate":
                return urljoin(base_url, href)
            fallback = fallback or urljoin(base_url, href)
        elif name in ("id", "guid") and not fallback:
            value = (child.text or "").strip()
            # A guid is only an address when the feed says it is one.
            if value.startswith(("http://", "https://")) and (
                child.attrib.get("isPermaLink", "true").lower() != "false"
            ):
                fallback = value
    return fallback


def _entry_date(entry: ElementTree.Element) -> date | None:
    for name in ("published", "pubdate", "updated", "date", "created"):
        child = _child(entry, name)
        if child is None:
            continue
        parsed = parse_date((child.text or "").strip())
        if parsed is not None:
            return parsed
    return None


def _entry_summary(entry: ElementTree.Element) -> str:
    """The longest of the several fields the two formats use for a body.

    Longest rather than first-found on purpose: a feed commonly carries BOTH a one-line
    `<summary>` and a full `<content:encoded>`, and the useful one is whichever says more.
    """
    best = ""
    for child in entry:
        if _local(child.tag) in ("summary", "description", "content", "encoded", "subtitle"):
            body = _text(child)
            if len(body) > len(best):
                best = body
    return best


def parse_feed(body: str, base_url: str = "") -> list[FeedEntry]:
    """Entries of an RSS/Atom document, in feed order. `[]` for anything unparseable.

    Never raises: a feed is one more thing fetched off the open web, and DESIGN Decision 8
    applies to it exactly as it applies to the fetch that produced it.
    """
    if not body or len(body) > MAX_FEED_BYTES:
        return []
    try:
        root = ElementTree.fromstring(body)
    except Exception:  # noqa: BLE001 - a feed we cannot parse simply has no entries
        return []

    entries: list[FeedEntry] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        title_element = _child(element, "title")
        title = _text(title_element) if title_element is not None else ""
        url = _entry_link(element, base_url)
        if not url.startswith(("http://", "https://")):
            continue
        entries.append(
            FeedEntry(
                title=title,
                url=url,
                summary=_entry_summary(element),
                published_at=_entry_date(element),
            )
        )
    return entries


def is_feed_url(url: str) -> bool:
    """True when the path alone says this address is a feed rather than a page."""
    segments = [part for part in urlsplit(url).path.lower().split("/") if part]
    return bool(segments) and segments[-1] in FEED_NAMES


class _AlternateLinks(HTMLParser):
    """Collect `<link rel="alternate" type="application/rss+xml" href=...>` in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        rels = values.get("rel", "").lower().split()
        if "alternate" not in rels and "feed" not in rels:
            return
        kind = values.get("type", "").split(";", 1)[0].strip().lower()
        href = values.get("href", "").strip()
        if href and (kind in FEED_CONTENT_TYPES or is_feed_url(href)):
            self.hrefs.append(href)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def advertised_feeds(base_url: str, markup: str) -> list[str]:
    """Absolute URLs of the feeds this page declares in its own `<head>`, deduped.

    THE ONLY DISCOVERY THAT COSTS NOTHING WHEN THERE IS NO FEED.  Guessing `/feed` on a
    site that has none spends a request per person to learn that; reading the `<link>` the
    site already published spends none, and it is also the only way to find a feed that
    does not live at a conventional path.
    """
    parser = _AlternateLinks()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a page we cannot parse advertises no feed
        return []
    found: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href).split("#", 1)[0]
        if absolute.startswith(("http://", "https://")) and absolute not in found:
            found.append(absolute)
    return found


def conventional_feed(url: str) -> str:
    """`https://host/anything` -> `https://host/feed`. The one guess acceptance 2 names.

    Anchored at the site ROOT rather than beside the page, because that is where the
    convention puts it and because a per-page guess would be one request per page rather
    than one per host.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}/feed"
