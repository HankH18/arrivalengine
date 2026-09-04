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
    "bare_domains_in",
    "hosts_in",
    "parse_date",
    "text_block",
    "urls_in",
]

log = logging.getLogger(__name__)

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

#: A HOSTNAME sitting in prose with no scheme in front of it, optionally with a path.
#:
#: THE MEASURED DEFECT (T-072). A roster writes a member's own site the way a person says
#: it out loud — `"writes the AVC blog (avc.com)"`, `"feld.com"`,
#: `"essays at nabeelqu.co"` — and none of those matches `_URL_IN_TEXT`, which requires a
#: scheme. So `urls_in` returned `[]` for EVERY person on the live roster, `self_page`
#: (the highest-trust `SourceKind` in the system) never received a seed, and `wayback`,
#: `hn` and `identity.on_own_host` lost the same input. Measured live on 2026-09-04:
#: ten people, ten empty seed lists, `self_page` in all ten zero-result lists.
#:
#: The lookahead-free shape below is deliberately conservative — the cost of a false
#: positive here is an outbound HTTP request to a host the member never named, and the
#: highest-trust stamp in the system on whatever comes back.
_BARE_DOMAIN = re.compile(
    r"(?<![\w@./-])"                      # not mid-word, not an e-mail local part
    r"((?:[a-z0-9][a-z0-9-]{0,61}\.)+[a-z]{2,24})"  # labels + TLD
    r"(?![\w-])"                          # the TLD ends here
    r"(/[^\s<>\"')\]]*)?",                # an optional path
    re.IGNORECASE,
)

#: The TLDs a bare domain is recognised under. An ALLOWLIST rather than "two or more
#: letters", because the loose rule reads ordinary prose as addresses: `Ph.D`, `M.Sc`,
#: `i.e`, and every sentence whose full stop is followed by a two-letter word. A domain
#: under a TLD not listed here is simply not seeded from prose — the roster can always
#: write `https://` in front of it, which is the unambiguous spelling and always wins.
_BARE_TLDS = frozenset(
    {
        "com", "org", "net", "edu", "gov", "int", "mil", "info", "biz", "name", "pro",
        "io", "co", "ai", "dev", "app", "me", "xyz", "blog", "news", "tech", "site",
        "online", "page", "link", "email", "press", "wiki", "space", "world", "today",
        "club", "life", "live", "media", "studio", "design", "works", "team", "company",
        "ventures", "capital", "fund", "group", "network", "house", "systems", "codes",
        "one", "cloud", "digital", "agency", "consulting", "institute", "foundation",
        "academy", "school", "eu", "uk", "us", "ca", "au", "nz", "de", "fr", "nl", "es",
        "it", "se", "no", "dk", "fi", "ie", "pt", "pl", "cz", "at", "ch", "be", "gr",
        "il", "za", "sg", "hk", "jp", "kr", "in", "br", "mx", "ar", "cl", "ru", "tr",
        "ua", "tv", "fm", "gg", "ly", "to", "sh", "is", "cc", "st", "so", "re", "am",
    }
)

#: Role nouns that describe a person's relationship to an organisation rather than naming
#: one. Stripped before an affiliation is used as a search term, so "co-founder, Pelmyre
#: Works" searches for the company and not for the job title.
#:
#: MATCHED PER TOKEN, NOT AS A WHOLE CANDIDATE (T-073). The check used to be
#: `candidate.lower() in _ROLE_WORDS`, which recognises `"co-founder"` and does not
#: recognise `"co-founder and partner"` — so a CONJOINED role phrase survived, sorted
#: first (it is written first in the roster) and became `identity.best_affiliation`'s
#: answer. Measured live on the ten-person roster: nine of ten people had a JOB TITLE
#: where their employer should be, so `wikipedia` searched
#: `"Josh Kopelman founder and partner"` and his own article was not in the first twenty
#: results.
_ROLE_WORDS = frozenset(
    {
        "advisor", "analyst", "architect", "artist", "associate", "author", "blogger",
        "board", "board member", "ceo", "cfo", "chair", "chairman", "chairperson",
        "chairwoman", "chief", "cio", "cmo", "co-founder", "cofounder", "consultant",
        "coo", "cro", "cto", "director", "editor", "engineer", "entrepreneur", "evp",
        "executive", "fellow", "founder", "general", "gp", "head", "investor",
        "journalist", "lead", "manager", "managing", "md", "member", "officer",
        "operator", "owner", "partner", "president", "principal", "professor",
        "researcher", "scientist", "staff", "svp", "trustee", "vp", "writer",
    }
)

#: Words that GLUE a role phrase together without naming anything. A candidate made only
#: of these and `_ROLE_WORDS` names a job, not an organisation.
_ROLE_CONNECTIVES = frozenset(
    {
        "a", "acting", "an", "and", "at", "briefly", "co", "current", "currently",
        "deputy", "emeritus", "ex", "for", "former", "formerly", "global", "in",
        "interim", "junior", "of", "senior", "the", "with", "&",
    }
)

#: Leading words stripped off an otherwise good organisation phrase: `"formerly Palantir"`
#: is a search for Palantir. Role words are NOT stripped this way — "General Electric"
#: would become "Electric" — so only the temporal/qualifying prefixes are listed.
_LEADING_QUALIFIERS = frozenset(
    {"acting", "briefly", "current", "currently", "ex", "former", "formerly", "interim"}
)

_TOKENS = re.compile(r"[a-z0-9&]+", re.IGNORECASE)

#: `;` joins two INDEPENDENT clauses of a detail — `"co-founder, Foundry Group;
#: co-founder, Techstars"` names two companies, and `"formerly Palantir; essays at
#: nabeelqu.co"` names one company and one website. Splitting there first is what lets a
#: clause carrying a URL be dropped without taking the company beside it down with it.
_SPLIT_CLAUSE = re.compile(r"\s*;\s*")

_SPLIT_AFFILIATION = re.compile(r"\s+(?:of|at|for|with)\s+|,\s*|\s+[|@]\s+", re.IGNORECASE)

_WAYBACK_TIMESTAMP = re.compile(r"^\d{14}$")


def _is_bare_domain(host: str) -> bool:
    """Is `host` a hostname a roster would write without a scheme? See `_BARE_TLDS`."""
    labels = host.lower().split(".")
    if len(labels) < 2 or labels[-1] not in _BARE_TLDS:
        return False
    # Every label at least two characters: `M.Sc`, `e.g` and `U.S.A` are prose, and a
    # one-letter label is rare enough in a roster to be worth losing to keep them out.
    return all(len(label) >= 2 for label in labels)


def bare_domains_in(text: str) -> list[str]:
    """Every scheme-less address in `text`, as `https://` URLs, in order, deduped.

    Scheme-carrying URLs are removed first so `https://notes.example.org/m` is read once,
    by `_URL_IN_TEXT`, and not a second time as the bare host inside it.
    """
    found: list[str] = []
    for host, path in _BARE_DOMAIN.findall(_URL_IN_TEXT.sub(" ", text)):
        if not _is_bare_domain(host):
            continue
        url = f"https://{host}{(path or '').rstrip('.,;')}"
        if url not in found:
            found.append(url)
    return found


def urls_in(details: list[str]) -> list[str]:
    """Every address mentioned in a `PersonRef.details` list, in order, deduped.

    Both spellings a roster actually uses: a full `http(s)://` URL, and a bare hostname
    sitting in prose (`"writes the AVC blog (avc.com)"`), which is promoted to `https://`.
    See `_BARE_DOMAIN` for the measured reason the second one is here.
    """
    found: list[str] = []
    for detail in details:
        for match in _URL_IN_TEXT.findall(detail):
            url = match.rstrip(".,;")
            if url not in found:
                found.append(url)
        for url in bare_domains_in(detail):
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


def _names_a_job(candidate: str) -> bool:
    """True when every word of `candidate` is a role word or the glue between two.

    `"co-founder"` yes, `"co-founder and partner"` yes, `"general partner"` yes,
    `"Foundry Group"` no, `"General Electric"` no — `electric` is not a role word, so the
    phrase survives even though `general` is.
    """
    tokens = [token.lower() for token in _TOKENS.findall(candidate)]
    if not tokens:
        return False
    return all(token in _ROLE_WORDS or token in _ROLE_CONNECTIVES for token in tokens)


def _without_qualifier(candidate: str) -> str:
    """`"formerly Palantir"` -> `"Palantir"`. Only `_LEADING_QUALIFIERS` are stripped."""
    stripped = candidate
    while True:
        head, separator, tail = stripped.partition(" ")
        if not separator or head.lower() not in _LEADING_QUALIFIERS:
            return stripped
        stripped = tail.strip()


def affiliations(details: list[str]) -> list[str]:
    """Organisation-shaped phrases from `details`, best first.

    `["co-founder, Pelmyre Works", "Austin, Texas", "https://…"]` -> `["Pelmyre Works",
    "Austin", "Texas"]`.  Deliberately generous rather than clever: these are *search
    terms* handed to sources that will simply return nothing for a bad one, and a resolver
    (T-2) decides afterwards which hits are actually this person.  Being wrong here costs
    one request; being too narrow costs the only lead.

    Generous is not the same as indiscriminate, and two measured failures were the latter
    (T-073, live roster of ten):

    * **A conjoined job title is not an organisation.** The role check compared the WHOLE
      candidate against `_ROLE_WORDS`, so `"co-founder"` was dropped and
      `"co-founder and partner"` was kept — and because the roster writes the title before
      the company, the job title became the FIRST affiliation and therefore
      `identity.best_affiliation`'s answer for nine of the ten people. `_names_a_job`
      reads the candidate word by word instead.
    * **A `;` joins two clauses, and one of them may carry the website.** Skipping a whole
      detail because it mentions a URL threw away `"formerly Palantir"` along with
      `"essays at nabeelqu.co"`. The clause carrying the address is dropped; the company
      beside it is not.
    """
    out: list[str] = []
    for detail in details:
        for clause in _SPLIT_CLAUSE.split(detail):
            # An address is not an employer, in either spelling. The whole CLAUSE goes:
            # what is left of `"site: https://x.example/"` once the url is removed is the
            # word "site", which is worse than nothing as a search term.
            if _URL_IN_TEXT.search(clause) or bare_domains_in(clause):
                continue
            for fragment in _SPLIT_AFFILIATION.split(clause):
                candidate = _without_qualifier(fragment.strip(" .;:-"))
                if not candidate or _names_a_job(candidate):
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
