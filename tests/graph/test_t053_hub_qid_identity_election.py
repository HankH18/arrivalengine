"""T-053: a QID is an IDENTITY, so the graph's tie-break may not be the alphabet.

``_canonical_hub_ids`` elects one ``hub_id`` per label group. Before this ticket the
election ran through ``_elect``, whose tie-break is ``min(..., key=lambda kv: (-kv[1],
kv[0]))`` -- most common, then lexicographic. For a type or a label that is right: every
candidate DESCRIBES the same node, one spelling has to win, and lexicographic at least
makes the winner a function of the set rather than of the order the dossiers arrived in.

For a QID it is not. A QID claims WHICH entity this is, and ``Q4242`` beating ``Q7777``
because "4" sorts before "7" is dossier order in a better costume. ``extract._hub_qid`` and
``extract._unambiguous`` (T-036) reached that conclusion one stage upstream and refuse on a
tie; extraction sees one person at a time, so a disagreement BETWEEN dossiers first becomes
visible here.

**What the arbitrary winner actually broke, measured against the unmodified module.**
Group membership is fixed by ``_identity_key`` -- the LABEL -- so the elected id never
decides who is inside a group. It decides the node's NAME, and through the convergence
``_canonical_hub_ids`` documents ("two carriers who state the SAME id under different
labels ... both elect it and pass 2 keys them to the same node") it decides whether this
group welds onto a DIFFERENT-labelled one. On the five-person corpus ``_swap_corpus``
builds, renaming the two opaque QIDs and changing nothing else moved the graph from::

    hub:wd:Q4242  label='Foundry Seed 2019'   n_carriers=2   b's evidence ['b-foundry']
    hub:wd:Q7777  label='Harborline Capital'  n_carriers=2   b's evidence ['b-harbor']
    match a->[b, c] == [('b', 100.0), ('c', 0.0)]

to::

    hub:wd:Q4242  label='Foundry Seed 2019'   n_carriers=3   b's evidence
                                                  ['b-foundry', 'b-harbor']
    match a->[b, c] == [('b', 44.0), ('c', 44.0)]

-- "Harborline Capital" stopped existing as an entity, two of B's hubs became one edge with
their evidence pooled, and every score moved. Nothing in the input changed but the spelling
of two opaque identifiers.

**Nothing here grades against a file this ticket owns.** The composed fallback id is
compared with ``extract.canonical_hub_id``, which is outside this ticket's scope and is the
function that spells that form everywhere else; everything else is a literal or the
product's own behaviour on a corpus built here.
"""

from __future__ import annotations

import itertools

import pytest
from t5_graph_helpers import filler, load_dossiers, load_raw, make_dossier, make_hub

from arrival.extract import canonical_hub_id
from arrival.graph import (
    WIKIDATA_PREFIX,
    _canonical_hub_ids,
    _elect,
    _hub_identity,
    build_graph,
    hub_node,
    match,
    person_node,
)
from arrival.util import slug

# `_elect_qid` is imported INSIDE the one test that names it directly. Importing it at
# module scope makes this whole file uncollectable against a `graph.py` that does not have
# it -- which is the module this file exists to fail against, and a collection error says
# nothing about behaviour. Everything else here is written against the PUBLIC surface for
# the same reason: `build_graph` and `match` are what a consumer sees, so these assertions
# keep their meaning whatever the internals are called.

pytestmark = pytest.mark.ticket("T-5")

FOUNDRY = "Foundry Seed 2019"
HARBOR = "Harborline Capital"
QA = "wd:Q4242"
QB = "wd:Q7777"
SLUG_FORM = "investor:foundry-seed-2019"


def _hub_nodes(graph):
    return sorted(n for n, d in graph.nodes(data=True) if d.get("kind") == "hub")


def _with_evidence(hub, *fact_ids):
    return hub.model_copy(update={"evidence_fact_ids": list(fact_ids)})


def _swap_corpus(q_for_a: str, q_for_b: str):
    """A and B disagree about ``FOUNDRY``'s QID; B and C agree about ``HARBOR``'s.

    The only difference between the two calls this module makes is WHICH of two opaque
    strings each person states. Every count, every label, every recency is identical, so a
    result that moves between them moved on the alphabet alone.
    """
    return [
        make_dossier("a", "A", [_with_evidence(make_hub(q_for_a, FOUNDRY, "investor"), "a-1")]),
        make_dossier(
            "b",
            "B",
            [
                _with_evidence(make_hub(q_for_b, FOUNDRY, "investor"), "b-foundry"),
                _with_evidence(make_hub(q_for_b, HARBOR, "investor"), "b-harbor"),
            ],
        ),
        make_dossier("c", "C", [_with_evidence(make_hub(q_for_b, HARBOR, "investor"), "c-1")]),
        *filler(2),
    ]


def _summary(graph):
    """Everything a consumer can observe, with the QID SPELLINGS abstracted away.

    Node names are reduced to their labels so the two orientations of ``_swap_corpus`` are
    comparable at all: what must not move is the graph's SHAPE -- how many entities there
    are, who carries them, whose evidence sits on which edge, and what anyone scores.
    """
    nodes = {}
    for node, data in graph.nodes(data=True):
        if data.get("kind") != "hub":
            continue
        nodes[data["label"]] = (
            data["type"],
            data["n_carriers"],
            tuple(
                sorted(
                    (person, tuple(graph.edges[person, node]["hub"].evidence_fact_ids))
                    for person in graph[node]
                )
            ),
        )
    scores = tuple((m.other.person_id, m.score) for m in match(graph, "a", ["b", "c"]))
    return nodes, scores


# --- the reproduction ------------------------------------------------------


def test_renaming_two_contested_qids_does_not_change_the_graph():
    """The regression itself. Fails on the unmodified module with the shapes quoted above."""
    one = build_graph(_swap_corpus(QA, QB))
    other = build_graph(_swap_corpus(QB, QA))
    assert _summary(one) == _summary(other), (
        "the graph moved when two opaque QIDs were renamed, so the identity of a hub was "
        f"decided by ASCII order:\n  {_summary(one)}\n  {_summary(other)}"
    )


def test_a_contested_qid_never_welds_two_labels_onto_one_node():
    for q_a, q_b in ((QA, QB), (QB, QA)):
        graph = build_graph(_swap_corpus(q_a, q_b))
        labels = sorted(d["label"] for _, d in graph.nodes(data=True) if d.get("kind") == "hub")
        assert labels == [FOUNDRY, HARBOR], (
            f"two entities became {labels} when A said {q_a} and B said {q_b}"
        )
        b_edges = [
            graph.edges[person_node("b"), node]["hub"].evidence_fact_ids
            for node in sorted(graph[person_node("b")])
        ]
        assert sorted(b_edges) == [["b-foundry"], ["b-harbor"]], (
            f"B's evidence was pooled across two entities: {b_edges}"
        )
        assert [(m.other.person_id, m.score) for m in match(graph, "a", ["b", "c"])] == [
            ("b", 100.0),
            ("c", 0.0),
        ]


def test_the_contested_group_is_named_by_neither_of_the_competing_qids():
    for q_a, q_b in ((QA, QB), (QB, QA)):
        ids = {
            d["hub_id"]
            for _, d in build_graph(_swap_corpus(q_a, q_b)).nodes(data=True)
            if d.get("kind") == "hub" and d["label"] == FOUNDRY
        }
        assert ids == {SLUG_FORM}, (
            f"the contested label was named {ids}; a QID nobody corroborated must not be "
            "adopted, and the composed form is the identity that remains"
        )


# --- refusal is cheap: the within-label join, which is the point, survives ---


def test_carriers_who_disagree_about_the_qid_still_share_the_node():
    """The cost of refusing is the CROSS-label merge and nothing else."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]),
            make_dossier("b", "B", [make_hub(QB, FOUNDRY, "investor")]),
            *filler(3),
        ]
    )
    assert len(_hub_nodes(graph)) == 1, _hub_nodes(graph)
    assert match(graph, "a", ["b"])[0].score == 100.0, (
        "refusing the QID must not split the label group -- a hub the graph splits "
        "contributes nothing to anybody"
    )


def test_the_refused_group_falls_back_to_an_id_a_carrier_actually_stated():
    """Rule 2. A stated ``{type}:{slug(label)}`` beats a composed one: no silent rename."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]),
            make_dossier("b", "B", [make_hub(QB, FOUNDRY, "investor")]),
            make_dossier("c", "C", [make_hub("company:foundry-seed-2019", FOUNDRY, "company")]),
            *filler(2),
        ]
    )
    assert _hub_nodes(graph) == [hub_node("company:foundry-seed-2019")], _hub_nodes(graph)


def test_the_composed_fallback_is_what_extract_would_have_emitted():
    """Rule 3, graded against ``extract.canonical_hub_id`` -- outside this ticket's scope.

    Refusing a QID here must land on the SAME string extraction lands on when it refuses
    one (T-036), or the two stages disagree about the identity of the same hub.
    """
    graph = build_graph(_swap_corpus(QA, QB))
    contested = [
        d for _, d in graph.nodes(data=True) if d.get("kind") == "hub" and d["label"] == FOUNDRY
    ]
    assert len(contested) == 1
    assert contested[0]["hub_id"] == canonical_hub_id(
        contested[0]["type"], contested[0]["label"], None
    )
    assert not contested[0]["hub_id"].startswith(WIKIDATA_PREFIX)


def test_a_carrier_who_never_saw_wikidata_joins_the_refused_group():
    """Why rule 3 composes rather than inventing: the composed id is the one an unresolved
    carrier states, so refusing a QID makes the group MORE joinable, not less."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]),
            make_dossier("b", "B", [make_hub(QB, FOUNDRY, "investor")]),
            make_dossier("c", "C", [make_hub(SLUG_FORM, FOUNDRY, "investor")]),
            *filler(2),
        ]
    )
    assert _hub_nodes(graph) == [hub_node(SLUG_FORM)]
    assert graph.nodes[hub_node(SLUG_FORM)]["n_carriers"] == 3
    assert match(graph, "a", ["b", "c"])[0].score == match(graph, "a", ["c"])[0].score


# --- what the election may still do ----------------------------------------


def test_a_single_qid_still_wins_however_few_carriers_state_it():
    """Refusal is for two QIDs in COMPETITION. One QID against four slug forms is not a
    competition -- the slug forms are the ABSENCE of an entity claim, not a rival one."""
    crowd = [make_dossier(x, x.upper(), [make_hub(SLUG_FORM, FOUNDRY, "investor")]) for x in "bcde"]
    graph = build_graph([make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]), *crowd])
    assert _hub_nodes(graph) == [hub_node(QA)]
    assert graph.nodes[hub_node(QA)]["n_carriers"] == 5


def test_the_better_corroborated_qid_wins_rather_than_everything_refusing():
    """Ranking on evidence, then refusing a TIE -- not refusing whenever two ids appear."""
    said_qa = [make_dossier(x, x.upper(), [make_hub(QA, FOUNDRY, "investor")]) for x in "ab"]
    said_qb = make_dossier("c", "C", [make_hub(QB, FOUNDRY, "investor")])
    graph = build_graph([*said_qa, said_qb, *filler(2)])
    assert _hub_nodes(graph) == [hub_node(QA)], (
        "two carriers corroborated Q4242 against two different people's documents and one "
        "corroborated Q7777; that is evidence, and it must decide"
    )


def test_the_qid_vote_counts_people_not_occurrences():
    """One dossier naming a QID twice is one reading repeated, not two corroborations."""
    twice = make_dossier(
        "a",
        "A",
        [
            _with_evidence(make_hub(QA, FOUNDRY, "investor"), "a-1"),
            _with_evidence(make_hub(QA, "Foundry  Seed 2019", "investor"), "a-2"),
        ],
    )
    once = make_dossier("b", "B", [make_hub(QB, FOUNDRY, "investor")])
    graph = build_graph([twice, once, *filler(3)])
    assert _hub_nodes(graph) == [hub_node(SLUG_FORM)], (
        f"{_hub_nodes(graph)}: A stated Q4242 twice in one dossier, which is one person's "
        "reading; that must not outvote B's independently corroborated Q7777"
    )


def test_two_dossiers_for_one_person_are_one_vote():
    """``build_graph`` accepts two dossiers per ``person_id`` and folds them into one leaf;
    the identity vote must fold them too."""
    twice = [make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]) for _ in range(2)]
    once = make_dossier("b", "B", [make_hub(QB, FOUNDRY, "investor")])
    graph = build_graph([*twice, once, *filler(3)])
    assert _hub_nodes(graph) == [hub_node(SLUG_FORM)], _hub_nodes(graph)


def test_an_uncontested_qid_still_merges_two_labels():
    """The merge refusal costs, priced explicitly: when the QID is NOT contested it still
    happens, so the fix removes exactly the arbitrary merges and no others."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]),
            make_dossier("b", "B", [make_hub(QA, "Foundry Capital", "investor")]),
            *filler(3),
        ]
    )
    assert _hub_nodes(graph) == [hub_node(QA)], (
        f"{_hub_nodes(graph)}: one QID under two labels is one entity, and joining it is "
        "what the election is for"
    )
    assert match(graph, "a", ["b"])[0].score == 100.0


# --- the description votes are untouched ------------------------------------


def test_elect_still_breaks_a_description_tie_lexicographically():
    """``_elect`` is the TYPE and LABEL vote and keeps its old rule exactly."""
    assert _elect({"company": 1, "investor": 1}) == "company"
    assert _elect({"investor": 2, "company": 1}) == "investor"
    assert _elect({"Zebra": 1, "Apple": 1, "Mango": 1}) == "Apple"


def test_a_type_tie_is_still_broken_lexicographically_end_to_end():
    """One carrier says ``company``, one says ``investor``; the node must still get a type
    and a boost rather than refusing, because a type is a description of one node."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, FOUNDRY, "company")]),
            make_dossier("b", "B", [make_hub(QA, FOUNDRY, "investor")]),
            *filler(3),
        ]
    )
    node = graph.nodes[hub_node(QA)]
    assert node["type"] == "company", "the type vote must not have learned to refuse"
    assert node["type_boost"] == pytest.approx(1.5)


def test_a_label_tie_is_still_broken_lexicographically():
    assert _hub_identity([("investor", "Bravo Fund"), ("investor", "Alpha Fund")]) == (
        "investor",
        "Alpha Fund",
    )


def test_elect_qid_refuses_only_a_tie():
    from arrival.graph import _elect_qid  # noqa: PLC0415 -- see the module-scope note

    assert _elect_qid({}) is None
    assert _elect_qid({QA: {"a"}}) == QA
    assert _elect_qid({QA: {"a", "b"}, QB: {"c"}}) == QA
    assert _elect_qid({QB: {"a", "b"}, QA: {"c"}}) == QB
    assert _elect_qid({QA: {"a"}, QB: {"b"}}) is None
    assert _elect_qid({QA: {"a"}, QB: {"b"}, "wd:Q1": {"c"}}) is None


# --- order independence and the no-regression guard -------------------------


def test_the_election_is_the_same_under_every_dossier_order():
    """Every permutation of a corpus that contains a contested QID, a third spelling and a
    cross-label carrier must give ONE answer."""
    people = [
        make_dossier("a", "A", [make_hub(QA, FOUNDRY, "investor")]),
        make_dossier("b", "B", [make_hub(QB, FOUNDRY, "company")]),
        make_dossier("c", "C", [make_hub(SLUG_FORM, FOUNDRY, "investor")]),
        make_dossier("d", "D", [make_hub(QB, HARBOR, "investor")]),
    ]
    answers = set()
    for order in itertools.permutations([*people, *filler(2)]):
        graph = build_graph(list(order))
        answers.add(
            (
                tuple(_hub_nodes(graph)),
                tuple(
                    (n, graph.nodes[n]["hub_id"], graph.nodes[n]["type"], graph.nodes[n]["label"])
                    for n in _hub_nodes(graph)
                ),
                tuple(m.score for m in match(graph, "a", ["b", "c", "d"])),
            )
        )
    assert len(answers) == 1, f"the election moved with dossier order: {sorted(answers)}"


def test_an_empty_label_hub_keeps_its_own_stated_id():
    """``_identity_key`` falls back to ``\\0{hub_id}`` when a label slugs to nothing, so
    such a hub is alone in its group and there is nothing to elect. The composed branch must
    never fire there -- ``{type}:`` with an empty slug would name every one of them alike."""
    graph = build_graph(
        [
            make_dossier("a", "A", [make_hub(QA, "", "investor")]),
            make_dossier("b", "B", [make_hub("topic:", "", "topic")]),
            *filler(3),
        ]
    )
    assert _hub_nodes(graph) == sorted([hub_node(QA), hub_node("topic:")]), _hub_nodes(graph)
    assert match(graph, "a", ["b"])[0].score == 0.0


def test_the_committed_corpus_is_untouched_by_the_refusal():
    raw = load_raw()
    stated = {hub["hub_id"] for dossier in raw.values() for hub in dossier["hubs"]}
    graph = build_graph(load_dossiers())
    assert {n.removeprefix("hub:") for n in _hub_nodes(graph)} == stated


def test_canonical_hub_ids_returns_one_id_per_label_group():
    """The map is total over the keys, whatever the election decided."""
    dossiers = _swap_corpus(QA, QB)
    canonical = _canonical_hub_ids(dossiers)
    keys = {slug(hub.label) for dossier in dossiers for hub in dossier.hubs}
    assert set(canonical) == keys
    assert all(isinstance(value, str) and value for value in canonical.values())
