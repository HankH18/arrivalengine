"""WEBPOLISH: what a BROWSER gets back, as opposed to what an integration gets back.

Four defects were found by driving the deployed product in Chrome (T-093..T-096). Three of
them are pinned by `test_tbrowser_accessibility_and_layout.py`, whose `xfail(strict=True)`
markers this lane deleted on fixing them. This module pins the fourth (T-096) and the parts
of the other three that the TESTBROWSER module does not reach -- plus the two small polish
decisions taken alongside them, so that reverting either is a red test rather than a quiet
regression.

The seam every test here uses is the one `app.py` already had and the 404 path did not use:
`_is_form_post` says the caller is a browser submitting a form, `_wants_json` lets one opt
back out. The contract being defended is that STATUS is about the world (the person is not
on the roster: 404, always) while REPRESENTATION follows the caller (a person gets a page,
an integration gets JSON).
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from tbrowser_corpus import build_corpus

from arrival.web.app import create_app
from arrival.web.graph_view import _count
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("WEBPOLISH")

OPENER = "Ask about the scheduling group they run."

#: A name no dossier holds. Shaped like a typo a host would actually make at a demo.
OFF_ROSTER = "harlwo-vane"

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
BROWSER_ACCEPT = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "dossiers")


@pytest.fixture
def client(corpus, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


# ===================================================================== T-096
# A typo in a demo showed a JSON blob where every other form path shows a page.


@pytest.mark.parametrize("route", ["/arrive", "/leave"])
def test_a_form_post_of_an_off_roster_name_lands_on_a_page_not_a_json_blob(client, route):
    """Measured on the live deploy before the fix, with a real browser Accept header:

        HTTP/2 404
        content-type: application/json

        {"error":"not on roster"}

    Every other form path on the app 303s to a page. This one dropped the host onto a bare
    blob with the styling, the navigation and the way back all gone -- on the one page the
    demo asks a stranger to type into.
    """
    response = client.post(route, data={"person_id": OFF_ROSTER}, headers=BROWSER_ACCEPT)

    assert response.status_code == 404, "the person really is not on the roster"
    assert response.headers["content-type"].startswith("text/html"), (
        f"{route}: a browser form post was answered with "
        f"{response.headers['content-type']!r}: {response.text[:200]}"
    )
    page = response.text
    assert "<h1>Not found</h1>" in page, page[:400]
    assert "{" not in page.split("<body>")[-1].split("<h1>")[0], "a raw JSON blob leaked"


@pytest.mark.parametrize("route", ["/arrive", "/leave"])
def test_the_off_roster_page_is_the_styled_shell_with_a_way_back(client, route):
    """A 404 a host cannot navigate out of is a dead end. `not_found.html` extends
    `base.html`, so it inherits the nav -- this asserts it actually arrives that way."""
    page = client.post(route, data={"person_id": OFF_ROSTER}, headers=BROWSER_ACCEPT).text

    nav = re.search(r"<nav>(.*?)</nav>", page, re.S)
    assert nav is not None, "the off-roster page has no nav, so there is no way back"
    for link in ('href="/"', 'href="/building"', 'href="/graph"', 'href="/corpus"'):
        assert link in nav.group(1), f"{route}: the off-roster page's nav is missing {link}"
    assert '<html lang="en">' in page
    assert "<style>" in page, "the off-roster page arrived unstyled"


@pytest.mark.parametrize("route", ["/arrive", "/leave"])
def test_the_off_roster_page_echoes_the_token_so_the_typo_is_visible(client, route):
    """A typo you cannot see is a typo you cannot fix."""
    page = client.post(route, data={"person_id": OFF_ROSTER}, headers=BROWSER_ACCEPT).text
    assert OFF_ROSTER in page, f"{route}: the page does not say what was not found"


@pytest.mark.parametrize("route", ["/arrive", "/leave"])
def test_a_json_caller_still_gets_designs_body_unchanged(client, route):
    """R4 and DESIGN's route table pin `404 {"error": "not on roster"}`. The fix above
    changes the representation for browsers ONLY; an integration must see no difference."""
    response = client.post(route, json={"person_id": OFF_ROSTER})
    assert response.status_code == 404
    assert response.json() == {"error": "not on roster"}
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("route", ["/arrive", "/leave"])
def test_a_form_post_that_explicitly_asks_for_json_gets_json(client, route):
    """`_wants_json` is the opt-out, and it must still work from a form-encoded body --
    otherwise a scripted client that posts urlencoded is forced onto an HTML page."""
    response = client.post(
        route,
        data={"person_id": OFF_ROSTER},
        headers={**FORM, "Accept": "application/json"},
    )
    assert response.status_code == 404
    assert response.json() == {"error": "not on roster"}


def test_a_form_post_with_no_usable_body_is_a_page_and_not_a_stack_trace(client):
    """The empty-token branch: there is no name to echo, so the sentence must still read."""
    response = client.post("/arrive", data={"person_id": ""}, headers=BROWSER_ACCEPT)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "roster member by that name" in response.text


def test_an_enormous_token_is_not_echoed_wholesale_into_the_page(client):
    """The body is caller-supplied and unbounded. The page is not the place to render it."""
    huge = "x" * 5000
    page = client.post("/arrive", data={"person_id": huge}, headers=BROWSER_ACCEPT).text
    assert huge not in page, "the whole caller-supplied token was rendered into the page"
    assert "x" * 40 in page, "the token was truncated so hard the host learns nothing"


def test_the_form_post_404_still_makes_no_llm_call(corpus, monkeypatch):
    """R4's real content is 'no live research', and it must survive the new branch: the
    refusal has to happen before any work, not after rendering something."""
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as client:
        response = client.post(
            "/arrive", data={"person_id": OFF_ROSTER}, headers=BROWSER_ACCEPT
        )
        assert response.status_code == 404
        assert llm.calls == [], f"an off-roster form post made {len(llm.calls)} LLM call(s)"

        # Positive control: the injected client IS the one this app uses, so the empty call
        # list above is restraint rather than a dead seam.
        assert client.post("/arrive", json={"person_id": "harlow-vane"}).status_code == 200
        assert llm.calls, "the injected LLM was never reached even on the happy path"


def test_a_successful_form_post_still_redirects_to_a_page(client):
    """The half that was already right, pinned so the fix cannot be made by breaking it."""
    arrived = client.post(
        "/arrive", data={"person_id": "harlow-vane"}, headers=FORM, follow_redirects=False
    )
    assert arrived.status_code == 303
    assert arrived.headers["location"].startswith("/digest/")


# ===================================================================== T-093
# The parts of the roster's accessible naming the TESTBROWSER module does not reach.


def _roster_buttons(client):
    page = client.get("/").text
    out = []
    for form in re.findall(r"<form\b([^>]*)>(.*?)</form>", page, re.S):
        attrs, body = form
        action = re.search(r'action="([^"]*)"', attrs)
        person = re.search(r'name="person_id" value="([^"]*)"', body)
        button = re.search(r"<button\b([^>]*)>(.*?)</button>", body, re.S)
        assert action and person and button, form
        label = re.search(r'aria-label="([^"]*)"', button.group(1))
        out.append(
            {
                "action": action.group(1),
                "person_id": person.group(1),
                "label": label.group(1) if label else None,
                "text": re.sub(r"<[^>]+>", "", button.group(2)).strip(),
            }
        )
    return out


def test_every_roster_buttons_accessible_name_starts_with_its_visible_text(client):
    """WCAG 2.1 SC 2.5.3, Label in Name. An `aria-label` REPLACES the visible text as the
    accessible name, so a voice-control user saying "click Arrive" stops matching unless the
    label still begins with the word on the button. "Arrive Harlow Vane" does; "Harlow Vane,
    arrive" would not, and is the shape this test exists to reject."""
    buttons = _roster_buttons(client)
    assert buttons, "no roster forms found, so this test is vacuous"
    for button in buttons:
        assert button["label"], f"{button['person_id']}: no aria-label on the button"
        assert button["label"].startswith(button["text"]), (
            f"accessible name {button['label']!r} does not start with the visible text "
            f"{button['text']!r}; a voice-control user cannot address this control"
        )


def test_each_buttons_accessible_name_names_the_member_that_buttons_form_acts_on(client):
    """Distinctness is not enough on its own: twenty distinct labels that are wired to the
    wrong hidden inputs would satisfy the TESTBROWSER test and mislead every user of them.
    This pins label AGAINST the person_id the same form posts."""
    page = client.get("/").text
    # The visible name in each row, taken from the row's own <strong>, is the answer key --
    # it is what a sighted user reads next to the button.
    rows = re.findall(r"<tr>(.*?)</tr>", page, re.S)
    checked = 0
    for row in rows:
        strong = re.search(r"<strong>(.*?)</strong>", row, re.S)
        if strong is None:
            continue
        visible_name = strong.group(1).strip()
        for verb in ("Arrive", "Leave"):
            label = re.search(rf'aria-label="({verb} [^"]*)"', row)
            assert label, f"row for {visible_name!r} has no {verb} aria-label"
            assert label.group(1) == f"{verb} {visible_name}", (
                f"button labelled {label.group(1)!r} sits in the row for {visible_name!r}"
            )
            checked += 1
    assert checked >= 8, f"only checked {checked} controls; the roster should have more"


def test_the_aria_labels_did_not_disturb_the_form_structure_the_graders_count(client):
    """`test_t8_app.py` counts `action="/arrive"` and `action="/leave"` occurrences, and the
    frozen T-8 suite parses these forms and submits them. Adding an attribute to the BUTTON
    must not add, remove or rename a form, an action or a field."""
    page = client.get("/").text
    roster_size = len(_roster_buttons(client)) // 2
    assert page.count('action="/arrive"') == roster_size
    assert page.count('action="/leave"') == roster_size
    assert page.count('name="person_id"') == roster_size * 2
    assert "<script" not in page.lower(), "SPEC non-goals: no JS at all"


# ===================================================================== T-094
# The pluraliser itself, and the agreement between spoken and visible text.


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0 shared hubs"), (1, "1 shared hub"), (2, "2 shared hubs"), (11, "11 shared hubs")],
)
def test_count_pluralises_a_regular_noun(n, expected):
    assert _count(n, "shared hub") == expected


@pytest.mark.parametrize(
    ("n", "expected"), [(0, "0 people"), (1, "1 person"), (2, "2 people"), (10, "10 people")]
)
def test_count_takes_an_irregular_plural(n, expected):
    assert _count(n, "person", "people") == expected


def _alt_and_caption(client):
    page = client.get("/graph").text
    svg = re.search(r'<svg\b[^>]*aria-label="([^"]*)"', page)
    title = re.search(r"<svg\b.*?<title>(.*?)</title>", page, re.S)
    lede = re.search(r'<p class="lede">(.*?)</p>', page, re.S)
    assert svg and title and lede, "the graph page changed shape"
    return svg.group(1), " ".join(title.group(1).split()), " ".join(lede.group(1).split())


@pytest.mark.parametrize(
    ("arrivals", "expected_shared"),
    [
        (("harlow-vane", "indigo-marsh"), "1 shared hub"),
        (("harlow-vane", "indigo-marsh", "juniper-crane"), "2 shared hubs"),
    ],
)
def test_the_spoken_and_the_visible_hub_count_agree(client, arrivals, expected_shared):
    """The defect was an accessible name that disagreed with the caption describing the same
    picture. Both directions are pinned: the right string present, the wrong one absent."""
    for person_id in arrivals:
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200

    alt, title, caption = _alt_and_caption(client)
    assert expected_shared in caption, f"the visible caption changed: {caption!r}"
    for spoken in (alt, title):
        assert expected_shared in spoken, f"text alternative disagrees with caption: {spoken!r}"
    if expected_shared == "1 shared hub":
        for spoken in (alt, title, caption):
            assert "1 shared hubs" not in spoken, f"ungrammatical: {spoken!r}"


def test_the_aria_label_and_the_svg_title_are_the_same_sentence(client):
    """They are the same string in the source and must stay so: a screen reader announces
    one of them and which one depends on the AT, so a divergence is a coin flip."""
    for person_id in ("harlow-vane", "indigo-marsh"):
        client.post("/arrive", json={"person_id": person_id})
    alt, title, _ = _alt_and_caption(client)
    assert alt == title, f"aria-label {alt!r} != <title> {title!r}"


def test_the_person_count_in_the_text_alternative_also_agrees(client):
    for person_id in ("harlow-vane", "indigo-marsh"):
        client.post("/arrive", json={"person_id": person_id})
    alt, _, _ = _alt_and_caption(client)
    assert "2 people" in alt, alt
    assert "2 person" not in alt


# ===================================================================== polish
# The two small decisions taken alongside the four defects.


def test_robots_txt_disallows_crawlers_rather_than_404ing(client):
    """A missing robots.txt is an affirmative "crawl everything" to a crawler, and every
    host-facing page here renders researched material about named real people on a public,
    unauthenticated URL. Reverting this is deleting the handler, and this test."""
    response = client.get("/robots.txt")
    assert response.status_code == 200, "a 404 here reads as 'no restrictions'"
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert re.search(r"(?im)^user-agent:\s*\*\s*$", body), body
    assert re.search(r"(?im)^disallow:\s*/\s*$", body), body


@pytest.mark.parametrize("route", ["/", "/building", "/graph", "/corpus"])
def test_every_page_declares_an_inline_favicon_so_no_request_404s(client, route):
    """Without a rel="icon" every browser on every page requests /favicon.ico, which this
    app does not serve -- a 404 in the network panel of the demo's own front door. A data:
    URI needs no route and no round trip."""
    page = client.get(route).text
    head = page.split("</head>")[0]
    icon = re.search(r'<link\b[^>]*rel="icon"[^>]*href="([^"]*)"', head)
    assert icon is not None, f"{route}: no <link rel=\"icon\"> in <head>"
    assert icon.group(1).startswith("data:image/svg+xml,"), (
        "the icon must be inline; a fetched one is the request this exists to remove"
    )


@pytest.mark.parametrize("route", ["/", "/graph", "/corpus"])
def test_the_favicon_plants_no_string_the_markup_graders_search_for(client, route):
    """`test_t056_skin.py` documents the trap: anything in <head> sits ABOVE the markup, and
    this project's graders locate things by substring offset. A literal `<svg` there would
    be found by `re.search(r"<svg\\b(.*?)>", page)` in the TESTBROWSER a11y module instead of
    the drawing on /graph, and an `id="` would move every R7 section anchor. The data URI is
    percent-encoded precisely so neither string exists."""
    head = client.get(route).text.split("</head>")[0]
    assert "<svg" not in head, "a literal <svg in <head> shadows the page's real drawing"
    assert 'id="' not in head, "see test_t056_skin.py: this moves every section anchor"
    assert "<script" not in head.lower()


def test_the_head_svg_shadowing_trap_is_real_and_this_module_would_catch_it(client):
    """A control for the test above: prove that the regex it defends really does take the
    FIRST <svg> on the page, so 'no <svg in head' is load-bearing rather than decorative."""
    for person_id in ("harlow-vane", "indigo-marsh"):
        client.post("/arrive", json={"person_id": person_id})
    page = client.get("/graph").text
    assert re.search(r"<svg\b(.*?)>", page, re.S), "no <svg> on /graph at all"
    poisoned = page.replace("<head>", "<head><svg id='x'></svg>", 1)
    first = re.search(r"<svg\b(.*?)>", poisoned, re.S)
    assert "id='x'" in first.group(1), (
        "the shadowing this defends against did not reproduce; the guard above may be "
        "measuring nothing"
    )
