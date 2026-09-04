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

WHAT THE 990 ROSTER IS ALSO FOR (T-018).  It is the only thing in this source that
connects a PERSON to an ORGANISATION, so it is also the filter.  Nonprofit Explorer
indexes organisation names, and `affiliations(details)` is documented as deliberately
generous — it cannot tell an employer from a city, so it hands back
`['Thornfield Loom', 'Providence', 'Rhode Island']` and a query on `Providence` returns
every charity in Providence.  Each of those used to become a `RawDoc` stamped
`source_kind="propublica"` and presented as a document about the member.  Downstream that
is worse than useless: T-3 extracts a fact about the SUBJECT from whatever text arrives,
the quote really is in the document and the url really does resolve, so the citation guard
passes and a stranger's charity is displayed as hers, sourced.

So two rules, and both matter:

* **A place is not an organisation name.**  A detail that ends in a US state is where she
  lives; it is never sent to a search over charity names.
* **She is emitted only if the 990 names her.**  No match on the officer list means no
  document — not a document that merely declines to claim she is on the board.  Names are
  compared word-by-word rather than as a substring, because half the filings in the real
  corpus read `QUENNEBECK MARISOL A`.

Two steps, both on the free v2 API with no key: `search.json?q=` to find candidate
organisations, then `organizations/{ein}.json` for the officer list.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, affiliations, text_block
from arrival.connectors.identity import US_STATES, carries_name, is_an_address
from arrival.contracts import PersonRef, RawDoc
from arrival.util import normalize_ws

__all__ = ["ProPublicaConnector"]

API = "https://projects.propublica.org/nonprofits/api/v2"
ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/{ein}"

#: More than a few org queries per person is a fishing expedition, not research.
MAX_QUERIES = 3

#: How many candidate organisations may be looked up before the budget is filled. Larger
#: than `budget` because most candidates are about to be rejected for not naming her.
MAX_CANDIDATES = 8

#: The name predicate, the state list and the address test all now live in
#: `identity.py`: every one of them was written here and copied, and a second spelling of
#: any of them is a second answer to "is this her?".
_US_STATES = US_STATES


def _is_the_member(person_name: str, officer_name: str) -> bool:
    """Does this roster line name the member?

    `carries_name` (identity.py): word-set containment, not substring. Real 990 rosters
    are written `QUENNEBECK MARISOL A` about as often as `Marisol Quennebeck`, and a
    substring test on the full name in roster order misses every one of them — which
    silently turns the one fact this connector exists to state, "she chairs this", into
    "this exists near her". Containment also means a shared surname alone is never a
    match.
    """
    return carries_name(officer_name, person_name)


_is_an_address = is_an_address


def organisation_queries(person: PersonRef) -> list[str]:
    """The member's name plus the ORGANISATIONS in `details` — never the places."""
    queries = [person.name]
    for detail in person.details:
        if _is_an_address(detail):
            continue
        for term in affiliations([detail]):
            if normalize_ws(term) in _US_STATES or term in queries:
                continue
            queries.append(term)
    return queries[:MAX_QUERIES]


class ProPublicaConnector(BaseConnector):
    """`kind="propublica"` — nonprofit affiliations and the boards they imply."""

    kind = "propublica"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        limit = max(1, min(budget + 3, MAX_CANDIDATES))

        seen_eins: list[str] = []
        rows: list[dict[str, Any]] = []
        for query in organisation_queries(person):
            if len(rows) >= limit:
                break
            for row in await self._organisations(query):
                ein = str(row.get("ein") or "")
                if not ein or ein in seen_eins:
                    continue
                seen_eins.append(ein)
                rows.append(row)
                if len(rows) >= limit:
                    break

        docs: list[RawDoc] = []
        for row in rows:
            if len(docs) >= budget:
                break
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

        # The roster IS the filter. An organisation that does not name her is one that
        # exists near her, and a document saying otherwise is false attribution with a
        # real url and a real quote attached.
        named = [line for line in officers if _is_the_member(person.name, line)]
        if not named:
            return None

        place = ", ".join(part for part in (row.get("city"), row.get("state")) if part)

        return self.doc(
            ORG_PAGE.format(ein=ein),
            title=str(row.get("name") or f"EIN {ein}"),
            text=text_block(
                str(row.get("name") or ""),
                f"EIN {row.get('strein') or ein}" + (f" — {place}" if place else ""),
                f"NTEE code {row['ntee_code']}" if row.get("ntee_code") else None,
                f"{person.name} is listed as: {'; '.join(named)}",
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
