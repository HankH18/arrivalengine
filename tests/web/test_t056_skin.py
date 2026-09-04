"""T-056: the three ways a stylesheet edit silently breaks the markup graders.

`base.html` holds the whole design system in one inlined `<style>`, so the next person to
restyle this app edits CSS and runs the suite. Three CSS constructs are individually
reasonable, produce a page that LOOKS identical, and turn other test modules red for
reasons that point nowhere near the stylesheet:

  1. styling R13's footer as `.exclusion-policy` instead of `main > footer`. The class name
     then appears in `<head>` as well as in the markup, and
     `test_t8_render.py::test_an_empty_digest_still_renders_all_six_sections`'s
     `html.count("exclusion-policy") == 1` fails.
  2. writing an attribute selector — `[id="say-out-loud"]`, `section[id="meet"]` — instead
     of a bare id selector. That plants the literal string `id="meet"` in `<head>`, ABOVE
     the markup, and every grader that locates a section with `html.index('id="meet"')`
     (this project's `test_t8_render.py`, and the frozen T-8 suite's section finder) now
     measures the stylesheet's offset instead of the section's. The order assertions fail
     with a message about section order, and the section is exactly where it always was.
  3. loading a webfont, an icon set or a theme toggle with a `<script>`. SPEC's non-goals
     say no JS at all, and `test_t8_app.py` asserts `"<script" not in page.text.lower()`.

Each is pinned here against a page the APP RENDERED, never against `base.html`'s source:
a stylesheet test that greps the stylesheet it is defending grades its own answer key, and
would stay green for a template that emitted nothing at all. The strings compared against
are the ones the OTHER modules hard-code -- `exclusion-policy`, `id="who"` and friends,
`<script`, `action="/arrive"` -- so this module fails for the same reason they would, one
layer earlier and with a message that names the cause.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from arrival.config import get_settings
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-056")

ARRIVING = "charlie"
OTHERS = ("alpha", "bravo", "delta")

OPENER = "Ask about the evaluation harness they open-sourced last spring."

#: The six R7 anchors, spelled exactly as `test_t8_render.py` and the frozen T-8 suite
#: spell them when they call `html.index(...)`. Order is R7's order.
SECTION_ANCHORS = (
    'id="who"',
    'id="meet"',
    'id="lately"',
    'id="not-on-the-first-page"',
    'id="say-out-loud"',
    'id="why-we-know-this"',
)


@pytest.fixture
def corpus(tmp_path, request):
    root = request.config.rootpath / "tests/fixtures/dossiers"
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


@pytest.fixture
def client(monkeypatch, corpus):
    """Every surface the app serves, with `/debug` switched on so it is graded too."""
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _pages(client):
    """(name, html) for one rendered instance of every template in the app.

    A skin lives in `base.html`, so a rule that breaks one page breaks all of them; the
    point of rendering all six is that the assertions below then hold for the whole app
    rather than for whichever page a future reader happened to think of.
    """
    for person_id in OTHERS:
        client.post("/arrive", json={"person_id": person_id})
    arrived = client.post("/arrive", json={"person_id": ARRIVING})
    assert arrived.status_code == 200, arrived.text
    digest_url = arrived.json()["digest_url"]

    pages = {
        "index.html": client.get("/"),
        "building.html": client.get("/building", headers={"accept": "text/html"}),
        "digest.html": client.get(digest_url),
        "debug.html": client.get(f"/debug/{ARRIVING}"),
        "not_found.html": client.get("/digest/no-such-digest"),
        "graph.html": client.get("/graph"),
        "corpus.html": client.get("/corpus"),
    }
    for name, response in pages.items():
        assert "<html" in response.text.lower(), f"{name} did not render a page"
    return {name: response.text for name, response in pages.items()}


def _head(html):
    """Everything before `</head>` -- the stylesheet, the title and the font links."""
    assert "</head>" in html, "the page has no <head> to inspect"
    return html[: html.index("</head>")]


def test_the_stylesheet_never_spells_the_r13_footers_class_name(client):
    """Trap 1. `exclusion-policy` must reach the page exactly once, from the markup.

    Would this still fail if the skin were reverted? Yes -- it fails for ANY stylesheet
    naming that class, which is what it is here to forbid.
    """
    pages = _pages(client)

    for name, html in pages.items():
        assert "exclusion-policy" not in _head(html), (
            f"{name}'s <head> names the R13 footer's class. Style it as `main > footer`: "
            "the class name in <head> is a second occurrence, and test_t8_render.py "
            'asserts html.count("exclusion-policy") == 1.'
        )

    # ...and the one occurrence the markup owes R13 is still there, on both surfaces that
    # carry the footer. This is the other half of the count: zero would also pass the
    # assertion above.
    assert pages["digest.html"].count("exclusion-policy") == 1
    assert pages["index.html"].count("exclusion-policy") == 1


def test_no_attribute_selector_plants_a_section_anchor_in_the_head(client):
    """Trap 2. `id="` may not appear in `<head>` at all.

    The graders locate sections by the offset of a raw substring, so ANY `id="..."` in the
    stylesheet moves a boundary -- including one naming an id that is not an R7 section,
    since the finder matches on substring. Bare id selectors (`#say-out-loud`) do not
    contain the string and are the way to write this.
    """
    for name, html in _pages(client).items():
        head = _head(html)
        assert 'id="' not in head, (
            f"{name}'s <head> contains the literal string 'id=\"'. An attribute selector "
            "there sits ABOVE the markup, so html.index('id=\"meet\"') stops finding the "
            "section. Write the selector as `#meet`."
        )


def test_each_r7_anchor_reaches_the_digest_exactly_once_and_in_order(client):
    """Trap 2, measured the way the graders measure it rather than by inspecting <head>.

    A duplicated anchor string is the failure mode; the count catches it wherever it came
    from, and the ordering is the assertion the other modules actually make.
    """
    html = _pages(client)["digest.html"]

    for anchor in SECTION_ANCHORS:
        assert html.count(anchor) == 1, (
            f"{anchor} appears {html.count(anchor)} times. Every grader that locates this "
            "section takes the FIRST occurrence, so a second one silently reorders R7."
        )

    offsets = [html.index(anchor) for anchor in SECTION_ANCHORS]
    assert offsets == sorted(offsets), (
        "the six R7 sections are no longer in R7's order by first occurrence: "
        + ", ".join(f"{a}@{o}" for a, o in zip(SECTION_ANCHORS, offsets, strict=True))
    )


def test_no_page_loads_any_script(client):
    """Trap 3. SPEC's non-goals: no JS framework, and no JS.

    Webfonts, icon sets and theme toggles all have a script-tag installation path that is
    the first hit in any search; this is the assertion that says to take the `<link>` one.
    """
    for name, html in _pages(client).items():
        assert "<script" not in html.lower(), f"{name} loads a script; SPEC forbids all JS"


def test_the_webfonts_are_optional_because_every_stack_names_a_local_fallback(client):
    """The font call: a `<link>`, and a page that still reads when the CDN is unreachable.

    The app mounts no StaticFiles, so the faces cannot be self-hosted and the only no-JS
    way to fetch them is a stylesheet `<link>` to a third party. A lobby with bad wifi
    therefore gets whatever the fallback stack names -- so no `font-family` may end at its
    webfont, and the generic family at the end is what guarantees a face is chosen.
    """
    head = _head(_pages(client)["digest.html"])

    assert "fonts.googleapis.com" in head, "the font link is gone from <head>"
    assert "<link" in head and "<script" not in head.lower(), (
        "the fonts must arrive through a <link>, never a script"
    )

    # every font stack the skin declares, in `--tokens` and in rules alike
    stacks = [
        stack.strip()
        for stack in re.findall(r"(?:font-family|--serif|--ui)\s*:\s*([^;}]+)", head)
    ]
    assert stacks, "no font stack found in <head>; has the stylesheet moved?"
    for stack in stacks:
        if stack.startswith("var("):
            continue  # an alias for one of the token stacks, checked in its own right
        families = [family.strip().strip("'\"") for family in stack.split(",")]
        assert len(families) > 1, (
            f"the stack {stack!r} names a single face. With fonts.googleapis.com blocked "
            "this text falls back to the browser default, not to the design's serif."
        )
        assert families[-1] in {"serif", "sans-serif", "monospace", "system-ui"}, (
            f"the stack {stack!r} does not end in a generic family, so the browser has no "
            "guaranteed face to fall back to."
        )


def test_a_stylesheet_edit_did_not_disturb_the_demo_drivers_forms(client, corpus):
    """The index is the demo. Its forms are markup, and a skin may not touch markup.

    `test_t8_app.py` counts `action="/arrive"` and `action="/leave"` with the quotes
    included, so a change of quoting style -- the kind a formatter makes -- fails there
    with a message about form counts. Pinned here against the roster's real length.
    """
    index = _pages(client)["index.html"]
    # the answer key is the fixture corpus on disk, which this ticket does not own
    roster_size = len(list(corpus.glob("*.json")))

    assert roster_size >= 4, "the fixture roster shrank; this test is no longer meaningful"
    assert index.count('action="/arrive"') == index.count('action="/leave"')
    assert index.count('action="/arrive"') >= roster_size, (
        "the roster lost Arrive/Leave forms, or their attributes stopped being "
        "double-quoted; both break test_t8_app.py's exact counts"
    )
