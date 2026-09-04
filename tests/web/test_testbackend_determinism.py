"""Identical input, identical output — byte for byte, and across processes.

The packet's rule: "Anything that differs between two runs of identical input is a
finding". A previous lane found `resolve.verdict_attribute` returning a value chosen by
`PYTHONHASHSEED` (`resolve.py:723-726` records the repair), which is the shape of defect
this module exists to catch on every OTHER surface — a set iterated without a sort, a dict
built from an unordered comprehension, a `sorted()` whose key ties.

Two exemptions, and they are exemptions rather than misses:

* **`Digest.digest_id`** is `uuid.uuid4().hex[:16]` (`digest.py:999`). It identifies an
  ARRIVAL, not a corpus, so two arrivals for one person must NOT share it — a shared id
  would make the second overwrite the first in `app.state.digests`. That is asserted here
  as a requirement rather than tolerated as noise.
* **`Digest.created_at`** is `datetime.now(UTC)` (`digest.py:1008`) with no clock seam.

Everything else is compared verbatim. The cross-process half runs the same corpus under
several `PYTHONHASHSEED` values, because a set-iteration order bug is invisible in one
interpreter: CPython randomises string hashing per PROCESS, so two runs inside one test
session share the seed and agree with each other while disagreeing with production.

Grading references: the app's own output compared against itself, `uuid`'s documented
shape, and `contracts.Digest`'s field names. Nothing here has an answer key.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBACKEND")

OPENER = "Ask about the evaluation harness they open-sourced last spring."
ORDER = ("alpha", "bravo", "charlie", "delta")

#: Everything on a `Digest` except the two fields that identify the EVENT rather than the
#: corpus. Named as a set so a new field on the contract is compared by default rather than
#: silently exempted.
EVENT_FIELDS = {"digest_id", "created_at"}


@pytest.fixture
def corpus(tmp_path, request):
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def _llm():
    return LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})


def _run(corpus_dir) -> dict[str, str]:
    """One whole app lifetime: boot, four arrivals, then every surface it serves."""
    app = create_app(dossier_dir=corpus_dir, llm=_llm())
    with TestClient(app, raise_server_exceptions=False) as client:
        digest_ids = []
        for person_id in ORDER:
            response = client.post("/arrive", json={"person_id": person_id})
            assert response.status_code == 200, response.text
            digest_ids.append(response.json()["digest_id"])

        out = {
            "index": client.get("/").text,
            "building_html": client.get("/building").text,
            "building_json": client.get("/building",
                                        headers={"accept": "application/json"}).text,
            "graph": client.get("/graph").text,
            "corpus": client.get("/corpus").text,
        }
        for person_id, digest_id in zip(ORDER, digest_ids, strict=True):
            digest = app.state.digests[digest_id]
            out[f"digest_model:{person_id}"] = digest.model_dump_json(exclude=EVENT_FIELDS)
            page = client.get(f"/digest/{digest_id}").text
            # The id is printed on the page and in every anchor; masking it is the only way
            # to diff the PAGE rather than the event that produced it.
            out[f"digest_page:{person_id}"] = page.replace(digest_id, "<DIGEST-ID>")
        out["_digest_ids"] = json.dumps(digest_ids)
    return out


def _diff(name, left, right):
    lines = list(difflib.unified_diff(left.splitlines(), right.splitlines(),
                                      "run-1", "run-2", lineterm="", n=1))
    return f"{name} differs between two runs of identical input:\n" + "\n".join(lines[:60])


def test_two_apps_over_one_corpus_produce_byte_identical_output(corpus):
    first, second = _run(corpus), _run(corpus)
    assert set(first) == set(second)
    for name in sorted(first):
        if name == "_digest_ids":
            continue
        assert first[name] == second[name], _diff(name, first[name], second[name])


def test_the_digest_ids_are_the_only_thing_that_moves(corpus):
    """States the exemption as a positive claim: two runs share nothing at all in their
    digest ids, and each id is a 16-character uuid4 hex prefix."""
    first, second = _run(corpus), _run(corpus)
    left = json.loads(first["_digest_ids"])
    right = json.loads(second["_digest_ids"])
    assert set(left).isdisjoint(right), "two independent arrivals reused a digest id"
    for digest_id in left + right:
        assert len(digest_id) == 16
        int(digest_id, 16)  # raises unless it is hex


def test_rebuilding_the_same_app_twice_in_one_process_is_also_identical(corpus):
    """The two runs above use two apps. This one proves the corpus itself carries no
    process state that a second boot would inherit — a module-level cache in `render` or
    `graph`, say — by interleaving the boots rather than running them back to back."""
    a = create_app(dossier_dir=corpus, llm=_llm())
    b = create_app(dossier_dir=corpus, llm=_llm())
    with TestClient(a, raise_server_exceptions=False) as ca, \
            TestClient(b, raise_server_exceptions=False) as cb:
        for person_id in ORDER:
            assert ca.post("/arrive", json={"person_id": person_id}).status_code == 200
            assert cb.post("/arrive", json={"person_id": person_id}).status_code == 200
        for route in ("/", "/building", "/graph", "/corpus"):
            assert ca.get(route).text == cb.get(route).text, route


def test_the_corpus_page_does_not_move_when_the_building_fills_up(corpus):
    """`/corpus` "takes no input at all and is not on the arrival path — it is a pure read
    of the corpus the app booted with, so it answers the same way whether the building is
    full or empty" (`web/app.py:corpus_page`). `/graph` is scoped to presence and MUST
    move; asserting both is what makes this a statement about the difference between them
    rather than about caching."""
    app = create_app(dossier_dir=corpus, llm=_llm())
    with TestClient(app, raise_server_exceptions=False) as client:
        empty_corpus = client.get("/corpus").text
        empty_graph = client.get("/graph").text
        for person_id in ORDER:
            client.post("/arrive", json={"person_id": person_id})
        assert client.get("/corpus").text == empty_corpus
        assert client.get("/graph").text != empty_graph


def _surfaces(corpus, sequence):
    app = create_app(dossier_dir=corpus, llm=_llm())
    with TestClient(app, raise_server_exceptions=False) as client:
        for person_id in sequence:
            assert client.post("/arrive", json={"person_id": person_id}).status_code == 200
        return {
            "graph": client.get("/graph").text,
            "corpus": client.get("/corpus").text,
            "index": client.get("/").text,
            "building": client.get("/building",
                                   headers={"accept": "application/json"}).json(),
        }


def test_the_order_people_arrive_in_does_not_change_the_corpus_or_the_roster_page(corpus):
    """`/corpus` and `/` take no presence input at all — `corpus_view(store)` and the
    roster loop over `store.people()` — so neither may move when the same people walk in
    backwards. `/building` is EXPECTED to reorder: presence keeps insertion order
    deliberately, because "who walked in most recently is real information to a host"
    (`web/presence.py`)."""
    forwards = _surfaces(corpus, ORDER)
    backwards = _surfaces(corpus, tuple(reversed(ORDER)))
    for name in ("corpus", "index"):
        assert forwards[name] == backwards[name], _diff(name, forwards[name], backwards[name])
    assert [p["person_id"] for p in forwards["building"]["present"]] == list(ORDER)
    assert [p["person_id"] for p in backwards["building"]["present"]] == list(reversed(ORDER))


def test_the_graph_page_is_a_pure_function_of_the_ordered_presence_list(corpus):
    """MEASURED, and recorded here because it is not obvious: `/graph` DOES move when the
    same four people arrive in a different order — the drawing's coordinates and the
    figure's text alternative both follow `present_ids`, which is arrival order.

    That is not non-determinism: arrival order is genuinely part of the route's input
    (`graph_view(store, presence.present())`). So the determinism claim `/graph` can be
    held to is the one asserted here — same corpus AND same order, in two separate apps,
    down to the byte — and the observation that a different order draws a different picture
    is asserted alongside it so the distinction cannot silently collapse in either
    direction.
    """
    forwards = _surfaces(corpus, ORDER)
    again = _surfaces(corpus, ORDER)
    assert forwards["graph"] == again["graph"], _diff("graph", forwards["graph"],
                                                      again["graph"])

    backwards = _surfaces(corpus, tuple(reversed(ORDER)))
    assert backwards["graph"] != forwards["graph"], (
        "the graph drawing no longer follows presence order; if that was deliberate this "
        "assertion is the place to record the new rule"
    )
    # ... and the reversed room is itself reproducible, which is what rules out randomness
    # as the cause of the difference above.
    assert backwards["graph"] == _surfaces(corpus, tuple(reversed(ORDER)))["graph"]


def test_the_roster_page_orders_by_display_name_not_by_id_or_by_file(corpus):
    """`DossierStore.people` sorts on `name.lower()`. Pinned because `sorted(glob)` gives
    the same answer for THIS corpus by coincidence — alpha/bravo/charlie/delta happen to be
    alphabetical — so the ordering rule is only observable once a name disagrees with its
    filename."""
    app = create_app(dossier_dir=corpus, llm=_llm())
    names = [person.name for person in app.state.store.people()]
    assert names == sorted(names, key=str.lower)


# ---------------------------------------------------------------------------
# Cross-process: the half that catches a PYTHONHASHSEED dependency.
# ---------------------------------------------------------------------------

#: Runs in a fresh interpreter under a fixed hash seed. Stdlib + the product only; the
#: socket guard is belt and braces — the corpus is local and the client is injected, so
#: nothing here has a reason to reach the network, and this makes that a fact rather than
#: an expectation (SPEC C7 applies to the whole suite, subprocesses included).
CHILD = textwrap.dedent(
    '''
    import hashlib, json, socket, sys

    def _no_network(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    socket.socket.connect = _no_network
    socket.socket.connect_ex = _no_network

    from fastapi.testclient import TestClient
    from arrival.web.app import create_app

    OPENER = {opener!r}
    ORDER = {order!r}
    CORPUS = {corpus!r}

    class Scripted:
        async def structured(self, *, system, user, schema, max_tokens=2000,
                             cache_prefix=True):
            return schema.model_validate({{"line": OPENER}})

    app = create_app(dossier_dir=CORPUS, llm=Scripted())
    client = TestClient(app, raise_server_exceptions=False)
    out = {{}}
    ids = []
    for person_id in ORDER:
        response = client.post("/arrive", json={{"person_id": person_id}})
        assert response.status_code == 200, response.text
        ids.append(response.json()["digest_id"])
    for route, name in (("/", "index"), ("/building", "building"), ("/graph", "graph"),
                        ("/corpus", "corpus")):
        out[name] = hashlib.sha256(client.get(route).text.encode()).hexdigest()
    for person_id, digest_id in zip(ORDER, ids):
        model = app.state.digests[digest_id].model_dump_json(
            exclude={{"digest_id", "created_at"}}
        )
        out["digest:" + person_id] = hashlib.sha256(model.encode()).hexdigest()
        page = client.get("/digest/" + digest_id).text.replace(digest_id, "<ID>")
        out["page:" + person_id] = hashlib.sha256(page.encode()).hexdigest()
    print(json.dumps(out))
    '''
)

SEEDS = ["0", "1", "4242", "99999"]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_whole_surface_is_identical_under_every_hash_seed(corpus, request, seed, tmp_path):
    """CPython randomises `str` hashing per process unless `PYTHONHASHSEED` is fixed, so a
    set iterated without a sort answers differently run to run. Every seed must produce the
    same digests and the same pages as seed 0.

    `PYTHONHASHSEED=0` is the reference rather than an average of the runs, so a failure
    names one seed and one surface instead of "they disagree".
    """
    root = request.config.rootpath
    script = tmp_path / "child.py"
    script.write_text(
        CHILD.format(opener=OPENER, order=ORDER, corpus=str(corpus)), encoding="utf-8"
    )
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    env.pop("DEBUG_VIEWS", None)
    env.pop("DOSSIER_DIR", None)

    def child(hash_seed):
        completed = subprocess.run(
            [sys.executable, str(script)],
            env={**env, "PYTHONHASHSEED": hash_seed},
            cwd=str(root), capture_output=True, text=True, timeout=300,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        return json.loads(completed.stdout.strip().splitlines()[-1])

    reference = child("0")
    under_seed = child(seed)
    assert set(reference) == set(under_seed)
    moved = [name for name in sorted(reference) if reference[name] != under_seed[name]]
    assert not moved, (
        f"PYTHONHASHSEED={seed} changed {moved}; identical input produced different output, "
        f"which is the `verdict_attribute` defect class"
    )


def test_the_cross_process_rig_can_actually_detect_a_difference(corpus, request, tmp_path):
    """Positive control for the seed sweep. Without it, a child script that crashed the
    same way every time — or one whose output did not depend on the corpus — would report
    'identical' and prove nothing. Changing the CORPUS must change the answer.
    """
    root = request.config.rootpath
    other = tmp_path / "other"
    other.mkdir()
    for name in ("alpha", "bravo"):
        raw = json.loads((corpus / f"{name}.json").read_text(encoding="utf-8"))
        (other / f"{name}.json").write_text(json.dumps(raw), encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONHASHSEED": "0"}
    env.pop("DEBUG_VIEWS", None)
    env.pop("DOSSIER_DIR", None)

    def child(corpus_dir, order):
        script = tmp_path / f"child-{hashlib.sha1(str(corpus_dir).encode()).hexdigest()[:8]}.py"
        script.write_text(
            CHILD.format(opener=OPENER, order=order, corpus=str(corpus_dir)), encoding="utf-8"
        )
        completed = subprocess.run(
            [sys.executable, str(script)], env=env, cwd=str(root),
            capture_output=True, text=True, timeout=300,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        return json.loads(completed.stdout.strip().splitlines()[-1])

    full = child(corpus, ("alpha", "bravo"))
    reduced = child(other, ("alpha", "bravo"))
    assert full["corpus"] != reduced["corpus"], (
        "a two-person corpus rendered the same /corpus page as a four-person one — the "
        "rig is not measuring what it claims to"
    )
