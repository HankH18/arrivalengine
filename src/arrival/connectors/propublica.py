"""ProPublica Nonprofit Explorer: boards, causes, and who else sits at that table.

WHY A NONPROFIT API IS ONE OF THE BEST MATCHMAKING SOURCES IN THIS PROJECT.  T-5 scores a
meeting on *shared hubs*, weighted by how rare the hub is.  "Both in tech" is worthless;
"both on the board of the same 40-person arts foundation" is the entire product.  Form 990
Part VII lists officers and directors by name, which is a rare, verifiable, non-obvious hub
with a second person already attached to it — the only source here that hands you the edge
and the neighbour in the same response.

TASTE.  The same filing carries revenue, expenses and officer compensation.  T-4 excludes
`wealth` outright and R11 never displays it, so this connector reads the *roster* and the
*mission code* and stops there.  A compensation figure that is never displayable is a
figure there is no reason to have collected.

Two steps, both on the free v2 API with no key: `search.json?q=` to find candidate
organisations, then `organizations/{ein}.json` for the officer list.  The queries are the
person's name and their affiliations, because Nonprofit Explorer indexes organisation
names, not people — searching only for the person finds the foundation named after them
and nothing else.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, affiliations, text_block
from arrival.contracts import PersonRef, RawDoc

__all__ = ["ProPublicaConnector"]

API = "https://projects.propublica.org/nonprofits/api/v2"
ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/{ein}"

#: More than a few org queries per person is a fishing expedition, not research.
MAX_QUERIES = 3


class ProPublicaConnector(BaseConnector):
    """`kind="propublica"` — nonprofit affiliations and the boards they imply."""

    kind = "propublica"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        queries = [person.name, *affiliations(person.details)][:MAX_QUERIES]

        seen_eins: list[str] = []
        rows: list[dict[str, Any]] = []
        for query in queries:
            if len(rows) >= budget:
                break
            for row in await self._organisations(query):
                ein = str(row.get("ein") or "")
                if not ein or ein in seen_eins:
                    continue
                seen_eins.append(ein)
                rows.append(row)
                if len(rows) >= budget:
                    break

        docs: list[RawDoc] = []
        for row in rows[:budget]:
            doc = await self._document(person, row)
            if doc is not None:
                docs.append(doc)
        return docs

    async def _organisations(self, query: str) -> list[dict[str, Any]]:
        payload = await self.get_json(f"{API}/search.json", params={"q": query})
        if not isinstance(payload, dict):
            return []
        rows = payload.get("organizations")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _document(self, person: PersonRef, row: dict[str, Any]) -> RawDoc | None:
        ein = str(row.get("ein") or "")
        detail = await self.get_json(f"{API}/organizations/{ein}.json")

        officers: list[str] = []
        if isinstance(detail, dict):
            organisation = detail.get("organization")
            container = organisation if isinstance(organisation, dict) else detail
            officers = _officer_lines(container.get("officers"))
            if not officers:
                filings = detail.get("filings_with_data")
                if isinstance(filings, list) and filings and isinstance(filings[0], dict):
                    officers = _officer_lines(filings[0].get("officers"))

        # Say plainly whether the subject is on this roster: the alternative is a fact that
        # reads as "they are involved with X" when the truth is "X exists near them".
        named = [line for line in officers if person.name.lower() in line.lower()]
        place = ", ".join(part for part in (row.get("city"), row.get("state")) if part)

        return self.doc(
            ORG_PAGE.format(ein=ein),
            title=str(row.get("name") or f"EIN {ein}"),
            text=text_block(
                str(row.get("name") or ""),
                f"EIN {row.get('strein') or ein}" + (f" — {place}" if place else ""),
                f"NTEE code {row['ntee_code']}" if row.get("ntee_code") else None,
                f"{person.name} is listed as: {'; '.join(named)}" if named else None,
                ("Officers and directors: " + "; ".join(officers)) if officers else None,
            ),
        )


def _officer_lines(officers: Any) -> list[str]:
    """`[{"name": ..., "title": ...}]` -> `["Name (Title)"]`. Compensation is dropped."""
    if not isinstance(officers, list):
        return []
    lines: list[str] = []
    for officer in officers:
        if not isinstance(officer, dict) or not officer.get("name"):
            continue
        name = str(officer["name"]).strip()
        title = str(officer.get("title") or "").strip()
        lines.append(f"{name} ({title})" if title else name)
    return lines
