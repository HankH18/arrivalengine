"""T-8: the HTTP surface — DESIGN's route table, driven through `fastapi.testclient`.

TASKS T-8 names the setup verbatim: "all via `fastapi.testclient` with the app pointed at
`tests/fixtures/dossiers/` and `LLMDouble`", and its acceptance 2 names the cast —
`charlie` arriving with `alpha`, `bravo`, `delta` present.

What these add on top of the frozen acceptance suite: the response SHAPES DESIGN pins but
the harness does not read (`digest_url`, `{"error": "not on roster"}`, `{"present": [...]}`),
the two body encodings one route has to answer, content negotiation on `/building`, and the
isolation between two apps built in one process.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arrival.config import get_settings
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-8")

ARRIVING = "charlie"          # Selin Ardahan; shares investor:foundry-seed-2019 with delta
OTHERS = ("alpha", "bravo", "delta")

#: R14-shaped, so `digest._validate_opener` accepts it and the page shows the model's line
#: rather than silently falling through to the template.
OPENER = "Ask about the evaluation harness they open-sourced last spring."


@pytest.fixture
def corpus(tmp_path, request):
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


@pytest.fixture
def llm():
    # `_opener_prompt` starts every user prompt with "Member: <name>", so one rule covers
    # every arrival in the corpus.
    return LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})


@pytest.fixture
def client(corpus, llm):
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _arrive(client, **body):
    return client.post("/arrive", json=body)


def _present_ids(client):
    response = client.get("/building", headers={"Accept": "application/json"})
    assert response.status_code == 200
    return [person["person_id"] for person in response.json()["present"]]


# --------------------------------------------------------------------------- R3 / R4


def test_arrive_answers_designs_three_key_body_and_records_presence(client, llm):
    """T-8 acceptance 2, and DESIGN's route table: `{"digest_id","person_id","digest_url"}`.

    The frozen harness only reads two of those three keys, so `digest_url` — the thing that
    makes the response usable by whatever fired the webhook — is pinned here.
    """
    for person_id in OTHERS:
        assert _arrive(client, person_id=person_id).status_code == 200

    response = _arrive(client, name="Selin Ardahan")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"digest_id", "person_id", "digest_url"}
    assert body["person_id"] == ARRIVING
    assert body["digest_url"] == f"/digest/{body['digest_id']}"
    assert client.get(body["digest_url"]).status_code == 200
    assert _present_ids(client) == [*OTHERS, ARRIVING]
    assert llm.calls_for("SayOutLoud"), "DESIGN Decision 12 puts one opener call on arrival"


def test_arrive_accepts_a_name_or_a_person_id_in_either_encoding(client):
    """One route, four spellings, one person.

    DESIGN pins the JSON body; TASKS acceptance 6 requires plain-HTML forms posting to the
    same route, and a browser sends those as `application/x-www-form-urlencoded`. Declaring
    a Pydantic request model would answer one and 422 the other.
    """
    assert _arrive(client, name="Selin Ardahan").status_code == 200
    assert _arrive(client, person_id=ARRIVING).status_code == 200
    assert client.post("/arrive", data={"person_id": ARRIVING}).status_code in (200, 303)
    assert client.post("/arrive", data={"name": "Selin Ardahan"}).status_code in (200, 303)
    assert _present_ids(client) == [ARRIVING]


def test_an_off_roster_arrival_is_refused_with_designs_body_and_makes_no_llm_call(client, llm):
    """R4. The refusal happens BEFORE any matching or LLM work, not after a check downstream."""
    response = _arrive(client, name="Wendell Ashgrove-Pike")
    assert response.status_code == 404
    assert response.json() == {"error": "not on roster"}
    assert llm.calls == []
    assert _present_ids(client) == []


def test_an_arrival_with_no_usable_body_is_off_roster_rather_than_a_500(client, llm):
    """A webhook that names nobody cannot be on the roster; it must not be a stack trace."""
    for response in (
        client.post("/arrive", json={}),
        client.post("/arrive", content=b"not json at all"),
        client.post("/arrive", json={"name": ""}),
    ):
        assert response.status_code == 404, response.text
    assert llm.calls == []


# --------------------------------------------------------------------------- R5 / R6


def test_leave_is_idempotent_and_answers_designs_present_list(client):
    """DESIGN's route table: `POST /leave` -> `200 {"present":[...]}`.

    R5 only asks that a departed person stop being proposed. They already have after the
    first call, so the second is a no-op with a 200 rather than an error a demo has to
    handle.
    """
    for person_id in OTHERS:
        _arrive(client, person_id=person_id)

    first = client.post("/leave", json={"person_id": "bravo"})
    assert first.status_code == 200
    assert [p["person_id"] for p in first.json()["present"]] == ["alpha", "delta"]

    second = client.post("/leave", json={"person_id": "bravo"})
    assert second.status_code == 200
    assert [p["person_id"] for p in second.json()["present"]] == ["alpha", "delta"]

    assert client.post("/leave", json={"person_id": "nobody-at-all"}).status_code == 404


def test_leaving_removes_a_person_from_the_next_digests_meet_section(client):
    """R5's actual requirement: "subsequent digests SHALL NOT propose them"."""
    for person_id in OTHERS:
        _arrive(client, person_id=person_id)
    before = client.get(_arrive(client, person_id=ARRIVING).json()["digest_url"]).text
    assert "Hollis Trent" in before  # delta, who shares the rare investor hub with charlie

    client.post("/leave", json={"person_id": "delta"})
    client.post("/leave", json={"person_id": ARRIVING})
    after = client.get(_arrive(client, person_id=ARRIVING).json()["digest_url"]).text
    assert "Hollis Trent" not in after
    assert "Teodoro Vance" in after, "nobody at all is proposed, so the check above is vacuous"


def test_building_serves_json_or_html_depending_on_the_accept_header(client):
    """DESIGN: "HTML list of present people (JSON if `Accept: application/json`)"."""
    _arrive(client, person_id="alpha")

    as_json = client.get("/building", headers={"Accept": "application/json"})
    assert as_json.headers["content-type"].startswith("application/json")
    assert as_json.json() == {
        "present": [{"person_id": "alpha", "name": "Teodoro Vance"}],
        "count": 1,
    }

    as_html = client.get("/building")
    assert as_html.headers["content-type"].startswith("text/html")
    assert "Teodoro Vance" in as_html.text
    assert "<html" in as_html.text.lower()


# --------------------------------------------------------------------------- R7 / R15


def test_an_unknown_digest_id_is_a_404_page_not_a_traceback(client):
    """DESIGN's route table: "404 if unknown". Digests are process-local and expire on restart."""
    response = client.get("/digest/deadbeefdeadbeef")
    assert response.status_code == 404
    assert "<html" in response.text.lower()


def test_debug_is_off_by_default_and_on_only_when_the_env_switch_is_set(monkeypatch, corpus, llm):
    """R15: "It is served only when env `DEBUG_VIEWS=1`; otherwise 404."

    Two apps, two environments, one process — which is also the reason `create_app` reads
    settings in its body instead of at import.
    """
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as closed:
        assert closed.get(f"/debug/{ARRIVING}").status_code == 404

    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as opened:
        response = opened.get(f"/debug/{ARRIVING}")
        assert response.status_code == 200
        # R15 names four things this view owes the operator.
        assert "rejected" in response.text.lower()
        assert "confidence" in response.text.lower()
        assert "hub" in response.text.lower()
        assert opened.get("/debug/nobody-at-all").status_code == 404


def test_debug_reaches_a_person_by_display_name_too(monkeypatch, corpus, llm):
    """The operator typing a name into the URL bar is the likeliest way this view is used."""
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as client:
        assert client.get("/debug/Selin Ardahan").status_code == 200


# --------------------------------------------------------------------------- the demo driver


def test_the_index_lists_every_roster_member_with_both_forms(client):
    """TASKS T-8 acceptance 6. The forms are plain HTML posting to the real routes."""
    page = client.get("/")
    assert page.status_code == 200
    for person_id, name in (
        ("alpha", "Teodoro Vance"),
        ("bravo", "Nadia Ellingsworth"),
        ("charlie", "Selin Ardahan"),
        ("delta", "Hollis Trent"),
    ):
        assert person_id in page.text
        assert name in page.text
    assert page.text.count('action="/arrive"') == 4
    assert page.text.count('action="/leave"') == 4
    assert "<script" not in page.text.lower(), "SPEC non-goals: no JS framework, and no JS"


def test_the_index_forms_redirect_a_browser_to_something_worth_looking_at(client):
    """A form post is a browser, not an API client: land it on a page, not on JSON."""
    arrived = client.post("/arrive", data={"person_id": ARRIVING}, follow_redirects=False)
    assert arrived.status_code == 303
    assert arrived.headers["location"].startswith("/digest/")

    left = client.post("/leave", data={"person_id": ARRIVING}, follow_redirects=False)
    assert left.status_code == 303
    assert left.headers["location"] == "/"


def test_no_host_facing_page_carries_a_fact_the_taste_filter_withheld(client, corpus):
    """R11/R12 across every surface a host can reach, on the unit corpus.

    The frozen suite grades this on its own corpus; this one grades it on
    `tests/fixtures/dossiers/`, where a different set of facts is withheld for different
    reasons — so the two cannot both be passing by accident of one fixture's ordering.
    """
    for person_id in OTHERS:
        _arrive(client, person_id=person_id)
    digest_url = _arrive(client, person_id=ARRIVING).json()["digest_url"]
    digest_html = client.get(digest_url).text

    facts = json.loads((corpus / "charlie.json").read_text(encoding="utf-8"))["facts"]
    hidden = [
        fact["text"]
        for fact in facts
        if fact["excluded"] or fact["provenance"]["confidence"] < 0.7
    ]
    shown = [
        fact["text"]
        for fact in facts
        if not fact["excluded"] and fact["provenance"]["confidence"] >= 0.7
    ]
    assert hidden, "the charlie fixture withholds nothing, so the assertions below are vacuous"
    # Positive control: an empty or errored page satisfies every negative assertion.
    assert any(text in digest_html for text in shown), (
        "the digest shows none of charlie's displayable facts either"
    )

    for page in (digest_html, client.get("/").text, client.get("/building").text):
        for secret in hidden:
            assert secret not in page


def test_two_apps_in_one_process_share_no_presence_and_no_corpus(corpus, llm, tmp_path):
    """Every handler closes over its own `app`, so a second app cannot inherit the first's state.

    The frozen harness builds several apps in one process against different directories; a
    module-level store or presence set would hand the second app the first one's data and
    grade the wrong page.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as populated, TestClient(
        create_app(dossier_dir=empty, llm=llm)
    ) as blank:
        populated.post("/arrive", json={"person_id": ARRIVING})
        assert _present_ids(populated) == [ARRIVING]
        assert _present_ids(blank) == []
        assert blank.post("/arrive", json={"person_id": ARRIVING}).status_code == 404
        assert _present_ids(populated) == [ARRIVING]
