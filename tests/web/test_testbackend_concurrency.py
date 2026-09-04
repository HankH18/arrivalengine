"""Presence under concurrent requests: the property `Presence`'s lock exists for.

`web/presence.py`'s module docstring makes a claim nothing in the repo executes: "The lock
is not theatre: uvicorn serves concurrent requests, and a set mutated from two request
handlers at once is the classic way a presence list loses a person." Every existing
concurrency test in the suite is connector-level (`tests/test_t0_doubles.py:269`,
`tests/research/test_t6_budgets.py:159`); nothing has ever issued two overlapping requests
at the app.

These do, from real OS threads through `TestClient`, which drives the ASGI app through a
blocking portal — so the handlers genuinely interleave on the event loop while the
assertions run on the main thread. The LLM double sleeps inside `make_digest`, which is
what forces the overlap: without an await point in the handler the requests would serialise
and the test would prove nothing.

What "torn" means here, stated as assertions rather than adjectives:

* `count` on `/building` always equals `len(present)` — a reader that saw the dict
  mid-mutation would disagree with itself.
* no `person_id` appears twice — `Presence.arrive` pops before it inserts, and a lost
  update there is a duplicate row.
* every id present is one the corpus knows — a torn dict read yields junk keys.
* no digest is lost: every id `/arrive` handed out is still addressable.

Grading references: HTTP literals, the corpus's own `person_id` set, and arithmetic. No
answer key this ticket wrote.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from arrival.web.app import DIGEST_HISTORY, create_app

pytestmark = pytest.mark.ticket("TESTBACKEND")

PEOPLE = ("alpha", "bravo", "charlie", "delta")


class SleepyLLM:
    """An `LLMClient` that awaits before answering, so two handlers really do overlap.

    Not `LLMDouble(delay=...)`: `make_digest` wraps its one call in
    `asyncio.wait_for(..., 2.5)`, so a delay near that budget turns a concurrency test into
    a timeout test. This sleeps for a few milliseconds — long enough to force a context
    switch, far short of the deadline — and counts its calls.
    """

    def __init__(self, delay: float = 0.005) -> None:
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    async def structured(self, *, system, user, schema: type[BaseModel], max_tokens=2000,
                         cache_prefix=True) -> BaseModel:
        with self._lock:
            self.calls += 1
        await asyncio.sleep(self.delay)
        return schema.model_validate({"line": "Ask what they are working on right now."})


@pytest.fixture
def corpus(tmp_path, request):
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


@pytest.fixture
def client(corpus, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    app = create_app(dossier_dir=corpus, llm=SleepyLLM())
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.app_under_test = app
        yield test_client


def _fan_out(client, calls):
    """Run `calls` — a list of (method, path, json) — on one thread each, all at once.

    A `threading.Barrier` is what makes this a concurrency test rather than a fast
    sequential one: every thread is built, then every thread waits, then they are released
    together. Without it the first request usually finishes before the last starts.
    """
    barrier = threading.Barrier(len(calls))
    results: list[tuple[int, int, dict | None]] = []
    guard = threading.Lock()

    def one(index, method, path, payload):
        barrier.wait(timeout=30)
        response = client.request(method, path, json=payload)
        body = None
        if response.status_code == 200 and response.headers["content-type"].startswith(
            "application/json"
        ):
            body = response.json()
        with guard:
            results.append((index, response.status_code, body))

    threads = [threading.Thread(target=one, args=(i, m, p, j), daemon=True)
               for i, (m, p, j) in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a request thread never finished"
    assert len(results) == len(calls)
    return results


def _building(client):
    response = client.get("/building", headers={"accept": "application/json"})
    assert response.status_code == 200
    body = response.json()
    ids = [person["person_id"] for person in body["present"]]
    assert body["count"] == len(ids), (
        f"`count` and `present` disagree: {body['count']} vs {len(ids)} — a reader saw the "
        f"presence dict mid-mutation"
    )
    assert len(ids) == len(set(ids)), f"a person is in the building twice: {ids}"
    assert set(ids) <= set(PEOPLE), f"the presence set holds an id the corpus does not: {ids}"
    return ids


def test_four_simultaneous_arrivals_all_land_and_none_is_lost(client):
    results = _fan_out(client, [("POST", "/arrive", {"person_id": p}) for p in PEOPLE])
    assert {status for _, status, _ in results} == {200}
    assert sorted(_building(client)) == sorted(PEOPLE)
    assert len({body["digest_id"] for _, _, body in results}) == 4, (
        "four arrivals are four events and must not share a digest id"
    )


def test_twenty_simultaneous_arrivals_of_the_same_person_are_one_person_and_twenty_digests(
    client,
):
    """The race `Presence.arrive`'s pop-then-insert is written against. Twenty threads
    mutating one key is where a plain `set().add` would still be correct and a dict
    rebuilt without a lock would not."""
    results = _fan_out(client, [("POST", "/arrive", {"person_id": "alpha"})
                                for _ in range(20)])
    assert {status for _, status, _ in results} == {200}
    assert _building(client) == ["alpha"]

    digest_ids = [body["digest_id"] for _, _, body in results]
    assert len(set(digest_ids)) == 20, "two concurrent arrivals collided on a digest id"
    for digest_id in digest_ids:
        assert client.get(f"/digest/{digest_id}").status_code == 200, (
            f"digest {digest_id} was handed out and then lost"
        )


def test_interleaved_arrivals_and_departures_never_tear_the_presence_list(client):
    """60 overlapping calls across four people, two thirds arrivals. The assertion is not
    on the final membership — that is genuinely racy and any outcome is legal — but on the
    INVARIANTS, which are not: the list agrees with its own count, holds no duplicate, and
    holds nothing the corpus cannot name."""
    calls = []
    for index in range(60):
        person = PEOPLE[index % len(PEOPLE)]
        route = "/leave" if index % 3 == 0 else "/arrive"
        calls.append(("POST", route, {"person_id": person}))

    results = _fan_out(client, calls)
    assert {status for _, status, _ in results} == {200}
    _building(client)

    # And the app is still usable afterwards, which a corrupted dict would not be.
    for person in PEOPLE:
        assert client.post("/leave", json={"person_id": person}).status_code == 200
    assert _building(client) == []
    assert client.post("/arrive", json={"person_id": "alpha"}).status_code == 200
    assert _building(client) == ["alpha"]


def test_reads_concurrent_with_writes_never_observe_a_broken_page(client):
    """Every read surface, hammered while presence is being mutated underneath it. A page
    that iterated the presence dict while another thread deleted from it would raise
    `RuntimeError: dictionary changed size during iteration` and 500."""
    calls: list[tuple[str, str, dict | None]] = []
    for index in range(48):
        person = PEOPLE[index % len(PEOPLE)]
        if index % 4 == 0:
            calls.append(("POST", "/arrive", {"person_id": person}))
        elif index % 4 == 1:
            calls.append(("POST", "/leave", {"person_id": person}))
        elif index % 4 == 2:
            calls.append(("GET", "/building", None))
        else:
            calls.append(("GET", "/graph", None))
    calls += [("GET", "/", None), ("GET", "/corpus", None)] * 4

    results = _fan_out(client, calls)
    bad = [(index, status) for index, status, _ in results if status >= 500]
    assert not bad, f"a read during concurrent presence mutation returned 5xx: {bad}"
    assert {status for _, status, _ in results} == {200}
    _building(client)


def test_the_digest_history_cap_holds_under_concurrent_arrivals(client):
    """`_remember` trims `digest_order` in a `while` loop with no lock. Under concurrent
    arrivals the two structures could drift; `DIGEST_HISTORY` is the number both must
    settle on, and the dict must never outgrow the list it is trimmed by."""
    app = client.app_under_test
    overflow = DIGEST_HISTORY + 30
    results = _fan_out(client, [("POST", "/arrive", {"person_id": "alpha"})
                                for _ in range(40)])
    assert {status for _, status, _ in results} == {200}
    for _ in range(overflow - 40):
        assert client.post("/arrive", json={"person_id": "alpha"}).status_code == 200

    assert len(app.state.digest_order) == DIGEST_HISTORY
    assert len(app.state.digests) <= DIGEST_HISTORY
    assert set(app.state.digests) <= set(app.state.digest_order), (
        "the dict holds a digest the order list no longer knows about, so it can never be "
        "evicted — an unbounded leak in a long-running demo"
    )


def test_every_concurrent_arrival_paid_for_exactly_one_model_call(client):
    """R3's cost model: one bounded `llm.structured` call per arrival, no retries and no
    per-request fan-out. Counted across a concurrent burst, where a shared client with
    accidental state could double-charge."""
    llm = client.app_under_test.state.llm
    before = llm.calls
    results = _fan_out(client, [("POST", "/arrive", {"person_id": p}) for p in PEOPLE] * 3)
    assert {status for _, status, _ in results} == {200}
    assert llm.calls - before == len(results)


def test_concurrent_off_roster_arrivals_cost_nothing_at_all(client):
    """R4 under load: the refusal happens before any matching or model work, so a burst of
    unknown names must not reach the client even once."""
    llm = client.app_under_test.state.llm
    before = llm.calls
    results = _fan_out(client, [("POST", "/arrive", {"person_id": f"ghost-{i}"})
                                for i in range(24)])
    assert {status for _, status, _ in results} == {404}
    assert llm.calls == before
    assert _building(client) == []
