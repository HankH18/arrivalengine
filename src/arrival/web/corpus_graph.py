"""The whole corpus as one picture: `GET /corpus`, computed here, drawn in SVG.

**How this differs from `/graph`.** `graph_view.py` answers R17 — "present people and their
shared hubs" — so it is scoped to the room and draws only hubs two or more people in the
room carry. This page answers the other question: *what does the system actually know?*
Every dossier in `DOSSIER_DIR`, every hub on the graph, every person the resolver refused,
whether or not anybody is standing in the building. It is the demo picture, and it is
deliberately not on the arrival path.

**The honest problem is the long tail, and the measured shape is not a hairball.** On the
ten-person corpus this page was designed against: 10 dossiers, 7 of them identified
confidently enough to enter the graph, 67 hubs, 68 edges — and 66 of those 67 hubs are
carried by exactly ONE person. Drawing 67 labelled nodes would be a wall of text in which
the one thing worth seeing (the single hub two people share) is invisible. So the drawing
splits the corpus by what each half is evidence of:

* a hub only one person carries is a fact ABOUT THAT PERSON. It is drawn as an unlabelled
  spoke in a burr around that person — texture, not text — with the label in the node's own
  SVG `<title>` and in the table below. Its length is what the hub is worth.
* a hub two or more people carry is a CONNECTION. It is drawn as a labelled node between
  its carriers, with an edge to each, exactly as `/graph` draws one.

The result reads at a glance as what the corpus is: a constellation of people each
surrounded by their own private facts, with the rare places they touch drawn large. It
degrades correctly — a corpus where nobody shares anything renders as burrs and says so.

**Why the layout is hand-rolled, and why networkx's are not used.** Not preference:
measured. `nx.spring_layout`, `kamada_kawai_layout`, `circular_layout`, `shell_layout` and
`spectral_layout` every one raise `ModuleNotFoundError: No module named 'numpy'` in this
environment — networkx 3.6.1 is installed, numpy is not, and it is not in
`pyproject.toml`'s dependency list. A force layout is therefore not available at any price
this ticket is allowed to pay. It would also have been the wrong answer: a force layout of
six disconnected components is six blobs whose relative positions networkx does not
separate, and a spoke's position would mean nothing, where here a spoke's LENGTH is the
hub's worth and a hub's POSITION is the centroid of the people who carry it.

networkx is still used, for the thing it is actually good at: `connected_components` and
`degree` over the real graph. "Six islands, one bridge" is a graph-theoretic fact about the
corpus, and it is on the page as a number.

**Determinism.** No randomness anywhere. People are placed in roster order (alphabetical by
display name, which is `DossierStore.people()`'s own order), hubs in `hub_rows`' total sort
order, spokes by worth then label. Two renders of one corpus are byte-identical, and
`tests/web/test_corpusgraph_corpus_page.py` pins that.

**R11/R12.** This page is host-facing, so `/debug` is the only page allowed to show withheld
material. :func:`hub_evidence` applies :func:`arrival.taste.is_displayable` to every fact
before it can reach the template, and it is written here rather than borrowed from
`graph_view.shared_hub_evidence` on purpose: the gate that protects this page has to live in
a file this page's own tests can remove, or "the gate holds" is a claim about somebody
else's module. Everything else fact-level on the page is a COUNT — how many facts were
withheld and under which of R12's clauses — which is the R11 story made concrete without
quoting a syllable of the material it is about.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import networkx as nx

from arrival.contracts import Fact
from arrival.graph import hub_node, person_node
from arrival.taste import EXCLUSION_POLICY, is_displayable
from arrival.web.graph_view import MIN_PRESENT_CARRIERS, hub_rows, present_roster
from arrival.web.render import withholding_reason
from arrival.web.store import DossierStore

__all__ = [
    "MIN_CARRIERS",
    "WITHHELD_LABELS",
    "corpus_layout",
    "corpus_roster",
    "corpus_statistics",
    "corpus_view",
    "hub_evidence",
]

#: A hub is a CONNECTION once two people carry it. One carrier is a fact about one person.
#:
#: ALIASED, not restated, and the alias is load-bearing. `hub_rows` is `graph_view`'s and
#: splits shared from solo on ITS OWN `MIN_PRESENT_CARRIERS`; this module reuses that split
#: and then classifies components, the per-person "shared with" column and the fan groups on
#: the same idea. An independent copy of the number therefore cannot be right — it can only
#: agree or silently contradict the split the page is built on. Found by sabotage: with a
#: separate constant, moving this one to 3 changed the page's component arithmetic while the
#: drawing kept drawing two-carrier hubs as shared, and nothing was wrong enough to fail.
MIN_CARRIERS = MIN_PRESENT_CARRIERS

#: R12's clauses and R11's categories, in the words a host would use. The keys are exactly
#: what `render.withholding_reason` returns, so a new `ExclusionReason` shows up as itself
#: rather than being silently folded into another bucket.
WITHHELD_LABELS: dict[str, str] = {
    "health": "health",
    "family": "family and relationships",
    "legal": "legal matters",
    "home_or_property": "home and property",
    "wealth": "wealth and money",
    "political": "political affiliation",
    "low_confidence": "below the confidence floor",
    "source_kind_not_displayable": "source kind we never quote",
    "excluded": "excluded, reason not recorded",
}

# --------------------------------------------------------------------------- geometry

_PERSON_R = 11.0
#: The people ring, before it grows for a crowd.
_RING_RX = 168.0
_RING_RY = 132.0
_RING_GROW_X = 10.0
_RING_GROW_Y = 11.0
_RING_FROM = 4

#: A burr: the spokes standing for the hubs only this person carries.
_SPOKE_GAP = 5.0
_SPOKE_MIN = 16.0
_SPOKE_SPAN = 34.0
_SPOKE_TIP_R = 2.4
_SPOKE_SPREAD_MAX = 0.55
_SPOKE_SPREAD_SHARE = 0.40

#: Room between the longest possible spoke and a person's name.
_NAME_GAP = 18.0

_HUB_R_MIN = 9.0
_HUB_R_SPAN = 13.0
_HUB_LABEL_MAX_CHARS = 28

#: Label metrics. A page with no JavaScript cannot measure text, so widths are estimated
#: from the character count at the ratios Cormorant Garamond and Inter actually run at. The
#: estimate only has to be generous enough that two boxes pushed apart by it do not touch.
_HUB_LABEL_SIZE = 15.0
_HUB_LABEL_RATIO = 0.50
_HUB_SUB_SIZE = 9.5
_HUB_SUB_RATIO = 0.78
_PERSON_LABEL_SIZE = 16.0
_PERSON_LABEL_RATIO = 0.50

_FAN_STEP = 122.0
_BOX_PAD = 9.0
_SEPARATION_PASSES = 200

_MARGIN = 18.0
_MIN_WIDTH = 720.0
_MAX_ASPECT = 2.2
_MIN_ASPECT = 0.72

_EDGE_WIDTH_MIN = 1.0
_EDGE_WIDTH_SPAN = 3.2
_EDGE_OPACITY_MIN = 0.30
_EDGE_OPACITY_SPAN = 0.48


# --------------------------------------------------------------------------- the roster


def corpus_roster(store: DossierStore) -> list[dict[str, Any]]:
    """Every person the corpus holds a dossier on, in display-name order.

    `graph_view.present_roster` is reused verbatim: it is not really about presence, it
    turns a sequence of person ids into rows carrying the name, the details, the graph node
    and whether the resolver placed that person in the graph at all — which is exactly what
    this page needs, for a different set of ids. Order comes from `DossierStore.people()`,
    which sorts by display name, so the ring is alphabetical and stable.

    `n_hubs` is added here: a person's degree in the bipartite graph is the number of hubs
    their dossier contributed, and it is what the burr around them draws.
    """
    ids = [person.person_id for person in store.people()]
    roster = present_roster(store, ids)
    graph = store.graph
    for row in roster:
        node = row["node"]
        row["n_hubs"] = int(graph.degree(node)) if node in graph else 0
    return roster


# --------------------------------------------------------------------------- the evidence


def hub_evidence(
    store: DossierStore, hub_id: str, person_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Per carrier, the DISPLAYABLE facts behind their edge into this hub.

    R11/R12, on a host-facing page. `arrival.graph` deliberately does not filter hubs —
    matching is not display — so a hub whose evidence was taste-excluded can legitimately be
    shared and must still never be quoted here. `taste.is_displayable` is the whole gate,
    and it bites on three independent clauses: the taste flag, the confidence floor, and the
    source kind.

    A carrier whose evidence is entirely withheld comes back with an empty `facts` list
    rather than being dropped. They really do carry the hub; the page says so in the absence
    voice instead of quietly showing one fewer person than the graph has.
    """
    target = hub_node(hub_id)
    graph = store.graph
    rows: list[dict[str, Any]] = []
    for person_id in person_ids:
        source = person_node(person_id)
        if not graph.has_edge(source, target):
            continue
        dossier = store.get(person_id)
        edge_hub = graph.edges[source, target]["hub"]
        facts: list[Fact] = []
        if dossier is not None:
            by_id = {fact.fact_id: fact for fact in dossier.facts}
            for fact_id in edge_hub.evidence_fact_ids:
                fact = by_id.get(fact_id)
                if fact is not None and is_displayable(fact) and fact not in facts:
                    facts.append(fact)
        person = graph.nodes[source].get("person")
        rows.append(
            {
                "person_id": person_id,
                "name": person.name if person is not None else person_id,
                "recency": float(graph.edges[source, target]["recency"]),
                "facts": facts,
            }
        )
    return rows


# --------------------------------------------------------------------------- the numbers


def corpus_statistics(store: DossierStore) -> dict[str, Any]:
    """Everything a demo audience asks about the corpus, counted off the real objects.

    Nothing here is recomputed arithmetic: hub weights come off the graph nodes
    `arrival.graph.build_graph` wrote, the display verdict on a fact comes from
    `render.withholding_reason`, which is R12's three clauses in the order they bite, and
    the component structure comes from networkx.

    The withheld counts are counts and categories only. That is the point of them: "we found
    six facts we chose not to show you, five about money and one about family" is the taste
    filter made legible, and printing the sentences would be the exact failure it exists to
    prevent.
    """
    graph = store.graph
    people_nodes = [n for n, d in graph.nodes(data=True) if d.get("kind") == "person"]
    hub_nodes = [n for n, d in graph.nodes(data=True) if d.get("kind") == "hub"]

    facts_total = 0
    facts_shown = 0
    withheld: dict[str, int] = {}
    accepted: set[str] = set()
    rejected: set[str] = set()
    cited: set[str] = set()
    resolved = 0
    for dossier in store.dossiers.values():
        if dossier.resolution.status == "resolved":
            resolved += 1
        accepted.update(dossier.resolution.accepted_doc_ids)
        rejected.update(verdict.doc_id for verdict in dossier.resolution.rejected)
        for fact in dossier.facts:
            facts_total += 1
            cited.add(fact.provenance.doc_id)
            reason = withholding_reason(fact)
            if reason is None:
                facts_shown += 1
            else:
                withheld[reason] = withheld.get(reason, 0) + 1

    withheld_rows = [
        {"reason": reason, "label": WITHHELD_LABELS.get(reason, reason), "count": count}
        for reason, count in sorted(withheld.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # Islands and bridges. A component holding two or more PEOPLE is a place the corpus
    # actually joins up; a component holding one is that person's own private constellation.
    components = [sorted(part) for part in nx.connected_components(graph)]
    components.sort(key=lambda part: (-len(part), part))
    islands = 0
    joined = 0
    joined_people = 0
    for part in components:
        heads = [n for n in part if graph.nodes[n].get("kind") == "person"]
        if len(heads) >= MIN_CARRIERS:
            joined += 1
            joined_people += len(heads)
        else:
            islands += 1

    carriers: dict[int, int] = {}
    for node in hub_nodes:
        degree = int(graph.degree(node))
        carriers[degree] = carriers.get(degree, 0) + 1

    types: dict[str, dict[str, Any]] = {}
    for node in hub_nodes:
        data = graph.nodes[node]
        row = types.setdefault(
            str(data["type"]),
            {"type": str(data["type"]), "count": 0, "type_boost": float(data["type_boost"])},
        )
        row["count"] += 1

    return {
        "dossiers": len(store),
        "resolved": resolved,
        "unresolved": len(store) - resolved,
        "graph_people": int(graph.graph.get("n_people", len(people_nodes))),
        "hubs": len(hub_nodes),
        "edges": int(graph.number_of_edges()),
        "facts_total": facts_total,
        "facts_shown": facts_shown,
        "facts_withheld": facts_total - facts_shown,
        "withheld_by_reason": withheld_rows,
        "docs_accepted": len(accepted),
        "docs_rejected": len(rejected),
        "docs_cited": len(cited),
        "docs_considered": len(accepted | rejected | cited),
        "nodes": len(people_nodes) + len(hub_nodes),
        "components": len(components),
        "largest_component": len(components[0]) if components else 0,
        "islands": islands,
        "joined_components": joined,
        "joined_people": joined_people,
        "carrier_histogram": [
            {"carriers": k, "hubs": v} for k, v in sorted(carriers.items())
        ],
        "type_rows": sorted(types.values(), key=lambda row: (-row["count"], row["type"])),
    }


# --------------------------------------------------------------------------- the drawing


def _text_width(text: str, size: float, ratio: float) -> float:
    return max(1.0, len(text)) * size * ratio


def _elide(label: str) -> str:
    if len(label) <= _HUB_LABEL_MAX_CHARS:
        return label
    return label[: _HUB_LABEL_MAX_CHARS - 1].rstrip() + "…"


def _shift(node: dict[str, Any], dx: float, dy: float) -> None:
    node["x"] += dx
    node["y"] += dy
    node["box"]["x"] += dx
    node["box"]["y"] += dy


def _overlap(a: dict[str, float], b: dict[str, float], pad: float) -> tuple[float, float]:
    """Signed overlap of two boxes on each axis, inflated by `pad`. <= 0 on either axis is clear."""
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) + pad
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) + pad
    return dx, dy


def _separate(movable: list[dict[str, Any]], fixed: Sequence[dict[str, float]]) -> None:
    """Push overlapping labelled hubs apart, in place, deterministically.

    A hub starts at the centroid of its carriers, which is where it MEANS something, so two
    hubs carried by the same people start on the same point. The fan below has already
    spread those; this pass finishes the job against everything else on the canvas, moving
    each box along its axis of LEAST overlap so a hub stays near the people it belongs to.

    There is no clamp to a canvas here, unlike `/graph`'s equivalent: this figure's viewBox
    is fitted to its content afterwards, so a hub pushed outward enlarges the frame instead
    of being shoved back into a collision.
    """
    for _ in range(_SEPARATION_PASSES):
        moved = False
        for index, first in enumerate(movable):
            for second in movable[index + 1 :]:
                dx, dy = _overlap(first["box"], second["box"], _BOX_PAD)
                if dx <= 0 or dy <= 0:
                    continue
                moved = True
                if dx <= dy:
                    direction = 1.0 if second["x"] >= first["x"] else -1.0
                    _shift(first, -direction * dx / 2, 0.0)
                    _shift(second, direction * dx / 2, 0.0)
                else:
                    direction = 1.0 if second["y"] >= first["y"] else -1.0
                    _shift(first, 0.0, -direction * dy / 2)
                    _shift(second, 0.0, direction * dy / 2)
            for obstacle in fixed:
                dx, dy = _overlap(first["box"], obstacle, _BOX_PAD)
                if dx <= 0 or dy <= 0:
                    continue
                moved = True
                if dx <= dy:
                    away = 1.0 if first["x"] >= obstacle["x"] + obstacle["w"] / 2 else -1.0
                    _shift(first, away * dx, 0.0)
                else:
                    away = 1.0 if first["y"] >= obstacle["y"] + obstacle["h"] / 2 else -1.0
                    _shift(first, 0.0, away * dy)
        if not moved:
            return


def _fan(
    shared: Sequence[dict[str, Any]], at: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Pre-spread the hubs that would otherwise start on one point.

    Two hubs carried by the same people have the same centroid, and every edge of one would
    be drawn on top of every edge of the other — six lines rendering as one. A group is
    fanned along the perpendicular of its own axis, symmetrically about the centroid, so the
    group's centre of mass still claims what it claims.
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in shared:
        holders = tuple(sorted(pid for pid in row["carriers"] if pid in at))
        if len(holders) < MIN_CARRIERS:
            continue
        groups.setdefault(holders, []).append(row)

    offsets: dict[str, tuple[float, float]] = {}
    for holders, rows in groups.items():
        if len(rows) < 2:
            continue
        points = [at[pid] for pid in holders]
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        axis_x, axis_y = points[0][0] - cx, points[0][1] - cy
        length = math.hypot(axis_x, axis_y)
        if length < 1e-9:
            axis_x, axis_y, length = 1.0, 0.0, 1.0
        ux, uy = -axis_y / length, axis_x / length
        for index, row in enumerate(rows):
            slide = (index - (len(rows) - 1) / 2.0) * _FAN_STEP
            offsets[row["hub_id"]] = (ux * slide, uy * slide)
    return offsets


def _person_label(x: float, y: float, cosine: float, sine: float, radius: float) -> dict[str, Any]:
    """Where a person's name goes: radially outward, past the longest spoke they could have."""
    label_x = x + cosine * radius
    label_y = y + sine * radius
    if cosine > 0.35:
        anchor = "start"
    elif cosine < -0.35:
        anchor = "end"
    else:
        anchor = "middle"
        label_y += 13.0 if sine >= 0 else -6.0
    return {"label_x": label_x, "label_y": label_y, "anchor": anchor}


def _label_box(label_x: float, label_y: float, anchor: str, text: str) -> dict[str, float]:
    width = _text_width(text, _PERSON_LABEL_SIZE, _PERSON_LABEL_RATIO)
    if anchor == "start":
        left = label_x
    elif anchor == "end":
        left = label_x - width
    else:
        left = label_x - width / 2
    return {
        "x": left,
        "y": label_y - _PERSON_LABEL_SIZE,
        "w": width,
        "h": _PERSON_LABEL_SIZE * 1.35,
    }


def _spokes(
    person: dict[str, Any], rows: Sequence[dict[str, Any]], spread: float, top: float
) -> list[dict[str, Any]]:
    """One spoke per hub only this person carries, fanned into the sector facing outward.

    Length is the hub's worth, so a burr's silhouette is the shape of what the corpus knows
    about that person: long spikes are rare, heavily-boosted hubs; short ones are cities and
    schools. The label is not drawn — sixty-six labels is a wall — it is in the spoke's own
    SVG `<title>`, which is a browser tooltip and needs no script, and in the table below.
    """
    if not rows:
        return []
    base = person["angle"]
    count = len(rows)
    spokes: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if person["alone"]:
            angle = base + 2 * math.pi * index / count
        elif count == 1:
            angle = base
        else:
            angle = base + (index / (count - 1) - 0.5) * 2 * spread
        share = (row["worth"] / top) if top > 0 else 0.0
        length = _SPOKE_MIN + _SPOKE_SPAN * share
        inner = _PERSON_R + _SPOKE_GAP
        ux, uy = math.cos(angle), math.sin(angle)
        spokes.append(
            {
                "hub_id": row["hub_id"],
                "label": row["label"],
                "type_word": row["type_word"],
                "worth": row["worth"],
                "worthless": row["worthless"],
                "x1": person["x"] + ux * inner,
                "y1": person["y"] + uy * inner,
                "x2": person["x"] + ux * (inner + length),
                "y2": person["y"] + uy * (inner + length),
                "tip_r": _SPOKE_TIP_R,
            }
        )
    return spokes


def corpus_layout(
    roster: Sequence[dict[str, Any]],
    shared: Sequence[dict[str, Any]],
    solo: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Coordinates for every person, spoke, shared hub and edge, in SVG user space.

    People sit on an ellipse in roster order, clockwise from the top. Each person's private
    hubs radiate outward from them as a burr; each shared hub sits at the centroid of the
    people who carry it, so its position is information — a hub between two names is carried
    by exactly those two, a hub in the middle by everybody.

    The frame is FITTED to the drawing rather than the drawing clamped to a frame: geometry
    is computed about the origin and the returned `view_x/view_y/width/height` is the bounding
    box of everything, inflated by a margin and nudged toward a readable aspect ratio. A long
    name on the rim therefore widens the picture instead of being cropped by it, which is the
    failure mode of a fixed canvas when a corpus arrives with longer names than the last one.
    """
    n = len(roster)
    crowd = max(0, n - _RING_FROM)
    rx = _RING_RX + _RING_GROW_X * crowd
    ry = _RING_RY + _RING_GROW_Y * crowd
    alone = n == 1

    solo_for: dict[str, list[dict[str, Any]]] = {}
    for row in solo:
        for person_id in row["carriers"]:
            solo_for.setdefault(person_id, []).append(row)

    all_worths = [row["worth"] for row in list(shared) + list(solo)]
    top_worth = max(all_worths) if all_worths else 0.0
    spread = min(_SPOKE_SPREAD_MAX, _SPOKE_SPREAD_SHARE * (2 * math.pi / n)) if n else 0.0

    people: list[dict[str, Any]] = []
    at: dict[str, tuple[float, float]] = {}
    for index, row in enumerate(roster):
        angle = -math.pi / 2 + (2 * math.pi * index / n if n else 0.0)
        # A lone person belongs in the middle of the canvas with their name beneath them,
        # not on the rim of a ring that has nothing else on it.
        cosine = 0.0 if alone else math.cos(angle)
        sine = 1.0 if alone else math.sin(angle)
        x = 0.0 if alone else rx * math.cos(angle)
        y = 0.0 if alone else ry * math.sin(angle)
        person = {
            **row,
            "x": x,
            "y": y,
            "r": _PERSON_R,
            "angle": angle if not alone else -math.pi / 2,
            "alone": alone,
        }
        person["spokes"] = _spokes(
            person,
            sorted(solo_for.get(row["person_id"], []), key=lambda r: (-r["worth"], r["label"])),
            spread,
            top_worth,
        )
        # The name sits just past THIS person's longest spoke, not past the longest spoke
        # anybody could have. A uniform radius lines the names up prettily and leaves a
        # person the resolver refused — who has no burr at all — with their name floating
        # eighty units from a dot it is supposed to be labelling.
        reach = max(
            (math.hypot(s["x2"] - x, s["y2"] - y) for s in person["spokes"]),
            default=_PERSON_R,
        )
        person.update(_person_label(x, y, cosine, sine, max(_PERSON_R + 16.0, reach + _NAME_GAP)))
        people.append(person)
        at[row["person_id"]] = (x, y)

    fan = _fan(shared, at)
    hubs: list[dict[str, Any]] = []
    for row in shared:
        points = [at[pid] for pid in row["carriers"] if pid in at]
        if not points:
            continue
        offset = fan.get(row["hub_id"], (0.0, 0.0))
        x = sum(p[0] for p in points) / len(points) + offset[0]
        y = sum(p[1] for p in points) / len(points) + offset[1]
        share = (row["worth"] / top_worth) if top_worth > 0 else 0.0
        radius = _HUB_R_MIN + _HUB_R_SPAN * share
        display = _elide(row["label"])
        sub = f"{row['type_word']} · {row['n_carriers']} people"
        label_w = _text_width(display, _HUB_LABEL_SIZE, _HUB_LABEL_RATIO)
        sub_w = _text_width(sub, _HUB_SUB_SIZE, _HUB_SUB_RATIO)
        box_w = max(2 * radius, label_w, sub_w) + 12.0
        label_dy = radius + 16.0
        sub_dy = label_dy + 13.0
        box_h = radius + sub_dy + 12.0
        hubs.append(
            {
                **row,
                "x": x,
                "y": y,
                "r": radius,
                "display_label": display,
                "sub": sub,
                "label_dy": label_dy,
                "sub_dy": sub_dy,
                "box": {"x": x - box_w / 2, "y": y - radius - 6.0, "w": box_w, "h": box_h},
            }
        )

    obstacles = [
        {"x": p["x"] - _PERSON_R, "y": p["y"] - _PERSON_R, "w": 2 * _PERSON_R, "h": 2 * _PERSON_R}
        for p in people
    ]
    _separate(hubs, obstacles)

    edges = _edges(people, hubs)
    # `linked` is on the person's SVG tooltip, not in their styling. An identified person who
    # shares nothing is a full participant in the corpus — they are drawn solid, with their
    # own burr — and dashing five of seven people because the corpus has not joined up yet
    # would make a true answer look like a page that failed.
    linked = {pid for hub in hubs for pid in hub["carriers"]}
    for person in people:
        person["linked"] = person["person_id"] in linked

    frame = _frame(people, hubs)
    return {"people": people, "hubs": hubs, "edges": edges, **frame}


def _edges(
    people: Sequence[dict[str, Any]], hubs: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One line per (carrier, shared hub), trimmed to the two node boundaries."""
    by_id = {person["person_id"]: person for person in people}
    weights = [
        hub["worth"] * carrier["recency"] for hub in hubs for carrier in _recencies(hub)
    ]
    top = max(weights) if weights else 0.0

    edges: list[dict[str, Any]] = []
    for hub in hubs:
        for carrier in _recencies(hub):
            person = by_id.get(carrier["person_id"])
            if person is None:
                continue
            weight = hub["worth"] * carrier["recency"]
            share = (weight / top) if top > 0 else 0.0
            dx = hub["x"] - person["x"]
            dy = hub["y"] - person["y"]
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx / length, dy / length
            edges.append(
                {
                    "person_id": carrier["person_id"],
                    "hub_id": hub["hub_id"],
                    "x1": person["x"] + ux * person["r"],
                    "y1": person["y"] + uy * person["r"],
                    "x2": hub["x"] - ux * hub["r"],
                    "y2": hub["y"] - uy * hub["r"],
                    "width": _EDGE_WIDTH_MIN + _EDGE_WIDTH_SPAN * share,
                    "opacity": _EDGE_OPACITY_MIN + _EDGE_OPACITY_SPAN * share,
                }
            )
    return edges


def _recencies(hub: dict[str, Any]) -> list[dict[str, Any]]:
    """Each carrier and their own recency on the edge into this hub.

    `corpus_view` attaches `recency_by_person` before laying anything out. `corpus_layout` is
    exported and can be called with rows straight out of `hub_rows`, which have not been
    through that step; those get recency 1.0 rather than a `KeyError` mid-drawing.
    """
    rows = hub.get("recency_by_person")
    if rows is not None:
        return list(rows)
    return [{"person_id": pid, "recency": 1.0} for pid in hub["carriers"]]


def _frame(
    people: Sequence[dict[str, Any]], hubs: Sequence[dict[str, Any]]
) -> dict[str, float]:
    """The viewBox: the bounding box of everything drawn, with a margin and a sane aspect."""
    boxes: list[dict[str, float]] = []
    for person in people:
        boxes.append(
            {
                "x": person["x"] - _PERSON_R,
                "y": person["y"] - _PERSON_R,
                "w": 2 * _PERSON_R,
                "h": 2 * _PERSON_R,
            }
        )
        boxes.append(
            _label_box(person["label_x"], person["label_y"], person["anchor"], person["name"])
        )
        for spoke in person["spokes"]:
            boxes.append(
                {
                    "x": min(spoke["x1"], spoke["x2"]) - _SPOKE_TIP_R,
                    "y": min(spoke["y1"], spoke["y2"]) - _SPOKE_TIP_R,
                    "w": abs(spoke["x2"] - spoke["x1"]) + 2 * _SPOKE_TIP_R,
                    "h": abs(spoke["y2"] - spoke["y1"]) + 2 * _SPOKE_TIP_R,
                }
            )
    boxes.extend(hub["box"] for hub in hubs)
    if not boxes:
        return {"view_x": 0.0, "view_y": 0.0, "width": _MIN_WIDTH, "height": _MIN_WIDTH / 2}

    left = min(box["x"] for box in boxes) - _MARGIN
    right = max(box["x"] + box["w"] for box in boxes) + _MARGIN
    top = min(box["y"] for box in boxes) - _MARGIN
    bottom = max(box["y"] + box["h"] for box in boxes) + _MARGIN
    width = max(_MIN_WIDTH, right - left)
    height = max(1.0, bottom - top)
    # Keep the frame inside a readable envelope: a very wide, very short viewBox scaled to
    # the column renders its labels at a few pixels, and a very tall one wastes the width.
    height = min(max(height, width / _MAX_ASPECT), width / _MIN_ASPECT)
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    return {
        "view_x": cx - width / 2,
        "view_y": cy - height / 2,
        "width": width,
        "height": height,
    }


# --------------------------------------------------------------------------- the view


def _sentence_list(parts: Sequence[str]) -> str:
    """"a, b and c" — a list a host can read out, not a comma-joined dump."""
    items = [part for part in parts if part]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _names(roster: Sequence[dict[str, Any]], person_ids: Sequence[str]) -> list[str]:
    by_id = {row["person_id"]: row["name"] for row in roster}
    return [by_id.get(pid, pid) for pid in person_ids]


def _alt_text(
    state: str, roster: Sequence[dict[str, Any]], shared: Sequence[dict[str, Any]], stats: dict
) -> tuple[str, str]:
    """The `aria-label`/`<title>` and the `<desc>` for the drawing.

    A picture with no text alternative is announced as "image", and the whole content of this
    one is names and numbers a page can perfectly well say out loud. It is markup, not
    script, so it costs nothing.
    """
    names = _sentence_list([row["name"] for row in roster])
    if state == "empty":
        return ("An empty corpus map.", "No dossiers have been built yet.")
    if state == "alone":
        return (
            f"A corpus map of one person: {names}.",
            f"{names} is the only dossier held, so nothing can be shared.",
        )
    if state == "unjoined":
        return (
            f"A corpus map of {len(roster)} people with no shared hubs.",
            f"On the roster: {names}. Every one of the {stats['hubs']} hubs is carried by "
            "exactly one person, so nothing on the record joins any two of them.",
        )
    hubs = _sentence_list(
        [f"{row['label']} ({row['carrier_sentence']})" for row in shared]
    )
    return (
        f"A corpus map of {len(roster)} people and {stats['hubs']} hubs, "
        f"{len(shared)} of them shared.",
        f"On the roster: {names}. Shared: {hubs}.",
    )


def corpus_view(store: DossierStore) -> dict[str, Any]:
    """Everything `corpus.html` needs, computed here so the template stays declarative.

    Four states, named rather than inferred in the template, because each is a deliberate
    answer and a `{% if %}` chain hides which:

    * `empty` — no dossiers at all. There is nothing to draw and the page says so.
    * `alone` — one dossier. One person and their own burr, which is a real answer.
    * `unjoined` — two or more people and no hub carried by two of them. The burrs are drawn
      with nothing between them: the honest picture of a corpus that has not joined up.
    * `corpus` — the ordinary case.
    """
    roster = corpus_roster(store)
    split = hub_rows(store, roster)
    shared = split["shared"]
    solo = split["solo"]

    for row in shared:
        evidence = hub_evidence(store, row["hub_id"], row["carriers"])
        row["evidence"] = evidence
        row["recency_by_person"] = [
            {"person_id": item["person_id"], "recency": item["recency"]} for item in evidence
        ]
        row["carrier_sentence"] = _sentence_list(_names(roster, row["carriers"]))
    for row in solo:
        row["carrier_sentence"] = _sentence_list(_names(roster, row["carriers"]))

    stats = corpus_statistics(store)
    stats["shared_hubs"] = len(shared)
    stats["solo_hubs"] = len(solo)

    figure = corpus_layout(roster, shared, solo) if roster else None

    if not roster:
        state = "empty"
    elif len(roster) == 1:
        state = "alone"
    elif not shared:
        state = "unjoined"
    else:
        state = "corpus"

    # The long tail, per person rather than as one ranking. On the ten-person corpus every
    # hub with a single carrier has the SAME idf — `ln(7/2)` — so a global "heaviest hubs"
    # table ties on weight and degenerates into an alphabetical slice of whichever companies
    # sort first, which looks like a ranking and is not one. Grouped by carrier, every one of
    # the hubs is on the page, each weight is read against its neighbours, and the question a
    # reader actually has ("what does it know about HIM?") is the one the table answers.
    names = {row["person_id"]: row["name"] for row in roster}
    by_person: dict[str, list[dict[str, Any]]] = {}
    for row in shared + solo:
        for person_id in row["carriers"]:
            by_person.setdefault(person_id, []).append(
                {
                    "hub_id": row["hub_id"],
                    "label": row["label"],
                    "type": row["type"],
                    "worth": row["worth"],
                    "shared": len(row["carriers"]) >= MIN_CARRIERS,
                    "others": _sentence_list(
                        [names.get(pid, pid) for pid in row["carriers"] if pid != person_id]
                    ),
                }
            )
    for rows in by_person.values():
        rows.sort(key=lambda row: (-row["worth"], row["label"].lower()))

    per_person = sorted(roster, key=lambda row: (-row["n_hubs"], row["name"].lower()))
    counts = _fact_counts(store)
    for row in per_person:
        row.update(counts.get(row["person_id"], {"kept": 0, "withheld": 0}))
        row["hubs"] = by_person.get(row["person_id"], [])

    summary, description = _alt_text(state, roster, shared, stats)
    return {
        "state": state,
        "roster": roster,
        "figure": figure,
        "stats": stats,
        "shared_hubs": shared,
        "solo_hubs": solo,
        "per_person": per_person,
        "unidentified": [row for row in roster if not row["in_graph"]],
        "unidentified_sentence": _sentence_list(
            [row["name"] for row in roster if not row["in_graph"]]
        ),
        "graph_summary": summary,
        "graph_description": description,
        "dossier_dir": str(store.dossier_dir),
        # R13's paragraph, on a page that shows material out of a dossier. Every host-facing
        # surface that prints researched material states what it never prints.
        "exclusion_policy": EXCLUSION_POLICY,
    }


def _fact_counts(store: DossierStore) -> dict[str, dict[str, int]]:
    """Per person: how many of their facts a host may see, and how many were held back."""
    counts: dict[str, dict[str, int]] = {}
    for person_id, dossier in store.dossiers.items():
        kept = sum(1 for fact in dossier.facts if withholding_reason(fact) is None)
        counts[person_id] = {"kept": kept, "withheld": len(dossier.facts) - kept}
    return counts
