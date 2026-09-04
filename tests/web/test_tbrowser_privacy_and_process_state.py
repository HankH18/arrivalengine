"""TESTBROWSER: the privacy sweep and the process-local-digest claim, both done by hand
against the live product first and pinned here so neither has to be done by hand again.

**The privacy sweep.** The exploratory pass fetched every host-facing route in every
presence state and searched the raw HTML, by string, for material the corpus withholds.
That sweep is reproduced here in full: `tbrowser_corpus` plants four sentinels, one per
reason a fact can be unpublishable, and every host-facing page is searched for all four.

The four are deliberately of different KINDS, because they fail through different code:

* an `excluded=True` fact (R11 family)             -> `taste.is_displayable` clause 1
* an `excluded=True` fact (R11 home_or_property)   -> `taste.is_displayable` clause 1
* a `source_kind` outside `DISPLAYABLE_KINDS`      -> `taste.is_displayable` clause 3
* a `provenance.confidence` under the floor        -> `taste.is_displayable` clause 2

A test that plants only the first kind passes while the other two leak, which is the
failure this module exists to prevent. `taste.py` owns all three clauses and this lane owns
none of them, so the assertions grade a module outside this lane's write scope.

**The process-local claim.** `not_found.html` tells the host, in words, that "Digests live
in this process's memory and clear on restart; a link from an earlier run will not resolve."
That sentence is a promise about behaviour. It was verified by hand -- a digest id was
minted, the uvicorn process restarted, and the old link followed -- and the test below
reproduces it by building a SECOND app, which is the same discontinuity `create_app` sees
across a restart.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from tbrowser_corpus import (
    LOW_CONFIDENCE_SENTINEL,
    UNDISPLAYABLE_SENTINEL,
    WITHHELD_ADDRESS,
    WITHHELD_SENTINEL,
    WITHHELD_STRINGS,
    build_corpus,
)

from arrival.config import get_settings
from arrival.taste import CONFIDENCE_FLOOR, DISPLAYABLE_KINDS
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBROWSER")

OPENER = "Ask about the scheduling group they run."

#: The whole host-facing surface. `/debug` is deliberately absent -- it is the one page
#: permitted to show withheld material, and it gets its own tests below.
HOST_FACING = ("/", "/building", "/graph", "/corpus")


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "dossiers")


@pytest.fixture
def llm():
    return LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})


@pytest.fixture
def client(corpus, llm, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _arrive(client, person_id):
    response = client.post("/arrive", json={"person_id": person_id})
    assert response.status_code == 200, response.text
    return response.json()["digest_id"]


# --------------------------------------------------------------------------- the sentinels
# These first three tests are the control: they prove each sentinel is genuinely
# unpublishable for the REASON claimed, so a later "it did not appear" means the filter
# held rather than that the fixture never carried the string.


def _all_facts(corpus):
    """Every fact in the corpus, so a control test cannot miss a sentinel that moved."""
    import json

    facts = []
    for path in sorted(corpus.glob("*.json")):
        facts.extend(json.loads(path.read_text())["facts"])
    return facts


def test_the_family_and_address_sentinels_are_carried_by_excluded_facts(corpus):
    facts = _all_facts(corpus)
    by_sentinel = {
        WITHHELD_SENTINEL: None,
        WITHHELD_ADDRESS: None,
        UNDISPLAYABLE_SENTINEL: None,
        LOW_CONFIDENCE_SENTINEL: None,
    }
    for fact in facts:
        for sentinel in by_sentinel:
            if sentinel in fact["text"] or sentinel in fact["provenance"]["quote"]:
                by_sentinel[sentinel] = fact

    assert by_sentinel[WITHHELD_SENTINEL]["excluded"] is True
    assert by_sentinel[WITHHELD_SENTINEL]["exclusion_reason"] == "family"
    assert by_sentinel[WITHHELD_ADDRESS]["excluded"] is True
    assert by_sentinel[WITHHELD_ADDRESS]["exclusion_reason"] == "home_or_property"


def test_the_source_kind_sentinel_is_undisplayable_only_because_of_its_kind(corpus):
    """It must be a fact the OTHER two clauses would have let through."""
    facts = _all_facts(corpus)
    fact = next(f for f in facts if UNDISPLAYABLE_SENTINEL in f["text"])
    assert fact["excluded"] is False, "clause 1 must not be what stops it"
    assert fact["provenance"]["confidence"] >= CONFIDENCE_FLOOR, "clause 2 must not stop it"
    assert fact["provenance"]["source_kind"] not in DISPLAYABLE_KINDS


def test_the_low_confidence_sentinel_is_undisplayable_only_because_of_its_confidence(corpus):
    facts = _all_facts(corpus)
    fact = next(f for f in facts if LOW_CONFIDENCE_SENTINEL in f["text"])
    assert fact["excluded"] is False, "clause 1 must not be what stops it"
    assert fact["provenance"]["source_kind"] in DISPLAYABLE_KINDS, "clause 3 must not stop it"
    assert fact["provenance"]["confidence"] < CONFIDENCE_FLOOR


# --------------------------------------------------------------------------- the sweep


@pytest.mark.parametrize("route", HOST_FACING)
@pytest.mark.parametrize("reason,needle", sorted(WITHHELD_STRINGS.items()))
def test_no_withheld_string_reaches_a_host_facing_page(client, route, reason, needle):
    """The exploratory sweep, as a matrix: every route x every withholding reason."""
    for person_id in ("harlow-vane", "indigo-marsh", "juniper-crane", "lumen-tack"):
        _arrive(client, person_id)
    body = client.get(route).text
    assert needle not in body, (
        f"{route} printed material withheld for: {reason}. Found {needle!r} in the rendered page."
    )


@pytest.mark.parametrize("reason,needle", sorted(WITHHELD_STRINGS.items()))
def test_no_withheld_string_reaches_a_digest_page(client, reason, needle):
    """Every digest of every person, in a room where everyone is present."""
    digests = {
        person_id: _arrive(client, person_id)
        for person_id in ("harlow-vane", "indigo-marsh", "juniper-crane", "lumen-tack")
    }
    for person_id, digest_id in digests.items():
        body = client.get(f"/digest/{digest_id}").text
        assert needle not in body, (
            f"the digest for {person_id} printed material withheld for: {reason}. Found {needle!r}."
        )


def test_the_sweep_would_actually_catch_a_leak(client):
    """A positive control for the sweep itself.

    `/debug` is the one page allowed to print withheld material. If the sentinels never
    appear THERE either, then every "not found" above is evidence about the fixture rather
    than about the filter, and this whole module measures nothing.
    """
    with TestClient(
        create_app(dossier_dir=client.app.state.store.dossier_dir, llm=None)
    ) as operator:
        operator.app.state.debug_views = True
        body = operator.get("/debug/harlow-vane").text + operator.get("/debug/indigo-marsh").text

    for reason, needle in sorted(WITHHELD_STRINGS.items()):
        assert needle in body, (
            f"the operator view does not show material withheld for {reason}, so the "
            f"host-facing sweep for {needle!r} proves nothing"
        )


# --------------------------------------------------------------------------- R15 switch


def test_debug_is_a_404_when_the_switch_is_off(client):
    """Not a 403: a 403 would confirm the dossier is there to see."""
    for spelling in ("harlow-vane", "Harlow Vane", "lumen-tack", "nobody-at-all"):
        response = client.get(f"/debug/{spelling}")
        assert response.status_code == 404, spelling
        assert "Withheld" not in response.text
        assert WITHHELD_SENTINEL not in response.text


def test_debug_views_is_read_at_factory_time_not_import_time(corpus, llm, monkeypatch):
    """Two apps in one process, built either side of the environment changing."""
    # `get_settings` is lru_cached, so the cache has to be dropped between the two builds
    # or the second app reads the first app's environment. That caching is exactly why the
    # factory-time read matters: an app that snapshotted settings at IMPORT time could not
    # be corrected by clearing the cache at all.
    monkeypatch.setenv("DEBUG_VIEWS", "0")
    get_settings.cache_clear()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as off:
        assert off.get("/debug/harlow-vane").status_code == 404

    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as on:
        response = on.get("/debug/harlow-vane")
        assert response.status_code == 200
        assert WITHHELD_SENTINEL in response.text


# ------------------------------------------------------- the process-local digest claim


def test_a_digest_link_from_an_earlier_process_does_not_resolve(corpus, llm):
    """The sentence on `not_found.html` is a promise; this executes it.

    Verified by hand first: a digest id was minted against a running uvicorn, the process
    was killed and restarted against the same corpus, and the old link returned 404 with
    this page. A second `create_app` is the same discontinuity.
    """
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as first:
        digest_id = _arrive(first, "harlow-vane")
        assert first.get(f"/digest/{digest_id}").status_code == 200

    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as second:
        stale = second.get(f"/digest/{digest_id}")
        assert stale.status_code == 404
        # The page must SAY why, or the host is left thinking the product lost the digest.
        assert "clear on restart" in stale.text
        assert "will not resolve" in stale.text
        assert digest_id in stale.text, "the page should name the id that failed"


def test_presence_does_not_survive_a_new_app(corpus, llm):
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as first:
        _arrive(first, "harlow-vane")
        _arrive(first, "indigo-marsh")
        assert first.get("/building", headers={"Accept": "application/json"}).json()["count"] == 2

    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as second:
        fresh = second.get("/building", headers={"Accept": "application/json"}).json()
        assert fresh == {"present": [], "count": 0}


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)",  # a bare tag, no slash: reaches the handler
        '"><img src=x onerror=alert(1)>',  # attribute-breakout shape
        "&lt;already-escaped&gt;",
        "a" * 400,
    ],
)
def test_a_hostile_digest_id_is_escaped_into_the_not_found_page(corpus, llm, hostile):
    """A digest id is user-controlled input that the 404 page echoes back.

    Ids containing a literal `/` are not tested here: they split into extra path segments
    and are refused by the router before any template runs, which is a different (and also
    safe) code path.
    """
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as client:
        response = client.get("/digest/" + hostile)
        assert response.status_code == 404
        body = response.text
        # What makes an echo dangerous is a `<` that the browser parses as a tag opener.
        # `onerror=alert(1)` sitting inside a <p> as escaped TEXT is inert, so the check is
        # for tag introducers, not for the scary-looking substring.
        assert "<script" not in body.lower()
        assert "<img" not in body.lower()
        # and the echo must actually be there, escaped -- otherwise this asserts nothing
        if "<" in hostile:
            assert "&lt;" in body, "the hostile id was neither escaped nor echoed"


# --------------------------------------------------------------------------- second arrival


def test_two_arrivals_for_one_person_mint_two_digests_and_one_presence_entry(client):
    """Observed by hand on both the local server and the live deploy."""
    first = _arrive(client, "harlow-vane")
    second = _arrive(client, "harlow-vane")
    assert first != second, "a re-arrival must be its own digest, not a cached one"
    assert client.get(f"/digest/{first}").status_code == 200
    assert client.get(f"/digest/{second}").status_code == 200

    present = client.get("/building", headers={"Accept": "application/json"}).json()
    ids = [p["person_id"] for p in present["present"]]
    assert ids.count("harlow-vane") == 1, f"presence listed a person twice: {ids}"
    assert present["count"] == 1


def test_nobody_is_ever_proposed_to_meet_themselves(client):
    """R3 adds the arriving person to presence BEFORE matching, so this is a real risk."""
    for person_id in ("harlow-vane", "indigo-marsh", "juniper-crane"):
        _arrive(client, person_id)
    digest_id = _arrive(client, "harlow-vane")
    body = client.get(f"/digest/{digest_id}").text
    meet = re.search(r'<section id="meet">(.*?)</section>', body, re.S).group(1)
    assert "<strong>Harlow Vane</strong>" not in meet
