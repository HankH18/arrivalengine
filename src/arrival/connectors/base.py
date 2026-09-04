"""What every connector shares: the never-raise contract, the budget, and the citation.

A `Connector` (contracts.py) is two things — a `SourceKind` and
`async search(person, budget) -> list[RawDoc]` — with one hard rule attached: **it must
never raise.**  DESIGN Decision 8 spells out why.  The build fans out over ten sources for
every person on the roster; if one dead API can take down a run, the operator's only
recovery is to retry the whole thing and hope, and "half the internet is down" is a normal
Tuesday for a fan-out over free endpoints.  So `search` here is a sealed wrapper and every
subclass writes `_search` instead.  A subclass CAN still return `[]`; it cannot throw.

The other two invariants live here for the same reason — because ten copies of a rule is
zero copies of a rule:

* **Budget is a cap on documents returned**, not on requests attempted, and it is applied
  after de-duplication so a talkative source cannot spend a person's whole allowance on
  three copies of one page (DESIGN §Budget, `docs_per_connector`).
* **Every `RawDoc` is a citation.**  `doc_id == sha1(url)[:16]` (imported from
  `arrival.util`, never re-spelled), a fetchable `http(s)` url, non-empty text, and a
  `source_kind` that names the source the text actually came from — T-3's non-obvious rule
  and R11's display rules both key off it, so a doc stamped with the wrong kind is worse
  than a doc that does not exist.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from arrival.config import Settings, get_settings
from arrival.contracts import PersonRef, RawDoc, SourceKind
from arrival.http.client import fetch_json, fetch_text
from arrival.http.extract import MAX_TEXT_CHARS, clip
from arrival.util import doc_id

__all__ = [
    "BaseConnector",
    "affiliations",
    "hosts_in",
    "parse_date",
    "text_block",
    "urls_in",
]

log = logging.getLogger(__name__)

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

#: Role nouns that describe a person's relationship to an organisation rather than naming
#: one. Stripped before an affiliation is used as a search term, so "co-founder, Pelmyre
#: Works" searches for the company and not for the job title.
_ROLE_WORDS = frozenset(
    {
        "advisor", "analyst", "board", "board member", "ceo", "cfo", "chair", "chairman",
        "chief", "co-founder", "cofounder", "coo", "cto", "director", "engineer",
        "founder", "gp", "head", "investor", "lead", "manager", "member", "partner",
        "president", "principal", "professor", "researcher", "scientist", "svp", "vp",
    }
)

_SPLIT_AFFILIATION = re.compile(r"\s+(?:of|at|for|with)\s+|,\s*|\s+[|@]\s+", re.IGNORECASE)

_WAYBACK_TIMESTAMP = re.compile(r"^\d{14}$")


def urls_in(details: list[str]) -> list[str]:
    """Every http(s) URL mentioned in a `PersonRef.details` list, in order, deduped."""
    found: list[str] = []
    for detail in details:
        for match in _URL_IN_TEXT.findall(detail):
            url = match.rstrip(".,;")
            if url not in found:
                found.append(url)
    return found


def hosts_in(details: list[str]) -> list[str]:
    """The hostnames of `urls_in(details)`, deduped, `www.` kept as given."""
    hosts: list[str] = []
    for url in urls_in(details):
        host = (urlsplit(url).hostname or "").lower()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def affiliations(details: list[str]) -> list[str]:
    """Organisation-shaped phrases from `details`, best first.

    `["co-founder, Pelmyre Works", "Austin, Texas", "https://…"]` -> `["Pelmyre Works",
    "Austin", "Texas"]`.  Deliberately generous rather than clever: these are *search
    terms* handed to sources that will simply return nothing for a bad one, and a resolver
    (T-2) decides afterwards which hits are actually this person.  Being wrong here costs
    one request; being too narrow costs the only lead.
    """
    out: list[str] = []
    for detail in details:
        if _URL_IN_TEXT.search(detail):
            continue
        for fragment in _SPLIT_AFFILIATION.split(detail):
            candidate = fragment.strip(" .;:-")
            if not candidate or candidate.lower() in _ROLE_WORDS:
                continue
            if len(candidate) < 3 or not any(ch.isalpha() for ch in candidate):
                continue
            if candidate not in out:
                out.append(candidate)
    return out


def parse_date(value: Any) -> date | None:
    """Best-effort date from the several shapes public APIs use. `None` when unsure.

    A wrong `published_at` propagates into the digest's recency scoring, so anything not
    confidently parsed is left as `None` rather than guessed.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, int) and 10**9 < value < 10**11:  # unix seconds
        return datetime.fromtimestamp(value).date()
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _WAYBACK_TIMESTAMP.match(text):  # Wayback CDX: YYYYMMDDhhmmss
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def text_block(*parts: object) -> str:
    """Join non-empty parts with newlines, collapsing blanks. The body of a built RawDoc."""
    lines = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    return "\n".join(lines)


class BaseConnector:
    """Shared machinery. Subclasses set `kind` and implement `_search`."""

    #: The `SourceKind` every doc this connector emits is stamped with.
    kind: SourceKind

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{type(self).__name__} kind={self.kind!r}>"

    @property
    def settings(self) -> Settings:
        """Settings, resolved at USE time.

        An injected `Settings` wins; otherwise `get_settings()` — read here and never at
        import time, so a process that configures itself after import still gets it right.
        """
        return self._settings if self._settings is not None else get_settings()

    # -- the sealed public contract ----------------------------------------------------

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        """Up to `budget` documents about `person`. Never raises (DESIGN Decision 8).

        The guard covers the budget check and `_finalise` as well as `_search`. Both used
        to sit OUTSIDE it, so `search(person, None)` raised `TypeError` and a subclass
        returning the wrong type raised `AttributeError` from `_finalise` -- past the
        wrapper whose whole job is that nothing gets past it. "Never raises" has to mean
        the method, not just the part of it a subclass wrote.
        """
        try:
            limit = int(budget)
        except (TypeError, ValueError):
            log.warning("%s connector got a non-numeric budget %r", self.kind, budget)
            return []
        if limit <= 0:
            return []
        try:
            docs = await self._search(person, limit)
            return self._finalise(docs, limit)
        except Exception as exc:  # noqa: BLE001 - the whole point: a dead source is []
            log.warning("%s connector failed for %s: %s", self.kind, person.person_id, exc)
            return []

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        raise NotImplementedError  # pragma: no cover - abstract

    # -- helpers for subclasses --------------------------------------------------------

    def _finalise(self, docs: list[RawDoc] | None, budget: int) -> list[RawDoc]:
        """Drop blanks, de-duplicate by `doc_id`, then apply the cap."""
        seen: set[str] = set()
        kept: list[RawDoc] = []
        for doc in docs or []:
            # `isinstance`, not `is not None`: a subclass bug that yields the wrong type
            # should cost that document, not the nine good ones beside it.
            if not isinstance(doc, RawDoc) or doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            kept.append(doc)
            if len(kept) >= budget:
                break
        return kept

    def doc(
        self,
        url: str,
        *,
        title: str = "",
        text: str,
        published_at: date | None = None,
        fetched_at: datetime | None = None,
    ) -> RawDoc | None:
        """Build a citation from data already in hand. `None` if it would not be one.

        Used where a source's own API response already contains the prose (a search
        snippet, an abstract, a filing description) and re-fetching the landing page would
        buy nothing but a second round trip.
        """
        if not url or not url.startswith(("http://", "https://")):
            return None
        body = clip(text_block(text), MAX_TEXT_CHARS)
        if not body.strip():
            return None
        return RawDoc(
            doc_id=doc_id(url),
            source_kind=self.kind,
            url=url,
            title=title.strip()[:300],
            text=body,
            published_at=published_at,
            fetched_at=fetched_at or datetime.now(UTC),
        )

    async def get_json(self, url: str, **kwargs: Any) -> Any | None:
        """`fetch_json` with this connector's settings attached."""
        kwargs.setdefault("settings", self.settings)
        return await fetch_json(url, **kwargs)

    async def get_page(self, url: str, **kwargs: Any) -> RawDoc | None:
        """`fetch_text` stamped with this connector's `kind`."""
        kwargs.setdefault("settings", self.settings)
        kwargs.setdefault("source_kind", self.kind)
        return await fetch_text(url, **kwargs)
