"""SEC EDGAR full-text search: the "not on the first page" source.

R7 asks for one fact a guest would not expect anyone to know, and DESIGN's non-obvious
eligibility list puts `edgar` at the front of it.  A Form D naming someone as an executive
officer of a company that raised in 2019, or a Form 3 filed the week they joined a board,
is public record that no amount of Googling a name surfaces — it is exactly the fact that
makes a host sound like they were paying attention rather than reading a search page.

TASTE, EXPLICITLY.  Forms 3/4/5 and D are *role and affiliation* filings: who is an
officer, director or beneficial owner of what.  This connector reads them for the
affiliation and deliberately does not go near the dollar amounts on the same page — R11
and T-4's `wealth` exclusion mean a share count is never displayable, so fetching it would
be collecting something the product has already promised not to say.

`efts.sec.gov/LATEST/search-index` is EDGAR's own full-text endpoint.  SEC's fair-access
policy requires a declared User-Agent with a contact address (the client sends one) and
10 requests/second (the rate limiter knows sec.gov).
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, parse_date, text_block
from arrival.connectors.identity import identifies
from arrival.contracts import PersonRef, RawDoc

__all__ = ["EdgarConnector"]

FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index"
FILING_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{adsh}-index.htm"

#: Ownership and exempt-offering filings: they name people and their roles. Deliberately
#: NOT 10-K/10-Q, which are company financials and say nothing a host should repeat.
FORMS = "3,4,5,D"


def _first_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                return item
    return ""


class EdgarConnector(BaseConnector):
    """`kind="edgar"` — ownership and exempt-offering filings naming this person."""

    kind = "edgar"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        payload = await self.get_json(
            FULL_TEXT_SEARCH,
            params={
                # The quoted phrase is what keeps this from returning every filing that
                # happens to contain the surname: EDGAR full-text search is not fielded.
                "q": f'"{person.name}"',
                "forms": FORMS,
                "hits": max(1, min(budget, 10)),
            },
        )
        hits = self._hits(payload)

        docs: list[RawDoc] = []
        for hit in hits:
            if len(docs) >= budget:
                break
            if not self._names_the_person(person, hit):
                continue
            doc = self._document(hit)
            if doc is not None:
                docs.append(doc)
        return docs

    @staticmethod
    def _names_the_person(person: PersonRef, hit: dict[str, Any]) -> bool:
        """Is this filing about the member, or merely returned by a search for her name?

        `display_names` is EDGAR's own answer to "who is this filing about" — the filer
        and every reporting person on it — and this connector was already rendering it
        into the document title and never once comparing it to `details`. Two ways that
        goes wrong, and the recorded fixture contains one of each:

        * A **company-only filing**: `display_names == ["Thornfield Loom Inc. (CIK ...)"]`,
          no person on it at all, emitted as a document *about a person* and cited to
          sec.gov. The corpus has shipped that document since the connector was written.
        * A **same-name stranger's** Form 4. EDGAR full-text search is not fielded, so the
          quoted phrase in `q` narrows the words, never the person; every Marisol
          Quennebeck who has ever been an officer of anything comes back together.

        The affiliation is checked HERE rather than added to `q` on purpose. Narrowing the
        remote query would drop a filing that names her without naming the company —
        exactly the "not on the first page" filing this connector exists for — and EDGAR's
        ranking is not observable from here, while `display_names` is.
        """
        source = hit.get("_source")
        source = source if isinstance(source, dict) else hit
        names = source.get("display_names")
        names = [str(name) for name in names] if isinstance(names, list) else []
        description = str(source.get("file_description") or "")
        return identifies(
            person,
            names=names,
            prose=[description],
            context=[*names, description],
        )

    @staticmethod
    def _hits(payload: Any) -> list[dict[str, Any]]:
        """EDGAR wraps results Elasticsearch-style: `hits.hits[]._source`."""
        if not isinstance(payload, dict):
            return []
        outer = payload.get("hits")
        rows = outer.get("hits") if isinstance(outer, dict) else outer
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _document(self, hit: dict[str, Any]) -> RawDoc | None:
        source = hit.get("_source")
        source = source if isinstance(source, dict) else hit
        adsh = str(source.get("adsh") or "").strip()
        if not adsh:
            identifier = str(hit.get("_id") or "")
            adsh = identifier.split(":", 1)[0]
        if not adsh:
            return None

        cik = _first_str(source.get("ciks")).lstrip("0") or _first_str(source.get("ciks"))
        names = source.get("display_names")
        who = ", ".join(names) if isinstance(names, list) else str(names or "")
        form = str(source.get("form") or source.get("root_forms") or "")
        filed = str(source.get("file_date") or "")

        url = FILING_INDEX.format(
            cik=cik or "0",
            accession=adsh.replace("-", ""),
            adsh=adsh,
        )
        return self.doc(
            url,
            title=f"SEC Form {form} — {who}".strip(" —"),
            text=text_block(
                f"SEC EDGAR filing {adsh}" + (f", form {form}" if form else ""),
                f"Filed: {filed}" if filed else None,
                f"Filed by / named in filing: {who}" if who else None,
                source.get("file_description"),
            ),
            published_at=parse_date(filed),
        )
