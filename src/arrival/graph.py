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
* the "why" path is the two-hop route through the pair's top CONTRIBUTING hub -- the same hub
  the ``why`` names. On an ordinary corpus that is also the ``cost = 1/(1+idf)`` shortest
  route, but the path is constructed rather than searched, for two reasons recorded in
  :func:`_path`: a Dijkstra tie is broken by adjacency insertion order (i.e. by dossier
  order), and a searched route can run through a hub the ``why`` refuses to name.

One thing this module does that is easy to miss: **it elects the identity of a hub across
its carriers.** ``Hub.hub_id`` is ``"wd:Q123"`` when Wikidata resolved the entity and
``"{type}:{slug(label)}"`` otherwise, so the id is a function of two things the evidence does
not fix -- the type the model chose, and whether a Wikidata document happened to be retrieved
*for that particular person*. Two people who genuinely share a hub therefore arrive with
different ids and score 0, which is exactly the case this engine exists to find. One
``extract()`` call sees one person and has nothing to reconcile against; ``build_graph`` is
the first place that sees them all, so the reconciliation lives here. See
:func:`_canonical_hub_ids`.

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

from arrival.contracts import Dossier, Hub, HubContribution, Match, PersonRef
from arrival.util import slug

__all__ = [
    "DEFAULT_TYPE_BOOST",
    "PERSON_PREFIX",
    "REF_SHARERS",
    "REF_TYPE_BOOST",
    "TYPE_BOOST",
    "WIKIDATA_PREFIX",
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

#: The prefix ``extract.canonical_hub_id`` gives an id Wikidata resolved. An id carrying it
#: names an ENTITY rather than a spelling, so it wins the identity election in
#: :func:`_canonical_hub_ids` however few carriers stated it.
WIKIDATA_PREFIX = "wd:"

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
    best_type = _elect(types)
    best_label = _elect(labels)
    return best_type, best_label


def _elect(counts: dict[str, int]) -> str:
    """Most common wins, ties broken lexicographically.

    The one voting rule this module has, factored out so the type vote, the label vote and
    the hub-id vote cannot drift apart. ``extract._most_common`` deliberately duplicates it
    for the WITHIN-dossier half of the same problem; ``graph`` sits downstream of ``extract``,
    so importing upward would invert the dependency. If this tie-break ever changes, that
    copy has to change with it.
    """
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _identity_key(hub: Hub) -> str:
    """What the evidence actually fixes about a hub: its label, normalised.

    NOT its ``hub_id``. The id is ``"wd:Q123"`` when a Wikidata document happened to be
    retrieved for that person and ``"{type}:{slug(label)}"`` otherwise, so two carriers of one
    real hub disagree whenever the model picked a different type or only one of them saw a QID
    -- and a disagreement makes them two nodes, which scores the pair 0. An empty label leaves
    nothing to group by, so such a hub falls back to standing alone under its own id.
    """
    return slug(hub.label) or f"\0{hub.hub_id}"


def _canonical_hub_ids(dossiers: Sequence[Dossier]) -> dict[str, str]:
    """Elect ONE ``hub_id`` per real hub, keyed by :func:`_identity_key`.

    Two rules, in order:

    1. **A ``wd:`` id wins.** It names an entity Wikidata resolved rather than a spelling one
       extraction chose, so a single carrier stating one settles the group.
    2. Otherwise the ids vote, by :func:`_elect`.

    The elected id is always one a carrier actually STATED -- nothing is recomputed from the
    label. That matters twice over: an id whose slug has drifted from its label never has its
    node silently renamed, and because the stated ids are ``{type}:{slug(label)}``, electing
    among them by "most common, then lexicographic" agrees by construction with the type
    :func:`_hub_identity` elects from the same occurrences.

    Two carriers who state the SAME id under different labels land in different groups, and
    still converge: each group's only stated id is that one, so both elect it and pass 2 keys
    them to the same node.

    The tradeoff, stated plainly: two genuinely different hubs that share a label ("Apple" the
    company, "Apple" the topic) are merged. Label collision is the price of joining carriers
    who disagree about type, and joining them is the entire point -- a hub the graph splits
    contributes nothing to anybody.
    """
    stated: dict[str, dict[str, int]] = {}
    for dossier in dossiers:
        for hub in dossier.hubs:
            counts = stated.setdefault(_identity_key(hub), {})
            counts[hub.hub_id] = counts.get(hub.hub_id, 0) + 1

    canonical: dict[str, str] = {}
    for key, counts in stated.items():
        qids = {i: n for i, n in counts.items() if i.startswith(WIKIDATA_PREFIX)}
        canonical[key] = _elect(qids or counts)
    return canonical


def _one_hub_per_person(
    hubs: Sequence[Hub], hub_id: str, hub_type: str, label: str
) -> Hub:
    """One person's :class:`Hub` for one elected hub: the graph's IDENTITY, their own EVIDENCE.

    A hub has exactly one identity in the graph -- the elected ``hub_id``, ``type`` and
    ``label`` -- and every edge into its node must agree with it, because consumers read
    :class:`HubContribution`'s ``hub`` and the node's own numbers side by side:

    * the frozen T-5 suite compares ``hub.hub_id`` with the node name, at ``path[1]``;
    * the digest's ``data-reasoning`` block (R10) prints ``hub.label`` and ``hub.type`` in
      the same row as ``type_boost``, which is computed from the ELECTED type. A Hub keeping
      its carrier's own dissenting type therefore renders "city" beside a boost of 1.5.

    Only what is genuinely the person's own survives: the freshest ``recency`` they recorded
    for this hub, and their ``evidence_fact_ids``, which resolve in their dossier and nobody
    else's.

    Deterministic by construction: the occurrence whose own id was elected wins, then the
    lexicographically smallest id, then the smallest label -- never list order. The common
    case, where a carrier already agrees with the election, returns their Hub untouched.
    """
    ordered = sorted(hubs, key=lambda h: (h.hub_id != hub_id, h.hub_id, h.label))
    base = ordered[0]
    evidence: list[str] = []
    for hub in ordered:
        for fact_id in hub.evidence_fact_ids:
            if fact_id not in evidence:
                evidence.append(fact_id)
    recency = max(hub.recency for hub in ordered)
    elected = (hub_id, hub_type, label, recency)
    if (base.hub_id, base.type, base.label, base.recency) == elected:
        if base.evidence_fact_ids == evidence:
            return base  # the ordinary case: nothing to reconcile, so no copy is made
    return base.model_copy(
        update={
            "hub_id": hub_id,
            "type": hub_type,
            "label": label,
            "recency": recency,
            "evidence_fact_ids": evidence,
        }
    )


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

    # Pass 1: elect each hub's identity, then record who carries what and how they describe
    # it. IDF needs the whole population before any edge weight is meaningful, so nothing can
    # be written until every dossier has been seen -- and the identity election needs it too,
    # for the same reason.
    canonical = _canonical_hub_ids(dossiers)
    carriers: dict[str, set[str]] = {}
    described: dict[str, list[tuple[str, str]]] = {}
    held: dict[str, dict[str, list[Hub]]] = {}
    for dossier in dossiers:
        person_id = dossier.person.person_id
        mine = held.setdefault(person_id, {})
        for hub in dossier.hubs:
            hub_id = canonical[_identity_key(hub)]
            carriers.setdefault(hub_id, set()).add(person_id)
            described.setdefault(hub_id, []).append((hub.type, hub.label))
            # Two dossiers for one person, or one dossier listing a hub twice, are one edge.
            # `_one_hub_per_person` folds them by a rule, not by whichever was written last.
            mine.setdefault(hub_id, []).append(hub)

    # Person nodes in person_id order, not dossier order. Node and edge INSERTION order is
    # the only remaining channel through which a caller's dossier order could reach an
    # output -- networkx iterates adjacency in insertion order, so a Dijkstra tie anywhere
    # downstream would otherwise be decided by a filesystem glob (T-013).
    by_person = {d.person.person_id: d.person for d in dossiers}
    for person_id in sorted(by_person):
        person = by_person[person_id]
        graph.add_node(
            person_node(person_id),
            bipartite=0,
            kind="person",
            person_id=person_id,
            person=person,
        )

    # N is the number of PERSON NODES, not the number of dossiers: two dossiers for the same
    # person_id are one leaf, and counting them twice would deflate every idf in the graph.
    n_people = sum(1 for _, data in graph.nodes(data=True) if data.get("kind") == "person")

    # Pass 2: hub nodes and the edges, now that N is known.
    for person_id in sorted(held):
        source = person_node(person_id)
        for hub_id in sorted(held[person_id]):
            hub_type, label = _hub_identity(described[hub_id])
            hub = _one_hub_per_person(held[person_id][hub_id], hub_id, hub_type, label)
            idf = hub_idf(n_people, len(carriers[hub_id]))
            boost = TYPE_BOOST.get(hub_type, DEFAULT_TYPE_BOOST)
            target = hub_node(hub_id)
            if target not in graph:
                graph.add_node(
                    target,
                    bipartite=1,
                    kind="hub",
                    hub_id=hub_id,
                    label=label,
                    type=hub_type,
                    idf=idf,
                    type_boost=boost,
                    n_carriers=len(carriers[hub_id]),
                )
            graph.add_edge(
                source,
                target,
                recency=hub.recency,
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
    """The two-hop route through the pair's top CONTRIBUTING hub -- the one the ``why`` names.

    ``cost = 1/(1+idf)`` already makes the rarest shared hub the cheapest crossing, so on any
    ordinary corpus this IS the plain weighted shortest path. The two can diverge when a
    high-idf hub carries a low type boost -- the top CONTRIBUTION maximises
    ``idf * recency * boost`` while the cheapest edge only maximises ``idf``. When they
    disagree the top contributor wins, because the path is the picture of the why and must
    show the reason the pair actually scored (T-5 acceptance 3).

    The route is CONSTRUCTED, not searched, and both reasons were measured:

    * **T-013.** ``nx.shortest_path`` breaks an equal-cost tie by adjacency insertion order,
      which is the order the dossiers arrived in. Over all 720 permutations of a six-dossier
      corpus, 480 produced a different ``path`` for byte-identical input while every score,
      contribution and why stayed identical. Two people are always adjacent to a hub they
      share, so the shortest route through it is the direct two hops and there is nothing
      left to search.
    * **T-016.** A hub the clamp zeroed contributes nothing, so :func:`_why` refuses to name
      it -- and a path routed through it then names a hub the why denies. Reproduced on the
      frozen corpus: ``runa-okonkwo`` -> ``mira-hollowell`` answered
      ``why="Nothing in common on the record yet."`` beside
      ``path=[..., "hub:city:austin", ...]``. When no hub contributed, there is no route
      worth showing and none is invented -- the same answer this already gave a pair with no
      shared hub at all.
    """
    if arriving not in graph or other not in graph:
        return []
    top = next((c for c in components if c.contribution > 0), None)
    if top is None:
        return []
    via = hub_node(top.hub.hub_id)
    if not (graph.has_edge(arriving, via) and graph.has_edge(via, other)):
        return []  # defensive: a contribution always comes from a hub adjacent to both
    return [arriving, via, other]


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
