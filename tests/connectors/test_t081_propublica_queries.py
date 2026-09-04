"""T-081: a query to an index of organisation NAMES has to be an organisation name.

MEASURED LIVE, 2026-09-04, against Nonprofit Explorer's free v2 `search.json`:

    q=author                     -> 1260 organisations
    q=founder and partner        ->    1
    q=Philadelphia               -> 10000
    q=San Francisco              ->  9267
    q=NOT the author/apologist … ->    0

`organisation_queries` skipped a detail only when `identity.is_an_address` recognised it,
and that test needs a US STATE — so a bare city passes it. What was ACTUALLY going out for
the ten-person roster, though, was the JOB TITLE: `affiliations` is documented as
deliberately generous and returns conjoined titles on purpose, `identity.best_affiliation`
applies `names_a_job` to drop them and this connector did not. `q=author` for Eric Ries,
`q=founder and partner` for Josh Kopelman, `q=co-founder and partner` for Fred Wilson and
Hunter Walk, `q=writer and researcher` for Nabeel Qureshi.

THE TWO HALVES ARE NOT INDEPENDENT, WHICH IS THE REASON THIS MODULE EXISTS.  `MAX_QUERIES`
is 3, and for every person on the roster the job title occupied the third slot — so the
city was truncated away and never sent. Dropping the job title alone would have PROMOTED
`q=Philadelphia` and `q=San Francisco` into the budget and turned a latent defect into a
live one. `test_dropping_the_job_title_does_not_promote_the_city` is that trap held down.

Nothing here changes what the connector OUTPUTS: the officer filter rejected all of it
already. It changes what a courtesy API is asked for.

ANSWER KEYS.  This lane owns `connectors/propublica.py` and `resolve.py`. Every
expectation below is either a literal written in this file (a synthetic person's own
details) or `data/roster.yaml`, a data file outside this lane's ownership. Nothing is
compared against a module this lane can write.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from t1_ambiguity import MEMBER, search

from arrival.connectors.base import names_a_job
from arrival.connectors.propublica import MAX_QUERIES, organisation_queries
from arrival.contracts import PersonRef

pytestmark = pytest.mark.ticket("T-1")

#: Josh Kopelman's shape with synthetic words: a conjoined job title in front of the
#: organisation, and a bare city that names no US state.
CONJOINED_TITLE = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["founder and partner, Harrowgate Systems", "Trondheim"],
)

#: Nabeel Qureshi's shape: a detail that says who the member is NOT.
DENIAL = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=[
        "co-founder, Harrowgate Systems",
        "Trondheim",
        "NOT the author/apologist Dara Whitfield who died in 2011",
    ],
)


def test_a_job_title_is_never_sent_as_an_organisation_name() -> None:
    """The query that was actually going out. `q=author` is 1,260 live organisations."""
    queries = organisation_queries(CONJOINED_TITLE)

    assert "Harrowgate Systems" in queries, (
        f"the fix is to ask for fewer things, not none. Asked {queries!r}"
    )
    assert not any(names_a_job(query) for query in queries[1:]), (
        f"a job title went to an index of organisation NAMES: {queries!r}. "
        "`identity.best_affiliation` already applies this filter where a query is built; "
        "this connector builds queries too"
    )


def test_the_members_own_city_is_never_sent_as_an_organisation_name() -> None:
    """`Trondheim` names no US state, so `is_an_address` cannot see it. No gazetteer needed.

    Which detail is the place is already decided structurally by `resolve.city_detail` --
    it is the detail naming no role and no organisation.
    """
    for person in (CONJOINED_TITLE, DENIAL):
        queries = organisation_queries(person)
        assert "Trondheim" not in queries, (
            f"the member's city was searched as a charity name: {queries!r}"
        )


def test_a_detail_saying_who_the_member_is_not_is_never_a_query() -> None:
    """A denial is not an affiliation, however generously `affiliations` reads it."""
    queries = organisation_queries(DENIAL)

    assert not any("died" in query or query.startswith("NOT") for query in queries), (
        f"a roster line naming the person the member is NOT became a query: {queries!r}"
    )
    assert "Harrowgate Systems" in queries


def test_dropping_the_job_title_does_not_promote_the_city() -> None:
    """The trap: the city was only ever bounded out by `MAX_QUERIES`, not refused.

    With the job title gone there is a free slot in the budget, and a fix that removed only
    the title would have spent it on `q=Trondheim` -- a latent defect made live by its own
    repair. The city must be absent because it is REFUSED, not because it did not fit.
    """
    queries = organisation_queries(CONJOINED_TITLE)

    assert len(queries) < MAX_QUERIES, (
        "pre-condition: this person must leave a slot unused, or the assertion below "
        f"cannot tell refusal from truncation. Asked {queries!r}"
    )
    assert "Trondheim" not in queries, (
        f"a budget slot fell free and the city took it: {queries!r}"
    )


def test_the_members_name_is_still_the_first_query() -> None:
    """Every refusal above is a refusal to ask for MORE, never for the one that matters."""
    for person in (CONJOINED_TITLE, DENIAL, MEMBER):
        assert organisation_queries(person)[0] == person.name


def test_the_budget_is_still_respected() -> None:
    many = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=[
            "co-founder, Harrowgate Systems; co-founder, Tinbridge Labs",
            "Trondheim",
            "chair, Pelmyre Works; trustee, Marrowfield Press",
        ],
    )
    assert len(organisation_queries(many)) <= MAX_QUERIES


def test_the_live_roster_asks_only_for_organisations() -> None:
    """The ten people this product actually ships with, read from a file this lane owns none of."""
    roster = yaml.safe_load(Path("data/roster.yaml").read_text(encoding="utf-8"))
    assert len(roster["people"]) >= 10, "the roster shrank; this measurement is about all of it"

    # Written out as literals rather than derived from `resolve.city_detail`: this lane owns
    # that function, and an answer key the gradee can write measures nothing.
    places = {
        "new york", "boulder", "colorado", "philadelphia", "san francisco",
        "sydney", "australia",
    }

    for entry in roster["people"]:
        person = PersonRef(
            person_id="x", name=entry["name"], details=list(entry["details"])
        )
        queries = organisation_queries(person)
        assert queries[0] == entry["name"]
        for query in queries[1:]:
            assert not names_a_job(query), f"{entry['name']}: job title query {query!r}"
            assert not query.upper().startswith("NOT "), (
                f"{entry['name']}: denial sent as a query {query!r}"
            )
            assert query.strip().lower() not in places, (
                f"{entry['name']}: a place was searched as a charity name: {query!r}"
            )


# --- and the same thing through the real request path -------------------------------


def test_the_connector_really_only_asks_for_those(monkeypatch, tmp_path) -> None:
    """Not the pure function -- the requests the connector actually issues.

    A green assertion on `organisation_queries` proves the list is right, not that the list
    is what goes over the wire.
    """
    person = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["founder and partner, Harrowgate Systems", "Philadelphia"],
    )

    def route(request):
        return {"total_results": 0, "organizations": []}

    _, requested = search("propublica", route, monkeypatch, tmp_path, person=person)

    assert requested, "the connector has to look before it declines"
    assert any("q=Dara+Whitfield" in url or "q=Dara%20Whitfield" in url for url in requested), (
        f"the member's own name was never asked for: {requested!r}"
    )
    assert not any("Philadelphia" in url for url in requested), (
        f"the member's city went to Nonprofit Explorer as an organisation name: {requested!r}"
    )
    assert not any("founder" in url and "partner" in url for url in requested), (
        f"the member's job title went out as an organisation name: {requested!r}"
    )
