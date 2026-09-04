"""`GET /corpus` — the whole corpus as one page, and the ways it could quietly be wrong.

Every assertion here grades against something this change does NOT own: the fixture corpus
in `tests/fixtures/dossiers/` (T-0's), `arrival.graph.build_graph` and `arrival.graph.hub_idf`
(the project's own arithmetic), `arrival.taste.is_displayable`, or a literal. Nothing is
compared against `corpus_graph.py`'s constants or against a snapshot this module produced —
a test that grades a module by asking that module what it says is green for a module that
says nothing.

The distinction this page has to hold on to is that it is NOT `/graph`. `/graph` is scoped
to who is in the building; this one is scoped to the corpus, so the strongest single
assertion in this file is that an arrival does not change it by one byte.
"""

from __future__ import annotations

import datetime as dt

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from arrival.contracts import Dossier, Fact, Hub, PersonRef, Provenance, Resolution
from arrival.graph import build_graph, hub_idf
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("CORPUSGRAPH")

OPENER = "Ask about the evaluation harness they open-sourced last spring."

#: The four fixture people, spelled as `tests/fixtures/dossiers/` spells them.
NAMES = {
    "alpha": "Teodoro Vance",
    "bravo": "Nadia Ellingsworth",
    "charlie": "Selin Ardahan",
    "delta": "Hollis Trent",
}

#: R7's six section anchors, spelled as `test_t8_render.py` and the frozen T-8 suite spell
#: them when they call `html.index(...)`. No page but a digest may plant one.
SECTION_ANCHORS = (
    'id="who"',
    'id="meet"',
    'id="lately"',
    'id="not-on-the-first-page"',
    'id="say-out-loud"',
    'id="why-we-know-this"',
)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def fixture_dir(request):
    """The corpus this change does not own, and may therefore be graded against."""
    return request.config.rootpath / "tests/fixtures/dossiers"


@pytest.fixture
def corpus(tmp_path, fixture_dir):
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(fixture_dir.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


@pytest.fixture
def dossiers(fixture_dir):
    return [
        Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(fixture_dir.glob("*.json"))
    ]


@pytest.fixture
def truth(dossiers):
    """What `arrival.graph` says about the fixture corpus. This is the answer key."""
    graph = build_graph(d for d in dossiers if d.resolution.status == "resolved")
    hubs = [n for n, data in graph.nodes(data=True) if data.get("kind") == "hub"]
    return {
        "graph": graph,
        "people": [n for n, data in graph.nodes(data=True) if data.get("kind") == "person"],
        "hubs": hubs,
        "edges": graph.number_of_edges(),
        "shared": [n for n in hubs if graph.degree(n) >= 2],
        "solo": [n for n in hubs if graph.degree(n) == 1],
    }


def _llm():
    return LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})


@pytest.fixture
def client(corpus):
    with TestClient(create_app(dossier_dir=corpus, llm=_llm())) as test_client:
        yield test_client


def _page(client):
    response = client.get("/corpus")
    assert response.status_code == 200, response.text[:400]
    return response.text


# --------------------------------------------------------------------------- literal corpora


def _provenance(doc_id, *, confidence=0.9, source_kind="self_page", quote="a verbatim span"):
    return Provenance(
        doc_id=doc_id,
        url=f"https://example.invalid/{doc_id}",
        source_kind=source_kind,
        quote=quote,
        retrieved_at=dt.datetime(2026, 1, 2, 3, 4, 5),
        confidence=confidence,
    )


def _fact(fact_id, text, **provenance):
    excluded = provenance.pop("excluded", False)
    reason = provenance.pop("exclusion_reason", None)
    return Fact(
        fact_id=fact_id,
        text=text,
        category="affiliation",
        provenance=_provenance(f"doc-{fact_id}", **provenance),
        excluded=excluded,
        exclusion_reason=reason,
    )


def _person(person_id, name, hub_id, label, *, status="resolved", facts=None, hub_facts=None):
    facts = facts if facts is not None else [_fact(f"{person_id}-f1", f"{name} works on {label}.")]
    hub_facts = hub_facts if hub_facts is not None else [facts[0].fact_id]
    return Dossier(
        person=PersonRef(person_id=person_id, name=name, details=[]),
        resolution=Resolution(
            person_id=person_id,
            status=status,
            strong_keys={},
            accepted_doc_ids=[],
            rejected=[],
            confidence=0.9 if status == "resolved" else 0.2,
        ),
        facts=facts,
        hubs=[
            Hub(
                hub_id=hub_id,
                label=label,
                type=hub_id.split(":", 1)[0],
                recency=1.0,
                evidence_fact_ids=hub_facts,
            )
        ],
        built_at=dt.datetime(2026, 1, 2, 3, 4, 5),
    )


def _write(directory, people):
    directory.mkdir(parents=True, exist_ok=True)
    for dossier in people:
        (directory / f"{dossier.person.person_id}.json").write_text(
            dossier.model_dump_json(), encoding="utf-8"
        )
    return directory


def _serve(directory):
    return TestClient(create_app(dossier_dir=directory, llm=_llm()))


# --------------------------------------------------------------------------- the route


def test_the_route_answers_and_names_every_person_in_the_corpus(client):
    """The whole roster, not the room. Nobody has arrived, and everybody is on the page."""
    page = _page(client)
    for person_id, name in NAMES.items():
        assert name in page, f"{name} is in the corpus and must be on the corpus page"
        assert person_id in page, f"{person_id} is in the corpus and must be on the corpus page"


def test_an_arrival_does_not_change_the_corpus_page_by_one_byte(client):
    """The one property that makes this page a different thing from `/graph`.

    `/graph` is R17 and is scoped to presence, so an arrival changes it. This page is a pure
    read of the corpus the app booted with: it takes no input, consults no presence, and must
    answer identically to a full building and an empty one. A single presence-dependent
    number leaking into it would make this fail.
    """
    before = _page(client)
    for person_id in NAMES:
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200
    after = _page(client)
    assert after == before, "the corpus page moved when somebody walked in"


def test_two_apps_over_one_corpus_render_the_identical_page(corpus):
    """Determinism, which a hand-rolled layout has to earn and a seeded force layout fakes.

    Two APPS rather than two requests to one app: a layout that memoised its geometry on the
    first request would pass a two-request check while still being non-deterministic at boot.
    """
    with _serve(corpus) as first, _serve(corpus) as second:
        assert first.get("/corpus").text == second.get("/corpus").text


# --------------------------------------------------------------------------- the drawing


def test_the_drawing_holds_one_spoke_per_solo_hub_and_one_edge_per_shared_carrier(client, truth):
    """Nothing is silently dropped: the counts come off `arrival.graph`, not off the view.

    A solo hub is drawn as one unlabelled spoke on its carrier's burr; a shared hub is drawn
    as a labelled node with one edge per carrier. Those two numbers are properties of the
    graph `build_graph` returns, so the drawing is graded against the graph rather than
    against itself.
    """
    page = _page(client)
    graph = truth["graph"]
    spokes = len(truth["solo"])
    edges = sum(graph.degree(node) for node in truth["shared"])

    assert spokes > 0 and edges > 0, "the fixture corpus is degenerate; this test is vacuous"
    assert page.count('<line class="burr-line"') == spokes
    assert page.count('<line class="corpus-edge"') == edges
    assert page.count('<circle class="person-dot"') == len(truth["people"])
    assert page.count('<circle class="hub-dot"') == len(truth["shared"])


def test_every_hub_label_reaches_the_page_even_the_ones_not_drawn_as_nodes(client, truth):
    """The long tail is summarised, not discarded. All 9 fixture hubs are named somewhere."""
    page = _page(client)
    for node in truth["hubs"]:
        label = truth["graph"].nodes[node]["label"]
        assert label in page, f"{label} is on the graph and is nowhere on the corpus page"


def test_the_weights_on_the_page_are_the_projects_own_arithmetic(client, truth):
    """`idf` and `worth` are read off the graph, never recomputed by the view.

    Graded against `arrival.graph.hub_idf`, which is DESIGN Decision 3's smoothed, clamped
    formula, and against the node's own `type_boost` — so a view that invented a prettier
    number would fail here even though its page still looked plausible.
    """
    page = _page(client)
    graph = truth["graph"]
    checked = 0
    for node in truth["shared"]:
        data = graph.nodes[node]
        expected = hub_idf(graph.graph["n_people"], data["n_carriers"])
        assert expected == pytest.approx(float(data["idf"]))
        if expected <= 0:
            continue  # a hub everybody carries clamps to zero and is reported as such
        checked += 1
        assert f"{expected:.4f}" in page, f"{data['label']}'s idf is not on the page"
        assert f"{expected * float(data['type_boost']):.4f}" in page
    assert checked, "no shared hub in the fixture corpus has a non-zero idf; test is vacuous"


# --------------------------------------------------------------------------- the numbers


def test_the_headline_counts_match_the_corpus_on_disk(client, truth, dossiers):
    """Every tally is counted off the objects the app booted with."""
    page = _page(client)
    tallies = {
        "dossiers": len(dossiers),
        "identified": len(truth["people"]),
        "hubs": len(truth["hubs"]),
        "edges": truth["edges"],
        "shared hubs": len(truth["shared"]),
    }
    for label, count in tallies.items():
        assert f'<span class="tally-n">{count}</span>' in page, (
            f"the page does not report {count} as a tally, and {label} is {count}"
        )
        assert label in page, f"the tally for {label} is unlabelled"
    facts = sum(len(d.facts) for d in dossiers)
    assert f"{facts} researched fact" in page or f'"tally-n">{facts}<' in page, (
        "the total fact count is not on the page"
    )


def test_the_component_arithmetic_on_the_page_is_networkxs_answer(client, truth, tmp_path):
    """Islands and bridges, graded against `nx.connected_components` over `build_graph`.

    This is the test the sabotage rig said was missing. Without it, moving the "a hub is
    shared at two carriers" threshold changed the page's component arithmetic and nothing
    went red — the number was on the page and nothing checked it.
    """
    components = list(nx.connected_components(truth["graph"]))
    page = _page(client)
    assert f"{len(components)} connected component" in page
    assert f"largest holds {max(len(part) for part in components)} of the" in page

    # Two people joined by one hub: exactly one component, and it holds more than one person.
    pair = _write(
        tmp_path / "pair",
        [
            _person("kit", "Kit Known", "company:harbor", "Harbor"),
            _person("lee", "Lee Known", "company:harbor", "Harbor"),
        ],
    )
    with _serve(pair) as client_pair:
        joined = _page(client_pair)
    assert "exactly one of them holds more than one person" in joined
    assert "0 are one person on their own" in joined


def test_a_shared_hub_names_the_other_carrier_in_the_per_person_table(client, truth):
    """The long-tail table has to distinguish a connection from a private fact.

    Graded against the graph's own degree: a hub with two carriers names the other person; a
    hub with one says nobody else does. A threshold that drifted away from `hub_rows`' own
    split shows up here as a shared hub claiming nobody else carries it.
    """
    page = _page(client)
    graph = truth["graph"]
    pairs = [n for n in truth["shared"] if graph.degree(n) == 2]
    assert pairs, "the fixture corpus has no two-carrier hub; this test is vacuous"
    for node in pairs:
        carriers = [
            graph.nodes[p]["person"].name
            for p in graph[node]
            if graph.nodes[p].get("kind") == "person"
        ]
        for name in carriers:
            assert name in page
    assert "nobody else" in page, "a hub carried by one person must say so"


def test_a_person_the_resolver_refused_is_named_and_marked_refused(tmp_path):
    """An unresolved dossier is kept out of the graph population on purpose (store.py).

    They still exist, and a page about the corpus that quietly omitted them would be lying
    about what the corpus holds. They appear, marked, carrying nothing.
    """
    people = [
        _person("kit", "Kit Known", "company:harbor", "Harbor"),
        _person("lee", "Lee Known", "company:harbor", "Harbor"),
        _person("mo", "Mo Maybe", "company:mist", "Mist", status="unresolved"),
    ]
    directory = _write(tmp_path / "refused", people)
    with _serve(directory) as client:
        page = _page(client)
    assert "Mo Maybe" in page
    assert "refused" in page.lower()
    # Their hub was never in the graph population, so it is not on the page either.
    assert "Mist" not in page
    assert "Harbor" in page, "the positive control: a resolved person's hub is shown"


def test_withheld_facts_are_counted_by_category_and_never_quoted(tmp_path):
    """R11 made concrete: the count and the category are the story, the text never is."""
    secret = _fact(
        "hidden",
        "Kit is said to be worth a great deal of money.",
        excluded=True,
        exclusion_reason="wealth",
    )
    shown = _fact("open", "Kit led the Harbor Fund seed round in 2019.")
    people = [
        _person("kit", "Kit Known", "company:harbor", "Harbor", facts=[secret, shown],
                hub_facts=[shown.fact_id]),
        _person("lee", "Lee Known", "company:harbor", "Harbor"),
    ]
    directory = _write(tmp_path / "withheld-count", people)
    with _serve(directory) as client:
        page = _page(client)
    assert "wealth" in page, "the category of a withheld fact is the point of reporting it"
    assert secret.text not in page, "a withheld fact's text reached a host-facing page"
    assert secret.provenance.quote not in page


# --------------------------------------------------------------------------- degenerate states


def test_an_empty_corpus_renders_real_prose_and_no_drawing(tmp_path):
    """A missing corpus is an empty one (store.py), and the page has to say so."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    with _serve(empty) as client:
        response = client.get("/corpus")
    assert response.status_code == 200
    assert "<svg" not in response.text, "there is nothing to draw and nothing was drawn"
    assert "no dossiers" in response.text.lower()


def test_a_corpus_of_one_person_draws_that_person(tmp_path):
    """One dossier is a real answer to 'what does it know', not an error."""
    solo = [_person("kit", "Kit Known", "company:harbor", "Harbor")]
    directory = _write(tmp_path / "alone", solo)
    with _serve(directory) as client:
        page = _page(client)
    assert "Kit Known" in page
    assert "<svg" in page
    assert "One dossier" in page
    assert page.count('<circle class="person-dot"') == 1


def test_a_corpus_where_nobody_shares_anything_says_so_rather_than_looking_broken(tmp_path):
    """The measured shape of a real corpus, in miniature: hubs everywhere, nothing joined."""
    people = [
        _person("kit", "Kit Known", "company:harbor", "Harbor"),
        _person("lee", "Lee Known", "company:mist", "Mist"),
        _person("moe", "Moe Known", "company:vale", "Vale"),
    ]
    directory = _write(tmp_path / "unjoined", people)
    with _serve(directory) as client:
        page = _page(client)
    assert page.count('<line class="corpus-edge"') == 0
    assert page.count('<line class="burr-line"') == 3
    assert "not one hub carried by two of them" in page.lower()
    for name in ("Kit Known", "Lee Known", "Moe Known"):
        assert name in page


def test_a_corpus_nobody_could_be_identified_in_still_renders(tmp_path):
    """Nothing is in the graph, so there are no hubs — and no traceback either."""
    people = [
        _person("kit", "Kit Known", "company:harbor", "Harbor", status="unresolved"),
        _person("lee", "Lee Known", "company:mist", "Mist", status="unresolved"),
    ]
    directory = _write(tmp_path / "nobody", people)
    with _serve(directory) as client:
        response = client.get("/corpus")
    assert response.status_code == 200
    assert "Kit Known" in response.text and "Lee Known" in response.text
    assert response.text.count('<line class="burr-line"') == 0


# --------------------------------------------------------------------------- the skin traps


def test_the_page_loads_no_script_at_all(client):
    """SPEC's non-goals forbid JavaScript, and `test_t8_app.py` asserts it of the app."""
    page = _page(client)
    assert "<script" not in page.lower()
    assert "javascript" not in page.lower()


def test_the_footers_class_reaches_the_page_exactly_once_and_never_from_the_head(client):
    """Trap 1 of `test_t056_skin.py`, applied to a page that module does not yet enumerate."""
    page = _page(client)
    assert page.count("exclusion-policy") == 1
    head = page[: page.index("</head>")]
    assert "exclusion-policy" not in head


def test_the_head_never_spells_an_attribute_selector(client):
    """Trap 2: an `id="..."` in <head> moves every section offset a grader measures."""
    page = _page(client)
    assert 'id="' not in page[: page.index("</head>")]


def test_the_corpus_page_plants_no_r7_section_anchor(client):
    """This is not a digest. A grader sweeping every page must find no R7 section here."""
    page = _page(client)
    for anchor in SECTION_ANCHORS:
        assert anchor not in page, f"{anchor} on a page that is not a digest"


def test_the_drawing_carries_a_text_alternative(client):
    """A picture with no alternative is announced as 'image'. Markup, so it costs nothing."""
    page = _page(client)
    assert 'role="img"' in page
    assert "<desc>" in page
    for name in NAMES.values():
        assert name in page
