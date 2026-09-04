"""T-5 acceptance 2 and 5: the score, its components, and the normalisation.

The corpus here is the FOUR T-0 fixture dossiers (N = 4), not the five-person frozen
grading corpus (N = 5). The two populations produce different numbers from the same
formula, so every constant below is derived from N = 4 and none is copied across.
"""

from __future__ import annotations

import itertools
import math

import pytest
from t5_graph_helpers import RARE_HUB_ID, filler, fixture_graph, make_dossier, make_hub

from arrival.graph import build_graph, match, reference_score

pytestmark = pytest.mark.ticket("T-5")


@pytest.fixture
def graph():
    return fixture_graph()


N = 4
IDF_RARE = math.log(N / 3)  # carried by charlie and delta
REF = IDF_RARE * 1.5  # ln(N/3) * 1.5
RAW_CHARLIE_DELTA = IDF_RARE * 1.0 * 1.5  # idf * recency * investor boost


def _by_person(matches):
    return {m.other.person_id: m for m in matches}


# --- acceptance 2: rare beats generic -------------------------------------


def test_rare_hub_pair_ranks_first(graph):
    matches = match(graph, "charlie", ["alpha", "bravo", "delta"])
    assert [m.other.person_id for m in matches][0] == "delta"
    assert matches[0].contributions[0].hub.hub_id == RARE_HUB_ID
    assert matches[0].contributions[0].contribution > 0


def test_generic_only_pair_scores_zero_with_all_zero_contributions(graph):
    matches = match(graph, "alpha", ["bravo"])
    assert len(matches) == 1
    pair = matches[0]
    assert pair.score == 0
    assert pair.contributions, "alpha and bravo do share hubs; the components must show them"
    assert [c.contribution for c in pair.contributions] == [0.0, 0.0]

    # Sabotage companion: a matcher returning 0 for everything would pass the above.
    assert match(graph, "charlie", ["delta"])[0].score == 100


def test_clamped_hubs_are_reported_but_worth_nothing(graph):
    pair = match(graph, "charlie", ["delta"])[0]
    by_hub = {c.hub.hub_id: c for c in pair.contributions}
    assert set(by_hub) == {RARE_HUB_ID, "city:austin", "topic:machine-learning"}
    assert by_hub["city:austin"].idf_weight == 0.0
    assert by_hub["city:austin"].type_boost == 0.5, "the boost is still reported, R10"
    assert by_hub["city:austin"].contribution == 0.0


def test_unique_hubs_never_contribute(graph):
    """company:quillmark has the highest idf in the corpus and belongs to one person."""
    assert graph.nodes["hub:company:quillmark"]["idf"] > graph.nodes[f"hub:{RARE_HUB_ID}"]["idf"]
    for other in ("alpha", "bravo", "delta"):
        hub_ids = {c.hub.hub_id for c in match(graph, "charlie", [other])[0].contributions}
        assert "company:quillmark" not in hub_ids


# --- acceptance 3: components sum to raw ----------------------------------


def test_contributions_are_sorted_desc_and_are_the_product_of_their_parts(graph):
    for arriving, other in itertools.permutations(("alpha", "bravo", "charlie", "delta"), 2):
        m = match(graph, arriving, [other])[0]
        values = [c.contribution for c in m.contributions]
        assert values == sorted(values, reverse=True), f"{arriving}->{other}: {values}"
        for c in m.contributions:
            assert c.contribution == pytest.approx(c.idf_weight * c.recency * c.type_boost)


def test_components_sum_to_the_raw_score_before_normalisation(graph):
    m = match(graph, "charlie", ["delta"])[0]
    raw = sum(c.contribution for c in m.contributions)
    assert raw == pytest.approx(RAW_CHARLIE_DELTA)
    assert m.score == pytest.approx(min(100, round(100 * raw / REF)))


def test_contribution_recency_is_the_min_of_the_two_edges():
    """A hub current for one person and stale for the other is only as live as the staler."""
    hub_id, label = "company:acme", "Acme"
    fresh = make_dossier("fresh", "Fresh Person", [make_hub(hub_id, label, "company", 1.0)])
    stale = make_dossier("stale", "Stale Person", [make_hub(hub_id, label, "company", 0.25)])

    graph = build_graph([fresh, stale, *filler(3)])
    forward = match(graph, "fresh", ["stale"])[0].contributions[0]
    backward = match(graph, "stale", ["fresh"])[0].contributions[0]

    assert forward.recency == 0.25
    assert backward.recency == 0.25, "min is symmetric; the arriving side must not win"
    assert forward.contribution == pytest.approx(backward.contribution)
    # The arriving person's own Hub object is the one exposed (its evidence ids resolve there).
    assert forward.hub.recency == 1.0
    assert backward.hub.recency == 0.25


# --- acceptance 5: normalisation ------------------------------------------


def test_reference_is_ln_n_over_three_times_the_best_boost(graph):
    assert reference_score(4) == pytest.approx(REF)
    assert reference_score(10) == pytest.approx(math.log(10 / 3) * 1.5)
    assert graph.graph["ref"] == pytest.approx(REF)


def test_charlie_delta_is_100_and_alpha_bravo_is_0(graph):
    assert match(graph, "charlie", ["delta"])[0].score == 100
    assert match(graph, "alpha", ["bravo"])[0].score == 0


def test_every_score_is_inside_zero_to_one_hundred(graph):
    for arriving in ("alpha", "bravo", "charlie", "delta"):
        others = [p for p in ("alpha", "bravo", "charlie", "delta") if p != arriving]
        for m in match(graph, arriving, others):
            assert 0 <= m.score <= 100, f"{arriving} -> {m.other.person_id}: {m.score}"


def test_score_is_capped_at_100_even_when_raw_beats_the_reference():
    """Two rare investor hubs are worth 2x REF; the cap must hold."""
    hubs = [
        make_hub("investor:one", "Fund One", "investor"),
        make_hub("investor:two", "Fund Two", "investor"),
    ]
    a = make_dossier("a", "Person A", hubs)
    b = make_dossier("b", "Person B", hubs)

    graph = build_graph([a, b, *filler(2)])
    m = match(graph, "a", ["b"])[0]
    raw = sum(c.contribution for c in m.contributions)
    assert raw > graph.graph["ref"]
    assert m.score == 100


def test_score_stays_in_range_when_a_hub_carries_an_out_of_range_recency():
    """``Hub.recency`` is documented 0..1 but the contract does not validate it.

    A bad extractor must not be able to push a score outside 0..100, in either direction.
    """
    for bad in (-2.0, 5.0):
        a = make_dossier("a", "A", [make_hub("company:x", "X", "company", recency=bad)])
        b = make_dossier("b", "B", [make_hub("company:x", "X", "company")])
        m = match(build_graph([a, b, *filler(3)]), "a", ["b"])[0]
        assert 0 <= m.score <= 100, f"recency {bad} produced score {m.score}"


def test_a_population_too_small_for_the_reference_does_not_divide_by_zero():
    """``REF = ln(N/3)*1.5`` is 0 at N=3 and negative below it."""
    for n_extra in range(0, 2):
        a = make_dossier("a", "A", [make_hub("company:x", "X", "company")])
        b = make_dossier("b", "B", [make_hub("company:x", "X", "company")])
        graph = build_graph([a, b, *filler(n_extra)])
        assert graph.graph["ref"] == 0.0
        m = match(graph, "a", ["b"])[0]
        assert 0 <= m.score <= 100


# --- acceptance 5: who comes back -----------------------------------------


def test_every_present_person_comes_back_including_the_zero_scorers(graph):
    matches = match(graph, "charlie", ["alpha", "bravo", "delta"])
    assert sorted(m.other.person_id for m in matches) == ["alpha", "bravo", "delta"]
    assert sorted(m.other.person_id for m in matches if m.score == 0) == ["alpha", "bravo"]


def test_the_arriving_person_is_never_in_their_own_result(graph):
    """R3 adds the arriving person to the presence set BEFORE matching, so this happens."""
    matches = match(graph, "charlie", ["alpha", "charlie", "delta"])
    assert "charlie" not in [m.other.person_id for m in matches]
    assert sorted(m.other.person_id for m in matches) == ["alpha", "delta"]


def test_matches_are_full_person_refs_not_bare_ids(graph):
    m = match(graph, "charlie", ["delta"])[0]
    assert m.other.person_id == "delta"
    assert m.other.name == "Hollis Trent", "the PersonRef must come from the dossier, not the id"


def test_a_repeated_present_id_yields_one_match(graph):
    matches = match(graph, "charlie", ["delta", "delta", "alpha"])
    assert [m.other.person_id for m in matches] == ["delta", "alpha"]


def test_an_unknown_present_id_is_skipped_rather_than_crashing(graph):
    matches = match(graph, "charlie", ["delta", "nobody-here"])
    assert [m.other.person_id for m in matches] == ["delta"]


def test_an_unknown_arriving_person_returns_zero_scores_rather_than_crashing(graph):
    matches = match(graph, "ghost", ["alpha", "delta"])
    assert sorted(m.other.person_id for m in matches) == ["alpha", "delta"]
    assert all(m.score == 0 and not m.contributions and not m.path for m in matches)


def test_match_is_deterministic_across_repeated_calls(graph):
    first = match(graph, "charlie", ["alpha", "bravo", "delta"])
    second = match(graph, "charlie", ["alpha", "bravo", "delta"])
    assert [m.model_dump() for m in first] == [m.model_dump() for m in second]


def test_ordering_is_total_even_when_rounded_scores_tie():
    """Two pairs can round to the same integer from different raw scores."""
    shared_a = make_hub("topic:one", "Topic One", "topic")
    shared_b = make_hub("topic:two", "Topic Two", "topic", recency=0.999)
    arriving = make_dossier("arriving", "Arriving", [shared_a, shared_b])
    near = make_dossier("near", "Near", [shared_b])
    far = make_dossier("far", "Far", [shared_a])

    graph = build_graph([arriving, near, far, *filler(3)])
    order = [m.other.person_id for m in match(graph, "arriving", ["near", "far"])]
    scores = {m.other.person_id: m.score for m in match(graph, "arriving", ["near", "far"])}
    assert scores["far"] == scores["near"], "this fixture is only interesting if they tie"
    assert order == ["far", "near"], "the higher RAW score must still sort first"
