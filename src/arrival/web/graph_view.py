"""The interest graph as a picture: R17's `GET /graph`, computed here, drawn in SVG.

R17 is one sentence — "the interest graph (people as leaves, entities/interests as hubs)
SHALL be viewable as a simple rendered graph on `/graph` showing present people and their
shared hubs" — and DESIGN's route table adds only `| GET /graph | — | optional R17 |`. So
everything below is a judgement, and each one is written down where it is made.

**This module is a VIEW, not a feature.** `DossierStore.graph` is a live `networkx.Graph`
built at boot by `arrival.graph.build_graph`; every number this page shows — a hub's `idf`,
its `type_boost`, a person's `recency` on an edge — is read off that graph, never recomputed
here. The one thing this module derives of its own is geometry.

**Why a layout of our own rather than `nx.spring_layout`.** networkx ships layouts and they
were considered. Two reasons against:

* A force layout's coordinates mean nothing to a reader — a node is where the simulation
  left it. Here a hub is placed at the CENTROID OF THE PRESENT PEOPLE WHO CARRY IT, so its
  position *is* information: a hub two people share sits on the line between them, and a hub
  everybody carries sits in the middle. That is the picture R17 describes.
* `nx.spring_layout` and `nx.kamada_kawai_layout` both go through numpy/scipy and both are
  free to place two labels on top of each other; the de-collision pass would have to be
  written anyway. Writing the placement too costs one function and buys meaning.

**What is drawn, and what is not.** R17 scopes itself: "present people and their SHARED
hubs". A hub carried by exactly one present person is not a connection, so it is named in a
sentence under the figure rather than drawn — see :func:`graph_view`. A shared hub whose
`idf` clamped to zero (everybody on the roster carries it) IS drawn, marked as worth
nothing, because the digest's own reasoning table lists those hubs for the same reason: "we
do both live in Austin, and Austin is worth nothing here" is part of an honest answer.

**R11/R12.** `/graph` is host-facing, not `/debug`. `arrival.graph` deliberately does not
filter hubs — matching is not display — so a hub whose evidence was taste-excluded can
legitimately be shared and must still never have that evidence quoted here.
:func:`shared_hub_evidence` therefore applies :func:`arrival.taste.is_displayable` to every
fact before it can reach the page, and a hub with nothing displayable behind it says so
rather than rendering an empty list. This is the same rule `render.py` states for the
digest, implemented separately because `render.py` belongs to another change.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import networkx as nx

from arrival.contracts import Dossier, Fact, PersonRef
from arrival.graph import hub_node, person_node
from arrival.taste import EXCLUSION_POLICY, is_displayable
from arrival.web.store import DossierStore

__all__ = [
    "CANVAS_WIDTH",
    "MIN_PRESENT_CARRIERS",
    "graph_view",
    "hub_rows",
    "layout",
    "present_roster",
    "shared_hub_evidence",
]

#: A hub is SHARED once two present people carry it. One carrier is a fact about a person,
#: not a connection between two, and R17 asks for the connections.
MIN_PRESENT_CARRIERS = 2

#: The SVG's user-space width. The page scales it with `width="100%"`, so this is an aspect
#: ratio and a coordinate system rather than a pixel size.
CANVAS_WIDTH = 840

#: The people ring, before it is grown for a crowd.
_RING_RX = 262.0
_RING_RY = 158.0
#: Room outside the ring for a person's name.
_LABEL_BAND = 84.0

_PERSON_R = 12.0
_HUB_R_MIN = 11.0
_HUB_R_SPAN = 15.0

#: Label metrics. The page cannot measure text without JavaScript, so widths are estimated
#: from the character count at the ratios Cormorant Garamond and Inter actually run at. The
#: estimate only has to be generous enough that two boxes pushed apart by it do not touch.
_HUB_LABEL_SIZE = 16.0
_HUB_LABEL_RATIO = 0.50
_HUB_SUB_SIZE = 10.0
_HUB_SUB_RATIO = 0.78  # Inter at 0.14em letter-spacing, small caps
_PERSON_LABEL_SIZE = 17.0
_PERSON_LABEL_RATIO = 0.50

#: A hub label longer than this is elided in the drawing and given in full in the table
#: below it (and in the node's SVG `<title>`, which is a browser tooltip and needs no script).
_HUB_LABEL_MAX_CHARS = 30

#: How far apart hubs sharing one carrier set are fanned before the separation pass runs.
#: Wide enough that their labels start clear of each other on the common two-person graph.
_FAN_STEP = 150.0

#: Gap held between two label boxes, and between a hub box and a person's.
_BOX_PAD = 10.0
#: Keep everything this far inside the canvas.
_CANVAS_MARGIN = 12.0
_SEPARATION_PASSES = 260

_EDGE_WIDTH_MIN = 1.0
_EDGE_WIDTH_SPAN = 3.6
_EDGE_OPACITY_MIN = 0.28
_EDGE_OPACITY_SPAN = 0.5

#: How a hub type reads in the caption under a node. `HubType` is a closed Literal, so this
#: is total; the `.get` fallback exists only so a future member renders as itself.
_TYPE_WORDS: dict[str, str] = {
    "company": "company",
    "investor": "investor",
    "school": "school",
    "board": "board",
    "topic": "topic",
    "city": "city",
    "technology": "technology",
    "event": "event",
    "cause": "cause",
    "person": "person",
}


# --------------------------------------------------------------------------- the roster


def present_roster(store: DossierStore, present_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Who is in the building, in arrival order, with what the graph knows about each.

    Arrival order rather than alphabetical, because `Presence` keeps it and it is the only
    order that carries information (`presence.py`: "who walked in most recently is real
    information to a host"). Duplicates are collapsed defensively; `Presence` cannot produce
    one, but this function is also called with a plain list in tests.

    `in_graph` is False for a person whose dossier is UNRESOLVED: `DossierStore` keeps those
    out of the graph population on purpose, so they carry no hubs and can share nothing. They
    are still present, and R17 says to show present people, so they are drawn — as a leaf
    with no edges, which is exactly what they are.
    """
    graph = store.graph
    roster: list[dict[str, Any]] = []
    seen: set[str] = set()
    for person_id in present_ids:
        if person_id in seen:
            continue
        seen.add(person_id)
        dossier: Dossier | None = store.get(person_id)
        node = person_node(person_id)
        person: PersonRef | None = None
        if node in graph:
            person = graph.nodes[node].get("person")
        if person is None and dossier is not None:
            person = dossier.person
        roster.append(
            {
                "person_id": person_id,
                "name": person.name if person is not None else person_id,
                "details": list(person.details) if person is not None else [],
                "node": node,
                "in_graph": node in graph,
                "unresolved": (
                    dossier is not None and dossier.resolution.status != "resolved"
                ),
            }
        )
    return roster


def _hub_nodes_of(graph: nx.Graph, node: str) -> list[str]:
    if node not in graph:
        return []
    return sorted(n for n in graph[node] if graph.nodes[n].get("kind") == "hub")


# --------------------------------------------------------------------------- the hubs


def shared_hub_evidence(
    store: DossierStore, hub_id: str, person_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Per carrier, the DISPLAYABLE facts behind their edge into this hub.

    The edge carries that person's own `Hub`, whose `evidence_fact_ids` resolve in that
    person's dossier and nobody else's (`graph.build_graph`). `is_displayable` is applied to
    every one of them, because this is the last code between a taste-excluded sentence and a
    host-facing page and `arrival.graph` does not filter hubs — a hub whose evidence was
    withheld can legitimately be shared and must still never be quoted here (R11, R12).

    A carrier whose evidence is entirely withheld comes back with an empty `facts` list
    rather than being dropped: they really do carry the hub, and the page says so in the
    absence voice instead of quietly showing one fewer person than the graph has.
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
        person = graph.nodes[source].get("person") if source in graph else None
        rows.append(
            {
                "person_id": person_id,
                "name": person.name if person is not None else person_id,
                "recency": float(graph.edges[source, target]["recency"]),
                "facts": facts,
            }
        )
    return rows


def hub_rows(store: DossierStore, roster: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Split every hub the present people carry into the shared ones and the solo ones.

    Sorted by `worth` (`idf * type_boost`) descending, then by label, then by hub id: the
    rarest, most telling connection is first everywhere on the page, and the order is total
    so two runs over one corpus render byte-identically.

    `n_carriers` is the count across the WHOLE roster, not the present subset, because that
    is what `idf` was computed from. Both numbers are shown, since "two of the five people we
    hold dossiers on" and "two of the two people here" are different claims.
    """
    graph = store.graph
    present_ids = [row["person_id"] for row in roster if row["in_graph"]]
    carriers: dict[str, set[str]] = {}
    for person_id in present_ids:
        for node in _hub_nodes_of(graph, person_node(person_id)):
            carriers.setdefault(node, set()).add(person_id)

    shared: list[dict[str, Any]] = []
    solo: list[dict[str, Any]] = []
    for node, holding in carriers.items():
        data = graph.nodes[node]
        idf = float(data["idf"])
        boost = float(data["type_boost"])
        # Present-carrier order follows the roster, so the evidence list under a hub reads
        # in the same order as the list of people at the top of the page.
        holders = [pid for pid in present_ids if pid in holding]
        row = {
            "hub_id": str(data["hub_id"]),
            "node": node,
            "label": str(data["label"]),
            "type": str(data["type"]),
            "type_word": _TYPE_WORDS.get(str(data["type"]), str(data["type"])),
            "idf": idf,
            "type_boost": boost,
            "worth": idf * boost,
            "worthless": idf <= 0.0,
            "carriers": holders,
            "n_present": len(holders),
            "n_carriers": int(data.get("n_carriers", len(holders))),
        }
        (shared if len(holders) >= MIN_PRESENT_CARRIERS else solo).append(row)

    def order(row: dict[str, Any]) -> tuple[float, str, str]:
        return (-row["worth"], row["label"].lower(), row["hub_id"])

    shared.sort(key=order)
    solo.sort(key=order)
    return {"shared": shared, "solo": solo}


# --------------------------------------------------------------------------- geometry


def _text_width(text: str, size: float, ratio: float) -> float:
    return max(1.0, len(text)) * size * ratio


def _elide(label: str) -> str:
    if len(label) <= _HUB_LABEL_MAX_CHARS:
        return label
    return label[: _HUB_LABEL_MAX_CHARS - 1].rstrip() + "…"


def _overlap(a: dict[str, float], b: dict[str, float], pad: float) -> tuple[float, float]:
    """Signed overlap of two boxes on each axis, inflated by `pad`. <= 0 on either axis is clear."""
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) + pad
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) + pad
    return dx, dy


def _separate(
    movable: list[dict[str, Any]],
    fixed: Sequence[dict[str, float]],
    width: float,
    height: float,
) -> None:
    """Push overlapping hub boxes apart, in place, deterministically.

    A hub starts at the centroid of its carriers, which is where it MEANS something, and two
    hubs carried by the same set of people therefore start on the same point — `Austin` and
    `Remote work`, both held by all five people in the frozen corpus, land exactly on top of
    each other. So the ideal position is a starting point and this pass makes the drawing
    legible around it, moving each box along its axis of LEAST overlap (which keeps the
    displacement small, so a hub stays near the people it belongs to).

    Determinism matters as much as separation: the iteration order is the caller's sort order,
    a dead-centre tie is broken by index rather than by chance, and there is no randomness
    anywhere, so one corpus renders one picture on every run and a test can assert about it.
    """
    for _ in range(_SEPARATION_PASSES):
        moved = False
        for i, first in enumerate(movable):
            for second in movable[i + 1 :]:
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
                    direction = 1.0 if first["x"] >= obstacle["x"] + obstacle["w"] / 2 else -1.0
                    _shift(first, direction * dx, 0.0)
                else:
                    direction = 1.0 if first["y"] >= obstacle["y"] + obstacle["h"] / 2 else -1.0
                    _shift(first, 0.0, direction * dy)
        for box in movable:
            _clamp(box, width, height)
        if not moved:
            return


def _shift(node: dict[str, Any], dx: float, dy: float) -> None:
    node["x"] += dx
    node["y"] += dy
    node["box"]["x"] += dx
    node["box"]["y"] += dy


def _clamp(node: dict[str, Any], width: float, height: float) -> None:
    box = node["box"]
    dx = 0.0
    dy = 0.0
    if box["x"] < _CANVAS_MARGIN:
        dx = _CANVAS_MARGIN - box["x"]
    elif box["x"] + box["w"] > width - _CANVAS_MARGIN:
        dx = (width - _CANVAS_MARGIN) - (box["x"] + box["w"])
    if box["y"] < _CANVAS_MARGIN:
        dy = _CANVAS_MARGIN - box["y"]
    elif box["y"] + box["h"] > height - _CANVAS_MARGIN:
        dy = (height - _CANVAS_MARGIN) - (box["y"] + box["h"])
    if dx or dy:
        _shift(node, dx, dy)


def _person_box(x: float, y: float, name: str, anchor: str, label_y: float) -> dict[str, float]:
    label_w = _text_width(name, _PERSON_LABEL_SIZE, _PERSON_LABEL_RATIO)
    if anchor == "start":
        left = x + _PERSON_R
    elif anchor == "end":
        left = x - _PERSON_R - label_w
    else:
        left = x - label_w / 2
    top = min(y - _PERSON_R, label_y - _PERSON_LABEL_SIZE)
    bottom = max(y + _PERSON_R, label_y + _PERSON_LABEL_SIZE * 0.35)
    return {
        "x": min(left, x - _PERSON_R),
        "y": top,
        "w": max(left + label_w, x + _PERSON_R) - min(left, x - _PERSON_R),
        "h": bottom - top,
    }


def _fan_offsets(
    shared: Sequence[dict[str, Any]], at: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Pre-spread the hubs that would otherwise start on one point.

    Two hubs carried by the same present people have the same centroid, and three of them
    (`Austin`, `Remote work` and `Foundry Seed 2019` between two people) would then be
    stacked on the segment joining those two — every edge drawn on top of every other edge,
    which renders as ONE line and hides five of the six.

    So a group is fanned along the perpendicular of its own axis: for two carriers that is
    the perpendicular of the chord, and the group opens into a lens whose strands are
    separately visible. The fan is symmetric about the centroid, so the group's centre of
    mass is still the thing the position claims it is.
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in shared:
        present_carriers = tuple(pid for pid in row["carriers"] if pid in at)
        if len(present_carriers) < 2:
            continue
        groups.setdefault(tuple(sorted(present_carriers)), []).append(row)

    offsets: dict[str, tuple[float, float]] = {}
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        points = [at[pid] for pid in key]
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        axis_x, axis_y = points[0][0] - cx, points[0][1] - cy
        length = math.hypot(axis_x, axis_y)
        if length < 1e-9:
            axis_x, axis_y, length = 1.0, 0.0, 1.0
        # perpendicular to the group's axis, unit length
        ux, uy = -axis_y / length, axis_x / length
        step = _FAN_STEP
        for index, row in enumerate(rows):
            slide = (index - (len(rows) - 1) / 2.0) * step
            offsets[row["hub_id"]] = (ux * slide, uy * slide)
    return offsets


def layout(
    roster: Sequence[dict[str, Any]], shared: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Coordinates for every node and edge, in SVG user space.

    People sit on an ellipse in ARRIVAL ORDER, clockwise from the top: the ring is the
    building, and reading it clockwise is reading the evening. Each hub starts at the
    centroid of the present people who carry it, which is what makes the drawing mean
    something — a hub two people share lies on the line between them; a hub everyone carries
    lies in the middle — and :func:`_separate` then makes it legible without discarding that.

    Node size and edge weight both read off the graph: a hub's radius scales with
    `idf * type_boost`, an edge's width with that worth times THIS person's recency. A hub
    the clamp zeroed therefore draws as a small hollow ring on hairlines, which is the true
    picture of a connection worth nothing.
    """
    n = len(roster)
    crowd = max(0, n - 6)
    rx = _RING_RX + 9.0 * crowd
    ry = _RING_RY + 13.0 * crowd
    width = float(CANVAS_WIDTH)
    height = 2.0 * (ry + _LABEL_BAND)
    cx = width / 2.0
    cy = height / 2.0

    people: list[dict[str, Any]] = []
    at: dict[str, tuple[float, float]] = {}
    for index, row in enumerate(roster):
        angle = -math.pi / 2 + (2 * math.pi * index / n if n else 0.0)
        # A lone person belongs in the middle of the canvas, not on the rim of a ring that
        # has nothing else on it.
        x = cx if n == 1 else cx + rx * math.cos(angle)
        y = cy if n == 1 else cy + ry * math.sin(angle)
        cosine = 0.0 if n == 1 else math.cos(angle)
        sine = 0.0 if n == 1 else math.sin(angle)
        if cosine > 0.35:
            anchor, label_x = "start", x + _PERSON_R + 9
        elif cosine < -0.35:
            anchor, label_x = "end", x - _PERSON_R - 9
        else:
            anchor, label_x = "middle", x
        label_y = y + (_PERSON_R + 22 if sine >= 0 else -(_PERSON_R + 13))
        if n == 1:
            anchor, label_x, label_y = "middle", x, y + _PERSON_R + 24
        people.append(
            {
                **row,
                "x": x,
                "y": y,
                "r": _PERSON_R,
                "label_x": label_x,
                "label_y": label_y,
                "anchor": anchor,
            }
        )
        at[row["person_id"]] = (x, y)

    worths = [row["worth"] for row in shared]
    top_worth = max(worths) if worths else 0.0

    # Hubs carried by the SAME set of present people share one centroid exactly, so they are
    # fanned apart before anything else happens — along the perpendicular of the group's own
    # geometry, which for the two-person case is the perpendicular of the chord joining them.
    # Doing this first rather than leaving it to `_separate` is what stops the two-person
    # graph collapsing into one vertical line: `_separate` moves along the axis of LEAST
    # overlap, which for wide label boxes is the axis the people are already on, and the
    # edges then lie on top of each other. Fanned sideways they read as a braid.
    fan = _fan_offsets(shared, at)

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
        sub = f"{row['type_word']} · {row['n_present']} here"
        label_w = _text_width(display, _HUB_LABEL_SIZE, _HUB_LABEL_RATIO)
        sub_w = _text_width(sub, _HUB_SUB_SIZE, _HUB_SUB_RATIO)
        box_w = max(2 * radius, label_w, sub_w) + 12.0
        label_dy = radius + 17.0
        sub_dy = label_dy + 14.0
        box_h = (radius + sub_dy + 6.0) + 6.0
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
                "label_w": label_w,
                "box": {"x": x - box_w / 2, "y": y - radius - 6.0, "w": box_w, "h": box_h},
            }
        )

    obstacles = [
        _person_box(p["x"], p["y"], p["name"], p["anchor"], p["label_y"]) for p in people
    ]
    _separate(hubs, obstacles, width, height)

    edges = _edges(people, hubs, at)
    for person in people:
        person["linked"] = any(person["person_id"] in hub["carriers"] for hub in hubs)
    # A leaf is drawn as LOOSE — dashed, muted — only when it is the odd one out. When
    # nothing at all is shared, every leaf is unjoined and dashing them all says nothing
    # while making a legitimate answer ("nobody here has anything in common yet") look like
    # a page that failed. The single-person graph is the same case.
    any_linked = any(person["linked"] for person in people)
    for person in people:
        person["loose"] = any_linked and not person["linked"]

    return {
        "width": width,
        "height": height,
        "people": people,
        "hubs": hubs,
        "edges": edges,
    }


def _edges(
    people: Sequence[dict[str, Any]],
    hubs: Sequence[dict[str, Any]],
    at: dict[str, tuple[float, float]],
) -> list[dict[str, Any]]:
    """One line per (present carrier, shared hub), trimmed to the two node boundaries.

    Trimmed rather than drawn centre-to-centre so a hairline does not emerge from inside a
    filled node, which at these sizes reads as a smudge.
    """
    by_id = {person["person_id"]: person for person in people}
    weights = [
        hub["worth"] * carrier["recency"]
        for hub in hubs
        for carrier in _carrier_recencies(hub)
    ]
    top = max(weights) if weights else 0.0

    edges: list[dict[str, Any]] = []
    for hub in hubs:
        for carrier in _carrier_recencies(hub):
            person = by_id.get(carrier["person_id"])
            if person is None or carrier["person_id"] not in at:
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
                    "weight": weight,
                    "recency": carrier["recency"],
                }
            )
    return edges


def _carrier_recencies(hub: dict[str, Any]) -> list[dict[str, Any]]:
    """Each present carrier and their own recency on the edge into this hub.

    `graph_view` attaches `recency_by_person` from the graph before laying anything out.
    `layout` is exported and can be called with hub rows straight out of :func:`hub_rows`,
    which have not been through that step; those get recency 1.0, i.e. every edge weighted by
    the hub's worth alone, rather than a `KeyError` in the middle of a drawing.
    """
    rows = hub.get("recency_by_person")
    if rows is not None:
        return list(rows)
    return [{"person_id": pid, "recency": 1.0} for pid in hub["carriers"]]


# --------------------------------------------------------------------------- the view


def _sentence_list(parts: Sequence[str]) -> str:
    """"a, b and c" — a list a host can read out, not a comma-joined dump."""
    items = [part for part in parts if part]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _names(roster: Sequence[dict[str, Any]], person_ids: Iterable[str]) -> list[str]:
    by_id = {row["person_id"]: row["name"] for row in roster}
    return [by_id.get(pid, pid) for pid in person_ids]


def graph_view(store: DossierStore, present_ids: Sequence[str]) -> dict[str, Any]:
    """Everything `graph.html` needs, computed here so the template stays declarative.

    The four states the page can be in are named rather than inferred in the template,
    because each one is a deliberate answer and a template `{% if %}` chain hides which:

    * `empty` — nobody is here. There is no graph, and the page says so in prose.
    * `alone` — one person. One leaf is drawn, because a graph of one person is a real
      answer to "who is here", and no hub can be shared by one person by definition.
    * `unconnected` — two or more people and no shared hub. The leaves are drawn with no
      edges: the honest picture, and the same shape R8 asks the digest to take.
    * `graph` — the ordinary case.

    Every degenerate state draws what it legitimately can and states the rest in a sentence,
    so a thin page reads as a true answer rather than as something that failed to load.
    """
    roster = present_roster(store, present_ids)
    split = hub_rows(store, roster)
    shared = split["shared"]
    solo = split["solo"]

    for row in shared + solo:
        evidence = shared_hub_evidence(store, row["hub_id"], row["carriers"])
        row["evidence"] = evidence
        row["recency_by_person"] = [
            {"person_id": item["person_id"], "recency": item["recency"]} for item in evidence
        ]
        row["displayable_facts"] = sum(len(item["facts"]) for item in evidence)
        row["carrier_sentence"] = _sentence_list(_names(roster, row["carriers"]))

    figure = layout(roster, shared) if roster else None

    if not roster:
        state = "empty"
    elif len(roster) == 1:
        state = "alone"
    elif not shared:
        state = "unconnected"
    else:
        state = "graph"

    linked = {pid for row in shared for pid in row["carriers"]}
    unlinked = [row for row in roster if row["person_id"] not in linked]
    # With one person here, naming the carrier after every hub repeats the same name down
    # the line; the sentence already says "held by one person here" and there is only one.
    solo_sentence = _sentence_list(
        [
            f"{row['label']} ({row['carrier_sentence']})" if len(roster) > 1 else row["label"]
            for row in solo
        ]
    )

    summary, description = _alt_text(state, roster, shared)
    return {
        "state": state,
        "roster": roster,
        "present_count": len(roster),
        "roster_size": len(store),
        "graph_population": int(store.graph.graph.get("n_people", 0)),
        "figure": figure,
        "shared_hubs": shared,
        "solo_hubs": solo,
        "solo_sentence": solo_sentence,
        "unlinked": unlinked,
        "unlinked_sentence": _sentence_list([row["name"] for row in unlinked]),
        "graph_summary": summary,
        "graph_description": description,
        # R13's paragraph, on a page that shows material out of a dossier. `index.html`
        # carries it for the same reason: any host-facing surface that prints researched
        # material states what it never prints.
        "exclusion_policy": EXCLUSION_POLICY,
    }


def _count(n: int, singular: str, plural: str | None = None) -> str:
    """`1 shared hub`, `2 shared hubs`, `1 person`, `3 people` — a count and a noun agreeing.

    Every count `_alt_text` speaks goes through this. The templates already do it inline
    (`graph.html`: `shared hub{{ '' if shared_hubs | length == 1 else 's' }}`;
    `corpus.html`: `hub{{ '' if person.n_hubs == 1 else 's' }}`), and the text alternative
    describes the SAME picture as the visible caption — so a hardcoded plural here does not
    merely read badly, it makes the screen-reader text disagree with the text on screen.
    That is what happened: with one shared hub the caption read "1 shared hub" and the
    `aria-label` fifteen lines away read "1 shared hubs".

    The `people` counts were correct only by accident of the state machine above — `empty`
    and `alone` intercept 0 and 1, so `len(roster)` was never 1 here. Routing them through
    this too means the sentence stays grammatical if that ever stops being true, rather
    than depending on a caller two functions away to keep it so.
    """
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _alt_text(
    state: str, roster: Sequence[dict[str, Any]], shared: Sequence[dict[str, Any]]
) -> tuple[str, str]:
    """The `aria-label`/`<title>` and the `<desc>` for the drawing.

    A picture with no text alternative is a picture a screen reader announces as "image", and
    the whole content of this one is names and labels the page can perfectly well say. The
    long form names every person and every shared hub, so the drawing is not the only way to
    read the graph — and because it is markup rather than script, it costs nothing to add.
    """
    names = _sentence_list([row["name"] for row in roster])
    if state == "empty":
        return ("An empty interest graph.", "Nobody is in the building.")
    if state == "alone":
        return (
            f"An interest graph with one person: {names}.",
            f"{names} is the only person in the building, so nothing is shared.",
        )
    if state == "unconnected":
        return (
            f"An interest graph of {_count(len(roster), 'person', 'people')} "
            "with no shared hubs.",
            f"In the building: {names}. Nothing on the record connects any two of them.",
        )
    hubs = _sentence_list(
        [
            f"{row['label']} ({row['carrier_sentence']})"
            if row.get("carrier_sentence")
            else row["label"]
            for row in shared
        ]
    )
    return (
        f"An interest graph of {_count(len(roster), 'person', 'people')} "
        f"joined by {_count(len(shared), 'shared hub')}.",
        f"In the building: {names}. Shared: {hubs}.",
    )
