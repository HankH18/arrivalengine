"""T-5 acceptance 3 and 4: the graph path as the "why", and the deterministic sentence.

``Match.path`` is the weighted shortest route under ``cost = 1/(1+idf)`` and runs through
the highest-contributing shared hub. ``Match.why`` is a template, never an LLM call, and
under R18 it is read aloud in a lobby: labels, not ids, and no parentheticals.
"""

from __future__ import annotations

import ast
import inspect
import math

import networkx as nx
import pytest
from t5_graph_helpers import (
    RARE_HUB_ID,
    RARE_HUB_LABEL,
    filler,
    fixture_graph,
    make_dossier,
    make_hub,
)

import arrival.graph as graph_module
from arrival.graph import build_graph, hub_node, match, person_node

pytestmark = pytest.mark.ticket("T-5")


@pytest.fixture
def graph():
    return fixture_graph()


def _path_cost(g, path):
    # Not strict=True: `path[1:]` is one shorter than `path` by construction.
    return sum(g.edges[u, v]["cost"] for u, v in zip(path, path[1:], strict=False))


# --- the path -------------------------------------------------------------


def test_path_runs_person_hub_person_through_the_rare_hub(graph):
    m = match(graph, "charlie", ["delta"])[0]
    assert list(m.path) == [
        person_node("charlie"),
        hub_node(RARE_HUB_ID),
        person_node("delta"),
    ]


def test_path_always_passes_through_the_top_contributing_hub(graph):
    # justify-test-edit (T-016). The quantifier was "every pair"; it is now "every pair
    # that HAS a contributing hub", and the pairs it used to cover wrongly are asserted
    # directly below instead. The old form was wrong independently of any implementation:
    # it contradicted `test_why_never_names_a_hub_that_contributed_nothing` in this same
    # file -- `why` refuses to name a hub the clamp zeroed, while this demanded `path` run
    # through exactly such a hub whenever it happened to sort first. Both cannot be "the
    # picture of the why" (T-5 acceptance 3). Reproduced on the FROZEN corpus, so the
    # contradiction is in the product and not in this fixture: runa-okonkwo ->
    # mira-hollowell answered why='Nothing in common on the record yet.' beside
    # path=['person:runa-okonkwo', 'hub:city:austin', 'person:mira-hollowell'].
    covered = 0
    for arriving in ("alpha", "bravo", "charlie", "delta"):
        others = [p for p in ("alpha", "bravo", "charlie", "delta") if p != arriving]
        for m in match(graph, arriving, others):
            if not any(c.contribution > 0 for c in m.contributions):
                continue
            covered += 1
            assert m.path[0] == person_node(arriving)
            assert m.path[-1] == person_node(m.other.person_id)
            assert m.path[1] == hub_node(m.contributions[0].hub.hub_id), (
                f"{arriving} -> {m.other.person_id}: path {m.path} skips the top hub"
            )
    assert covered == 2, (
        "the corpus must still contain a scoring pair in both directions, or the assertion "
        f"above is vacuous; covered {covered}"
    )


def test_path_is_empty_when_no_shared_hub_contributed_anything(graph):
    """T-016: the path is the picture of the why, so it may not name what the why denies.

    The complement of the assertion above, added with it. alpha and bravo share Austin and
    Machine learning, both carried by all four people and both clamped to 0 -- `why` says so
    ("Nothing in common on the record yet."), and the path must not then contradict it by
    routing through Austin.
    """
    covered = 0
    for arriving in ("alpha", "bravo", "charlie", "delta"):
        others = [p for p in ("alpha", "bravo", "charlie", "delta") if p != arriving]
        for m in match(graph, arriving, others):
            if any(c.contribution > 0 for c in m.contributions):
                continue
            covered += 1
            assert m.contributions, "this pair is only interesting if it DOES share hubs"
            assert m.path == [], (
                f"{arriving} -> {m.other.person_id}: why={m.why!r} names nothing, but "
                f"path={m.path} names {m.path[1] if len(m.path) > 1 else '?'}"
            )
            assert "Austin" not in m.why and "Machine learning" not in m.why
    assert covered == 10, f"expected 10 zero-scoring ordered pairs in this corpus, got {covered}"


def test_path_is_the_cheapest_route_under_the_cost_weight(graph):
    """Independent check: networkx's own Dijkstra agrees on this corpus."""
    m = match(graph, "charlie", ["delta"])[0]
    expected = nx.shortest_path(
        graph, person_node("charlie"), person_node("delta"), weight="cost"
    )
    assert list(m.path) == list(expected)
    assert _path_cost(graph, m.path) == pytest.approx(2 / (1 + math.log(4 / 3)))
    # ... and it beats the clamped-hub routes, whose edges cost 1/(1+0) = 1 each.
    assert _path_cost(graph, m.path) < 2.0


def test_the_top_hub_wins_when_it_is_not_the_cheapest_edge():
    """A deliberate divergence, documented in ``_path``.

    The cheapest edge maximises ``idf`` alone; the top contribution maximises
    ``idf * recency * type_boost``. Here a rare SCHOOL (boost 0.8) has the cheaper edge
    while a commoner INVESTOR (boost 1.5) is the bigger contributor. The path is the
    picture of the why, so it must show the investor.
    """
    school = make_hub("school:rare-poly", "Rare Polytechnic", "school")
    investor = make_hub("investor:common-fund", "Common Fund", "investor")
    a = make_dossier("a", "Person A", [school, investor])
    b = make_dossier("b", "Person B", [school, investor])
    also = [make_dossier(x, x.upper(), [investor]) for x in ("c", "d")]

    g = build_graph([a, b, *also, *filler(6)])
    assert g.graph["n_people"] == 10
    assert g.nodes[hub_node("school:rare-poly")]["idf"] > (
        g.nodes[hub_node("investor:common-fund")]["idf"]
    ), "the fixture is only interesting if the school edge is the cheaper one"

    m = match(g, "a", ["b"])[0]
    assert m.contributions[0].hub.hub_id == "investor:common-fund"
    assert m.path[1] == hub_node("investor:common-fund")

    plain = nx.shortest_path(g, person_node("a"), person_node("b"), weight="cost")
    assert plain[1] == hub_node("school:rare-poly"), (
        "this test asserts a deliberate divergence; if the plain shortest path already "
        "runs through the top hub the fixture has stopped exercising it"
    )


def test_path_for_a_pair_with_no_shared_hub_at_all():
    a = make_dossier("a", "Person A", [make_hub("company:one", "One", "company")])
    b = make_dossier("b", "Person B", [make_hub("company:two", "Two", "company")])

    g = build_graph([a, b, *filler(2)])
    m = match(g, "a", ["b"])[0]
    assert m.score == 0
    assert m.contributions == []
    assert m.path == [], "disconnected people have no route, and none is invented"


# --- the why --------------------------------------------------------------


def test_why_names_the_top_hub_by_label_and_never_by_id(graph):
    why = match(graph, "charlie", ["delta"])[0].why
    assert RARE_HUB_LABEL in why
    assert RARE_HUB_ID not in why
    assert "investor:" not in why and "hub:" not in why


def test_why_is_deterministic(graph):
    assert match(graph, "charlie", ["delta"])[0].why == match(graph, "charlie", ["delta"])[0].why


def test_why_is_speakable_under_r18(graph):
    """No URLs, no parentheticals, no scores, one sentence."""
    for arriving in ("alpha", "bravo", "charlie", "delta"):
        others = [p for p in ("alpha", "bravo", "charlie", "delta") if p != arriving]
        for m in match(graph, arriving, others):
            why = m.why
            assert why.strip() == why and why, f"empty or padded why: {why!r}"
            assert why[0].isupper(), why
            assert why.endswith("."), why
            assert "(" not in why and ")" not in why, f"parenthetical in a spoken line: {why}"
            assert "http" not in why, why
            assert str(int(m.score)) not in why or m.score == 0, f"score leaked into {why!r}"


def test_why_never_names_a_hub_that_contributed_nothing(graph):
    """charlie and delta also share Austin; claiming it would be a lie about the score."""
    why = match(graph, "charlie", ["delta"])[0].why
    assert "Austin" not in why
    assert "Machine learning" not in why


def test_why_names_at_most_two_hubs():
    hubs = [
        make_hub("investor:one", "Fund Alpha", "investor"),
        make_hub("investor:two", "Fund Beta", "investor"),
        make_hub("investor:three", "Fund Gamma", "investor", recency=0.5),
    ]
    a = make_dossier("a", "Person A", hubs)
    b = make_dossier("b", "Person B", hubs)

    why = match(build_graph([a, b, *filler(3)]), "a", ["b"])[0].why
    assert "Fund Alpha" in why and "Fund Beta" in why
    assert "Fund Gamma" not in why, "the third hub is real but the template names at most two"


def test_why_for_a_pair_with_nothing_worth_sharing(graph):
    why = match(graph, "alpha", ["bravo"])[0].why
    assert why and why.endswith(".")
    assert "Austin" not in why and "Machine learning" not in why


def test_why_phrasing_varies_by_hub_type():
    """A school is 'came through', an investor is 'backed by'; the sentence reads as English."""
    seen = set()
    for hub_type, label in (("school", "Bellhaven"), ("investor", "Foundry"), ("city", "Austin")):
        hub = make_hub(f"{hub_type}:x", label, hub_type)
        a = make_dossier("a", "A", [hub])
        b = make_dossier("b", "B", [hub])
        why = match(build_graph([a, b, *filler(3)]), "a", ["b"])[0].why
        assert label in why
        seen.add(why.replace(label, "{}"))
    assert len(seen) == 3, f"every hub type must get its own phrasing, got {seen}"


# --- the non-goal ---------------------------------------------------------


def test_the_matcher_makes_no_llm_call_and_imports_no_llm():
    """T-5 non-goal: no LLM, no embeddings. Enforced against the module's own imports."""
    tree = ast.parse(inspect.getsource(graph_module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {name for name in imported if "llm" in name or "anthropic" in name}
    assert not forbidden, f"the matcher must not reach for a model: {forbidden}"
    assert "arrival.contracts" in imported and "networkx" in imported
