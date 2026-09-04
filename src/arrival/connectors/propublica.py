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

WHERE THE OFFICER LIST ACTUALLY LIVES NOW (T-074, measured live 2026-09-04).  The JSON
detail endpoint no longer carries one.  `organizations/530196605.json` (the American
National Red Cross, an organisation with thirteen filings on file) answers 200 with
`organization` — the IRS Business Master File record, no `officers` key — and
`filings_with_data` — the SOI financial extract, sixty-eight numeric columns and no
`officers` key either.  The only officer-shaped field anywhere in that payload is
`compnsatncurrofcr`, an aggregate compensation FIGURE, which is exactly the thing T-4
excludes and R11 never displays.

So the one rule this connector exists to enforce — *she is emitted only if the 990 names
her* — could not fire against the live API at all, and the connector returned zero
documents for every person on every run while its recorded tests stayed green, because
the recorded corpus records an `officers` array the API does not send.

The roster IS still published: it is on Nonprofit Explorer's own organisation PAGE, under
"Key Employees and Officers", at the same `ORG_PAGE` url this connector already cites.  So
the officer read is now two-tier — the JSON array when a payload carries one, and the
organisation page when it does not.  The JSON tier is kept rather than replaced because it
is the shape the API documented and may carry again, and because reading it costs the
request the connector was already making.

Compensation is dropped on both tiers, and on the HTML tier that is not incidental: the
page puts three salary columns beside every name, and `_OfficerRows` reads the FIRST cell
of a row and nothing else.

*Also measured, and it is not a defect:* `search.json` answers **404 with a well-formed
zero-results body** when a query matches nothing (`{"total_results": 0, "organizations":
[]}`), and 200 when it matches something — including for multi-word queries.  A 404 from
this endpoint means "no such charity", not "no such endpoint".
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from arrival.connectors.base import BaseConnector, affiliations, names_a_job, text_block
from arrival.connectors.identity import US_STATES, carries_name, identifies, is_an_address
from arrival.contracts import PersonRef, RawDoc
from arrival.http.client import fetch_record
from arrival.resolve import asserts_negation, city_detail
from arrival.util import normalize_ws

__all__ = ["ProPublicaConnector", "officers_on_page"]

API = "https://projects.propublica.org/nonprofits/api/v2"
ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/{ein}"

#: More than a few org queries per person is a fishing expedition, not research.
MAX_QUERIES = 3

#: How many candidate organisations may be looked up before the budget is filled. Larger
#: than `budget` because most candidates are about to be rejected for not naming her.
MAX_CANDIDATES = 8

#: How many organisation PAGES may be read per person when the JSON carries no roster.
#: Smaller than `MAX_CANDIDATES` on purpose: the page is a third of a megabyte of HTML and
#: the JSON call is a few kilobytes, so the fallback is a different kind of request and
#: gets its own, tighter budget.
MAX_PAGE_LOOKUPS = 4

#: How many of an organisation's officers are rendered into `RawDoc.text`. The MATCH
#: against the member is always made over the whole roster; only the printed neighbour
#: list is capped.
MAX_OFFICERS_SHOWN = 40

#: The row class Nonprofit Explorer marks each officer with in its Compensation table.
_OFFICER_ROW_CLASS = "employee-row"

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
    """The member's name plus the ORGANISATIONS in `details` — never a place, a job or a
    denial.

    Nonprofit Explorer indexes organisation NAMES, so a query that is not one is a request
    to a courtesy API that can only return strangers. Three tests, and each was measured
    live on 2026-09-04 against the ten-person roster:

    * **A JOB TITLE is not an organisation, and it is the one that was actually going out.**
      `affiliations` is documented as deliberately generous and returns conjoined titles on
      purpose (`connectors.base.names_a_job` explains why: a title is noise as a query and
      evidence as a check). `identity.best_affiliation` applies that filter and this
      function did not, so `q=author` went out for Eric Ries — **1,260 organisations**,
      live — alongside `q=founder and partner`, `q=co-founder and partner`,
      `q=co-founder and CEO` and `q=writer and researcher`. This is where the wasted
      requests were.

    * **A PLACE is not an organisation, and `is_an_address` alone does not find one.** That
      test needs a US STATE, so `Philadelphia` (10,000 organisations, live) and
      `San Francisco` (9,267) pass it. They were not in fact being sent — `MAX_QUERIES=3`
      truncated them away behind the job title — which means dropping the job title above
      would have PROMOTED them into the budget and made a latent defect a live one. The two
      halves of this fix are not independent; shipping either alone is worse than shipping
      neither.

      No gazetteer is needed and none is added. Which detail is the place is already
      decided, structurally, by `resolve.city_detail` — it is the detail that names no role
      and no organisation — and a second spelling of that question here would be a second
      answer to it.

    * **A DENIAL is not a query.** `"NOT the author/apologist Nabeel Qureshi who died in
      2017"` is a roster line that says who the member is NOT, and `affiliations` hands it
      back whole. `resolve.asserts_negation` refuses it, in the one place that question is
      answered.

    What this does NOT fix: a non-US city that is not the person's own city detail — a
    fourth detail naming somebody else's town would still be searched. That needs a
    gazetteer, and a gazetteer is a dependency this ticket may not add.
    """
    queries = [person.name]
    place = city_detail(person)
    for detail in person.details:
        if _is_an_address(detail) or detail == place or asserts_negation(detail):
            continue
        for term in affiliations([detail]):
            if normalize_ws(term) in _US_STATES or term in queries or names_a_job(term):
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
        pages_left = MAX_PAGE_LOOKUPS
        for row in rows:
            if len(docs) >= budget:
                break
            doc, spent = await self._document(person, row, pages_left)
            pages_left -= spent
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

    async def _officers_from_page(self, ein: str) -> list[str]:
        """The roster off the organisation's own page. `[]` when the page has none.

        `fetch_record` rather than `get_page`: the officer table is STRUCTURE, and
        `fetch_text` hands back the extracted prose with the markup — and therefore the
        row boundaries — already gone.
        """
        record = await fetch_record(ORG_PAGE.format(ein=ein), settings=self.settings)
        if record is None:
            return []
        return officers_on_page(record.body)

    async def _document(
        self, person: PersonRef, row: dict[str, Any], pages_left: int = 0
    ) -> tuple[RawDoc | None, int]:
        """`(document, organisation pages fetched)` for one candidate organisation."""
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

        # T-074: the live API sends no roster at all, so a JSON payload without one is the
        # NORMAL case rather than a broken organisation. The page carries it.
        spent = 0
        if not officers and ein and pages_left > 0:
            spent = 1
            officers = await self._officers_from_page(ein)

        # The roster IS the filter. An organisation that does not name her is one that
        # exists near her, and a document saying otherwise is false attribution with a
        # real url and a real quote attached.
        named = [line for line in officers if _is_the_member(person.name, line)]
        if not named:
            return None, spent

        place = ", ".join(part for part in (row.get("city"), row.get("state")) if part)

        # A 990 roster line naming her is necessary and NOT sufficient. "Marisol Quennebeck,
        # Chair" appears on the boards of every organisation any Marisol Quennebeck has ever
        # sat on, and this connector reaches those boards by searching her NAME — so the
        # organisation itself has to be one the roster recognises, by where it is or by what
        # it is called. Otherwise a stranger's board seat becomes the member's, cited to a
        # real IRS filing.
        if not identifies(
            person,
            names=named,
            context=[str(row.get("name") or ""), place, str(row.get("ntee_code") or "")],
        ):
            return None, spent

        return (
            self.doc(
                ORG_PAGE.format(ein=ein),
                title=str(row.get("name") or f"EIN {ein}"),
                text=text_block(
                    str(row.get("name") or ""),
                    f"EIN {row.get('strein') or ein}" + (f" — {place}" if place else ""),
                    f"NTEE code {row['ntee_code']}" if row.get("ntee_code") else None,
                    f"{person.name} is listed as: {'; '.join(named)}",
                    # Capped: the organisation PAGE lists every officer of every filing
                    # year on file, which for a national charity is 119 names. The hub
                    # this connector exists to draw is "who else sits at that table", and
                    # a hundred more names is not more table, it is 20k of RawDoc.text
                    # spent on people nobody will meet.
                    ("Officers and directors: " + "; ".join(officers[:MAX_OFFICERS_SHOWN]))
                    if officers
                    else None,
                ),
            ),
            spent,
        )


class _OfficerRows(HTMLParser):
    """`Name (Title)` for each `tr.employee-row` of a Nonprofit Explorer organisation page.

    ONLY THE FIRST CELL OF EACH ROW IS READ, and that is the taste rule made structural
    rather than promised. The three cells beside it are `Compensation`, `Related` and
    `Other` — dollar figures for a named individual, the single most sensitive thing on
    this page and a category T-4 excludes outright. A parser that collected the row and
    filtered afterwards would be one refactor away from carrying salaries into `RawDoc.text`
    for T-3 to quote; this one never has them in hand.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.officers: list[str] = []
        self._in_row = False
        self._cell = 0
        self._in_span = False
        self._name: list[str] = []
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "tr":
            self._in_row = _OFFICER_ROW_CLASS in values.get("class", "").split()
            self._cell = 0
            self._name = []
            self._title = []
            self._in_span = False
        elif tag == "td" and self._in_row:
            self._cell += 1
            self._in_span = False
        elif tag == "span" and self._in_row and self._cell == 1:
            self._in_span = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self._in_span = False
        elif tag == "tr" and self._in_row:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._in_row or self._cell != 1:
            return
        (self._title if self._in_span else self._name).append(data)

    def close(self) -> None:  # pragma: no cover - a truncated page still yields its rows
        super().close()
        self._flush()

    def _flush(self) -> None:
        self._in_row = False
        name = " ".join("".join(self._name).split())
        title = " ".join("".join(self._title).split()).strip("()")
        self._name = []
        self._title = []
        if not name:
            return
        line = f"{name} ({title})" if title else name
        if line not in self.officers:
            self.officers.append(line)


def officers_on_page(markup: str) -> list[str]:
    """`["Name (Title)"]` from an organisation page's Compensation table. Never raises."""
    parser = _OfficerRows()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a page we cannot parse simply names no officers
        return list(parser.officers)
    return parser.officers


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
