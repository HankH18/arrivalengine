"""R17: `GET /graph`, the interest graph rendered as a page.

SPEC R17 is one sentence — "the interest graph (people as leaves, entities/interests as
hubs) SHALL be viewable as a simple rendered graph on `/graph` showing present people and
their shared hubs" — so almost every property worth pinning is a judgement `graph_view.py`
made, and each test below says which one it is defending.

**What these grade against.** Never `graph_view.py` or `graph.html`: a test that compares a
page to the module that produced it grades its own answer key. The keys here are
`tests/fixtures/dossiers/` (files this change does not own), `arrival.graph.build_graph`
(which decides what a hub is worth and who carries it), `arrival.taste.is_displayable`
(which decides what may be shown), and literal corpora built in the test itself.

**The one that matters most is the R11/R12 pair.** `arrival.graph` deliberately does not
filter hubs, because matching is not display, so a hub whose evidence was taste-excluded can
legitimately be shared and reach this page — and its evidence must still never be printed
here. `/debug` is the only page allowed to show withheld material.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import re

import pytest
from fastapi.testclient import TestClient

from arrival.contracts import (
    Dossier,
    Fact,
    Hub,
    PersonRef,
    Provenance,
    Resolution,
)
from arrival.graph import build_graph, hub_node, person_node
from arrival.taste import is_displayable
from arrival.web.app import create_app
from arrival.web.graph_view import graph_view
from arrival.web.store import DossierStore
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("R-17")

#: R14-shaped so `digest._validate_opener` takes it and `POST /arrive` completes; the graph
#: page itself makes no LLM call, but arriving is how presence gets set.
OPENER = "Ask about the evaluation harness they open-sourced last spring."

NAMES = {
    "alpha": "Teodoro Vance",
    "bravo": "Nadia Ellingsworth",
    "charlie": "Selin Ardahan",
    "delta": "Hollis Trent",
}


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def fixture_dir(request):
    """The corpus this change does not own, and therefore may be graded against."""
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
def client(corpus):
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _graph_page(client, *person_ids):
    for person_id in person_ids:
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200
    response = client.get("/graph")
    assert response.status_code == 200, response.text[:400]
    return response.text


def _expected_shared_hubs(dossiers, present_ids):
    """The hubs at least two PRESENT people carry, straight out of `build_graph`.

    This is the answer key for "what should be drawn": R17 says "their shared hubs", and
    `arrival.graph` — not this change — decides who carries what.
    """
    graph = build_graph(d for d in dossiers if d.resolution.status == "resolved")
    counted: dict[str, int] = {}
    for person_id in present_ids:
        node = person_node(person_id)
        if node not in graph:
            continue
        for neighbour in graph[node]:
            if graph.nodes[neighbour].get("kind") == "hub":
                counted[neighbour] = counted.get(neighbour, 0) + 1
    return {node for node, n in counted.items() if n >= 2}


# --------------------------------------------------------------------------- the route


def test_the_graph_route_serves_a_page(client):
    """DESIGN's route table: `| GET /graph | — | optional R17 |`."""
    response = client.get("/graph")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()
    assert "<svg" not in response.text.lower(), (
        "nobody has arrived, so there is no graph to draw and none should be drawn"
    )


def test_no_page_this_route_serves_loads_a_script(client):
    """SPEC's non-goals and `test_t8_app.py`: no JS, so the drawing has to be markup.

    Would this still fail if the drawing were reverted to a library? Yes — every
    browser-side graph library arrives through a `<script>`, which is why the page is inline
    SVG in the first place.
    """
    for staged in ([], ["alpha"], ["alpha", "bravo", "charlie", "delta"]):
        page = _graph_page(client, *staged)
        assert "<script" not in page.lower()
        assert "javascript" not in page.lower()


def test_the_page_names_every_present_person_and_nobody_absent(client):
    page = _graph_page(client, "alpha", "bravo")

    for person_id in ("alpha", "bravo"):
        assert NAMES[person_id] in page, f"{person_id} is present and unnamed"
        assert f'data-person="{person_id}"' in page, f"{person_id} is present and undrawn"
    for person_id in ("charlie", "delta"):
        assert NAMES[person_id] not in page, f"{person_id} is not here and is on the page"
        assert f'data-person="{person_id}"' not in page


def test_a_departure_takes_a_person_off_the_graph(client):
    """R5 reaches this page too: presence is the population, so leaving removes a leaf."""
    _graph_page(client, "alpha", "bravo")
    assert client.post("/leave", json={"person_id": "bravo"}).status_code == 200

    page = client.get("/graph").text
    assert NAMES["alpha"] in page
    assert NAMES["bravo"] not in page
    assert 'data-person="bravo"' not in page


# --------------------------------------------------------------------------- the hubs


def test_exactly_the_shared_hubs_are_drawn_and_a_solo_hub_is_not(client, dossiers):
    """R17 scopes itself to "their SHARED hubs"; `build_graph` says which those are.

    `alpha` and `charlie` share Austin and Machine learning; Northgate Labs is alpha's
    alone and Quillmark is charlie's alone. A hub only one present person carries is a fact
    about a person, not a connection, so it is not a node.
    """
    page = _graph_page(client, "alpha", "charlie")
    expected = _expected_shared_hubs(dossiers, ["alpha", "charlie"])

    assert expected, "the fixture corpus no longer has a shared hub; this test is vacuous"
    assert page.count('<g class="node-hub') == len(expected)
    for node in expected:
        hub_id = node.removeprefix("hub:")
        assert f'data-hub="{hub_id}"' in page, f"{hub_id} is shared and is not drawn"

    for solo in ("company:northgate-labs", "company:quillmark"):
        assert hub_node(solo) not in expected, "the fixture corpus changed under this test"
        assert f'data-hub="{solo}"' not in page, (
            f"{solo} is carried by one present person and must not be drawn as a connection"
        )


def test_a_solo_hub_is_still_named_in_prose_rather_than_silently_dropped(client):
    """Not drawing a hub is a choice; hiding that the choice was made is not.

    `Northgate Labs` is alpha's alone while charlie is here, so it is named in the sentence
    under the figure and nowhere in the drawing.
    """
    page = _graph_page(client, "alpha", "charlie")
    assert "Northgate Labs" in page
    assert "Held by one person here" in page


def test_a_shared_hub_appears_once_however_many_people_carry_it(client):
    """One node per hub, not one per carrier — a hub is a hub, and people are its leaves.

    Machine learning is carried by all four fixture people, so the count would grow with the
    room if the page were emitting a node per edge. It is measured at two presence counts so
    the assertion is about the SHAPE rather than about one number.
    """
    two = _graph_page(client, "alpha", "bravo")
    four = _graph_page(client, "charlie", "delta")

    for page, present in ((two, 2), (four, 4)):
        assert page.count('data-person="alpha"') == 1
        # one <g class="node-hub"> in the drawing, one <details> in the list below it
        assert page.count('data-hub="topic:machine-learning"') == 2, (
            f"with {present} people present the hub is emitted more than once"
        )
        assert page.count('<g class="node-hub node-flat" data-hub="topic:machine-learning"') == 1


def test_the_rarest_shared_hub_is_the_biggest_node_and_carries_the_heaviest_edges(
    corpus, dossiers
):
    """S5 in a picture: a rare shared hub must beat a generic one, visibly.

    Graded against `build_graph`'s own numbers — a hub's `idf` and `type_boost` — never
    against the view's. `investor:foundry-seed-2019` is on two of the four fixture people
    while `city:austin` is on all four, so the ordering is the corpus's, not this change's.
    """
    store = DossierStore.load(corpus)
    view = graph_view(store, ["charlie", "delta"])
    graph = store.graph

    hubs = view["figure"]["hubs"]
    assert len(hubs) >= 2, "need two shared hubs for the comparison to mean anything"

    for hub in hubs:
        data = graph.nodes[hub_node(hub["hub_id"])]
        assert hub["worth"] == pytest.approx(float(data["idf"]) * float(data["type_boost"]))

    ranked = sorted(hubs, key=lambda h: -h["worth"])
    assert ranked[0]["hub_id"] == "investor:foundry-seed-2019"
    assert ranked[0]["r"] > ranked[-1]["r"], "the rarest hub is not the largest node"

    by_hub = {hub["hub_id"]: hub for hub in hubs}
    widths = {}
    for edge in view["figure"]["edges"]:
        widths.setdefault(edge["hub_id"], []).append(edge["width"])
    assert min(widths["investor:foundry-seed-2019"]) > max(
        widths[ranked[-1]["hub_id"]]
    ), "the rarest hub's edges are not the heaviest"
    assert by_hub[ranked[-1]["hub_id"]]["worthless"] is True


# --------------------------------------------------------------------------- geometry


@pytest.mark.parametrize(
    "present",
    [
        ["alpha"],
        ["alpha", "bravo"],
        ["charlie", "delta"],
        ["alpha", "bravo", "charlie"],
        ["alpha", "bravo", "charlie", "delta"],
    ],
)
def test_no_two_hub_labels_overlap_and_nothing_leaves_the_canvas(corpus, present):
    """The drawing has to be readable, and no browser is available to measure text.

    Both properties are about the OUTPUT, not about how it was computed: two label boxes
    that intersect are two labels printed on top of each other, and a node outside the
    viewBox is a node nobody sees. A layout change that broke either would fail here whatever
    algorithm replaced it.
    """
    store = DossierStore.load(corpus)
    figure = graph_view(store, present)["figure"]

    assert len(figure["people"]) == len(present)
    if len(present) > 1:
        assert figure["hubs"], "no hub is drawn, so the overlap assertions prove nothing"

    for first, second in itertools.combinations(figure["hubs"], 2):
        a, b = first["box"], second["box"]
        clear = (
            a["x"] + a["w"] <= b["x"]
            or b["x"] + b["w"] <= a["x"]
            or a["y"] + a["h"] <= b["y"]
            or b["y"] + b["h"] <= a["y"]
        )
        assert clear, f"{first['label']!r} and {second['label']!r} are drawn on top of each other"

    for hub in figure["hubs"]:
        box = hub["box"]
        assert 0 <= box["x"] and box["x"] + box["w"] <= figure["width"]
        assert 0 <= box["y"] and box["y"] + box["h"] <= figure["height"]
    for person in figure["people"]:
        assert 0 <= person["x"] <= figure["width"]
        assert 0 <= person["y"] <= figure["height"]


def test_the_same_room_draws_the_same_picture_twice(corpus):
    """No randomness anywhere: two renders of one presence set are byte-identical.

    A force-directed layout seeded from the clock would pass every other test here and give
    a host a different picture on every refresh.
    """
    store = DossierStore.load(corpus)
    first = graph_view(store, ["alpha", "bravo", "charlie", "delta"])["figure"]
    second = graph_view(store, ["alpha", "bravo", "charlie", "delta"])["figure"]
    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(
        second, sort_keys=True, default=str
    )


def test_every_present_person_is_drawn_exactly_once_as_a_leaf(client):
    page = _graph_page(client, "alpha", "bravo", "charlie", "delta")
    assert page.count('<g class="node-person') == 4
    for person_id in NAMES:
        assert page.count(f'data-person="{person_id}"') == 1


# --------------------------------------------------------------- the degenerate states


def test_the_empty_room_renders_prose_and_not_an_empty_container(client):
    """Nobody here is an answer, and it has to read as one.

    An empty `<svg>` or a bare heading looks like a page that failed to load, in front of the
    person this product is being demonstrated to.
    """
    page = client.get("/graph").text
    assert "<svg" not in page.lower()

    # the TEXT inside each absence element, with the markup taken out — an emptied <p> that
    # still carries the class must not read as prose just because the tags around it do.
    body = page[page.index("<article") : page.index("</article>")]
    paragraphs = [
        " ".join(re.sub(r"<[^>]+>", " ", inner).split())
        for inner in re.findall(r'<p class="absent">(.*?)</p>', body, re.DOTALL)
    ]
    assert paragraphs, "the empty state renders no absence element at all"
    for text in paragraphs:
        assert len(text.split()) >= 6, f"the empty state is a stub, not a sentence: {text!r}"
    assert max(len(text.split()) for text in paragraphs) >= 25, (
        "no absence line on the empty page explains what an empty graph means"
    )
    for name in NAMES.values():
        assert name not in page, "nobody is here, so nobody should be named"


def test_one_person_is_drawn_as_one_leaf_with_no_hubs(client):
    """A shared hub takes two people. One person is a real answer, not an error."""
    page = _graph_page(client, "alpha")
    assert "<svg" in page
    assert page.count('<g class="node-person') == 1
    assert page.count('<g class="node-hub') == 0
    assert NAMES["alpha"] in page
    assert 'class="absent"' in page


def test_people_with_nothing_in_common_are_drawn_unjoined(corpus, tmp_path):
    """Two people, no shared hub: leaves and no edges, said plainly.

    Built from a literal corpus rather than the fixtures, because everyone in the fixture
    corpus shares Austin — the state cannot be reached there, and a degenerate state nothing
    can reach is a degenerate state nobody has looked at.
    """
    apart = [
        _person("solo-one", "Ada One", "topic:sailing", "Sailing"),
        _person("solo-two", "Bo Two", "topic:baking", "Baking"),
    ]
    directory = _write(tmp_path / "apart", apart)
    store = DossierStore.load(directory)
    view = graph_view(store, ["solo-one", "solo-two"])

    assert view["state"] == "unconnected"
    assert view["shared_hubs"] == []
    assert view["figure"]["edges"] == []
    assert len(view["figure"]["people"]) == 2
    # nobody is the odd one out when nobody is joined to anything
    assert [person["loose"] for person in view["figure"]["people"]] == [False, False]


def test_a_present_person_the_graph_cannot_place_is_still_shown_and_labelled(tmp_path):
    """An UNRESOLVED dossier is kept out of the graph population by `DossierStore` on
    purpose, so that person carries no hubs. R17 says to show present people, so they are
    drawn — as a leaf with no edges — and the page says why rather than leaving a mystery dot.
    """
    people = [
        _person("known-one", "Kit Known", "investor:harbor-fund", "Harbor Fund"),
        _person("known-two", "Lee Known", "investor:harbor-fund", "Harbor Fund"),
        _person("hazy-one", "Moss Hazy", "topic:sailing", "Sailing", status="unresolved"),
    ]
    store = DossierStore.load(_write(tmp_path / "hazy", people))
    view = graph_view(store, ["known-one", "known-two", "hazy-one"])

    drawn = {person["person_id"]: person for person in view["figure"]["people"]}
    assert set(drawn) == {"known-one", "known-two", "hazy-one"}
    assert drawn["hazy-one"]["in_graph"] is False
    assert drawn["hazy-one"]["loose"] is True
    assert drawn["known-one"]["loose"] is False
    assert [row["person_id"] for row in view["unlinked"]] == ["hazy-one"]
    assert not any(
        "hazy-one" in hub["carriers"] for hub in view["shared_hubs"]
    ), "an unresolved person cannot share a hub; they are not in the graph"


# ------------------------------------------------------------------------ R11 and R12


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
    hub_type = hub_id.split(":", 1)[0]
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
                type=hub_type,
                recency=1.0,
                evidence_fact_ids=hub_facts,
            )
        ],
        built_at=dt.datetime(2026, 1, 2, 3, 4, 5),
    )


def _write(directory, dossiers):
    directory.mkdir(parents=True, exist_ok=True)
    for dossier in dossiers:
        (directory / f"{dossier.person.person_id}.json").write_text(
            dossier.model_dump_json(), encoding="utf-8"
        )
    return directory


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("taste-excluded", {"excluded": True, "exclusion_reason": "family"}),
        ("below the confidence floor", {"confidence": 0.4}),
        ("a never-displayable source kind", {"source_kind": "fec"}),
    ],
)
def test_a_withheld_fact_behind_a_shared_hub_never_reaches_the_page(tmp_path, label, kwargs):
    """R11 / R12 on the one page that could leak them, all three independent clauses.

    `arrival.graph` does not filter hubs — matching is not display — so the hub itself is
    legitimately shared and legitimately drawn. Its EVIDENCE is a different question, and
    `taste.is_displayable` is the only answer to it.

    The positive control is inside the test: the OTHER carrier's fact, which passes every
    clause, must appear. Without it a page that rendered no evidence at all — or no page at
    all — would satisfy every "not in" assertion here.
    """
    withheld = _fact(
        "hidden", "Kit and their spouse keep a workshop on Mockingbird Terrace.", **kwargs
    )
    shown = _fact("open", "Lee led the Harbor Fund seed round in 2019.")
    people = [
        _person("kit", "Kit Known", "investor:harbor-fund", "Harbor Fund", facts=[withheld]),
        _person("lee", "Lee Known", "investor:harbor-fund", "Harbor Fund", facts=[shown]),
    ]
    directory = _write(tmp_path / f"withheld-{len(label)}", people)

    assert not is_displayable(withheld), f"the {label} fixture is displayable; test is vacuous"
    assert is_displayable(shown), "the positive control is not displayable; test is vacuous"

    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=directory, llm=llm)) as client:
        page = _graph_page(client, "kit", "lee")

    assert "Harbor Fund" in page, "the hub is shared and must still be drawn"
    assert shown.text in page, "the positive control: displayable evidence is shown"
    assert withheld.text not in page, f"a {label} fact reached a host-facing page"
    assert withheld.provenance.quote not in page
    assert "Nothing behind this we are willing to show." in page


def test_no_withheld_fact_from_the_committed_fixtures_reaches_the_page(client, dossiers):
    """The same rule, swept over every withheld fact in the corpus rather than a built one.

    Graded against `taste.is_displayable` and the fixture files — neither of which this
    change owns — so it keeps holding as the corpus grows.
    """
    page = _graph_page(client, "alpha", "bravo", "charlie", "delta")

    withheld = [
        fact for dossier in dossiers for fact in dossier.facts if not is_displayable(fact)
    ]
    assert withheld, "the fixture corpus has no withheld fact; this test is vacuous"
    shown = [fact for dossier in dossiers for fact in dossier.facts if is_displayable(fact)]
    assert any(fact.text in page for fact in shown), (
        "no displayable fact reached the page either, so the assertions below prove nothing"
    )

    for fact in withheld:
        assert fact.text not in page, f"{fact.fact_id} is withheld and is on the page"
        assert fact.provenance.quote not in page, f"{fact.fact_id}'s quote is on the page"


def test_the_page_states_what_it_never_shows(client):
    """R13's paragraph, on a surface that prints researched material."""
    page = _graph_page(client, "alpha", "bravo")
    assert page.count("exclusion-policy") == 1
    assert "exclusion-policy" not in page[: page.index("</head>")]


# ------------------------------------------------------------------- markup discipline


def test_the_graph_page_plants_no_r7_section_anchor(client):
    """This page is not a digest, so it must not answer to a digest's section finder.

    The frozen T-8 finder locates an R7 section by a container's `id`/`class`/`aria-label`/
    `data-*` or a heading's text. A page that named one would be a second, ungraded surface
    claiming to be a digest.
    """
    page = _graph_page(client, "alpha", "bravo", "charlie", "delta")
    runs = (
        "who",
        "meet",
        "lately",
        "not-on-the-first-page",
        "say-out-loud",
        "why-we-know-this",
    )
    for run in runs:
        for attribute in ("id", "class", "aria-label"):
            assert f'{attribute}="{run}"' not in page
            assert f'{attribute}="{run} ' not in page
