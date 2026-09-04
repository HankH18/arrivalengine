"""T-077: the joins the live corpus needed, pinned end to end through `match`.

The measurement this ticket started from, on `data/dossiers/`: 68 hubs over 7 resolved
people, every hub carried by exactly ONE of them, 1 of 21 pairs sharing anything. Three
causes were named. This module pins the graph's half of the first — which was already
repaired, in `_identity_key`, and had no regression test naming the shape it repaired — and
the consequences of the other two, which are extractor changes whose whole point is that
`match` can finally see them.

Every expected value here is a literal in this file or a name from `arrival.contracts`.
Nothing reads a constant out of `arrival.graph`, which this ticket owns.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from arrival.contracts import Dossier, Hub, PersonRef, Resolution
from arrival.graph import build_graph, hub_node, match

pytestmark = pytest.mark.ticket("T-5")

BUILT = datetime(2026, 2, 20, 14, 0, tzinfo=UTC)


def _person(person_id: str) -> PersonRef:
    return PersonRef(person_id=person_id, name=person_id.replace("-", " ").title())


def _dossier(person_id: str, *hubs: Hub) -> Dossier:
    return Dossier(
        person=_person(person_id),
        resolution=Resolution(
            person_id=person_id, status="resolved", accepted_doc_ids=[], rejected=[],
            confidence=0.9,
        ),
        facts=[],
        hubs=list(hubs),
        built_at=BUILT,
    )


def _hub(hub_id: str, label: str, hub_type: str) -> Hub:
    return Hub(hub_id=hub_id, label=label, type=hub_type, recency=1.0, evidence_fact_ids=[])


def _filler(n: int) -> list[Dossier]:
    """People carrying nothing in common, so N is realistic and the idf is not degenerate."""
    return [
        _dossier(f"filler-{i}", _hub(f"company:only-{i}", f"Only {i}", "company"))
        for i in range(n)
    ]


def _shared(graph, a: str, b: str) -> set[str]:
    left, right = f"person:{a}", f"person:{b}"
    return {
        node
        for node in set(graph[left]) & set(graph[right])
        if graph.nodes[node].get("kind") == "hub"
    }


# --------------------------------------------------------------------------
# cause 1: the hub TYPE may not decide whether two carriers meet
# --------------------------------------------------------------------------


def test_one_organisation_typed_two_ways_is_one_node_and_connects_its_carriers():
    """The only real connection in the live corpus, and the shape that nearly lost it.

    Measured verbatim in `data/dossiers/`: `company:y-combinator` on one member and
    `investor:y-combinator` on another — one organisation, two ids, and grouping by
    `hub_id` gave a 0-of-21 corpus while grouping by LABEL gave 1. The repair lives in
    `graph._identity_key`; nothing named this shape, so nothing would have caught its
    removal.
    """
    dossiers = [
        _dossier("emmett", _hub("company:y-combinator", "Y Combinator", "company")),
        _dossier("steve", _hub("investor:y-combinator", "Y Combinator", "investor")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)

    shared = _shared(graph, "emmett", "steve")
    assert len(shared) == 1, f"one organisation became {len(shared)} nodes: {sorted(shared)}"
    scored = match(graph, "emmett", ["steve"])
    assert scored[0].score > 0, "two carriers of one organisation scored zero"
    assert "Y Combinator" in scored[0].why
    assert scored[0].path == ["person:emmett", next(iter(shared)), "person:steve"]


def test_an_acronym_and_its_expansion_are_one_institution_and_are_said_once():
    """The second face of cause 1, measured on the repaired live corpus.

    `school:mit` and `school:massachusetts-institute-of-technology` were carried by the SAME
    two people, so the pair did not merely fail to join on them -- it joined TWICE for one
    real reason. Measured: score 53 instead of 27, and the spoken line "Both came through
    Massachusetts Institute of Technology; both came through MIT."
    """
    both = [
        _hub("school:mit", "MIT", "school"),
        _hub("school:massachusetts-institute-of-technology",
             "Massachusetts Institute of Technology", "school"),
    ]
    dossiers = [_dossier("brad", *both), _dossier("fred", *both), *_filler(6)]
    graph = build_graph(dossiers)

    shared = _shared(graph, "brad", "fred")
    assert len(shared) == 1, f"one institution became {len(shared)} nodes: {sorted(shared)}"
    scored = match(graph, "brad", ["fred"])
    assert len(scored[0].contributions) == 1, "one reason was counted twice"
    why = scored[0].why
    assert why.count("both") + why.count("Both") == 1, f"one reason, said twice: {why!r}"
    assert "MIT" in why


def test_an_acronym_two_names_could_expand_to_is_refused_rather_than_assigned():
    """R2 again: an alphabetical winner between two expansions is arrival order in costume."""
    dossiers = [
        _dossier("brad", _hub("school:mit", "MIT", "school")),
        _dossier("fred", _hub("school:massachusetts-institute-of-technology",
                              "Massachusetts Institute of Technology", "school")),
        _dossier("nadia", _hub("school:manchester-institute-of-technology",
                               "Manchester Institute of Technology", "school")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)

    assert not _shared(graph, "brad", "fred"), "a contested acronym was resolved by sorting"
    assert not _shared(graph, "brad", "nadia"), "a contested acronym was resolved by sorting"


def test_a_short_name_that_is_not_written_as_an_acronym_is_never_folded():
    """The orthography is the evidence, and it has to be the ONLY thing standing here.

    "Ada" really is the initials of "Applied Data Analytics" — same letters, same type,
    exactly one claimant, so every other condition of the fold is satisfied. The one thing
    that says these are two companies rather than one is that "Ada" is written as a name and
    not as an abbreviation.
    """
    dossiers = [
        _dossier("a", _hub("company:ada", "Ada", "company")),
        _dossier("b", _hub("company:applied-data-analytics",
                           "Applied Data Analytics", "company")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    assert not _shared(graph, "a", "b"), "an ordinary short name was folded into an expansion"


def test_a_two_letter_abbreviation_is_never_folded():
    """Where the rule stops being about one organisation and starts being a coincidence.

    "BA" is Bank of America, British Airways and a Bachelor of Arts, and a wrong fold does
    not merely connect one pair: it pools two entities' labels, types and evidence for every
    person in the graph. Three letters is where the abbreviations that actually appear in
    dossiers live — MIT, USV, IBM, NYU — and two is where the collisions do.
    """
    dossiers = [
        _dossier("a", _hub("company:ba", "BA", "company")),
        _dossier("b", _hub("company:bank-of-america", "Bank of America", "company")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    assert not _shared(graph, "a", "b"), "a two-letter abbreviation was expanded on a guess"
    assert match(graph, "a", ["b"])[0].score == 0


def test_an_acronym_is_not_folded_across_hub_types():
    """An acronym company and an expansion school are two things however the letters line up."""
    dossiers = [
        _dossier("a", _hub("company:mit", "MIT", "company")),
        _dossier("b", _hub("school:massachusetts-institute-of-technology",
                           "Massachusetts Institute of Technology", "school")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    assert not _shared(graph, "a", "b"), "the fold crossed a hub type"


def test_the_acronym_fold_does_not_depend_on_dossier_order():
    short = _dossier("brad", _hub("school:mit", "MIT", "school"))
    long = _dossier("fred", _hub("school:massachusetts-institute-of-technology",
                                 "Massachusetts Institute of Technology", "school"))
    forwards = build_graph([short, long, *_filler(6)])
    backwards = build_graph([long, short, *_filler(6)])

    def hubs(graph):
        return {
            node: (data["type"], data["label"], data["n_carriers"])
            for node, data in graph.nodes(data=True)
            if data.get("kind") == "hub"
        }

    assert hubs(forwards) == hubs(backwards)
    assert match(forwards, "brad", ["fred"])[0].score == match(backwards, "brad", ["fred"])[0].score


def test_two_different_names_for_one_institution_are_left_alone():
    """The case this deliberately does NOT solve, pinned so nobody later thinks it does.

    "The Wharton School of Business" and "University of Pennsylvania" are one institution and
    both are in the live corpus. Nothing in the two strings relates them; only a knowledge
    base does, and inventing one here would be manufacturing a connection rather than finding
    one. Two people who share it stay unconnected, and that is the honest answer until a real
    alias source exists.
    """
    dossiers = [
        _dossier("fred", _hub("school:the-wharton-school-of-business",
                              "The Wharton School of Business", "school")),
        _dossier("josh", _hub("school:university-of-pennsylvania",
                              "University of Pennsylvania", "school")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    assert not _shared(graph, "fred", "josh")
    assert match(graph, "fred", ["josh"])[0].score == 0


def test_the_elected_type_of_a_split_hub_does_not_depend_on_dossier_order():
    """Which of the two types wins decides the boost, so it may not be a filesystem glob."""
    left = _dossier("emmett", _hub("company:y-combinator", "Y Combinator", "company"))
    right = _dossier("steve", _hub("investor:y-combinator", "Y Combinator", "investor"))
    forwards = build_graph([left, right, *_filler(5)])
    backwards = build_graph([right, left, *_filler(5)])

    def described(graph):
        return {
            node: (data["type"], data["label"], data["idf"], data["type_boost"])
            for node, data in graph.nodes(data=True)
            if data.get("kind") == "hub"
        }

    assert described(forwards) == described(backwards)


# --------------------------------------------------------------------------
# cause 2: a shared city is weak evidence, which is a reason to HAVE it
# --------------------------------------------------------------------------


def test_a_shared_city_connects_two_people_and_says_so_out_loud():
    """DESIGN prices a city at the lowest boost precisely because it is weak evidence.

    Weak is not nothing: the live roster puts five of ten members in one city and the graph
    saw none of it, so the Meet section was empty for every one of them.
    """
    dossiers = [
        _dossier("hunter", _hub("city:san-francisco", "San Francisco", "city")),
        _dossier("steve", _hub("city:san-francisco", "San Francisco", "city")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    scored = match(graph, "hunter", ["steve"])

    assert scored[0].score > 0, "two members of one city scored zero"
    why = scored[0].why
    assert "San Francisco" in why
    assert "city:" not in why and "hub:" not in why, f"an id reached a spoken line: {why!r}"
    assert why.endswith(".") and len(why.split()) <= 30


def test_a_city_everybody_shares_is_worth_nothing_and_is_never_the_reason():
    """The existing defence against a degenerate hub, restated now that cities exist.

    `hub_idf` clamps a hub the whole population carries to zero, so a club where everyone
    lives in one city gets no free connections out of it — and `_why` refuses to name a hub
    that contributed nothing rather than saying something true and worthless out loud.
    """
    everywhere = _hub("city:san-francisco", "San Francisco", "city")
    dossiers = [_dossier(f"member-{i}", everywhere) for i in range(6)]
    graph = build_graph(dossiers)
    scored = match(graph, "member-0", ["member-1"])

    assert scored[0].score == 0
    assert "San Francisco" not in scored[0].why


# --------------------------------------------------------------------------
# cause 3: an abstraction must lose to a specific entity at equal rarity
# --------------------------------------------------------------------------


def test_a_shared_field_connects_two_people_with_a_line_a_host_can_say():
    dossiers = [
        _dossier("fred", _hub("topic:venture-capital", "venture capital", "topic")),
        _dossier("hunter", _hub("topic:venture-capital", "venture capital", "topic")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    scored = match(graph, "fred", ["hunter"])

    assert scored[0].score > 0
    why = scored[0].why
    assert "venture capital" in why
    assert "topic:" not in why, f"an id reached a spoken line: {why!r}"
    assert why[0].isupper() and why.endswith(".")
    assert "(" not in why and ")" not in why, "R18: no parentheticals in a spoken line"


def test_a_rare_shared_entity_outranks_a_shared_field_at_equal_rarity():
    """S5, restated for the abstraction layer: the specific thing must win.

    Both hubs are carried by exactly the same two people, so idf and recency are identical
    and only the type boost separates them. If a field could outrank a company, the
    abstraction layer would be burying the reason the pair actually matters.
    """
    both = [
        _hub("company:quillmark-labs", "Quillmark Labs", "company"),
        _hub("topic:venture-capital", "venture capital", "topic"),
    ]
    dossiers = [_dossier("fred", *both), _dossier("hunter", *both), *_filler(5)]
    graph = build_graph(dossiers)
    scored = match(graph, "fred", ["hunter"])
    top = scored[0].contributions[0]

    assert top.hub.hub_id == "company:quillmark-labs", (
        "an abstraction outranked the specific entity two people actually share"
    )
    assert scored[0].path[1] == hub_node("company:quillmark-labs")
    assert scored[0].why.startswith("Both connected to Quillmark Labs")


def test_a_field_on_more_people_is_worth_less_than_an_entity_on_two():
    """The self-regulating half: a field spreads, and spreading is what costs it weight.

    This is why the abstraction layer needs no blocklist of its own. Four carriers of a
    field against two of a company, in one population, and the rarer thing wins on the
    arithmetic DESIGN already specified.
    """
    field = _hub("topic:venture-capital", "venture capital", "topic")
    company = _hub("company:quillmark-labs", "Quillmark Labs", "company")
    dossiers = [
        _dossier("fred", field, company),
        _dossier("hunter", field, company),
        _dossier("sarah", field),
        _dossier("josh", field),
        *_filler(3),
    ]
    graph = build_graph(dossiers)
    scored = match(graph, "fred", ["hunter"])
    ordered = [c.hub.hub_id for c in scored[0].contributions]

    assert ordered[0] == "company:quillmark-labs", f"contribution order was {ordered}"
    assert scored[0].contributions[0].contribution > scored[0].contributions[1].contribution


def test_every_pair_that_shares_nothing_still_says_so_rather_than_inventing_a_reason():
    """R2 applied to hubs: the alternative to a connection is silence, never a guess."""
    dossiers = [
        _dossier("fred", _hub("topic:venture-capital", "venture capital", "topic")),
        _dossier("melanie", _hub("company:canva", "Canva", "company")),
        *_filler(5),
    ]
    graph = build_graph(dossiers)
    scored = match(graph, "fred", ["melanie"])

    assert scored[0].score == 0
    assert scored[0].contributions == []
    assert scored[0].path == []
    assert "venture capital" not in scored[0].why and "Canva" not in scored[0].why
