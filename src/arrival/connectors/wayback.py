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
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from arrival.connectors.base import BaseConnector, parse_date, urls_in
from arrival.connectors.identity import is_shared_host, on_own_host
from arrival.contracts import PersonRef, RawDoc

__all__ = ["WaybackConnector"]

CDX = "https://web.archive.org/cdx/search/cdx"
REPLAY = "https://web.archive.org/web/{timestamp}/{url}"

#: The CDX column order when `fl` is not given. Read from the header row when present.
_DEFAULT_FIELDS = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


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

        docs: list[RawDoc] = []
        for pattern in patterns:
            if len(docs) >= budget:
                break
            docs.extend(await self._captures(person, pattern, budget - len(docs)))
        return docs

    async def _captures(self, person: PersonRef, pattern: str, limit: int) -> list[RawDoc]:
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
        rows = self._rows(payload)

        docs: list[RawDoc] = []
        for row in rows:
            if len(docs) >= limit:
                break
            timestamp = row.get("timestamp") or ""
            original = row.get("original") or ""
            if not timestamp or not original:
                continue
            # The identity check belongs on the CDX row, not on the archived page. What is
            # being cited is a capture OF A URL, and whether that url is the member's web
            # space is knowable before the fetch and not reliably knowable after it: an
            # archived About page may never spell her name.
            if not on_own_host(original, person):
                continue
            doc = await self.get_page(
                REPLAY.format(timestamp=timestamp, url=original),
                published_at=parse_date(timestamp),
            )
            if doc is not None:
                docs.append(doc)
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
