"""Privacy at the API layer: the withheld material, hunted for over HTTP.

R11/R12 are graded per-renderer elsewhere (`tests/web/test_t040_source_evidence.py`,
`test_t17_graph_page.py`, `test_corpusgraph_corpus_privacy.py`). What none of those does is
ask the question from OUTSIDE: given an app booted over a corpus that contains a withheld
sentence, can any HTTP response a caller can obtain be made to contain that sentence?

The method is a SENTINEL. Every withheld string in the corpus below carries a token that
appears nowhere else in the repository, so a leak is a substring test rather than a
judgement about prose, and a renderer that paraphrases, truncates or HTML-escapes the
sentence still trips it — the escaped forms are searched too.

Three separate withholding clauses are planted, because `taste.is_displayable` ANDs three
independent conditions and a leak through any one of them is a leak:

1. `fact.excluded` — the taste filter's own verdict.
2. `provenance.confidence < CONFIDENCE_FLOOR` — a fact nobody was sure enough about.
3. `provenance.source_kind not in DISPLAYABLE_KINDS` — `fec` is the documented example:
   high confidence, not excluded, and still not showable.

And two operator-only strings that are not facts at all: a rejected `Verdict.evidence`
(kept "for /debug", `contracts.Resolution`) and the `disambiguator` beside it.

Grading references: `arrival.taste.CONFIDENCE_FLOOR`, `DISPLAYABLE_KINDS` and
`EXCLUSION_POLICY` read from `src/` at run time (never transcribed here — a copied constant
would make this test grade against itself), plus sentinel strings this module invents as
INPUT. No fixture is an answer key.
"""

from __future__ import annotations

import html
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape as markup_escape

from arrival.config import get_settings
from arrival.contracts import SourceKind
from arrival.taste import CONFIDENCE_FLOOR, DISPLAYABLE_KINDS, EXCLUSION_POLICY
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBACKEND")

OPENER = "Ask about the evaluation harness they open-sourced last spring."

#: One token per withholding clause. Alphanumeric so no escaping scheme can hide it, and
#: absent from the repository so a hit is unambiguous.
EXCLUDED_TOKEN = "ZQXWITHHELDEXCLUDEDZQX"
LOWCONF_TOKEN = "ZQXWITHHELDLOWCONFZQX"
KIND_TOKEN = "ZQXWITHHELDBADKINDZQX"
VERDICT_TOKEN = "ZQXREJECTEDEVIDENCEZQX"
DISAMBIGUATOR_TOKEN = "ZQXREJECTEDDISAMBZQX"

ALL_TOKENS = (EXCLUDED_TOKEN, LOWCONF_TOKEN, KIND_TOKEN, VERDICT_TOKEN,
              DISAMBIGUATOR_TOKEN)

#: A `SourceKind` that is real on the contract and refused by the taste layer.
NON_DISPLAYABLE_KIND = next(
    kind for kind in SourceKind.__args__ if kind not in DISPLAYABLE_KINDS
)


def _planted_fact(person_id: str, suffix: str, text: str, *, excluded=False,
                  reason=None, confidence=0.95, source_kind="search"):
    return {
        "fact_id": f"{person_id}-{suffix}",
        "text": text,
        "category": "affiliation",
        "provenance": {
            "doc_id": "0739f354798a77b4",
            "url": "https://travisledger.example/2026/05/whatever",
            "source_kind": source_kind,
            "quote": text,
            "published_at": "2026-05-09",
            "retrieved_at": "2026-08-30T15:04:11Z",
            "confidence": confidence,
        },
        "excluded": excluded,
        "exclusion_reason": reason,
    }


@pytest.fixture
def corpus(tmp_path, request):
    """The fixture corpus with three unshowable facts and a poisoned verdict per person.

    Each planted fact is also attached to a real hub's `evidence_fact_ids`, so every
    hub-evidence renderer (`/graph`, `/corpus`, the digest's meet rows) has to resolve it
    and then decline to print it. A filter applied only at the `lately`/`non_obvious` seam
    would pass a naive version of this test and fail this one.
    """
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        person_id = raw["person"]["person_id"]
        planted = [
            _planted_fact(person_id, "excluded",
                          f"They own a house at 1442 Quarrystone Lane {EXCLUDED_TOKEN}.",
                          excluded=True, reason="home_or_property"),
            _planted_fact(person_id, "lowconf",
                          f"They may have sat on the Harborline board {LOWCONF_TOKEN}.",
                          confidence=max(0.0, CONFIDENCE_FLOOR - 0.2)),
            _planted_fact(person_id, "badkind",
                          f"They gave to a campaign committee {KIND_TOKEN}.",
                          source_kind=NON_DISPLAYABLE_KIND),
        ]
        raw["facts"].extend(planted)
        for hub in raw["hubs"]:
            hub["evidence_fact_ids"] = list(hub["evidence_fact_ids"]) + [
                fact["fact_id"] for fact in planted
            ]
        for verdict in raw["resolution"].get("rejected", []):
            verdict["evidence"] = f"{VERDICT_TOKEN} {verdict['evidence']}"
            verdict["disambiguator"] = f"{DISAMBIGUATOR_TOKEN} {verdict['disambiguator']}"
        (destination / path.name).write_text(json.dumps(raw), encoding="utf-8")
    return destination


def _client(corpus, monkeypatch, *, debug: bool):
    if debug:
        monkeypatch.setenv("DEBUG_VIEWS", "1")
    else:
        monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    get_settings.cache_clear()
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    return TestClient(create_app(dossier_dir=corpus, llm=llm),
                      raise_server_exceptions=False)


def _every_host_facing_response(client) -> dict[str, str]:
    """Every body a caller can obtain without the operator switch, with everyone present."""
    bodies: dict[str, str] = {}
    digest_urls = []
    for person_id in ("alpha", "bravo", "charlie", "delta"):
        response = client.post("/arrive", json={"person_id": person_id})
        assert response.status_code == 200, response.text
        bodies[f"POST /arrive ({person_id})"] = response.text
        digest_urls.append(response.json()["digest_url"])

    for url in digest_urls:
        page = client.get(url)
        assert page.status_code == 200
        bodies[f"GET {url}"] = page.text

    bodies["GET /"] = client.get("/").text
    bodies["GET /building"] = client.get("/building").text
    bodies["GET /building (json)"] = client.get(
        "/building", headers={"accept": "application/json"}).text
    bodies["GET /graph"] = client.get("/graph").text
    bodies["GET /corpus"] = client.get("/corpus").text
    bodies["POST /leave"] = client.post("/leave", json={"person_id": "delta"}).text
    bodies["GET /graph (one left)"] = client.get("/graph").text
    for person_id in ("alpha", "bravo", "charlie", "delta"):
        bodies[f"GET /debug/{person_id} (switch off)"] = client.get(
            f"/debug/{person_id}").text
    bodies["GET /digest/unknown"] = client.get("/digest/nope").text
    return bodies


def _forms(token: str) -> list[str]:
    """The token as any renderer could have written it."""
    return [
        token,
        html.escape(token),
        urllib.parse.quote(token),
        json.dumps(token)[1:-1],
        token.lower(),
        token.upper(),
    ]


@pytest.mark.parametrize("token", ALL_TOKENS)
def test_no_host_facing_response_carries_any_withheld_string(corpus, monkeypatch, token):
    with _client(corpus, monkeypatch, debug=False) as client:
        bodies = _every_host_facing_response(client)
    hits = {
        name: form
        for name, body in bodies.items()
        for form in _forms(token)
        if form in body
    }
    assert not hits, f"{token} reached a host-facing surface: {hits}"


def test_the_sentinels_really_are_in_the_corpus_the_app_booted_over(corpus, monkeypatch):
    """Positive control. Without it, a typo in the fixture — or a corpus the app never
    read — would make every leak test above pass by testing nothing at all.

    `/debug` is the surface R15 licenses to show withheld material, so with the switch ON
    every sentinel MUST appear. That is the same assertion inverted, and it is what proves
    the tokens are reachable in principle.
    """
    with _client(corpus, monkeypatch, debug=True) as client:
        page = client.get("/debug/alpha")
        assert page.status_code == 200
        missing = [token for token in ALL_TOKENS if token not in page.text]
        assert not missing, (
            f"{missing} never reached /debug either, so the leak tests proved nothing"
        )


def test_the_operator_switch_opens_debug_and_nothing_else(corpus, monkeypatch):
    """Turning `/debug` on must not widen any other page. The withheld material is scoped
    to the route, not to the process."""
    with _client(corpus, monkeypatch, debug=True) as client:
        bodies = _every_host_facing_response(client)
    # `_every_host_facing_response` re-requests /debug, which is now open; drop those.
    bodies = {name: body for name, body in bodies.items() if "/debug/" not in name}
    hits = {
        name: token
        for name, body in bodies.items()
        for token in ALL_TOKENS
        if token in body
    }
    assert not hits, f"DEBUG_VIEWS=1 widened a host-facing page: {hits}"


@pytest.mark.parametrize(
    "route,accept",
    [("/arrive", None), ("/leave", None), ("/building", "application/json")],
)
def test_no_json_response_carries_fact_text_of_any_kind(corpus, monkeypatch, route, accept):
    """The JSON surfaces answer with identity and counts only — `{digest_id, person_id,
    digest_url}`, `{present}` and `{present, count}`. Pinned as a SHAPE assertion rather
    than a substring one: a future field carrying a sentence would pass a leak test written
    against today's sentinels and fail this."""
    headers = {"accept": accept} if accept else None
    with _client(corpus, monkeypatch, debug=True) as client:
        if route == "/building":
            client.post("/arrive", json={"person_id": "alpha"})
            body = client.get(route, headers=headers).json()
            assert set(body) == {"present", "count"}
            assert all(set(entry) == {"person_id", "name"} for entry in body["present"])
            return
        body = client.post(route, json={"person_id": "alpha"}, headers=headers).json()
    if route == "/arrive":
        assert set(body) == {"digest_id", "person_id", "digest_url"}
        assert all(isinstance(value, str) for value in body.values())
    else:
        assert set(body) == {"present"}
        assert all(set(entry) == {"person_id", "name"} for entry in body["present"])


def test_a_low_confidence_fact_is_gated_by_the_floor_the_taste_module_declares(
    tmp_path, request, monkeypatch
):
    """The floor is `<`, so `CONFIDENCE_FLOOR` itself SHOWS and anything under it does not.
    Read from `arrival.taste` at run time rather than transcribed, so moving the constant
    moves this test with it instead of silently disagreeing."""
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    at_floor = "ZQXATTHEFLOORZQX"
    below = "ZQXBELOWTHEFLOORZQX"
    for path in sorted(root.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        person_id = raw["person"]["person_id"]
        raw["facts"].extend([
            _planted_fact(person_id, "atfloor", f"They ship weekly {at_floor}.",
                          confidence=CONFIDENCE_FLOOR),
            _planted_fact(person_id, "below", f"They ship weekly {below}.",
                          confidence=CONFIDENCE_FLOOR - 1e-9),
        ])
        (destination / path.name).write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    get_settings.cache_clear()
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=destination, llm=llm),
                    raise_server_exceptions=False) as client:
        bodies = _every_host_facing_response(client)
    joined = "\n".join(bodies.values())
    assert below not in joined, "a fact below the confidence floor reached a page"
    assert at_floor in joined, (
        "a fact AT the floor was withheld; the comparison is `<`, so the floor value "
        "itself is displayable and this test would otherwise pass vacuously"
    )


def test_every_page_that_withholds_something_says_so(corpus, monkeypatch):
    """R13: the pages that apply the exclusion policy must also state it, so a host can see
    that something was withheld rather than assume nothing was found. Compared against
    `taste.EXCLUSION_POLICY` itself, HTML-escaped the way Jinja writes it."""
    with _client(corpus, monkeypatch, debug=False) as client:
        client.post("/arrive", json={"person_id": "alpha"})
        digest_url = client.post(
            "/arrive", json={"person_id": "charlie"}).json()["digest_url"]
        pages = {
            "/": client.get("/").text,
            "/graph": client.get("/graph").text,
            "/corpus": client.get("/corpus").text,
            digest_url: client.get(digest_url).text,
        }
    # `markupsafe.escape` and not `html.escape`: the two disagree on the apostrophe
    # (`&#39;` vs `&#x27;`) and the policy text is full of them, so the stdlib spelling
    # would never match a Jinja-rendered page. Escaping through the library Jinja actually
    # uses is what makes this a comparison against the constant rather than against a
    # guess about how it was written out.
    # An emptied `EXCLUSION_POLICY` would make `"" in body` true on every page, so the
    # substring test alone is vacuous by construction. Measured: a sabotage setting the
    # constant to "" was NOT caught until this guard was added.
    assert EXCLUSION_POLICY.strip(), "the exclusion policy is empty; the check below is vacuous"
    assert len(EXCLUSION_POLICY) > 200, (
        "the exclusion policy has shrunk to something that could match by accident"
    )
    for clause in ("home address", "health", "net worth", "political"):
        assert clause in EXCLUSION_POLICY, f"R13's {clause!r} clause is gone from the policy"

    escaped = str(markup_escape(EXCLUSION_POLICY))
    for name, body in pages.items():
        assert EXCLUSION_POLICY in body or escaped in body, (
            f"{name} withholds material without stating the policy (R13)"
        )


#: The one external origin every page is allowed to name. `tests/web/test_t056_skin.py`
#: pins the decision itself ("the webfonts are optional because every stack names a local
#: fallback"); what is asserted below is that it is still the ONLY one.
ALLOWED_EXTERNAL_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def test_no_page_the_app_serves_loads_a_script_or_an_unexpected_third_party(
    corpus, monkeypatch
):
    """Not privacy of the corpus but of the VIEWER: a staff page pulling a third-party
    resource tells whoever serves it that someone opened the arrival board.

    `test_t056_skin.py` asserts "no page loads any script" for the templates it renders;
    this asserts it for every response the ROUTES actually produce — the 404 pages
    included, which that file never reaches — and adds the stricter half it does not check:
    the set of external origins the browser FETCHES is exactly the webfont CDN. A beacon,
    analytics tag or CDN would fail here even if it arrived as a `<link>` rather than a
    `<script>`.

    "Fetches" is the load-bearing word. A citation's `<a href="https://...">` is the whole
    point of provenance and tells nobody anything until a host clicks it, so only the
    attributes a browser resolves on its own are scanned: `src`, `<link href>`, `srcset`,
    `@import` and CSS `url(...)`.
    """
    import re

    with _client(corpus, monkeypatch, debug=True) as client:
        bodies = _every_host_facing_response(client)
        bodies["GET /debug/alpha (open)"] = client.get("/debug/alpha").text
        bodies["GET /debug/unknown"] = client.get("/debug/nope").text

    fetched = re.compile(
        r"""(?:\bsrc\s*=\s*["']|\bsrcset\s*=\s*["']|<link\b[^>]*?\bhref\s*=\s*["']"""
        r"""|@import\s+["']|\burl\(\s*["']?)(?:https?:)?//([A-Za-z0-9.\-]+)""",
        re.IGNORECASE | re.DOTALL,
    )
    for name, body in bodies.items():
        assert "<script" not in body.lower(), f"{name} loads a script"
        assert "javascript:" not in body.lower(), f"{name} carries a javascript: url"
        unexpected = {
            host for host in fetched.findall(body) if host not in ALLOWED_EXTERNAL_HOSTS
        }
        assert not unexpected, f"{name} fetches from an unexpected origin: {unexpected}"


def test_the_third_party_scan_can_actually_see_a_planted_beacon(corpus, monkeypatch):
    """Positive control for the regex above. A pattern that matched nothing would pass the
    previous test on any page at all, so it is run against markup that definitely should
    fail."""
    import re

    fetched = re.compile(
        r"""(?:\bsrc\s*=\s*["']|\bsrcset\s*=\s*["']|<link\b[^>]*?\bhref\s*=\s*["']"""
        r"""|@import\s+["']|\burl\(\s*["']?)(?:https?:)?//([A-Za-z0-9.\-]+)""",
        re.IGNORECASE | re.DOTALL,
    )
    beacons = [
        '<img src="https://tracker.example/pixel.gif">',
        "<link rel=stylesheet href='//cdn.example/app.css'>",
        '<style>@import "https://evil.example/x.css";</style>',
        '<div style="background:url(//beacon.example/b.png)"></div>',
    ]
    for markup in beacons:
        hosts = {h for h in fetched.findall(markup) if h not in ALLOWED_EXTERNAL_HOSTS}
        assert hosts, f"the scan cannot see {markup!r}"
    # ... and it must NOT fire on a citation link, which is the distinction it exists for.
    citation = '<a href="https://austintechreview.example/2026/07/x">source</a>'
    assert not fetched.findall(citation)
