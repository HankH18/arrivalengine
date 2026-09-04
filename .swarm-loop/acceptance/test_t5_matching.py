"""FROZEN acceptance tests for ticket T-5 -- the interest graph and matching.

Requirements graded here: R10 (the score components are exposed), SPEC S5 (a rare shared
hub outranks generic ones), DESIGN Decision 3 (IDF with a clamp at 0, type boosts, the
fixed REF normalisation, and the weighted shortest path as the "why").

Driven entirely by the five ORCHESTRATOR-OWNED dossiers in `fixtures/dossiers/`. The
expected numbers below are not taken from the spec on trust: `fixtures/CORPUS-PROOF.md`
records an independent recomputation of them from the committed JSON (heapq Dijkstra,
cross-checked against networkx), and every one of them is ALSO recomputed here, in
`_expected_raw`, from the same formula applied to the fixtures as loaded. The literals and
the recomputation must agree; if they ever stop agreeing, the fixture changed.

Product imports are inside the test bodies on purpose -- see the note in test_t4_taste.py.
"""

from __future__ import annotations

import json
import math

import pytest

pytestmark = pytest.mark.t5


# --------------------------------------------------------------------------- constants

ARRIVING = "runa-okonkwo"
PRESENT = ["mira-hollowell", "theo-baptiste", "sil-vantorre", "jem-arrowood"]

RARE_HUB_ID = "investor:foundry-seed-2019"
RARE_HUB_LABEL = "Foundry Seed 2019"

#: DESIGN Decision 3, verbatim.
TYPE_BOOST = {
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

#: The pinned outcome (frozen spec §1a; independently confirmed in CORPUS-PROOF.md).
EXPECTED_SCORES = {
    "sil-vantorre": 100,
    "jem-arrowood": 67,
    "mira-hollowell": 0,
    "theo-baptiste": 0,
}

EXPECTED_PATH_TO_SIL = [
    "person:runa-okonkwo",
    "hub:investor:foundry-seed-2019",
    "person:sil-vantorre",
]


# ---------------------------------------------------------------------------- helpers


def _dossier_dicts(frozen_fixtures) -> dict[str, dict]:
    """The five resolved dossiers, as raw JSON, keyed by person_id.

    `dossiers_unresolved/` is a SEPARATE directory precisely so the unresolved person
    never enters the graph and never perturbs N.
    """
    out = {}
    for path in sorted((frozen_fixtures / "dossiers").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["person"]["person_id"]] = raw
    assert len(out) == 5, f"expected 5 frozen dossiers, found {sorted(out)}"
    return out


def _dossiers(frozen_fixtures) -> list:
    from arrival.contracts import Dossier

    raws = _dossier_dicts(frozen_fixtures)
    return [Dossier.model_validate(raws[person_id]) for person_id in sorted(raws)]


def _idf_table(raws: dict[str, dict]) -> dict[str, float]:
    """idf(hub) = max(0, ln(N / (1 + n_people_on_hub))), recomputed from the fixtures."""
    n_people = len(raws)
    carriers: dict[str, set[str]] = {}
    for person_id, raw in raws.items():
        for hub in raw["hubs"]:
            carriers.setdefault(hub["hub_id"], set()).add(person_id)
    return {
        hub_id: max(0.0, math.log(n_people / (1 + len(people))))
        for hub_id, people in carriers.items()
    }


def _expected_raw(raws: dict[str, dict], a: str, b: str) -> float:
    """Sum of idf * min(recency_a, recency_b) * type_boost over shared hubs."""
    idf = _idf_table(raws)
    hubs_a = {h["hub_id"]: h for h in raws[a]["hubs"]}
    hubs_b = {h["hub_id"]: h for h in raws[b]["hubs"]}
    total = 0.0
    for hub_id in set(hubs_a) & set(hubs_b):
        recency = min(hubs_a[hub_id]["recency"], hubs_b[hub_id]["recency"])
        total += idf[hub_id] * recency * TYPE_BOOST[hubs_a[hub_id]["type"]]
    return total


def _ref(raws: dict[str, dict]) -> float:
    """REF = ln(N / 3) * 1.5 -- one rare hub shared by exactly two people, best boost."""
    return math.log(len(raws) / 3) * 1.5


def _match_all(frozen_fixtures, arriving=ARRIVING, present=None):
    from arrival.graph import build_graph, match

    graph = build_graph(_dossiers(frozen_fixtures))
    return match(graph, arriving, list(PRESENT if present is None else present))


def _by_person(matches) -> dict[str, object]:
    return {m.other.person_id: m for m in matches}


# ------------------------------------------------------------------------------- tests


def test_rare_hub_pair_outranks_generic_pairs_across_the_whole_ranking(frozen_fixtures):
    """SPEC S5: the rare-hub pair outranks the generic pair, and the full order is pinned."""
    matches = _match_all(frozen_fixtures)
    order = [m.other.person_id for m in matches]
    assert len(order) == 4, f"expected one Match per present person, got {order}"

    assert order[0] == "sil-vantorre", f"the rare investor hub must rank first; got {order}"
    assert order[1] == "jem-arrowood", f"the rare topic hub must rank second; got {order}"
    assert set(order[2:]) == {"mira-hollowell", "theo-baptiste"}, (
        f"the two generic-hub-only people must rank last; got {order}"
    )

    top = matches[0]
    assert top.contributions, "the winning Match exposes no score components (R10)"
    assert top.contributions[0].hub.hub_id == RARE_HUB_ID, (
        f"top contribution hub should be the rare investor hub, got "
        f"{top.contributions[0].hub.hub_id!r}"
    )


def test_generic_hubs_clamp_to_zero_for_the_mira_theo_pair(frozen_fixtures):
    """DESIGN Decision 3: ln(N/(1+n)) clamped at 0 zeroes a hub everybody carries."""
    raws = _dossier_dicts(frozen_fixtures)
    shared = {h["hub_id"] for h in raws["mira-hollowell"]["hubs"]} & {
        h["hub_id"] for h in raws["theo-baptiste"]["hubs"]
    }
    assert len(shared) == 2, f"the fixture pair must actually share hubs, found {sorted(shared)}"
    assert _expected_raw(raws, "mira-hollowell", "theo-baptiste") == 0.0

    matches = _match_all(frozen_fixtures, arriving="mira-hollowell", present=["theo-baptiste"])
    assert len(matches) == 1
    pair = matches[0]
    assert pair.score == 0, f"two people sharing only clamped hubs must score 0, got {pair.score}"
    nonzero = [(c.hub.hub_id, c.contribution) for c in pair.contributions if c.contribution != 0]
    assert not nonzero, f"every contribution for this pair must be 0, got {nonzero}"

    # Sabotage companion: a matcher that returns 0 for everything would pass the above.
    winner = _match_all(frozen_fixtures, present=["sil-vantorre"])[0]
    assert winner.score == 100, (
        f"control pair must still score 100, got {winner.score}; the zero above is meaningless "
        "if the matcher scores every pair zero"
    )


def test_scores_are_normalised_against_ref_to_100_and_67(frozen_fixtures):
    """DESIGN Decision 3: score = min(100, round(100 * raw / REF)), REF = ln(N/3) * 1.5."""
    raws = _dossier_dicts(frozen_fixtures)
    ref = _ref(raws)
    matches = _by_person(_match_all(frozen_fixtures))

    recomputed = {
        person_id: min(100, round(100 * _expected_raw(raws, ARRIVING, person_id) / ref))
        for person_id in PRESENT
    }
    assert recomputed == EXPECTED_SCORES, (
        "the fixture corpus no longer produces the pinned scores; the fixtures changed under "
        f"the frozen expectation: recomputed {recomputed}, pinned {EXPECTED_SCORES}"
    )

    actual = {person_id: matches[person_id].score for person_id in PRESENT}
    assert actual == EXPECTED_SCORES, f"scores diverged: {actual} != {EXPECTED_SCORES}"


def test_contributions_are_sorted_desc_and_sum_to_the_raw_score(frozen_fixtures):
    """R10 / T-5 acceptance 3: the exposed components are ordered and add up to the raw score."""
    raws = _dossier_dicts(frozen_fixtures)
    matches = _by_person(_match_all(frozen_fixtures))

    for person_id in PRESENT:
        m = matches[person_id]
        values = [c.contribution for c in m.contributions]
        assert values == sorted(values, reverse=True), (
            f"{person_id}: contributions are not sorted descending: {values}"
        )
        for c in m.contributions:
            assert c.contribution == pytest.approx(c.idf_weight * c.recency * c.type_boost), (
                f"{person_id}/{c.hub.hub_id}: contribution {c.contribution} != "
                f"idf {c.idf_weight} * recency {c.recency} * boost {c.type_boost}"
            )
        assert sum(values) == pytest.approx(_expected_raw(raws, ARRIVING, person_id)), (
            f"{person_id}: exposed components sum to {sum(values)}, but the raw score computed "
            f"from the fixtures is {_expected_raw(raws, ARRIVING, person_id)}"
        )


def test_path_is_the_weighted_shortest_route_through_the_top_hub(frozen_fixtures):
    """T-5 acceptance 3: `Match.path` is the cost=1/(1+idf) shortest path via the top hub."""
    top = _by_person(_match_all(frozen_fixtures))["sil-vantorre"]
    assert list(top.path) == EXPECTED_PATH_TO_SIL, (
        f"expected the rare-hub route {EXPECTED_PATH_TO_SIL}, got {list(top.path)}"
    )
    assert top.path[1] == f"hub:{top.contributions[0].hub.hub_id}", (
        "the path must run through the highest-contributing hub, not an arbitrary shared one"
    )


def test_why_is_deterministic_and_names_the_top_hub_label(frozen_fixtures):
    """T-5 acceptance 4: `Match.why` is a template naming the shared hub -- no LLM, no drift."""
    first = _by_person(_match_all(frozen_fixtures))["sil-vantorre"].why
    second = _by_person(_match_all(frozen_fixtures))["sil-vantorre"].why

    assert isinstance(first, str) and first.strip(), "Match.why is empty"
    assert first == second, (
        f"Match.why is not deterministic across two identical calls: {first!r} != {second!r}"
    )
    assert RARE_HUB_LABEL in first, (
        f"the why must name the top shared hub by label; {RARE_HUB_LABEL!r} missing from {first!r}"
    )
    assert RARE_HUB_ID not in first, (
        f"R18: the why is read aloud, so it names the hub LABEL, not the id: {first!r}"
    )


def test_arriving_person_is_absent_and_every_present_person_is_returned(frozen_fixtures):
    """T-5 acceptance 5: all present people come back, including the zero scorers; the
    arriving person never does. The <=3 cap is the digest's job (R7), not the matcher's."""
    matches = _match_all(frozen_fixtures)
    returned = [m.other.person_id for m in matches]

    assert sorted(returned) == sorted(PRESENT), (
        f"every present person must appear exactly once: got {sorted(returned)}"
    )
    zero_scorers = [m.other.person_id for m in matches if m.score == 0]
    assert sorted(zero_scorers) == ["mira-hollowell", "theo-baptiste"], (
        f"zero-scoring present people must still be returned; got {zero_scorers}"
    )

    # R3 adds the arriving person to the presence set BEFORE matching, so `present` will
    # contain them in production. They must still never match themselves.
    with_self = _match_all(frozen_fixtures, present=PRESENT + [ARRIVING])
    self_returned = [m.other.person_id for m in with_self]
    assert ARRIVING not in self_returned, (
        f"the arriving person must never appear in their own Meet list: {self_returned}"
    )
    assert sorted(self_returned) == sorted(PRESENT), (
        f"present-minus-self must be returned when the arriving person is in `present`: "
        f"got {sorted(self_returned)}"
    )


def test_hubs_whose_evidence_facts_are_excluded_still_participate_in_matching(frozen_fixtures):
    """DESIGN / T-5 acceptance 1: matching is not display, so excluded facts' hubs stay."""
    from arrival.contracts import Dossier
    from arrival.graph import build_graph, match

    raws = _dossier_dicts(frozen_fixtures)
    clean = build_graph([Dossier.model_validate(raws[k]) for k in sorted(raws)])
    clean_nodes = set(clean.nodes())
    assert f"hub:{RARE_HUB_ID}" in clean_nodes, (
        f"the rare hub is not a node in the graph: {sorted(n for n in clean_nodes)}"
    )

    # Mark every fact cited as evidence for the rare hub as taste-excluded, in memory only.
    censored = json.loads(json.dumps(raws))
    censored_ids = set()
    for person_id in ("runa-okonkwo", "sil-vantorre"):
        evidence = set()
        for hub in censored[person_id]["hubs"]:
            if hub["hub_id"] == RARE_HUB_ID:
                evidence.update(hub["evidence_fact_ids"])
        for fact in censored[person_id]["facts"]:
            if fact["fact_id"] in evidence:
                fact["excluded"] = True
                fact["exclusion_reason"] = "family"
                censored_ids.add(fact["fact_id"])
    assert censored_ids, "no evidence facts were found to censor; the fixture changed"

    censored_graph = build_graph([Dossier.model_validate(censored[k]) for k in sorted(censored)])
    assert f"hub:{RARE_HUB_ID}" in set(censored_graph.nodes()), (
        "a hub whose evidence facts are all excluded was dropped from the graph; matching is "
        "not display (DESIGN Decision 3 / T-5 acceptance 1)"
    )
    assert set(censored_graph.nodes()) == clean_nodes, (
        "excluding facts changed the graph's node set: "
        f"{sorted(set(censored_graph.nodes()) ^ clean_nodes)}"
    )

    censored_match = _by_person(match(censored_graph, ARRIVING, list(PRESENT)))
    assert censored_match["sil-vantorre"].score == 100, (
        "the rare-hub score must not depend on whether the evidence facts are displayable; got "
        f"{censored_match['sil-vantorre'].score}"
    )
