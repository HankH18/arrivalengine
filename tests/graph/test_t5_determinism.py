"""T-013: identical input must produce an identical ``Match``, whatever order it arrives in.

A caller loading ``data/dossiers/`` gets whatever order the filesystem hands back, so dossier
order is not an input the product controls. ``build_graph``'s existing guard test checks node
attributes and the edge SET across forward-vs-reversed -- 2 of 720 orders, and it never calls
``match()``. That left the one field nothing checked: ``Match.path``.

The defect this pins, measured before the fix on the six-dossier corpus below: **480 of 720
permutations produced a different path for byte-identical input**, while every score,
contribution and why stayed identical across all 720. ``nx.shortest_path`` breaks an
equal-cost tie by adjacency insertion order, and adjacency insertion order was dossier order.

Nothing here compares against a stored answer: the assertion is that the product agrees with
ITSELF on the same input, which is a property no fixture can be paraphrased into.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest
from t5_graph_helpers import filler, make_dossier, make_hub

from arrival.graph import build_graph, match, person_node

pytestmark = pytest.mark.ticket("T-5")

#: Two company hubs and six people. alice and frank share NO hub, but three people (bob,
#: carol, dan) carry both, so alice reaches frank by three routes of EXACTLY equal cost --
#: the tie a weighted shortest-path search has to break by something, and the something it
#: used to reach for was insertion order.
_SHARED_A = ("company:port-authority", "Port Authority", "company")
_SHARED_B = ("company:quay-holdings", "Quay Holdings", "company")

_CORPUS_SPEC: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("alice", (_SHARED_A,)),
    ("bob", (_SHARED_A, _SHARED_B)),
    ("carol", (_SHARED_A, _SHARED_B)),
    ("dan", (_SHARED_A, _SHARED_B)),
    ("eve", (_SHARED_B,)),
    ("frank", (_SHARED_B,)),
)
_IDS = tuple(person_id for person_id, _ in _CORPUS_SPEC)


def _corpus():
    return [
        make_dossier(person_id, person_id.title(), [make_hub(*spec) for spec in specs])
        for person_id, specs in _CORPUS_SPEC
    ]


def _answers(order) -> list:
    """Every ordered pair's full answer, in an order that cannot itself carry the input's."""
    graph = build_graph(list(order))
    out = []
    for arriving in sorted(_IDS):
        for m in match(graph, arriving, sorted(p for p in _IDS if p != arriving)):
            out.append(
                {
                    "pair": [arriving, m.other.person_id],
                    "score": m.score,
                    "why": m.why,
                    "path": list(m.path),
                    "contributions": [
                        [c.hub.hub_id, c.idf_weight, c.recency, c.type_boost, c.contribution]
                        for c in m.contributions
                    ],
                }
            )
    return out


def _fingerprint(answers) -> str:
    return hashlib.sha256(json.dumps(answers, sort_keys=True).encode()).hexdigest()


def test_the_corpus_actually_contains_the_equal_cost_tie_this_module_exists_for():
    """Without the tie the permutation sweep below is 720 runs of nothing."""
    graph = build_graph(_corpus())
    routes = list(
        nx.all_shortest_paths(graph, person_node("alice"), person_node("frank"), weight="cost")
    )
    assert len(routes) >= 3, (
        "alice and frank must be joined by several EQUAL-cost routes, or a shortest-path "
        f"search has no tie to break by insertion order: {routes}"
    )
    assert all(len(route) == 5 for route in routes), routes
    pair = match(graph, "alice", ["frank"])[0]
    assert pair.contributions == [], "alice and frank must share no hub at all"


def test_every_one_of_the_720_dossier_orders_gives_a_byte_identical_answer():
    baseline = _answers(_corpus())
    expected = _fingerprint(baseline)

    differing = []
    for permutation in itertools.permutations(_corpus()):
        if _fingerprint(_answers(permutation)) != expected:
            differing.append([d.person.person_id for d in permutation])

    assert differing == [], (
        f"{len(differing)} of 720 dossier orders answered differently for identical input; "
        f"first offender {differing[0] if differing else None}"
    )


def test_the_path_field_specifically_is_order_independent():
    """The field the previous guard never looked at, isolated so a regression names itself."""
    baseline = {tuple(a["pair"]): tuple(a["path"]) for a in _answers(_corpus())}
    for permutation in itertools.permutations(_corpus()):
        for answer in _answers(permutation):
            pair = tuple(answer["pair"])
            assert tuple(answer["path"]) == baseline[pair], (
                f"order {[d.person.person_id for d in permutation]} moved {pair}'s path from "
                f"{list(baseline[pair])} to {answer['path']}"
            )


def test_the_answer_survives_a_different_python_hash_seed():
    """Dict and set iteration order is seeded per process, so one process cannot see this.

    Run out of process on purpose, three seeds, comparing the fingerprint of the same corpus
    built in two different orders under each.
    """
    root = Path(__file__).resolve().parents[2]
    program = (
        "import hashlib,json,sys;"
        f"sys.path[:0]=[{str(root / 'src')!r},{str(Path(__file__).parent)!r}];"
        "from test_t5_determinism import _answers,_corpus,_fingerprint;"
        "c=_corpus();"
        "print(_fingerprint(_answers(c)),_fingerprint(_answers(list(reversed(c)))))"
    )
    seen = set()
    for seed in ("0", "1", "524287"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        env.pop("PYTHONPATH", None)
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            env=env,
            cwd=root,
            timeout=120,
        )
        assert result.returncode == 0, f"seed {seed}: {result.stderr}"
        forward, backward = result.stdout.split()
        assert forward == backward, f"seed {seed}: dossier order moved the answer"
        seen.add(forward)
    assert len(seen) == 1, f"the answer itself moved with PYTHONHASHSEED: {seen}"


def test_a_pair_that_shares_nothing_is_told_so_rather_than_routed_around(  # noqa: E501
):
    """The shape that made the tie reachable: no shared hub, so no route is invented.

    The old code answered this pair with a five-node acquaintance chain chosen by insertion
    order. There is no ``why`` that chain could be the picture of.
    """
    graph = build_graph(_corpus())
    m = match(graph, "alice", ["frank"])[0]
    assert m.score == 0
    assert m.path == []
    assert m.why == match(build_graph(list(reversed(_corpus()))), "alice", ["frank"])[0].why


def test_two_people_with_no_hubs_at_all_still_answer():
    graph = build_graph([make_dossier("a", "A", []), make_dossier("b", "B", []), *filler(3)])
    m = match(graph, "a", ["b"])[0]
    assert (m.score, m.path, m.contributions) == (0.0, [], [])
