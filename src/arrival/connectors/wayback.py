"""The Wayback Machine: what a person's own site used to say.

WHY AN ARCHIVE IS A DIFFERENT SOURCE FROM THE LIVE PAGE.  A company's About page today
says what it wants to say today.  The 2019 capture says who the three founders were before
two of them left, what the product was called first, and which conference the launch was
timed to.  That is the "not on the first page" material R7 asks for, and it is material the
person themselves published — so it clears the taste line for the same reason `self_page`
does.  It is also the only source here that survives a company being acquired or a personal
site going dark, which for a club roster is a large fraction of the interesting people.

Two steps.  The CDX index (`/cdx/search/cdx`) lists captures for a host; `collapse=urlkey`
keeps one capture per distinct URL instead of two hundred of the homepage, and
`filter=statuscode:200` drops the error pages an archive is full of.  Then each chosen
capture is fetched through the shared client, so what lands in `RawDoc.text` is the real
archived prose rather than a description of a capture that exists.

WHY THIS CONNECTOR DE-DUPLICATES ON ITS OWN (T-038).  Every other connector's URL
identifies its content, so `BaseConnector._finalise`'s `doc_id == sha1(url)[:16]` is the
whole rule.  This is the one source where it is not: a replay address is
`/web/{timestamp}/{original}`, so two captures of ONE page have two urls, two `doc_id`s
and both survive.  Measured before this was fixed, on two CDX rows for
`thornfieldloom.example.com/about` with the same `digest`: two HTTP fetches, two documents,
two distinct `doc_id`s and exactly ONE distinct `RawDoc.text` — two of `max_docs_total`
spent, two LLM verdicts paid, and one sentence quotable twice as if two sources had said
it.  `collapse=urlkey` does not prevent it: it collapses by URL KEY, and `http://site/`,
`https://site/` and `site/about` vs `site/about/` are four keys over the same bytes.

The rule belongs here and not in `_finalise` for the reason the defect exists: the shared
base knows only urls, and a replay url is not one.  What identifies an archived page is
Wayback's own content hash, the `digest` column CDX already returns — see
`dedupe_by_digest`, which also runs BEFORE the fetch, so a duplicate costs no request
either.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from arrival.connectors.base import BaseConnector, parse_date, urls_in
from arrival.connectors.identity import is_shared_host, on_own_host
from arrival.contracts import PersonRef, RawDoc

__all__ = ["WaybackConnector", "dedupe_by_digest"]

CDX = "https://web.archive.org/cdx/search/cdx"
REPLAY = "https://web.archive.org/web/{timestamp}/{url}"

#: The CDX column order when `fl` is not given. Read from the header row when present.
_DEFAULT_FIELDS = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]

#: The CDX column carrying Wayback's own hash of the archived bytes. Two rows sharing it
#: are two addresses for one page, whatever their timestamps and urls say.
DIGEST_FIELD = "digest"


def _capture_rank(row: dict[str, str]) -> tuple[bool, str]:
    """Which of two captures of the SAME content to keep. Higher wins.

    `statuscode` first: a row the archive recorded as 200 is one whose replay renders, and
    `filter=statuscode:200` is a server-side hint the CDX API applies only when it feels
    like it — a row that arrives non-200 anyway should never displace a good one.

    Then the LATEST timestamp, and that choice is not cosmetic. `published_at` for a
    wayback document means "the archive observed this text on this date", and
    `extract.recency_for` turns it into the `recency` that `graph` multiplies into every
    edge weight. The pair this de-duplicates already contributes the newest capture's
    recency today, because `extract` takes `max(recency_for(doc.published_at) ...)` across
    a hub's evidence — so keeping the latest removes the duplicate and changes nothing
    else, while keeping the earliest would quietly age every de-duplicated capture as
    well. The prose is identical either way: that is what a shared digest means.
    """
    return (str(row.get("statuscode") or "").strip() == "200", str(row.get("timestamp") or ""))


def dedupe_by_digest(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per distinct archived CONTENT, in first-seen order, best capture winning.

    A row with no `digest` is never merged with anything, including another row with no
    digest. Absence of the key means "this archive did not tell us", not "the same as the
    other unknowns", and collapsing on a missing value would silently drop good captures
    the moment a `fl=` change or a header-less CDX response left the column out. Those
    rows fall through to `BaseConnector._finalise`, whose url dedupe is still the backstop.
    """
    kept: list[dict[str, str]] = []
    at: dict[str, int] = {}
    for row in rows:
        digest = str(row.get(DIGEST_FIELD) or "").strip()
        if not digest:
            kept.append(row)
            continue
        index = at.get(digest)
        if index is None:
            at[digest] = len(kept)
            kept.append(row)
        elif _capture_rank(row) > _capture_rank(kept[index]):
            # The winner takes the loser's PLACE rather than being appended: CDX returns
            # captures oldest-first, and re-ordering the list would change which pages a
            # tight budget reaches for reasons that have nothing to do with the budget.
            kept[index] = row
    return kept


def _cdx_patterns(person: PersonRef) -> list[str]:
    """The CDX url patterns to enumerate, one per URL the roster gave.

    THE WEAKNESS THIS CLOSES. The connector was anchored on a HOST from `details` and then
    asked CDX for `{host}/*`. On a domain the member owns that is exactly right — every
    path under it is theirs. On `linkedin.com`, `medium.com` or `substack.com` it
    enumerates every capture the archive holds of nine hundred million other people's
    profiles, and the connector then fetches and cites strangers' pages under the
    member's name. The roster line `https://www.linkedin.com/in/marisol-quennebeck` names
    a PAGE; only on a private domain does it also name a host.

    So a shared platform is anchored on the PATH the roster actually gave, and a domain of
    the member's own keeps the whole-host enumeration it had.
    """
    patterns: list[str] = []
    for url in urls_in(person.details):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            continue
        if is_shared_host(host):
            path = parts.path.rstrip("/")
            if not path:
                # A bare platform root names nobody, and `linkedin.com/*` is the whole
                # platform. There is nothing here to enumerate on this person's behalf.
                continue
            pattern = f"{host}{path}*"
        else:
            pattern = f"{host}/*"
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


class WaybackConnector(BaseConnector):
    """`kind="wayback"` — archived captures of the person's own or their company's site."""

    kind = "wayback"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        patterns = _cdx_patterns(person)
        if not patterns:
            return []

        # Shared across patterns, not per pattern: a roster naming both a company site and
        # a personal one routinely archives the same page under both, and a set that
        # restarted per pattern would let the second copy back in.
        seen_digests: set[str] = set()

        docs: list[RawDoc] = []
        for pattern in patterns:
            if len(docs) >= budget:
                break
            docs.extend(
                await self._captures(person, pattern, budget - len(docs), seen_digests)
            )
        return docs

    async def _captures(
        self,
        person: PersonRef,
        pattern: str,
        limit: int,
        seen_digests: set[str] | None = None,
    ) -> list[RawDoc]:
        payload = await self.get_json(
            CDX,
            params={
                "url": pattern,
                "output": "json",
                "collapse": "urlkey",
                "filter": "statuscode:200",
                "limit": max(1, min(limit * 4, 50)),
                "fl": ",".join(_DEFAULT_FIELDS),
            },
        )
        # The identity check belongs on the CDX row, not on the archived page. What is
        # being cited is a capture OF A URL, and whether that url is the member's web
        # space is knowable before the fetch and not reliably knowable after it: an
        # archived About page may never spell her name.
        #
        # It also has to run BEFORE the digest dedupe rather than after. A page mirrored
        # on a shared platform and on the member's own domain gives two rows with one
        # digest, and de-duplicating first could elect the row this connector is not
        # allowed to cite and then drop the one it is.
        candidates = [
            row
            for row in self._rows(payload)
            if row.get("timestamp")
            and row.get("original")
            and on_own_host(str(row["original"]), person)
        ]

        seen = seen_digests if seen_digests is not None else set()
        docs: list[RawDoc] = []
        for row in dedupe_by_digest(candidates):
            if len(docs) >= limit:
                break
            digest = str(row.get(DIGEST_FIELD) or "").strip()
            if digest and digest in seen:
                continue
            doc = await self.get_page(
                REPLAY.format(timestamp=row["timestamp"], url=row["original"]),
                published_at=parse_date(row["timestamp"]),
            )
            if doc is None:
                continue
            docs.append(doc)
            if digest:
                # Recorded only once the fetch actually produced a citation: a capture
                # that 404s or extracts to nothing must not block the next copy of it.
                seen.add(digest)
        return docs

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, str]]:
        """CDX JSON is a list of LISTS whose first element is the header row.

        Not a list of objects — an implementation that assumes objects gets a
        `AttributeError` on row 0 and, with the never-raise wrapper above, a silent `[]`.
        The header is honoured rather than assumed so a `fl=` change cannot mis-index.
        """
        if not isinstance(payload, list) or not payload:
            return []
        first = payload[0]
        if isinstance(first, dict):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(first, list):
            return []
        header = [str(column) for column in first]
        fields = header if "timestamp" in header else _DEFAULT_FIELDS
        body = payload[1:] if "timestamp" in header else payload
        rows: list[dict[str, str]] = []
        for row in body:
            if isinstance(row, list) and len(row) >= len(fields):
                rows.append({name: str(value) for name, value in zip(fields, row, strict=False)})
        return rows
