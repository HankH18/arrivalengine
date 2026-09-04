"""The interest graph and the matcher (T-5).

The user's interest-graph idea, made concrete. People are leaves, hubs are centres, and a
person-to-person score is the sum of what their SHARED hubs are worth. Nothing here is an
LLM call, an embedding, or a heuristic that cannot be printed on the digest page: R10
demands the components be visible, and S5 demands a rare shared hub beat a generic one.

The arithmetic, from DESIGN Decision 3, in the order it is applied:

* ``idf = max(0, ln(N / (1 + n)))`` -- N is the number of people in the graph, n the number
  of people carrying that hub. The denominator is **smoothed** (``1 + n``, not ``n``): a hub
  on 2 of 5 people is ``ln(5/3) = 0.5108``, and the clamp at 0 makes a hub everybody carries
  ("Austin", "Remote work") worth exactly nothing rather than slightly negative.
* ``type_boost`` from :data:`TYPE_BOOST` -- an investor beats a city by 3x at equal rarity.
* ``recency`` for a PAIR is the ``min`` of the two people's own edge recencies. A hub that is
  current for one person and stale for the other is only as live as the staler side.
* ``contribution = idf * recency * type_boost``, and ``raw`` is their sum over shared hubs.
* ``score = min(100, round(100 * raw / REF))`` with ``REF = ln(N / 3) * 1.5`` -- one rare hub
  shared by exactly two people, best type boost, full recency. The reference is FIXED rather
  than relative to the night's best pair, so a 100 means the same thing at every arrival.
* the "why" path is the shortest path under edge ``cost = 1/(1+idf)``, so the cheapest route
  between two people runs through their rarest shared hub.

Two things this module deliberately does NOT do:

* **It does not filter hubs.** Hubs whose evidence facts were taste-excluded still take part;
  matching is not display (T-5 acceptance 1). Stop-hub suppression is likewise an emit-time
  concern for the extractor, not a graph-time one -- and the stop list matches hub LABELS,
  never a type prefix, so ``investor:foundry-seed-2019`` is a legitimate hub and dropping it
  would delete the very signal this design rests on.
* **It does not cap the result.** ``match`` returns one :class:`~arrival.contracts.Match` per
  present person, zero scorers included. The <=3 cap belongs to the digest (R7).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import networkx as nx

from arrival.contracts import Dossier, HubContribution, Match, PersonRef

__all__ = [
    "DEFAULT_TYPE_BOOST",
    "PERSON_PREFIX",
    "REF_SHARERS",
    "REF_TYPE_BOOST",
    "TYPE_BOOST",
    "build_graph",
    "hub_idf",
    "hub_node",
    "match",
    "person_node",
    "reference_score",
]

#: DESIGN Decision 3: investor/board/company 1.5, event/cause/person 1.3,
#: technology/topic 1.0, school 0.8, city 0.5. Keyed by ``HubType``; DESIGN spells the 1.3
#: bucket "collaborator-person", which is the ``person`` member of the Literal.
TYPE_BOOST: dict[str, float] = {
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

#: Used only if ``HubType`` ever grows a member this table has not been taught. Neutral on
#: purpose: an unknown type should neither be silently deleted nor silently promoted.
DEFAULT_TYPE_BOOST = 1.0

#: The reference pair REF describes: one hub carried by exactly two people (so ``1 + n == 3``)
#: with the highest type boost and full recency.
REF_SHARERS = 3
REF_TYPE_BOOST = 1.5

PERSON_PREFIX = "person:"
HUB_PREFIX = "hub:"

#: Deterministic, speakable phrasing per hub type (R18: read aloud, so no ids, no
#: parentheticals, no scores). ``{label}`` is the hub's own label, verbatim.
_WHY_PHRASE: dict[str, str] = {
    "investor": "both backed by {label}",
    "board": "both on the board at {label}",
    "company": "both connected to {label}",
    "school": "both came through {label}",
    "city": "both rooted in {label}",
    "topic": "both deep in {label}",
    "technology": "both building on {label}",
    "event": "both regulars at {label}",
    "cause": "both behind {label}",
    "person": "both know {label}",
}
_WHY_FALLBACK = "both connected to {label}"

#: What a pair with no shared hub worth anything gets. Still a plain spoken sentence.
_WHY_NOTHING_SHARED = "Nothing in common on the record yet."

#: How many hubs the why names at most (T-5 acceptance 4).
_WHY_MAX_HUBS = 2


# --------------------------------------------------------------------- node naming


def person_node(person_id: str) -> str:
    """The graph node id for a person: ``person:{person_id}``."""
    return f"{PERSON_PREFIX}{person_id}"


def hub_node(hub_id: str) -> str:
    """The graph node id for a hub: ``hub:{hub_id}``.

    ``hub_id`` already carries its own ``{type}:`` prefix (or ``wd:``), so the node name is
    doubly prefixed on purpose -- ``hub:investor:foundry-seed-2019``.
    """
    return f"{HUB_PREFIX}{hub_id}"


# ------------------------------------------------------------------------ the maths


def hub_idf(n_people: int, n_carriers: int) -> float:
    """``max(0, ln(N / (1 + n)))`` -- DESIGN Decision 3, smoothed and clamped.

    >>> round(hub_idf(5, 2), 6)      # rare: two of five people
    0.510826
    >>> hub_idf(5, 5)                # everybody: ln(5/6) < 0, clamped
    0.0
    """
    if n_people <= 0 or n_carriers < 0:
        return 0.0
    return max(0.0, math.log(n_people / (1 + n_carriers)))


def reference_score(n_people: int) -> float:
    """``REF = ln(N / 3) * 1.5``: one rare hub on two people, best boost, full recency.

    Returns 0.0 for a population too small for the reference to be meaningful (``N <= 3``),
    which :func:`match` reads as "any overlap at all is a full-strength match".
    """
    if n_people < REF_SHARERS:
        return 0.0
    return math.log(n_people / REF_SHARERS) * REF_TYPE_BOOST


# ------------------------------------------------------------------------- building


def _hub_identity(descriptions: Sequence[tuple[str, str]]) -> tuple[str, str]:
    """Pick one ``(type, label)`` for a hub its carriers describe inconsistently.

    Most common wins, ties broken lexicographically, so the answer is the same however the
    dossiers were ordered. Order independence is the point: an ``add_node`` that skipped
    when the node already existed let a filesystem glob order decide a hub's type boost --
    measured, one pair scored 100 or 33 depending only on which dossier was read first.
    """
    types: dict[str, int] = {}
    labels: dict[str, int] = {}
    for hub_type, label in descriptions:
        types[hub_type] = types.get(hub_type, 0) + 1
        labels[label] = labels.get(label, 0) + 1
    best_type = min(types.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    best_label = min(labels.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return best_type, best_label


def build_graph(dossiers: Iterable[Dossier]) -> nx.Graph:
    """Build the bipartite person/hub graph.

    Nodes are ``person:{person_id}`` (``bipartite=0``, carrying the :class:`PersonRef` under
    ``person``) and ``hub:{hub_id}`` (``bipartite=1``, carrying ``idf``, ``type_boost``,
    ``label`` and ``type``). Every edge carries that person's own ``recency`` for the hub,
    the ``cost = 1/(1+idf)`` used by the path search, and the person's own :class:`Hub`
    object under ``hub`` -- because :class:`HubContribution` must expose the ARRIVING
    person's Hub, whose ``evidence_fact_ids`` resolve in the arriving dossier.

    Every dossier handed in becomes a person node; nothing is filtered here. Callers decide
    who belongs in the population (an unresolved dossier must be left out by the caller, or
    it perturbs N for everyone).

    The result does not depend on the ORDER the dossiers arrive in. That matters because a
    caller loading a directory gets whatever order the filesystem hands back: two people
    disagreeing about one hub's type -- only possible for a ``wd:`` id, since the
    ``{type}:{slug(label)}`` form encodes the type in the id -- would otherwise decide the
    type boost by glob order and move the score. See :func:`_hub_identity`.
    """
    dossiers = list(dossiers)
    graph = nx.Graph()

    # Pass 1: who carries what, and how each hub describes itself. IDF needs the whole
    # population before any edge weight is meaningful, so nothing can be written until every
    # dossier has been seen.
    carriers: dict[str, set[str]] = {}
    described: dict[str, list[tuple[str, str]]] = {}
    recencies: dict[tuple[str, str], float] = {}
    for dossier in dossiers:
        person_id = dossier.person.person_id
        for hub in dossier.hubs:
            carriers.setdefault(hub.hub_id, set()).add(person_id)
            described.setdefault(hub.hub_id, []).append((hub.type, hub.label))
            # A dossier that lists one hub twice keeps its FRESHEST recency, rather than
            # whichever entry happened to be written last.
            key = (person_id, hub.hub_id)
            recencies[key] = max(recencies.get(key, hub.recency), hub.recency)

    for dossier in dossiers:
        person = dossier.person
        graph.add_node(
            person_node(person.person_id),
            bipartite=0,
            kind="person",
            person_id=person.person_id,
            person=person,
        )

    # N is the number of PERSON NODES, not the number of dossiers: two dossiers for the same
    # person_id are one leaf, and counting them twice would deflate every idf in the graph.
    n_people = sum(1 for _, data in graph.nodes(data=True) if data.get("kind") == "person")

    # Pass 2: hub nodes and the edges, now that N is known.
    for dossier in dossiers:
        person_id = dossier.person.person_id
        source = person_node(person_id)
        for hub in dossier.hubs:
            hub_type, label = _hub_identity(described[hub.hub_id])
            idf = hub_idf(n_people, len(carriers[hub.hub_id]))
            boost = TYPE_BOOST.get(hub_type, DEFAULT_TYPE_BOOST)
            target = hub_node(hub.hub_id)
            if target not in graph:
                graph.add_node(
                    target,
                    bipartite=1,
                    kind="hub",
                    hub_id=hub.hub_id,
                    label=label,
                    type=hub_type,
                    idf=idf,
                    type_boost=boost,
                    n_carriers=len(carriers[hub.hub_id]),
                )
            graph.add_edge(
                source,
                target,
                recency=recencies[(person_id, hub.hub_id)],
                cost=1.0 / (1.0 + idf),
                hub=hub,
            )

    graph.graph["n_people"] = n_people
    graph.graph["ref"] = reference_score(n_people)
    return graph


# ------------------------------------------------------------------------ scoring


def _contributions(graph: nx.Graph, arriving: str, other: str) -> list[HubContribution]:
    """The per-shared-hub components, sorted by contribution descending.

    Every shared hub is reported, including the ones the clamp zeroed: R10 asks what the
    score is MADE of, and "we do share Austin, and Austin is worth nothing" is part of that
    answer. Ties keep hub-id order, so the list is stable across runs.
    """
    if arriving not in graph or other not in graph:
        return []

    shared = sorted(set(graph[arriving]) & set(graph[other]))
    components: list[HubContribution] = []
    for node in shared:
        data = graph.nodes[node]
        if data.get("kind") != "hub":
            continue  # a person-person edge is not part of this design; ignore it defensively
        idf = float(data["idf"])
        boost = float(data["type_boost"])
        recency = min(
            float(graph.edges[arriving, node]["recency"]),
            float(graph.edges[other, node]["recency"]),
        )
        components.append(
            HubContribution(
                hub=graph.edges[arriving, node]["hub"],  # the ARRIVING person's Hub object
                idf_weight=idf,
                recency=recency,
                type_boost=boost,
                contribution=idf * recency * boost,
            )
        )
    components.sort(key=lambda c: -c.contribution)
    return components


def _normalise(raw: float, ref: float) -> float:
    """``min(100, round(100 * raw / REF))``, floored at 0."""
    if raw <= 0:
        return 0.0
    if ref <= 0:
        # Too small a population for REF to mean anything: any overlap is the best there is.
        return 100.0
    return float(max(0, min(100, round(100 * raw / ref))))


def _path(
    graph: nx.Graph, arriving: str, other: str, components: Sequence[HubContribution]
) -> list[str]:
    """The weighted shortest route between two people, through their top hub.

    ``cost = 1/(1+idf)`` already makes the rarest shared hub the cheapest crossing, so on any
    ordinary corpus the plain shortest path IS the route through the top contributor. The two
    can diverge when a high-idf hub carries a low type boost -- the top CONTRIBUTION maximises
    ``idf * recency * boost`` while the cheapest edge only maximises ``idf``. When they
    disagree the top hub wins, because the path is the picture of the why and must show the
    reason the pair actually scored (T-5 acceptance 3); it is still the shortest path among
    those that pass through that hub.
    """
    if arriving not in graph or other not in graph:
        return []
    try:
        if components:
            via = hub_node(components[0].hub.hub_id)
            head = nx.shortest_path(graph, arriving, via, weight="cost")
            tail = nx.shortest_path(graph, via, other, weight="cost")
            return list(head) + list(tail[1:])
        return list(nx.shortest_path(graph, arriving, other, weight="cost"))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def _why(components: Sequence[HubContribution]) -> str:
    """A deterministic sentence naming up to two top hubs by LABEL. No LLM, ever.

    R18: this is read aloud to a host in a lobby, so it is a plain sentence -- no hub ids, no
    parentheticals, no scores. Only hubs that actually contributed are named: citing a hub the
    clamp zeroed would claim credit for a connection worth nothing.
    """
    named = [c for c in components if c.contribution > 0][:_WHY_MAX_HUBS]
    if not named:
        return _WHY_NOTHING_SHARED
    clauses = [
        _WHY_PHRASE.get(c.hub.type, _WHY_FALLBACK).format(label=c.hub.label) for c in named
    ]
    sentence = "; ".join(clauses)
    return sentence[:1].upper() + sentence[1:] + "."


def match(graph: nx.Graph, a: str, present: Sequence[str]) -> list[Match]:
    """Score the arriving person ``a`` against everyone in ``present``.

    Returns one :class:`Match` per present person, sorted by score descending, zero scorers
    included -- capping is the digest's job (R7). ``a`` never appears in their own result even
    when ``present`` contains them, which it will: R3 adds the arriving person to the presence
    set BEFORE matching. Present ids with no person node are skipped, since there is no
    :class:`PersonRef` to answer with.

    Ordering is total and deterministic: raw score descending, then person id, so two pairs
    that round to the same 0..100 still come back in a stable order.
    """
    arriving = person_node(a)
    ref = graph.graph.get("ref")
    if ref is None:
        ref = reference_score(
            sum(1 for _, data in graph.nodes(data=True) if data.get("kind") == "person")
        )

    seen: set[str] = set()
    ranked: list[tuple[float, str, Match]] = []
    for person_id in present:
        if person_id == a or person_id in seen:
            continue
        seen.add(person_id)
        node = person_node(person_id)
        if node not in graph:
            continue
        other: PersonRef = graph.nodes[node]["person"]

        components = _contributions(graph, arriving, node)
        raw = sum(c.contribution for c in components)
        ranked.append(
            (
                raw,
                person_id,
                Match(
                    other=other,
                    score=_normalise(raw, ref),
                    contributions=components,
                    path=_path(graph, arriving, node, components),
                    why=_why(components),
                ),
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [m for _, _, m in ranked]
