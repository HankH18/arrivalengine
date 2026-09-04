"""T-027: pin all ten type boosts through ``match``, not through the table.

**Why this module exists.** Only two of the ten boosts are load-bearing on the frozen grading
corpus. Its five dossiers carry hubs of five types, and of those:

* ``city:austin`` and ``topic:remote-work`` are held by all five people, so ``idf`` clamps to
  0 and multiplies whatever boost they carry away to nothing;
* ``company:lantern-freight`` and ``school:bellhaven-polytechnic`` are held by ONE person
  each, so they never appear in a pair's shared set at all.

That leaves ``investor`` and ``topic`` observable and the other eight -- ``board``, ``cause``,
``city``, ``company``, ``event``, ``person``, ``school``, ``technology`` -- structurally
invisible to the frozen metric. The corpus cannot be amended (three frozen modules pin values
derived from it), so the gap is closed here, in the layer where nothing is frozen.

**What this grades against.** DESIGN Decision 3, transcribed: "Type boosts: investor/board/
company 1.5, event/cause/collaborator-person 1.3, technology/topic 1.0, school 0.8, city 0.5",
and ``score = min(100, round(100 * raw / REF))`` with ``REF = ln(N/3) * 1.5``. The literals
below are that sentence, not a reading of ``arrival.graph``: this module deliberately does NOT
import ``TYPE_BOOST``, because a test that reads its expectation out of the module under test
measures nothing. The membership of ``HubType`` comes from ``arrival.contracts``, which T-5
does not own and may not widen.
"""

from __future__ import annotations

import math
import typing

import pytest
from t5_graph_helpers import filler, make_dossier, make_hub

from arrival.contracts import HubType
from arrival.graph import build_graph, match

pytestmark = pytest.mark.ticket("T-5")

#: DESIGN Decision 3, verbatim. DESIGN spells the 1.3 bucket "collaborator-person", which is
#: the ``person`` member of ``HubType``.
EXPECTED_BOOST: dict[str, float] = {
    "investor": 1.5,
    "board": 1.5,
    "company": 1.5,
    "event": 1.3,
    "cause": 1.3,
    "person": 1.3,
    "technology": 1.0,
    "topic": 1.0,
    "school": 0.8,
    "city": 0.5,
}

#: Ten people, two of them sharing one hub: N=10, n=2, so idf = ln(10/3) and REF = ln(10/3)*1.5
#: -- the reference pair itself, which makes ``score`` a direct readout of the boost.
POPULATION = 10
IDF = math.log(POPULATION / 3)
REF = IDF * 1.5

#: ``min(100, round(100 * idf * 1.0 * boost / REF))`` = ``round(100 * boost / 1.5)``. Every
#: boost bucket lands on its own score, so an inverted or copy-pasted row cannot hide.
EXPECTED_SCORE = {
    1.5: 100,
    1.3: 87,
    1.0: 67,
    0.8: 53,
    0.5: 33,
}


def _pair_on(hub_type: str):
    hub = make_hub(f"{hub_type}:the-thing", f"The {hub_type.title()} Thing", hub_type)
    a = make_dossier("a", "Person A", [hub])
    b = make_dossier("b", "Person B", [hub])
    graph = build_graph([a, b, *filler(POPULATION - 2)])
    assert graph.graph["n_people"] == POPULATION
    return match(graph, "a", ["b"])[0]


def test_the_ten_types_this_module_pins_are_exactly_the_ten_that_exist():
    """``HubType`` lives in ``contracts``, which T-5 neither owns nor may widen."""
    assert set(EXPECTED_BOOST) == set(typing.get_args(HubType)), (
        "HubType and the DESIGN boost table have diverged: "
        f"{set(EXPECTED_BOOST) ^ set(typing.get_args(HubType))}"
    )


@pytest.mark.parametrize(("hub_type", "boost"), sorted(EXPECTED_BOOST.items()))
def test_each_hub_type_carries_its_designed_boost_into_the_contribution(hub_type, boost):
    m = _pair_on(hub_type)
    assert len(m.contributions) == 1, m.contributions
    c = m.contributions[0]
    assert c.type_boost == boost, f"{hub_type}: type_boost {c.type_boost} != DESIGN's {boost}"
    assert c.idf_weight == pytest.approx(IDF)
    assert c.recency == 1.0
    assert c.contribution == pytest.approx(IDF * 1.0 * boost)


@pytest.mark.parametrize(("hub_type", "boost"), sorted(EXPECTED_BOOST.items()))
def test_each_hub_type_reaches_the_score_its_boost_implies(hub_type, boost):
    """The boost observed end-to-end, through the only surface the product exposes."""
    m = _pair_on(hub_type)
    assert m.score == EXPECTED_SCORE[boost], (
        f"{hub_type} (boost {boost}) scored {m.score}; DESIGN's "
        f"min(100, round(100 * ln(10/3) * {boost} / {REF:.5f})) is {EXPECTED_SCORE[boost]}"
    )


def test_the_boost_ordering_is_a_strict_ranking_and_not_a_flat_table():
    """S5's point: at equal rarity an investor must beat a school must beat a city."""
    scores = {hub_type: _pair_on(hub_type).score for hub_type in EXPECTED_BOOST}
    assert scores["investor"] > scores["event"] > scores["topic"] > scores["school"]
    assert scores["school"] > scores["city"]
    assert scores["investor"] == scores["board"] == scores["company"]
    assert scores["event"] == scores["cause"] == scores["person"]
    assert scores["technology"] == scores["topic"]
    assert len(set(scores.values())) == 5, (
        f"five boost buckets must give five distinct scores, got {sorted(set(scores.values()))}"
    )


def test_a_rare_city_still_loses_to_a_commoner_investor():
    """S5 stated as a comparison rather than as two numbers: rarity does not swamp type.

    The city hub is on 2 of 10 people (idf ln(10/3) = 1.204); the investor hub is on 4 of 10
    (idf ln(10/5) = 0.693). The city is nearly twice as rare and still loses, because
    0.5 * 1.204 < 1.5 * 0.693.
    """
    city = make_hub("city:rarecity", "Rare City", "city")
    fund = make_hub("investor:common-fund", "Common Fund", "investor")
    a = make_dossier("a", "A", [city, fund])
    b = make_dossier("b", "B", [city, fund])
    crowd = [make_dossier(x, x.upper(), [fund]) for x in ("c", "d")]

    graph = build_graph([a, b, *crowd, *filler(6)])
    assert graph.graph["n_people"] == POPULATION
    m = match(graph, "a", ["b"])[0]
    top = m.contributions[0]
    assert top.hub.hub_id == "investor:common-fund", (
        f"the boost is not carrying its weight: a 2-of-10 city outranked a 4-of-10 investor "
        f"({[(c.hub.hub_id, c.contribution) for c in m.contributions]})"
    )
    assert m.path[1] == "hub:investor:common-fund"
    assert "Common Fund" in m.why
