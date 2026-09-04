"""T-010(b): two people who share a hub must share its NODE, however they spell its id.

``Hub.hub_id`` is ``"wd:Q123"`` when Wikidata resolved the entity and ``"{type}:{slug(label)}"``
otherwise. Both halves of that rule depend on things the evidence does not fix:

* the **type** the model chose for the hub -- the same fund is an ``investor`` to one
  extraction and a ``company`` to another;
* whether a **Wikidata document happened to be retrieved for that particular person** --
  a per-person accident of search, not a property of the hub.

Either disagreement makes one real hub into two graph nodes, so the pair shares nothing and
scores 0 -- silently, and for exactly the case the matching engine exists to find. One
``extract()`` call sees one person and has nothing to reconcile against; ``build_graph`` is
the first thing in the pipeline that sees them all, so the reconciliation belongs here.

Measured before the fix, end to end:

    both dossiers say investor        -> score 100.0
    investor vs company, same entity  -> score   0.0, path=[]
    one dossier had a Wikidata doc    -> score   0.0, path=[]

Nothing here compares against a stored answer: every assertion is about the product's own
behaviour on a corpus this module builds.
"""

from __future__ import annotations

import itertools

import pytest
from t5_graph_helpers import (
    RARE_HUB_ID,
    RARE_HUB_LABEL,
    filler,
    load_dossiers,
    load_raw,
    make_dossier,
    make_hub,
)

from arrival.graph import build_graph, hub_node, match, person_node

pytestmark = pytest.mark.ticket("T-5")

LABEL = RARE_HUB_LABEL
QID = "wd:Q4242"


def _pair(hub_a, hub_b, extra=()):
    return build_graph(
        [
            make_dossier("a", "Person A", [hub_a]),
            make_dossier("b", "Person B", [hub_b]),
            *extra,
            *filler(3),
        ]
    )


def _hub_nodes(graph):
    return sorted(n for n, d in graph.nodes(data=True) if d.get("kind") == "hub")


# --- the three shapes that used to score 0 ---------------------------------


def test_two_carriers_who_disagree_about_the_type_still_share_the_hub():
    graph = _pair(
        make_hub(RARE_HUB_ID, LABEL, "investor"),
        make_hub("company:foundry-seed-2019", LABEL, "company"),
    )
    assert len(_hub_nodes(graph)) == 1, (
        f"one real hub became {len(_hub_nodes(graph))} nodes: {_hub_nodes(graph)}"
    )
    m = match(graph, "a", ["b"])[0]
    assert m.score == 100.0, f"the pair scored {m.score}; a type disagreement is not a hub"
    assert LABEL in m.why
    assert m.path == [person_node("a"), _hub_nodes(graph)[0], person_node("b")]


def test_a_carrier_whose_search_happened_to_find_wikidata_still_shares_the_hub():
    graph = _pair(
        make_hub(RARE_HUB_ID, LABEL, "investor"),
        make_hub(QID, LABEL, "investor"),
    )
    assert _hub_nodes(graph) == [hub_node(QID)], (
        "a resolved QID names the entity, so it must win the election: "
        f"{_hub_nodes(graph)}"
    )
    assert match(graph, "a", ["b"])[0].score == 100.0


def test_a_qid_wins_however_few_carriers_state_it():
    """One carrier of five, against four who only ever saw the slug form."""
    slug_form = make_hub(RARE_HUB_ID, LABEL, "investor")
    crowd = [make_dossier(x, x.upper(), [slug_form]) for x in ("c", "d", "e")]
    graph = _pair(slug_form, make_hub(QID, LABEL, "investor"), extra=crowd)
    assert _hub_nodes(graph) == [hub_node(QID)]
    assert graph.nodes[hub_node(QID)]["n_carriers"] == 5


# --- what the election may and may not do ----------------------------------


def test_the_elected_id_is_always_one_a_carrier_actually_stated():
    """Nothing is recomputed from the label, so no node is silently renamed."""
    stated = {RARE_HUB_ID, "company:foundry-seed-2019"}
    graph = _pair(
        make_hub(RARE_HUB_ID, LABEL, "investor"),
        make_hub("company:foundry-seed-2019", LABEL, "company"),
    )
    elected = graph.nodes[_hub_nodes(graph)[0]]["hub_id"]
    assert elected in stated, f"{elected!r} was invented; carriers stated {sorted(stated)}"


def test_the_contribution_reports_the_elected_id_not_the_carriers_own_spelling():
    """Consumers read ``HubContribution.hub.hub_id`` and compare it with the node name --
    the frozen T-5 suite does exactly that, at ``path[1]``."""
    graph = _pair(
        make_hub("company:foundry-seed-2019", LABEL, "company"),
        make_hub(QID, LABEL, "investor"),
    )
    for arriving in ("a", "b"):
        m = match(graph, arriving, ["b" if arriving == "a" else "a"])[0]
        assert m.contributions[0].hub.hub_id == QID
        assert m.path[1] == hub_node(m.contributions[0].hub.hub_id)


def test_the_arriving_persons_own_evidence_facts_survive_the_election():
    """``HubContribution.hub`` is the ARRIVING person's Hub: its evidence ids resolve only in
    the arriving dossier, so the election may rewrite the id and nothing else."""
    mine = make_hub(RARE_HUB_ID, LABEL, "investor")
    mine = mine.model_copy(update={"evidence_fact_ids": ["a-1", "a-2"]})
    theirs = make_hub(QID, LABEL, "investor").model_copy(
        update={"evidence_fact_ids": ["b-9"]}
    )
    graph = _pair(mine, theirs)
    contribution = match(graph, "a", ["b"])[0].contributions[0]
    assert contribution.hub.hub_id == QID
    assert contribution.hub.evidence_fact_ids == ["a-1", "a-2"]
    assert contribution.hub.label == LABEL


def test_the_reported_hub_agrees_with_the_node_the_boost_came_from():
    """R10: the digest prints `hub.label` and `hub.type` in the same row as `type_boost`.

    `src/arrival/web/templates/digest.html:60-63` renders `c.hub.type` beside
    `c.type_boost`, and the boost is computed from the ELECTED type. A contribution keeping
    its carrier's own dissenting type therefore renders "city" beside a boost of 1.5 --
    exposed reasoning that does not add up, in the one block whose whole job is to show the
    working. The graph elects one identity per hub, and every edge into that node reports it.
    """
    graph = _pair(
        make_hub(QID, LABEL, "city"),
        make_hub(QID, "Foundry Seed 2019 Fund", "investor"),
    )
    node = graph.nodes[hub_node(QID)]
    for arriving in ("a", "b"):
        c = match(graph, arriving, ["b" if arriving == "a" else "a"])[0].contributions[0]
        assert (c.hub.hub_id, c.hub.type, c.hub.label) == (
            node["hub_id"],
            node["type"],
            node["label"],
        ), f"{arriving}'s contribution reports {c.hub} but the node it scored is {node}"
        assert c.type_boost == pytest.approx(
            {"city": 0.5, "investor": 1.5}[node["type"]]
        ), "the boost and the reported type must be the same type"


def test_a_carrier_who_already_agrees_with_the_election_keeps_their_own_hub_object():
    """The ordinary case must not pay for the rare one -- and `test_t5_graph_shape`'s
    `edge["hub"] is hub` identity assertion depends on it."""
    shared = make_hub(RARE_HUB_ID, LABEL, "investor")
    graph = _pair(shared, shared)
    assert graph.edges[person_node("a"), hub_node(RARE_HUB_ID)]["hub"] is shared
    assert graph.edges[person_node("b"), hub_node(RARE_HUB_ID)]["hub"] is shared


def test_hubs_with_different_labels_are_not_merged():
    """The election joins spellings of one hub; it must not join two hubs."""
    graph = _pair(
        make_hub("investor:foundry-seed-2019", "Foundry Seed 2019", "investor"),
        make_hub("investor:harborline-capital", "Harborline Capital", "investor"),
    )
    assert len(_hub_nodes(graph)) == 2, _hub_nodes(graph)
    assert match(graph, "a", ["b"])[0].score == 0.0


def test_one_dossier_listing_the_same_hub_under_two_ids_becomes_one_edge():
    """The within-dossier half, as a safety net: it is ``extract``'s job, but a dossier that
    slipped through must not make one person count twice as a carrier."""
    twice = make_dossier(
        "twice",
        "Twice",
        [
            make_hub(RARE_HUB_ID, LABEL, "investor", recency=0.4).model_copy(
                update={"evidence_fact_ids": ["f1"]}
            ),
            make_hub(QID, LABEL, "investor", recency=0.9).model_copy(
                update={"evidence_fact_ids": ["f2"]}
            ),
        ],
    )
    once = make_dossier("once", "Once", [make_hub(QID, LABEL, "investor")])
    graph = build_graph([twice, once, *filler(3)])

    assert _hub_nodes(graph) == [hub_node(QID)]
    assert graph.number_of_edges() == 2, "a hub listed twice is one edge, not two"
    assert graph.nodes[hub_node(QID)]["n_carriers"] == 2, "one person is one carrier"
    edge = graph.edges[person_node("twice"), hub_node(QID)]
    assert edge["recency"] == 0.9, "the freshest of the folded entries wins"
    assert edge["hub"].hub_id == QID
    assert sorted(edge["hub"].evidence_fact_ids) == ["f1", "f2"], "no evidence is dropped"


# --- determinism and the no-regression guard -------------------------------


def test_the_election_does_not_depend_on_the_order_the_dossiers_arrive_in():
    hubs = [
        make_hub(RARE_HUB_ID, LABEL, "investor"),
        make_hub("company:foundry-seed-2019", LABEL, "company"),
        make_hub(QID, LABEL, "city"),
    ]
    people = [make_dossier(x, x.upper(), [h]) for x, h in zip("abc", hubs, strict=True)]
    answers = set()
    for order in itertools.permutations([*people, *filler(2)]):
        graph = build_graph(list(order))
        node = graph.nodes[_hub_nodes(graph)[0]]
        answers.add(
            (node["hub_id"], node["type"], node["label"], node["n_carriers"])
            + tuple(m.score for m in match(graph, "a", ["b", "c"]))
        )
    assert len(answers) == 1, f"the election moved with dossier order: {sorted(answers)}"


def test_the_unit_corpus_is_untouched_by_the_election():
    """A no-op on any corpus whose carriers already agree -- which is every committed one.

    The frozen grading corpus is in the same shape (every carrier of the rare hub says
    ``investor``, none carries a QID), so its pinned node name and its pinned 100/67/0/0 do
    not move either.
    """
    raw = load_raw()
    stated = {hub["hub_id"] for dossier in raw.values() for hub in dossier["hubs"]}
    graph = build_graph(load_dossiers())
    assert {n.removeprefix("hub:") for n in _hub_nodes(graph)} == stated, (
        "the election renamed a node on a corpus where every carrier already agreed"
    )
    assert hub_node(RARE_HUB_ID) in _hub_nodes(graph)
    assert graph.nodes[hub_node(RARE_HUB_ID)]["n_carriers"] == 2
