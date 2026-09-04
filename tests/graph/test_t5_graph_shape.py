"""T-5 acceptance 1: what ``build_graph`` actually returns.

A bipartite ``nx.Graph``: ``person:{id}`` leaves, ``hub:{hub_id}`` centres, per-edge
``recency``, per-hub-node ``idf`` and ``type_boost``, edge ``cost = 1/(1+idf)``. Hubs whose
evidence facts were taste-excluded stay in, because matching is not display.
"""

from __future__ import annotations

import math
import typing

import networkx as nx
import pytest
from t5_graph_helpers import (
    CLAMPED_HUB_IDS,
    RARE_HUB_ID,
    load_dossiers,
    load_raw,
    make_dossier,
    make_hub,
)

from arrival.contracts import HubType
from arrival.graph import (
    DEFAULT_TYPE_BOOST,
    TYPE_BOOST,
    build_graph,
    hub_idf,
    hub_node,
    person_node,
)

pytestmark = pytest.mark.ticket("T-5")


@pytest.fixture
def dossiers():
    return load_dossiers()


@pytest.fixture
def graph(dossiers):
    return build_graph(dossiers)


@pytest.fixture
def dossier_raw():
    return load_raw()

# N = 4 in this corpus, so a hub on 1 person is ln(4/2) and a hub on 2 is ln(4/3).
IDF_UNIQUE = math.log(4 / 2)
IDF_RARE = math.log(4 / 3)


def test_build_graph_returns_a_networkx_graph(graph):
    assert isinstance(graph, nx.Graph)
    assert not graph.is_directed(), "the interest graph is undirected; a match is symmetric"


def test_person_and_hub_nodes_use_the_pinned_naming(graph):
    people = {n for n, d in graph.nodes(data=True) if d.get("kind") == "person"}
    hubs = {n for n, d in graph.nodes(data=True) if d.get("kind") == "hub"}

    assert people == {person_node(p) for p in ("alpha", "bravo", "charlie", "delta")}
    assert hub_node(RARE_HUB_ID) in hubs
    assert people | hubs == set(graph.nodes()), "every node is either a person or a hub"
    assert all(n.startswith("person:") for n in people)
    assert all(n.startswith("hub:") for n in hubs)


def test_graph_is_bipartite_with_no_person_person_or_hub_hub_edges(graph):
    kinds = {n: d["kind"] for n, d in graph.nodes(data=True)}
    for u, v in graph.edges():
        assert {kinds[u], kinds[v]} == {"person", "hub"}, f"non-bipartite edge {u} -- {v}"
    assert nx.is_bipartite(graph)
    # The `bipartite` attribute is the networkx convention and must agree with `kind`.
    for node, data in graph.nodes(data=True):
        assert data["bipartite"] == (0 if data["kind"] == "person" else 1), node


def test_hub_nodes_carry_idf_and_type_boost_with_the_pinned_numbers(graph):
    rare = graph.nodes[hub_node(RARE_HUB_ID)]
    assert rare["idf"] == pytest.approx(IDF_RARE)
    assert rare["type_boost"] == 1.5
    assert rare["n_carriers"] == 2

    for hub_id in CLAMPED_HUB_IDS:
        node = graph.nodes[hub_node(hub_id)]
        assert node["idf"] == 0.0, f"{hub_id} is carried by everyone and must clamp to 0"
        assert node["n_carriers"] == 4

    unique = graph.nodes[hub_node("company:quillmark")]
    assert unique["idf"] == pytest.approx(IDF_UNIQUE)
    assert unique["type_boost"] == 1.5


def test_every_edge_carries_recency_cost_and_the_persons_own_hub(graph, dossiers):
    by_person = {d.person.person_id: d for d in dossiers}
    seen = 0
    for person_id, dossier in by_person.items():
        for hub in dossier.hubs:
            edge = graph.edges[person_node(person_id), hub_node(hub.hub_id)]
            assert edge["recency"] == hub.recency
            assert edge["hub"] is hub, "the edge must carry that person's own Hub object"
            idf = graph.nodes[hub_node(hub.hub_id)]["idf"]
            assert edge["cost"] == pytest.approx(1.0 / (1.0 + idf))
            seen += 1
    assert seen == graph.number_of_edges(), "every edge was accounted for by a dossier hub"


def test_a_stale_edge_keeps_its_own_recency_not_a_default(graph):
    """bravo's school hub is the one fixture edge with recency != 1.0."""
    edge = graph.edges[person_node("bravo"), hub_node("school:rio-verde-college")]
    assert edge["recency"] == 0.3


def test_idf_is_smoothed_and_clamped():
    assert hub_idf(5, 2) == pytest.approx(math.log(5 / 3))
    assert hub_idf(5, 5) == 0.0, "ln(5/6) is negative and must clamp, not go through"
    assert hub_idf(5, 1) == pytest.approx(math.log(5 / 2))
    # The unsmoothed form would be ln(N/n); it is NOT what this design uses.
    assert hub_idf(5, 2) != pytest.approx(math.log(5 / 2))
    assert hub_idf(0, 0) == 0.0, "an empty population must not raise"


def test_type_boost_covers_every_hub_type_exactly():
    """If ``HubType`` ever grows a member, this table must be taught about it."""
    declared = set(typing.get_args(HubType))
    assert set(TYPE_BOOST) == declared, f"TYPE_BOOST vs HubType: {set(TYPE_BOOST) ^ declared}"
    assert TYPE_BOOST["investor"] == TYPE_BOOST["board"] == TYPE_BOOST["company"] == 1.5
    assert TYPE_BOOST["event"] == TYPE_BOOST["cause"] == TYPE_BOOST["person"] == 1.3
    assert TYPE_BOOST["technology"] == TYPE_BOOST["topic"] == 1.0
    assert TYPE_BOOST["school"] == 0.8
    assert TYPE_BOOST["city"] == 0.5
    assert DEFAULT_TYPE_BOOST == 1.0


def test_hubs_of_excluded_facts_still_participate(dossier_raw):
    """Matching is not display: excluding a fact must not move one byte of the graph."""
    import copy

    from arrival.contracts import Dossier

    clean = build_graph([Dossier.model_validate(dossier_raw[k]) for k in sorted(dossier_raw)])

    censored = copy.deepcopy(dossier_raw)
    touched = 0
    for person_id in ("charlie", "delta"):
        evidence = {
            fid
            for hub in censored[person_id]["hubs"]
            if hub["hub_id"] == RARE_HUB_ID
            for fid in hub["evidence_fact_ids"]
        }
        for fact in censored[person_id]["facts"]:
            if fact["fact_id"] in evidence:
                fact["excluded"] = True
                fact["exclusion_reason"] = "family"
                touched += 1
    assert touched, "the fixture no longer cites any evidence fact for the rare hub"

    after = build_graph([Dossier.model_validate(censored[k]) for k in sorted(censored)])
    assert set(after.nodes()) == set(clean.nodes())
    assert set(after.edges()) == set(clean.edges())
    assert after.nodes[hub_node(RARE_HUB_ID)]["idf"] == clean.nodes[hub_node(RARE_HUB_ID)]["idf"]


def test_n_counts_person_nodes_not_dossiers():
    """Two dossiers for one person are one leaf; counting them twice deflates every idf."""
    hub = make_hub("company:acme", "Acme", "company")
    one = make_dossier("solo", "Solo Person", [hub])
    again = make_dossier("solo", "Solo Person", [hub])
    other = make_dossier("pair", "Pair Person", [hub])

    graph = build_graph([one, again, other])
    assert graph.graph["n_people"] == 2
    assert graph.nodes[hub_node("company:acme")]["idf"] == pytest.approx(hub_idf(2, 2))


def test_build_graph_accepts_an_empty_population():
    graph = build_graph([])
    assert graph.number_of_nodes() == 0
    assert graph.graph["n_people"] == 0
    assert graph.graph["ref"] == 0.0


def test_build_graph_accepts_any_iterable(dossiers):
    """A generator must not be consumed by the first pass and leave the second empty."""
    graph = build_graph(d for d in dossiers)
    assert graph.graph["n_people"] == 4
    assert graph.number_of_edges() == sum(len(d.hubs) for d in dossiers)
