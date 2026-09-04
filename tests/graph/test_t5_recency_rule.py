"""T-027: pin ``min`` as the pair-recency rule, with recencies that can tell it apart.

**Why this module exists.** Every hub in all five frozen dossiers has ``recency: 1.0``. On
that corpus ``min(ra, rb)``, ``max(ra, rb)``, ``mean``, ``ra`` and ``rb`` are the SAME
function of every input the frozen metric can see -- inverting the rule to ``max`` leaves the
scored ``t5`` criteria at 8/8. The unit corpus at ``tests/fixtures/dossiers/`` cannot see it
either: its one stale hub (``school:rio-verde-college``, recency 0.3) is carried by one
person, so it is never in a pair's shared set. The rule is therefore pinned here, on hubs two
people carry at DIFFERENT recencies.

**What this grades against.** DESIGN Decision 3 and TASKS T-5 acceptance 1: "a pair's
contribution uses ``min`` of the two edge recencies", with the product's stated reason -- a
hub that is current for one person and stale for the other is only as live as the staler
side. The arithmetic below is that sentence applied by hand; nothing is read back out of
``arrival.graph``.
"""

from __future__ import annotations

import math

import pytest
from t5_graph_helpers import filler, make_dossier, make_hub

from arrival.graph import build_graph, match, person_node

pytestmark = pytest.mark.ticket("T-5")

#: Five people, two of whom share one investor hub: N=5, n=2.
POPULATION = 5
IDF = math.log(POPULATION / 3)
BOOST = 1.5
REF = IDF * BOOST

HUB_ID = "investor:foundry-seed-2019"


def _graph(recency_a: float, recency_b: float):
    a = make_dossier("a", "Person A", [make_hub(HUB_ID, "Foundry Seed 2019", "investor",
                                                recency=recency_a)])
    b = make_dossier("b", "Person B", [make_hub(HUB_ID, "Foundry Seed 2019", "investor",
                                                recency=recency_b)])
    graph = build_graph([a, b, *filler(POPULATION - 2)])
    assert graph.graph["n_people"] == POPULATION
    return graph


def _score(recency: float) -> float:
    """``min(100, round(100 * idf * recency * boost / REF))`` = ``round(100 * recency)``."""
    return float(min(100, round(100 * IDF * recency * BOOST / REF)))


@pytest.mark.parametrize(
    ("recency_a", "recency_b", "expected"),
    [
        (1.0, 0.25, 0.25),
        (0.25, 1.0, 0.25),
        (1.0, 0.5, 0.5),
        (0.4, 0.9, 0.4),
        (0.6, 0.6, 0.6),
        (1.0, 1.0, 1.0),
    ],
)
def test_a_pairs_recency_is_the_min_of_the_two_edges(recency_a, recency_b, expected):
    m = match(_graph(recency_a, recency_b), "a", ["b"])[0]
    c = m.contributions[0]
    assert c.recency == pytest.approx(expected), (
        f"recencies {recency_a} and {recency_b} gave a pair recency of {c.recency}; DESIGN "
        f"says min, which is {expected}"
    )
    assert c.contribution == pytest.approx(IDF * expected * BOOST)
    assert m.score == _score(expected)


def test_min_is_distinguishable_from_max_mean_and_either_side():
    """The assertion the frozen corpus cannot make, because there every candidate agrees.

    With edges at 1.0 and 0.25 the five candidate rules give five different answers, so the
    single observed score falsifies four of them.
    """
    m = match(_graph(1.0, 0.25), "a", ["b"])[0]
    assert m.score == _score(0.25) == 25.0
    assert m.score != _score(1.0), "max(1.0, 0.25) -- the stale side was ignored"
    assert m.score != _score(0.625), "mean(1.0, 0.25) -- the stale side was only discounted"
    # 'the arriving person's own edge' and 'the other's' are 1.0 and 0.25 here, so the two
    # positional rules are already excluded by the two directions below.


def test_the_rule_is_symmetric_so_the_pair_scores_the_same_from_either_doorway():
    """R3 matches on arrival, so the same pair is computed from both ends on different nights."""
    graph = _graph(1.0, 0.25)
    forward = match(graph, "a", ["b"])[0]
    backward = match(graph, "b", ["a"])[0]
    assert forward.score == backward.score == 25.0
    assert forward.contributions[0].recency == backward.contributions[0].recency == 0.25


def test_each_edge_still_keeps_its_own_recency_and_only_the_pair_takes_the_min():
    """The min is a property of the PAIR. Writing it back to the edges would corrupt every
    other pair that person's hub takes part in."""
    graph = _graph(1.0, 0.25)
    assert graph.edges[person_node("a"), f"hub:{HUB_ID}"]["recency"] == 1.0
    assert graph.edges[person_node("b"), f"hub:{HUB_ID}"]["recency"] == 0.25


def test_a_third_carrier_at_full_recency_is_unaffected_by_a_stale_pair():
    """Three carriers, one stale: a-c is fresh, a-b is not, and neither leaks into the other."""
    hub_fresh = make_hub(HUB_ID, "Foundry Seed 2019", "investor")
    stale = make_hub(HUB_ID, "Foundry Seed 2019", "investor", recency=0.2)
    graph = build_graph(
        [
            make_dossier("a", "A", [hub_fresh]),
            make_dossier("b", "B", [stale]),
            make_dossier("c", "C", [hub_fresh]),
            *filler(7),
        ]
    )
    by_person = {m.other.person_id: m for m in match(graph, "a", ["b", "c"])}
    assert by_person["c"].contributions[0].recency == 1.0
    assert by_person["b"].contributions[0].recency == pytest.approx(0.2)
    assert by_person["c"].score > by_person["b"].score
    assert [m.other.person_id for m in match(graph, "a", ["b", "c"])] == ["c", "b"], (
        "the fresher pair must rank first"
    )


def test_a_zero_recency_hub_contributes_nothing_and_the_why_stops_claiming_it():
    """recency 0 is the extreme of the same rule, and the why/path must follow it down."""
    m = match(_graph(1.0, 0.0), "a", ["b"])[0]
    assert m.contributions[0].recency == 0.0
    assert m.contributions[0].contribution == 0.0
    assert m.score == 0.0
    assert "Foundry Seed 2019" not in m.why, (
        f"a hub worth nothing must not be named as the reason: {m.why!r}"
    )
    assert m.path == [], f"nor routed through: {m.path}"
