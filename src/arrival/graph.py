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

import logging
import math
import re
from collections.abc import Iterable, Sequence

import networkx as nx

from arrival.contracts import Dossier, Hub, HubContribution, Match, PersonRef
from arrival.taste import is_displayable
from arrival.util import slug

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TYPE_BOOST",
    "PERSON_PREFIX",
    "REF_SHARERS",
    "REF_TYPE_BOOST",
    "TYPE_BOOST",
    "WITHHELD_HUB_LABEL",
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
#: names an ENTITY rather than a spelling, so it beats every ``{type}:{slug(label)}`` id in
#: :func:`_canonical_hub_ids`'s identity election however few carriers stated it -- but it
#: does NOT beat a competing QID by sorting first. See :func:`_elect_qid`.
WIKIDATA_PREFIX = "wd:"

#: Deterministic, speakable phrasing per hub type (R18: read aloud, so no ids, no
#: parentheticals, no scores). ``{label}`` is the hub's label as :func:`_spoken_label`
#: renders it for MID-SENTENCE use -- which is the stored label itself in every case but
#: the common-noun one. Nothing else in the codebase sees that rendering; see
#: :func:`_spoken_label` for why the lower-casing happens here and not at the source.
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

#: What replaces a hub's LABEL when every fact evidencing it, across every carrier, is
#: withheld. See :func:`_nameable_hub_ids`.
#:
#: A label rather than a removal, because the hub itself is NOT removed: it keeps its node,
#: its edges, its idf and its contribution, so the score and the reasoning table still say
#: "you two do share something and here is what it is worth". Only the NAME goes. It reads
#: as a table cell on ``/graph``, ``/corpus`` and the R10 score-components table, and
#: :func:`_why` refuses to speak it at all.
WITHHELD_HUB_LABEL = "Withheld connection"

#: What a pair with no shared hub worth anything gets. Still a plain spoken sentence.
_WHY_NOTHING_SHARED = "Nothing in common on the record yet."

#: How many hubs the why names at most (T-5 acceptance 4).
_WHY_MAX_HUBS = 2

#: The hub types whose LABEL is a CATEGORY rather than the name of an entity. A topic, a
#: technology or a cause is a common noun ("developer-tools go-to-market", "evaluation
#: harnesses", "ocean cleanup"); a company, investor, school, board, city, event or person
#: is a NAME ("Quarrystone Labs", "Austin", "Foundry Seed 2019"), and lower-casing one
#: states something false about it. Only these three are candidates for :func:`_spoken_label`
#: -- the other seven keep whatever capitalisation the extractor recorded, always.
_COMMON_NOUN_HUB_TYPES: frozenset[str] = frozenset({"cause", "technology", "topic"})


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
    """Most common wins, ties broken lexicographically. For DESCRIPTIONS only.

    The type vote and the label vote, factored out so they cannot drift apart.
    ``extract._most_common`` deliberately duplicates it for the WITHIN-dossier half of the
    same problem; ``graph`` sits downstream of ``extract``, so importing upward would invert
    the dependency. If this tie-break ever changes, that copy has to change with it.

    **It is not used for the QID vote, and that asymmetry is the point** (T-053, the
    graph-side half of T-036). A type and a label are DESCRIPTIONS of one node: every
    candidate describes the same thing, some spelling has to win, and a lexicographic
    tie-break at least makes the winner a function of the SET rather than of the order the
    dossiers arrived in. A QID is an IDENTITY -- a claim about WHICH entity this is -- and
    alphabetical order is not evidence about that. ``Q4242`` beating ``Q7777`` because "4"
    sorts before "7" is arrival order in a better costume. See :func:`_elect_qid`.
    """
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _elect_qid(carriers: dict[str, set[str]]) -> str | None:
    """The QID the carriers corroborate, or ``None`` when two of them are equally attested.

    ``carriers`` maps each stated ``wd:`` id to the set of ``person_id``s that stated it.
    Ranking is on that count and REFUSES a tie -- ``extract._unambiguous`` and
    ``extract._hub_qid``, ported to the one place they cannot reach. (T-036 makes each
    dossier's QID a reading of that person's OWN documents; it has no second dossier to
    compare against, so a disagreement BETWEEN dossiers first becomes visible here.)

    **People, not occurrences.** The reason a QID gains weight from a second carrier is that
    each carrier's QID was corroborated against a different person's independently retrieved
    documents. One dossier naming the same QID twice is one reading repeated, not two, and
    ``build_graph`` accepts two dossiers for one ``person_id`` -- so the vote is over the
    set of people, while the type and label votes keep counting occurrences (which is what
    keeps them agreeing with :func:`_hub_identity`).

    A single stated QID still wins outright however few carriers state it: there is no
    competing claim about the entity, and the slug-form ids it beats are the ABSENCE of one.
    Only two QIDs in genuine competition can produce a refusal.
    """
    if not carriers:
        return None
    ranked = sorted(carriers.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        log.info(
            "refusing to name a hub by QID: %s are stated by equally many carriers, so an "
            "alphabetical winner would be arrival order wearing the costume of evidence",
            [qid for qid, _ in ranked[:2]],
        )
        return None
    return ranked[0][0]


#: The shortest acronym that may fold into its expansion. Two letters is where the rule
#: stops being about one organisation and starts being about a coincidence -- "VC", "PE",
#: "AI" and "GS" each name several things, and a wrong fold pools two entities' labels,
#: types and evidence for everybody in the graph. Three is where the abbreviations that
#: actually appear in dossiers live: MIT, USV, IBM, NYU, LSE.
MIN_ACRONYM_LETTERS = 3

#: Words an acronym skips. Only closed-class connectives -- nothing that could be the
#: distinguishing word of a name.
_ACRONYM_SKIP: frozenset[str] = frozenset({"of", "the", "and", "for", "at", "in", "on", "de"})

_WORDS = re.compile(r"[^0-9A-Za-z]+")


def _acronym_of(label: str) -> str | None:
    """The initials of a multi-word name -- "Massachusetts Institute of Technology" -> "MIT".

    Returns None for anything too short to be worth abbreviating. Connectives are skipped,
    which is how English forms these, and the count is of the words that survive the skip:
    "Bank of America" is two significant words and gets no acronym here, deliberately.
    """
    words = [word for word in _WORDS.split(label) if word]
    significant = [word for word in words if word.casefold() not in _ACRONYM_SKIP]
    if len(significant) < MIN_ACRONYM_LETTERS:
        return None
    return "".join(word[0] for word in significant).upper()


def _reads_as_an_acronym(label: str) -> bool:
    """Whether this label is written the way an abbreviation is: one all-capital token.

    "MIT" and "M.I.T." qualify; "Mit", "MIT Sloan" and "Google" do not. The orthography is
    the evidence -- the same argument :func:`_spoken_label` makes in the other direction --
    and requiring it is what keeps the fold from reaching an ordinary short name.
    """
    token = label.strip().replace(".", "")
    return len(token) >= MIN_ACRONYM_LETTERS and token.isalpha() and token.isupper()


def _acronym_aliases(described: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    """Identity keys that are an ACRONYM of another key in the same corpus, folded onto it.

    The second face of the hub-identity defect, and the one the live corpus made visible
    only once its documents were repaired: ``school:mit`` and
    ``school:massachusetts-institute-of-technology``, carried by the SAME two people, are one
    institution. Left split they do not merely fail to join -- they join TWICE for one real
    reason, which inflated that pair's score from 33 to 53 and produced the spoken line
    "Both came through Massachusetts Institute of Technology; both came through MIT."

    Four conditions, all of them mechanical, and every one of them is load-bearing:

    1. the short label is WRITTEN as an abbreviation (:func:`_reads_as_an_acronym`), so the
       fold can never reach an ordinary short name;
    2. the long label's initials are exactly it (:func:`_acronym_of`), which is a fact about
       the two strings rather than a guess about the world;
    3. both groups elect the SAME hub type, so an acronym company is never folded into an
       expansion school;
    4. exactly ONE expansion claims the acronym. Two are a genuine ambiguity and the answer
       is to refuse, not to pick -- the same reasoning :func:`_elect_qid` applies to a
       contested QID, and for the same reason: an alphabetical winner between "Manchester
       Institute of Technology" and "Massachusetts Institute of Technology" would be arrival
       order in a better costume.

    What it deliberately does NOT do is fold two DIFFERENT names for one institution --
    "The Wharton School of Business" and "University of Pennsylvania", both in this corpus.
    Nothing in those two strings relates them; only a knowledge base does, and inventing one
    here would be manufacturing a connection rather than finding it. That case is reported
    open rather than guessed at.
    """
    types = {key: _hub_identity(pairs)[0] for key, pairs in described.items()}
    labels = {key: _hub_identity(pairs)[1] for key, pairs in described.items()}
    claims: dict[str, list[str]] = {}
    for key, label in labels.items():
        acronym = _acronym_of(label)
        if acronym is not None:
            claims.setdefault(slug(acronym), []).append(key)

    alias: dict[str, str] = {}
    for key, label in sorted(labels.items()):
        if not _reads_as_an_acronym(label):
            continue
        expansions = sorted(
            other for other in claims.get(key, ()) if other != key and types[other] == types[key]
        )
        if not expansions:
            continue
        if len(expansions) > 1:
            log.info(
                "refusing to expand the acronym %r: %s all abbreviate to it, so a winner "
                "would be arrival order wearing the costume of evidence",
                label,
                [labels[other] for other in expansions],
            )
            continue
        alias[key] = expansions[0]
        log.info("folding the acronym %r onto %r: one entity, two spellings",
                 label, labels[expansions[0]])
    return alias


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

    Three rules, in order:

    1. **A ``wd:`` id wins, when the carriers agree on WHICH ONE.** It names an entity
       Wikidata resolved rather than a spelling one extraction chose, so a single carrier
       stating one settles the group. Two carriers stating DIFFERENT ones do not settle
       anything, and :func:`_elect_qid` refuses rather than picking the alphabetically
       smaller -- see the cost of that refusal below.
    2. Otherwise the ``{type}:{slug(label)}`` ids vote, by :func:`_elect`.
    3. If a refusal leaves no stated id at all -- every carrier named a contested QID and
       nobody the slug form -- the id is composed from the elected ``(type, label)``. This
       is the ONE case where the answer is not an id somebody stated, and it is deliberate:
       having just decided that no stated id may be believed, keeping one anyway would be
       the arbitrary pick all over again. The composed form is byte-identical to what
       ``extract.canonical_hub_id`` emits for a hub whose own QID it refused, so a third
       carrier who never saw Wikidata lands on this exact id and joins the group.

    Outside rule 3 the elected id is one a carrier actually STATED -- nothing is recomputed
    from the label. That matters twice over: an id whose slug has drifted from its label
    never has its node silently renamed, and because the stated ids are
    ``{type}:{slug(label)}``, electing among them by "most common, then lexicographic"
    agrees by construction with the type :func:`_hub_identity` elects from the same
    occurrences.

    Two carriers who state the SAME id under different labels land in different groups, and
    still converge: each group's only stated id is that one, so both elect it and pass 2 keys
    them to the same node.

    **What refusing costs, measured here rather than assumed from upstream.** Group
    membership is fixed by :func:`_identity_key`, i.e. by the LABEL, so the elected id never
    decides who is inside a group -- it decides the node's NAME, and through the convergence
    in the paragraph above, whether this group merges with a DIFFERENT-labelled one. That is
    the whole cost, and it is also where the damage was: with a lexicographic tie-break,
    whether "Harborline Capital" welds onto "Foundry Seed 2019" was decided by which opaque
    QID sorts first. Reproduced on a five-person corpus -- A states one QID for
    ``Foundry Seed 2019``, B states the other for that label AND for ``Harborline Capital``,
    C states it for ``Harborline Capital`` alone. Renaming the two QIDs, and nothing else,
    moved the graph from two hub nodes to one: ``Harborline Capital`` stopped existing as an
    entity, B's two hubs collapsed into one edge whose ``evidence_fact_ids`` pooled
    ``['b-foundry']`` into ``['b-foundry', 'b-harbor']``, and ``match`` went from
    ``[('b', 100.0), ('c', 0.0)]`` to ``[('b', 44.0), ('c', 44.0)]``.

    So refusal costs the cross-label merge, and only that: every carrier of the label still
    shares the node, which is the join this election exists to make. A wrong QID is
    unbounded by comparison -- it pools two entities' labels, types and
    ``evidence_fact_ids`` and moves every score that touches either.

    The tradeoff, stated plainly: two genuinely different hubs that share a label ("Apple" the
    company, "Apple" the topic) are merged. Label collision is the price of joining carriers
    who disagree about type, and joining them is the entire point -- a hub the graph splits
    contributes nothing to anybody.
    """
    stated: dict[str, dict[str, int]] = {}
    qid_carriers: dict[str, dict[str, set[str]]] = {}
    described: dict[str, list[tuple[str, str]]] = {}
    for dossier in dossiers:
        person_id = dossier.person.person_id
        for hub in dossier.hubs:
            key = _identity_key(hub)
            counts = stated.setdefault(key, {})
            counts[hub.hub_id] = counts.get(hub.hub_id, 0) + 1
            described.setdefault(key, []).append((hub.type, hub.label))
            if hub.hub_id.startswith(WIKIDATA_PREFIX):
                by_qid = qid_carriers.setdefault(key, {})
                by_qid.setdefault(hub.hub_id, set()).add(person_id)

    # An acronym and its expansion are one entity written two ways, so their groups are
    # merged BEFORE anything is elected: the pooled occurrences are what the type, label and
    # id votes must see. Every fold is a dict merge, so the result does not depend on the
    # order the dossiers arrived in. See `_acronym_aliases`.
    alias = _acronym_aliases(described)
    for short, long in sorted(alias.items()):
        for hub_id, count in stated.pop(short, {}).items():
            stated[long][hub_id] = stated[long].get(hub_id, 0) + count
        for qid, people in qid_carriers.pop(short, {}).items():
            qid_carriers.setdefault(long, {}).setdefault(qid, set()).update(people)
        described[long].extend(described.pop(short))

    canonical: dict[str, str] = {}
    for key, counts in stated.items():
        qid = _elect_qid(qid_carriers.get(key, {}))
        if qid is not None:
            canonical[key] = qid
            continue
        others = {i: n for i, n in counts.items() if not i.startswith(WIKIDATA_PREFIX)}
        if others:
            canonical[key] = _elect(others)
            continue
        hub_type, label = _hub_identity(described[key])
        canonical[key] = f"{hub_type}:{slug(label)}"
    # `_identity_key` is called again by `build_graph`, and it does not know about the fold.
    # Both spellings therefore have to resolve here, to the one id the merged group elected.
    for short, long in alias.items():
        canonical[short] = canonical[long]
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


def _nameable_hub_ids(
    dossiers: Sequence[Dossier], canonical: dict[str, str]
) -> set[str]:
    """The canonical hub ids that may have their LABEL rendered on a host-facing page.

    **The defect this closes (T-087).** Hub labels passed no taste gate at all. Two members
    each carrying a fact excluded ``home_or_property`` — the sentence naming the street they
    live on — produced the Meet row **"Both rooted in Ravensworth Hill."** The fact TEXT was
    correctly suppressed everywhere; the label carrying the same secret was rendered in the
    spoken ``why`` (which R18 says a host reads OUT LOUD, in a lobby, to the member it is
    about), in the R10 score-components table, on ``/graph`` and on ``/corpus``.

    **Why the gate is HERE and not at the four display sites.** ``build_graph`` is the one
    place a hub label is elected, and every host-facing surface reads the result of that
    election rather than the stored ``Hub.label``: ``/graph`` and ``/corpus`` read
    ``graph.nodes[node]["label"]``, and the R10 table and :func:`_why` read the edge's
    ``Hub`` object, which :func:`_one_hub_per_person` has already overwritten with the
    elected label. One gate here therefore covers all four, and it covers them **at boot**,
    on the corpus already committed — no rebuild, no re-extraction. Placing it at the four
    display sites instead would be four gates in three files to keep in step, and placing it
    at hub MINTING (``extract.py``) would fix only corpora built after the change, which is
    the half of the problem that is not on fire.

    **Why ``research._supported_hubs`` does not already cover it.** It applies the same
    rule, correctly, and it is a BUILD-TIME step: ``build_dossier`` calls it once, between
    ``apply_taste`` and writing the JSON, and nothing in the display path calls it ever
    again. So it protects a dossier built after the taste rules were right, and does nothing
    for the ten committed dossiers whose facts were ruled by the pronoun-anchored rules
    principle 4 replaces — those facts become withheld the moment ``is_displayable``
    re-checks them, and their hubs then need a gate that runs at display time. It also keys
    on ``Fact.excluded`` alone, where a display gate must ask the full R12 question.

    **The rule, and it is deliberately the weakest one that works.** A hub is withheld only
    when EVERY carrier's resolvable evidence for it is undisplayable. One displayable
    evidence fact anywhere keeps the label, so the matching design (T-5 acceptance 1: hubs
    take part whatever taste said) is untouched, and so is every hub in a corpus with
    nothing excluded.

    Following ``_supported_hubs`` exactly on the two edges that matter: an evidence id that
    resolves to no fact is **left alone rather than judged on silence**, and a hub with no
    evidence ids at all is nameable. Hubs are constructed without evidence all over the test
    corpora and in ``extract._roster_city_hub``; judging those on absence would redact the
    graph wholesale.
    """
    nameable: set[str] = set()
    for dossier in dossiers:
        known = {fact.fact_id: fact for fact in dossier.facts}
        for hub in dossier.hubs:
            hub_id = canonical[_identity_key(hub)]
            if hub_id in nameable:
                continue
            evidence = [known[fid] for fid in hub.evidence_fact_ids if fid in known]
            if not evidence or any(is_displayable(fact) for fact in evidence):
                nameable.add(hub_id)
    return nameable


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
    # R11 (T-087): which hubs may be NAMED. Computed here, over the whole population, for
    # the same reason idf is -- the answer is a property of every carrier at once, and a
    # hub one person cannot name is nameable if somebody else evidenced it cleanly.
    nameable = _nameable_hub_ids(dossiers, canonical)
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
            if hub_id not in nameable:
                # R11 (T-087). The hub keeps everything that makes it a MATCH -- its node,
                # its edges, its idf, its boost, its contribution -- and loses only the
                # name, because the name is the part a host says out loud.
                log.info(
                    "withholding the label of hub %s: every fact evidencing it is withheld",
                    hub_id,
                )
                label = WITHHELD_HUB_LABEL
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
    top = next(iter(_nameable(components)), None)
    if top is None:
        return []
    via = hub_node(top.hub.hub_id)
    if not (graph.has_edge(arriving, via) and graph.has_edge(via, other)):
        return []  # defensive: a contribution always comes from a hub adjacent to both
    return [arriving, via, other]


def _spoken_label(label: str, hub_type: str) -> str:
    """The hub's label as it should read MID-SENTENCE inside a ``why`` (R18).

    Hub labels are stored capitalised, because a label is also a heading: the R10 reasoning
    table and ``/debug`` print it as a standalone cell where "Developer-tools go-to-market"
    is right. Dropped into a phrase template it becomes a capitalised common noun in the
    middle of a sentence -- the measured line was
    ``"Both deep in Developer-tools go-to-market."`` -- and R18 exists to keep the host from
    stumbling over exactly that.

    **Why the fix is here and not at the source.** Lower-casing where the label is BORN
    (``extract``, the canonical ``hub_id``, the elected label in :func:`_hub_identity`)
    would change a value five other consumers read for other purposes: the graph-wide join
    key ``_identity_key``, ``HubContribution.hub.label`` (pinned by
    ``tests/graph/test_t5_hub_identity_election.py`` and by the frozen
    ``test_t3_extractor.py``'s ``{h.label: h}`` lookups), and the two Jinja templates that
    render the label as a table cell. The capitalisation is only wrong in ONE position --
    inside this sentence -- so exactly one function knows about it, and the stored ``Hub``
    is never touched.

    **The rule, and where it errs.** A label is lower-cased only when all four hold:

    1. its ``type`` is in :data:`_COMMON_NOUN_HUB_TYPES` -- a company or a school is a name;
    2. it is at least two words -- a lone token ("Austin", "Quillmark", "Kubernetes",
       "Rust") is far more often a name than a category, and there is nothing in a single
       word to tell the two apart;
    3. its first word is Capitalised-then-lowercase -- not an acronym or a CamelCase
       product, so "AI safety", "A/B testing" and "GitHub actions" keep their heads;
    4. no LATER word is capitalised -- English Title-Cases a multi-word proper name, so
       "Foundry Seed 2019" and "Bank of America" are refused by their own orthography even
       if something mistypes them as a topic.

    Only the leading character changes; the rest of the label is returned byte-identical,
    which is what keeps the already-lowercase tail of "Developer-tools go-to-market" intact.

    It errs toward LEAVING CAPITALISATION ALONE, deliberately, because the two failure
    directions are not symmetric: refusing to lower-case a common noun gives a mildly
    awkward line, while lower-casing a proper noun makes a false claim about somebody's
    company. So it knowingly misses a Title-Cased common noun ("Machine Learning" as a
    topic) and a single-word one ("Sailing"), and it knowingly gets "Rust compilers" wrong
    in the mild direction.

    >>> _spoken_label("Developer-tools go-to-market", "topic")
    'developer-tools go-to-market'
    >>> _spoken_label("Quarrystone Labs", "company")      # a name, not a category
    'Quarrystone Labs'
    >>> _spoken_label("Foundry Seed 2019", "topic")       # Title Case: still a name
    'Foundry Seed 2019'
    >>> _spoken_label("AI safety", "topic")               # an acronym head is left alone
    'AI safety'
    >>> _spoken_label("Austin", "topic")                  # one word: nothing to go on
    'Austin'
    """
    if hub_type not in _COMMON_NOUN_HUB_TYPES:
        return label
    words = label.split()
    if len(words) < 2:
        return label
    head = words[0]
    if not head[:1].isupper():
        return label  # already lower-case, or opens on a digit or a symbol
    if any(character.isupper() for character in head[1:]):
        return label  # "AI", "A/B", "GitHub", "PyTorch" -- an acronym or a product name
    if any(word[:1].isupper() for word in words[1:]):
        return label  # Title Case, so the orthography says this is a proper name
    stripped = label.lstrip()
    return label[: len(label) - len(stripped)] + stripped[:1].lower() + stripped[1:]


def _nameable(components: Sequence[HubContribution]) -> list[HubContribution]:
    """The components a sentence may actually cite, in order.

    Two filters, and they are separate requirements that happen to compose:

    * ``contribution > 0`` -- citing a hub the clamp zeroed claims credit for a connection
      worth nothing (T-016);
    * the label was not withheld by :func:`_nameable_hub_ids` -- R11. A withheld hub still
      SCORES, and the R10 table still lists it under :data:`WITHHELD_HUB_LABEL`, but no
      sentence names it: "Both rooted in Withheld connection." would be an absurd line to
      read aloud and would advertise the withholding to the member's face.

    Shared by :func:`_why` and :func:`_path` so the two cannot disagree. T-016 is explicit
    that a path routed through a hub the why refuses to name is a defect ("the path is the
    picture of the why"), and withholding a label creates exactly that opportunity.
    """
    return [
        c for c in components if c.contribution > 0 and c.hub.label != WITHHELD_HUB_LABEL
    ]


def _why(components: Sequence[HubContribution]) -> str:
    """A deterministic sentence naming up to two top hubs by LABEL. No LLM, ever.

    R18: this is read aloud to a host in a lobby, so it is a plain sentence -- no hub ids, no
    parentheticals, no scores. Only hubs that actually contributed are named: citing a hub the
    clamp zeroed would claim credit for a connection worth nothing.

    The label is rendered for mid-sentence use by :func:`_spoken_label`; the sentence-initial
    capital is applied here, AFTER the clauses are joined, and every phrase in
    :data:`_WHY_PHRASE` opens on "both", so a label is never the word that gets capitalised.
    """
    named = _nameable(components)[:_WHY_MAX_HUBS]
    if not named:
        return _WHY_NOTHING_SHARED
    clauses = [
        _WHY_PHRASE.get(c.hub.type, _WHY_FALLBACK).format(
            label=_spoken_label(c.hub.label, c.hub.type)
        )
        for c in named
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
