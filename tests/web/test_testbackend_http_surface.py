"""The HTTP surface, exhaustively: every route x every hostile input shape.

The project's ~1950 tests are unit-scoped; this module grades the SYSTEM boundary — what a
real caller (a webhook, a browser form, a curl, a scanner) can make the app do over HTTP.

**A 500 is a bug.** The rule this module enforces above all others: no input a caller can
send may produce an unhandled traceback. `web/app.py:_payload` states it as a contract —
"Nothing here can raise: a malformed body yields `{}`, which resolves to no person, which
is a 404" — and every case below is an attempt to falsify that sentence.

Grading references, none of them a file this ticket owns:

* HTTP/starlette literals: 200, 303, 404, 405, and the `allow` header on a 405.
* `web/app.py`'s own docstrings and DESIGN's route table, quoted at each assertion.
* `tests/fixtures/dossiers/*.json` — the pre-existing corpus, pinned against renaming by
  `tests/test_t0b_fixture_conventions.py`.

`TestClient` is the established idiom (`tests/web/test_t8_app.py:51`). It is used with
`raise_server_exceptions=False` HERE and nowhere else in the repo, deliberately: the
default re-raises a handler exception inside the test process, which reports a 500 as an
ERROR with a traceback. Turning it off is what lets a single parametrized case assert
`status < 500` for every input at once, so a regression surfaces as one failed assertion
naming the offending body rather than as a wall of tracebacks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBACKEND")

#: R14-shaped so `digest._validate_opener` accepts it and the arrival path runs end to end
#: rather than silently landing in the template fallback.
OPENER = "Ask about the evaluation harness they open-sourced last spring."

#: `tests/fixtures/dossiers/alpha.json`. Standing ruling 1: the fixture ids are mnemonics
#: and deliberately are NOT `slug(name)`, so both spellings are exercised explicitly.
KNOWN_ID = "alpha"
KNOWN_NAME = "Teodoro Vance"

POST_ROUTES = ("/arrive", "/leave")
GET_ROUTES = ("/", "/building", "/graph", "/corpus")


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
    # `digest._opener_prompt` starts every user prompt with "Member: <name>".
    return LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})


@pytest.fixture
def client(corpus, llm, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    with TestClient(create_app(dossier_dir=corpus, llm=llm),
                    raise_server_exceptions=False) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Method matrix. DESIGN's route table pins one verb per route; everything
#    else is starlette's 405, never a 500 and never a silent 200.
# ---------------------------------------------------------------------------

WRONG_METHODS_FOR_POST = ("GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
WRONG_METHODS_FOR_GET = ("POST", "PUT", "DELETE", "PATCH")


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("method", WRONG_METHODS_FOR_POST)
def test_a_post_route_refuses_every_other_verb_with_405(client, route, method):
    response = client.request(method, route)
    if method == "OPTIONS":
        # Starlette answers OPTIONS itself with an `allow` header; that is a 200, not a
        # route hit, and it must still never reach a handler.
        assert response.status_code in (200, 405), response.text
        return
    assert response.status_code == 405, f"{method} {route} -> {response.status_code}"
    # Starlette's 405 advertises what the route DOES take. A 405 with no `allow` is a
    # worse answer than a 404 because it tells a client the route exists and nothing else.
    assert "POST" in response.headers.get("allow", "")


@pytest.mark.parametrize("route", GET_ROUTES + ("/digest/x", "/debug/x"))
@pytest.mark.parametrize("method", WRONG_METHODS_FOR_GET)
def test_a_get_route_refuses_every_other_verb_with_405(client, route, method):
    response = client.request(method, route, content=b"")
    assert response.status_code == 405, f"{method} {route} -> {response.status_code}"
    assert "GET" in response.headers.get("allow", "")


# ---------------------------------------------------------------------------
# 2. Hostile bodies on the two POST routes. This is the class of input most
#    likely to produce a traceback, and the one `_payload` promises cannot.
# ---------------------------------------------------------------------------

JSON = {"content-type": "application/json"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
MULTIPART = {"content-type": "multipart/form-data; boundary=x"}

#: (label, kwargs-for-client.request). Every one of these must be a 404 — an arrival that
#: cannot name anybody is off-roster by definition (`web/app.py:_payload`) — and none of
#: them may cost a model call, because R4 refuses BEFORE any research happens.
UNNAMEABLE_BODIES: list[tuple[str, dict]] = [
    ("no body at all", {}),
    ("zero bytes", {"content": b""}),
    ("zero bytes declared json", {"content": b"", "headers": JSON}),
    ("truncated json", {"content": b"{", "headers": JSON}),
    ("json that is a list", {"content": b"[1, 2, 3]", "headers": JSON}),
    ("json that is a string", {"content": b'"alpha"', "headers": JSON}),
    ("json that is a number", {"content": b"42", "headers": JSON}),
    ("json that is null", {"content": b"null", "headers": JSON}),
    ("json that is true", {"content": b"true", "headers": JSON}),
    ("empty object", {"json": {}}),
    ("object with unrelated keys", {"json": {"who": "alpha", "id": "alpha"}}),
    ("person_id null", {"json": {"person_id": None}}),
    ("person_id empty string", {"json": {"person_id": ""}}),
    ("person_id all whitespace", {"json": {"person_id": "   \t\n "}}),
    ("person_id an int", {"json": {"person_id": 12345}}),
    ("person_id a float", {"json": {"person_id": 1.5}}),
    ("person_id a bool", {"json": {"person_id": True}}),
    ("person_id a list", {"json": {"person_id": ["alpha", "bravo"]}}),
    ("person_id an object", {"json": {"person_id": {"person_id": "alpha"}}}),
    ("person_id 10KB", {"json": {"person_id": "A" * 10_000}}),
    ("person_id path traversal", {"json": {"person_id": "../../../etc/passwd"}}),
    ("person_id absolute path", {"json": {"person_id": "/etc/passwd"}}),
    ("person_id html injection", {"json": {"person_id": "<script>alert(1)</script>"}}),
    ("person_id sql-ish", {"json": {"person_id": "alpha'; DROP TABLE people;--"}}),
    ("person_id NUL byte", {"json": {"person_id": "alp\x00ha"}}),
    ("person_id lone surrogate", {"content": rb'{"person_id": "\ud800"}', "headers": JSON}),
    ("person_id newlines", {"json": {"person_id": "alpha\r\nX-Injected: 1"}}),
    ("name null", {"json": {"name": None}}),
    ("name an int", {"json": {"name": 7}}),
    ("name empty", {"json": {"name": ""}}),
    ("urlencoded blank value", {"content": b"person_id=", "headers": FORM}),
    ("urlencoded no equals", {"content": b"alpha", "headers": FORM}),
    ("urlencoded junk", {"content": b"%%%%", "headers": FORM}),
    ("urlencoded wrong key", {"content": b"who=alpha", "headers": FORM}),
    ("json body declared as form", {"content": b'{"person_id": "alpha"}', "headers": FORM}),
    ("body is latin-1 bytes", {"content": b'{"person_id": "\xe9"}', "headers": JSON}),
    ("body is a bare NUL", {"content": b"\x00", "headers": JSON}),
    ("multipart terminator only", {"content": b"--x--\r\n", "headers": MULTIPART}),
    ("multipart empty body", {"content": b"", "headers": MULTIPART}),
    ("multipart unterminated",
     {"content": b'--x\r\nContent-Disposition: form-data; name="person_id"\r\n\r\nnobody',
      "headers": MULTIPART}),
    ("multipart/mixed", {"content": b"--x\r\n--x--\r\n",
                         "headers": {"content-type": "multipart/mixed; boundary=x"}}),
]


@pytest.mark.parametrize("route", POST_ROUTES)
@pytest.mark.parametrize("label,kwargs", UNNAMEABLE_BODIES, ids=[c[0] for c in UNNAMEABLE_BODIES])
def test_a_body_naming_nobody_is_a_404_and_never_a_traceback(client, llm, route, label, kwargs):
    response = client.post(route, **kwargs)
    assert response.status_code < 500, (
        f"{route} with {label!r} produced {response.status_code}: {response.text[:400]}"
    )
    assert response.status_code == 404, f"{route} with {label!r} -> {response.status_code}"


@pytest.mark.parametrize("route", POST_ROUTES)
def test_no_unnameable_body_on_either_route_costs_a_model_call(client, llm, route):
    """R4: 'an off-roster arrival triggers no live research', for the whole matrix at once.

    The per-case test above cannot assert this — pytest gives each parametrized case its
    own fixtures, so each gets a fresh `LLMDouble`. Replaying the matrix through ONE client
    is what makes 'zero calls' a statement about all 39 bodies rather than about one.
    """
    for label, kwargs in UNNAMEABLE_BODIES:
        response = client.post(route, **kwargs)
        assert response.status_code == 404, f"{label}: {response.status_code}"
    assert llm.calls == [], f"an unnameable body reached the model: {llm.calls}"


# ---------------------------------------------------------------------------
# 2b. THE ONE INPUT THAT DOES PRODUCE A TRACEBACK.
# ---------------------------------------------------------------------------

#: Four malformed `multipart/form-data` bodies, each of which makes `python_multipart`
#: raise and each of which is a 500 today. The cheapest to send is "boundary mismatch":
#: a body whose parts do not use the boundary the header declared.
MALFORMED_MULTIPART: list[tuple[str, bytes]] = [
    ("boundary mismatch", b"--y\r\n--y--\r\n"),
    ("empty part", b"--x\r\n--x--\r\n"),
    ("carriage return inside a part header",
     b"--x\r\nContent-Dis\rposition: form-data\r\n\r\nv\r\n--x--\r\n"),
    ("boundary longer than the RFC 2046 limit", b"--x\r\n--x--\r\n"),
]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN DEFECT, reproduced on the live deploy. `web/app.py:80` calls "
        "`await request.form()` outside any try, so `python_multipart.exceptions."
        "MultipartParseError` escapes `_payload` and FastAPI answers 500. This falsifies "
        "`_payload`'s own docstring (`web/app.py:73-74`): 'Nothing here can raise: a "
        "malformed body yields {}, which resolves to no person, which is a 404.' "
        "`src/` is read-only to this ticket, so the defect is reported rather than fixed; "
        "strict=True makes this test FAIL the day the guard lands, which is when the "
        "xfail should be deleted."
    ),
)
@pytest.mark.parametrize("route", POST_ROUTES)
def test_a_malformed_multipart_body_is_a_4xx_and_not_a_traceback(client, route):
    """`POST /arrive` and `POST /leave` must refuse a malformed multipart body, not 500.

    Reproduction against the deployed service, which answers 500 for the first case and
    404 for the JSON control::

        printf -- '--y\\r\\n--y--\\r\\n' > body.bin
        curl -i -X POST https://arrival-engine.onrender.com/arrive \\
             -H 'Content-Type: multipart/form-data; boundary=x' --data-binary @body.bin

    Note the near miss that already works: `Content-Type: multipart/form-data` with NO
    boundary parameter is a clean 400, because starlette raises its own `MultiPartException`
    there and catches it. Only the parse failures from the underlying `python_multipart`
    package get through, and `_payload` does not name them.
    """
    for label, body in MALFORMED_MULTIPART:
        header = dict(MULTIPART)
        if label.startswith("boundary longer"):
            header["content-type"] = "multipart/form-data; boundary=" + "z" * 5000
        response = client.post(route, content=body, headers=header)
        assert response.status_code < 500, (
            f"{route} with {label!r} produced {response.status_code}"
        )


@pytest.mark.parametrize("route", POST_ROUTES)
def test_a_multipart_content_type_with_no_boundary_is_already_a_clean_400(client, route):
    """The half of the multipart path that IS guarded, pinned so the fix above cannot
    regress it: starlette raises its own `MultiPartException` for a missing boundary and
    converts it to a 400, which never reaches `_payload`'s caller as a traceback."""
    response = client.post(route, content=b"--x\r\n--x--\r\n",
                           headers={"content-type": "multipart/form-data"})
    assert response.status_code == 400, response.text[:200]


@pytest.mark.parametrize("route", POST_ROUTES)
def test_the_off_roster_body_is_designs_own_error_object(client, route):
    """DESIGN's route table: `404 {"error": "not on roster"}` (`web/app.py:_not_on_roster`)."""
    response = client.post(route, json={"person_id": "nobody-here"})
    assert response.status_code == 404
    assert response.json() == {"error": "not on roster"}


# ---------------------------------------------------------------------------
# 3. Both accepted encodings, and a Content-Type that disagrees with the body.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("json person_id", {"json": {"person_id": KNOWN_ID}}),
        ("json name", {"json": {"name": KNOWN_NAME}}),
        ("json name as the id", {"json": {"name": KNOWN_ID}}),
        ("json person_id as the name", {"json": {"person_id": KNOWN_NAME}}),
        ("urlencoded person_id", {"content": b"person_id=alpha", "headers": FORM}),
        ("urlencoded name", {"content": b"name=Teodoro+Vance", "headers": FORM}),
        ("urlencoded percent-escaped", {"content": b"name=Teodoro%20Vance", "headers": FORM}),
        ("urlencoded, last value wins", {"content": b"person_id=nope&person_id=alpha",
                                         "headers": FORM}),
        ("multipart form", {"files": {"person_id": (None, "alpha")}}),
        ("no content-type at all", {"content": b'{"person_id": "alpha"}'}),
        ("content-type is garbage", {"content": b"person_id=alpha",
                                     "headers": {"content-type": "not/a-real-type"}}),
        ("form body declared as json", {"content": b"person_id=alpha", "headers": JSON}),
        ("charset on the content type", {"content": b'{"person_id": "alpha"}',
                                         "headers": {"content-type":
                                                     "application/json; charset=utf-8"}}),
        ("upper-case content type", {"content": b'{"person_id": "alpha"}',
                                     "headers": {"content-type": "APPLICATION/JSON"}}),
    ],
)
def test_arrive_reaches_the_same_person_through_every_encoding_it_accepts(
    client, label, kwargs
):
    """`_payload`'s contract: JSON *and* urlencoded, one route, no second endpoint.

    The last four cases are the Content-Type/body MISMATCHES. `_payload` handles them by
    falling back — `json.loads` failing hands the bytes to `_from_urlencoded` — so a client
    that mislabels its body is still understood rather than 400'd. That is a real
    behaviour, so it is pinned here rather than left to be rediscovered.
    """
    response = client.post("/arrive", **kwargs)
    assert response.status_code < 500, f"{label}: {response.text[:300]}"
    assert response.status_code in (200, 303), f"{label} -> {response.status_code}"


def test_a_form_post_redirects_a_browser_and_a_json_client_gets_a_body(client):
    """`_is_form_post(request) and not _wants_json(request)` — both halves.

    A browser form has no `Accept: application/json`, so it lands on the digest page (303).
    An API client that sends a form body but asks for JSON gets the JSON body.
    """
    browser = client.post("/arrive", content=b"person_id=alpha", headers=FORM,
                          follow_redirects=False)
    assert browser.status_code == 303
    assert browser.headers["location"].startswith("/digest/")

    api = client.post("/arrive", content=b"person_id=alpha",
                      headers={**FORM, "accept": "application/json"}, follow_redirects=False)
    assert api.status_code == 200
    assert set(api.json()) == {"digest_id", "person_id", "digest_url"}


def test_a_form_post_to_leave_lands_on_the_index_not_a_json_body(client):
    client.post("/arrive", json={"person_id": KNOWN_ID})
    browser = client.post("/leave", content=b"person_id=alpha", headers=FORM,
                          follow_redirects=False)
    assert browser.status_code == 303
    assert browser.headers["location"] == "/"


# ---------------------------------------------------------------------------
# 4. Content negotiation.
# ---------------------------------------------------------------------------

ACCEPT_HEADERS = [
    ("absent", None),
    ("*/*", "*/*"),
    ("text/html", "text/html"),
    ("application/json", "application/json"),
    ("json with a q value", "application/json;q=0.9"),
    ("html first, json second", "text/html, application/json;q=0.9"),
    ("an offer the app does not make", "application/xml"),
    ("a nonsense token", "not-a-media-type"),
    ("empty", ""),
    ("a very long header", "text/html, " * 500 + "*/*"),
]


@pytest.mark.parametrize("label,value", ACCEPT_HEADERS, ids=[a[0] for a in ACCEPT_HEADERS])
def test_building_negotiates_on_the_substring_and_never_406s_or_500s(client, label, value):
    """DESIGN's route table: "HTML list of present people (JSON if `Accept: application/json`)".

    `_wants_json` is a substring test, not a full RFC 9110 negotiation, and this pins the
    consequences rather than pretending otherwise: any header CONTAINING
    `application/json` gets JSON — q-values included — and everything else gets the page.
    An `Accept` the app cannot satisfy is served HTML, not a 406: DESIGN offers exactly two
    representations and the route table names no third answer.
    """
    headers = {} if value is None else {"accept": value}
    response = client.get("/building", headers=headers)
    assert response.status_code == 200, response.text[:300]
    wants_json = value is not None and "application/json" in value.lower()
    kind = "application/json" if wants_json else "text/html"
    assert response.headers["content-type"].startswith(kind), (
        f"Accept: {value!r} -> {response.headers['content-type']}"
    )


@pytest.mark.parametrize("route", ("/", "/graph", "/corpus"))
@pytest.mark.parametrize("label,value", ACCEPT_HEADERS, ids=[a[0] for a in ACCEPT_HEADERS])
def test_the_html_only_routes_ignore_accept_entirely(client, route, label, value):
    """Only `/building` negotiates. The other pages have one representation, and asking for
    JSON must serve the page rather than 406 or an empty body."""
    headers = {} if value is None else {"accept": value}
    response = client.get(route, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_the_building_json_body_is_designs_two_key_object(client):
    client.post("/arrive", json={"person_id": KNOWN_ID})
    body = client.get("/building", headers={"accept": "application/json"}).json()
    assert set(body) == {"present", "count"}
    assert body["count"] == len(body["present"]) == 1
    assert set(body["present"][0]) == {"person_id", "name"}
    assert body["present"][0] == {"person_id": KNOWN_ID, "name": KNOWN_NAME}


# ---------------------------------------------------------------------------
# 5. Presence semantics over HTTP.
# ---------------------------------------------------------------------------


def test_the_happy_path_actually_produces_a_digest_with_content_on_it(client, llm):
    """The anti-vacuity control for this whole module.

    Every other test here asserts a status code, and a broken app that answered 200 with an
    empty body would satisfy most of them. This one asserts the arrival path really ran:
    the model was called once with the person's name in the prompt, the page names them,
    it carries the model's opener rather than the template fallback, and it cites a source.
    """
    response = client.post("/arrive", json={"person_id": KNOWN_ID})
    assert response.status_code == 200
    body = response.json()
    assert body["person_id"] == KNOWN_ID
    assert body["digest_url"] == f"/digest/{body['digest_id']}"

    assert len(llm.calls_for("SayOutLoud")) == 1, llm.calls
    assert KNOWN_NAME in llm.calls_for("SayOutLoud")[0].user

    page = client.get(body["digest_url"])
    assert page.status_code == 200
    assert KNOWN_NAME in page.text
    assert OPENER in page.text, "the page fell back to the template instead of the model line"
    assert "northgatelabs.example" in page.text, "the digest cites no source at all"
    assert len(page.text) > 2000, "the digest page is a stub"


def test_arriving_twice_is_one_person_in_the_building_and_two_digests(client):
    first = client.post("/arrive", json={"person_id": KNOWN_ID})
    second = client.post("/arrive", json={"person_id": KNOWN_ID})
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["digest_id"] != second.json()["digest_id"], (
        "two arrivals are two events; a shared id would make the second overwrite the first"
    )
    body = client.get("/building", headers={"accept": "application/json"}).json()
    assert body["count"] == 1
    # Both digests stay addressable — the second arrival must not evict the first.
    for response in (first, second):
        assert client.get(response.json()["digest_url"]).status_code == 200


def test_arriving_again_moves_a_person_to_the_end_of_the_presence_order(client):
    """`Presence.arrive` pops before it inserts, so a re-arrival is the MOST RECENT one.

    "who walked in most recently is real information to a host" (`web/presence.py`), and
    the pop is the only line that makes it true — inserting a key that is already in a dict
    leaves its position alone. Measured: a sabotage deleting the pop was NOT caught until
    this test existed, because every other presence assertion is about membership.
    """
    for person_id in ("alpha", "bravo", "charlie"):
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200

    def present():
        return [p["person_id"] for p in client.get(
            "/building", headers={"accept": "application/json"}).json()["present"]]

    assert present() == ["alpha", "bravo", "charlie"]
    assert client.post("/arrive", json={"person_id": "alpha"}).status_code == 200
    assert present() == ["bravo", "charlie", "alpha"], (
        "a returning member did not become the most recent arrival"
    )
    # ... and a form post takes the same path.
    assert client.post("/leave", content=b"person_id=bravo", headers=FORM,
                       follow_redirects=False).status_code == 303
    assert present() == ["charlie", "alpha"]
    assert client.post("/arrive", content=b"person_id=charlie", headers=FORM,
                       follow_redirects=False).status_code == 303
    assert present() == ["alpha", "charlie"]


def test_leaving_someone_who_was_never_here_is_a_200_not_an_error(client):
    """`Presence.leave` returns whether they were here and `/leave` ignores it: R5 asks only
    that they stop being proposed, and they already have."""
    response = client.post("/leave", json={"person_id": KNOWN_ID})
    assert response.status_code == 200
    assert response.json() == {"present": []}


def test_leaving_twice_is_idempotent(client):
    client.post("/arrive", json={"person_id": KNOWN_ID})
    first = client.post("/leave", json={"person_id": KNOWN_ID})
    second = client.post("/leave", json={"person_id": KNOWN_ID})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"present": []}


def test_a_leave_for_an_unknown_person_is_a_404_before_it_touches_presence(client):
    client.post("/arrive", json={"person_id": KNOWN_ID})
    assert client.post("/leave", json={"person_id": "ghost"}).status_code == 404
    body = client.get("/building", headers={"accept": "application/json"}).json()
    assert body["count"] == 1, "a refused leave must not disturb the presence set"


def test_the_arriving_person_is_never_proposed_to_themselves(client):
    """`graph.match` never returns `a` in their own result, and `/arrive` adds them to
    presence BEFORE matching (`web/app.py:220`), so this is the crossing of the two."""
    client.post("/arrive", json={"person_id": KNOWN_ID})
    second = client.post("/arrive", json={"person_id": KNOWN_ID})
    page = client.get(second.json()["digest_url"])
    assert page.status_code == 200


# ---------------------------------------------------------------------------
# 6. Path parameters: /digest/{id} and /debug/{id}.
# ---------------------------------------------------------------------------

#: (label, segment, still_a_single_path_segment). The third element records whether the
#: segment survives URL decoding as ONE segment; the ones that do not decode to a `/` and
#: therefore miss `/digest/{digest_id}` entirely, which is starlette's 404 rather than the
#: app's page. Both answers are correct; conflating them is what hides a routing change.
HOSTILE_SEGMENTS = [
    ("unknown", "nope", True),
    ("space", "%20", True),
    ("percent-escaped dot dot", "%2e%2e", True),
    ("html", "%3Cscript%3Ealert(1)%3C%2Fscript%3E", False),
    ("nul byte", "%00", True),
    ("10KB", "A" * 10_000, True),
    ("astral plane", "%f0%9f%92%a9", True),
    ("newline", "a%0aX-Injected:%201", True),
    ("hash-ish", "abc%23def", True),
    ("query-ish", "abc%3Fdef", True),
    ("encoded slash", "a%2fb", False),
    ("backslash", "a%5cb", True),
    ("upper hex escape", "%41%42", True),
    ("percent with no hex", "abc%zz", True),
    ("trailing bare percent", "abc%", True),
]


@pytest.mark.parametrize(
    "label,segment,one_segment", HOSTILE_SEGMENTS, ids=[s[0] for s in HOSTILE_SEGMENTS]
)
def test_a_hostile_digest_id_is_a_404_and_never_a_traceback(client, label, segment, one_segment):
    response = client.get(f"/digest/{segment}")
    assert response.status_code < 500, f"{label}: {response.text[:300]}"
    assert response.status_code == 404
    if one_segment:
        assert response.headers["content-type"].startswith("text/html"), (
            "an unknown digest is the app's own not_found page, not starlette's JSON detail"
        )
    else:
        assert response.json() == {"detail": "Not Found"}, (
            "a segment carrying an encoded slash must miss the one-segment route entirely"
        )


@pytest.mark.parametrize(
    "label,segment,one_segment", HOSTILE_SEGMENTS, ids=[s[0] for s in HOSTILE_SEGMENTS]
)
def test_a_hostile_debug_id_is_a_404_and_never_a_traceback(client, label, segment, one_segment):
    response = client.get(f"/debug/{segment}")
    assert response.status_code < 500, f"{label}: {response.text[:300]}"
    assert response.status_code == 404


def test_an_escaped_slash_does_not_match_the_one_segment_route_at_all(client):
    """`%2f` decodes to `/` before routing, so `/digest/a%2fb` is a THREE-segment path and
    misses `/digest/{digest_id}` entirely. It is starlette's 404, not the app's page, and
    the distinction is the evidence that no handler ran on it."""
    response = client.get("/digest/a%2fb")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------
# 6b. Path decoding, driven at the ASGI boundary rather than through a client.
#
# `TestClient` cannot answer the packet's "URL-encoded and double-encoded path segments"
# question honestly: it hands httpx a URL, reads back httpx's ALREADY-UNQUOTED `.path`,
# and unquotes it again, so `%2541` reaches `scope["path"]` as `A` — two decodes where
# uvicorn does exactly one. Measured, with a middleware printing the scope:
#
#     GET /digest/%2541   ->  raw_path=b'/digest/%2541'   path='/digest/A'
#
# So the double-decode is a property of the TEST CLIENT and asserting it would pin a
# behaviour production does not have. Calling the ASGI app with a scope built here is the
# only way to state what the app does with a given decoded segment, so that is what these
# do: `path` is the single decode uvicorn performs, `raw_path` the bytes off the wire.
# ---------------------------------------------------------------------------


async def _asgi_get(app, path: str, raw_path: bytes) -> tuple[int, bytes]:
    """One GET straight into the ASGI app, with `path`/`raw_path` under our control."""
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": raw_path, "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver")], "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


@pytest.mark.parametrize(
    "path,raw_path",
    [
        # what uvicorn delivers for a SINGLY-encoded segment
        ("/digest/A", b"/digest/%41"),
        # what uvicorn delivers for a DOUBLY-encoded one: one decode leaves the escape
        ("/digest/%41", b"/digest/%2541"),
        ("/digest/%2e%2e%2f", b"/digest/%252e%252e%252f"),
        ("/digest/../../etc/passwd", b"/digest/..%2f..%2fetc%2fpasswd"),
        ("/digest/..", b"/digest/.."),
        ("/debug/../../etc/passwd", b"/debug/..%2f..%2fetc%2fpasswd"),
        ("/debug/%2e%2e", b"/debug/%252e%252e"),
    ],
)
async def test_no_path_a_proxy_can_deliver_reaches_a_handler_it_should_not(
    corpus, llm, path, raw_path
):
    """A traversal segment is never a route hit and never a 5xx, whichever decoding a proxy
    in front of the app happens to apply.

    Nothing under `/digest` or `/debug` touches the filesystem — both are dictionary
    lookups — so this is a routing assertion, not a file-access one: the value of pinning
    it is that a future route that DID read a path would inherit the same segments.
    """
    app = create_app(dossier_dir=corpus, llm=llm)
    status, _body = await _asgi_get(app, path, raw_path)
    assert status < 500, f"{raw_path!r} -> {status}"
    assert status == 404, f"{raw_path!r} -> {status}"


async def test_the_asgi_driver_itself_can_reach_a_real_page(corpus, llm):
    """Positive control for the driver above. Without it a bug in `_asgi_get` would make
    every traversal case pass for the wrong reason."""
    app = create_app(dossier_dir=corpus, llm=llm)
    status, body = await _asgi_get(app, "/building", b"/building")
    assert status == 200
    assert b"<html" in body.lower()


def test_a_digest_id_from_another_app_is_not_addressable_here(corpus, llm):
    """Presence and digest history are process-local per APP (DESIGN Decision 11). A digest
    minted by one app must 404 on another even over the identical corpus."""
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as first, \
            TestClient(create_app(dossier_dir=corpus, llm=LLMDouble()),
                       raise_server_exceptions=False) as second:
        minted = first.post("/arrive", json={"person_id": KNOWN_ID}).json()["digest_id"]
        assert first.get(f"/digest/{minted}").status_code == 200
        assert second.get(f"/digest/{minted}").status_code == 404


def test_a_digest_id_ages_out_of_the_history_and_answers_404_rather_than_500(client):
    """`DIGEST_HISTORY = 200` and `_remember` evicts the oldest. A demo left running past
    the cap must serve a 404 page for the evicted id, not fall over."""
    from arrival.web.app import DIGEST_HISTORY

    first = client.post("/arrive", json={"person_id": KNOWN_ID}).json()["digest_id"]
    for _ in range(DIGEST_HISTORY):
        client.post("/arrive", json={"person_id": KNOWN_ID})
    evicted = client.get(f"/digest/{first}")
    assert evicted.status_code == 404
    assert evicted.status_code < 500


def test_an_unknown_route_is_a_404_and_the_app_exposes_no_other_paths(client):
    for path in ("/nope", "/admin", "/.env", "/static/x.css", "/digest", "/debug",
                 "/arrive/", "/openapi.json.bak"):
        assert client.get(path).status_code in (404, 405, 307), path


# ---------------------------------------------------------------------------
# 7. Headers a real caller sends that must not change the answer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"x-forwarded-for": "127.0.0.1, ../../etc"},
        {"user-agent": "<script>alert(1)</script>"},
        {"content-length": "999999"},
        {"expect": "100-continue"},
        {"accept-encoding": "gzip, deflate, br"},
        {"cookie": "session=" + "A" * 4000},
        {"referer": "http://evil.example/" + "A" * 2000},
    ],
)
def test_a_hostile_header_does_not_change_the_arrival_answer(client, headers):
    response = client.post("/arrive", json={"person_id": KNOWN_ID}, headers=headers)
    assert response.status_code < 500, response.text[:300]
    assert response.status_code == 200
    assert response.json()["person_id"] == KNOWN_ID
