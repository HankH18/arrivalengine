"""TESTBROWSER: accessibility and phone-layout regressions, found by reading Chrome's
accessibility tree and by measuring real 390px / 320px viewports.

Three defects were found by hand and are recorded here as `xfail(strict=True)`. Strict is
the point: when someone fixes one, the test XPASSes, strict turns that into a failure, and
whoever made the fix is told to delete the marker. A defect recorded as a passing
"characterisation" test would instead pin the bug in place forever.

The rest of the module is not xfail -- it pins the accessibility properties that are
already RIGHT (the SVG text alternatives, the landmarks, the table semantics, the native
`<details>` disclosures), because those are the ones a future restyle would quietly break.

Everything is graded against `arrival.web.render`, `arrival.web.graph_view` and the
templates, none of which this lane may write.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from tbrowser_corpus import build_corpus

from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBROWSER")

OPENER = "Ask about the scheduling group they run."

#: The breakpoint `base.html` declares. Everything below is reasoned in rem against it.
PHONE_BREAKPOINT_REM = 40


def _all_css(page: str) -> str:
    """Every `<style>` block on the page, concatenated.

    `base.html` carries the shell stylesheet and `graph.html` / `corpus.html` each add a
    second block of their own, so reading only the first one silently misses the figure
    and drawing rules.
    """
    blocks = re.findall(r"<style>(.*?)</style>", page, re.S)
    assert blocks, "the page carries no stylesheet at all"
    return "\n".join(blocks)


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "dossiers")


@pytest.fixture
def client(corpus, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _arrive(client, *person_ids):
    for person_id in person_ids:
        assert client.post("/arrive", json={"person_id": person_id}).status_code == 200


# ===================================================================== DEFECT 1
# The roster's 20 form controls share two accessible names between them.


def test_every_roster_control_names_the_member_it_acts_on(client):
    """Chrome's accessibility tree for `/` on the live deploy reads, in full:

        button "Arrive"   x10
        button "Leave"    x10

    Twenty controls, two distinct accessible names. The only thing that distinguishes one
    Arrive from another is `<input type="hidden" name="person_id">`, which assistive
    technology never announces -- so a screen-reader user tabbing the roster hears
    "Arrive, button. Leave, button." ten times over with no way to tell whose row they are
    on. WCAG 2.1 SC 4.1.2 (Name, Role, Value): the accessible name must identify the
    control's purpose.

    The fix is one attribute, e.g. `aria-label="Arrive {{ row.person.name }}"`.
    """
    page = client.get("/").text
    names = []
    for form in re.findall(r"<form\b.*?</form>", page, re.S):
        person = re.search(r'name="person_id" value="([^"]*)"', form)
        button = re.search(r"<button\b([^>]*)>(.*?)</button>", form, re.S)
        assert person and button, form
        attrs, text = button.group(1), re.sub(r"<[^>]+>", "", button.group(2)).strip()
        label = re.search(r'aria-label="([^"]*)"', attrs)
        names.append((label.group(1) if label else text, person.group(1)))

    assert len(names) >= 8, "expected a roster with several members"
    distinct = {name for name, _ in names}
    assert len(distinct) == len(names), (
        f"{len(names)} roster controls share only {len(distinct)} accessible names "
        f"({sorted(distinct)}); a screen reader cannot tell them apart"
    )


test_every_roster_control_names_the_member_it_acts_on = pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN DEFECT found by TESTBROWSER: index.html gives all 20 roster buttons the "
        "accessible name 'Arrive' or 'Leave'. Delete this marker when they name the member."
    ),
)(test_every_roster_control_names_the_member_it_acts_on)


# ===================================================================== DEFECT 2
# The SVG's text alternative does not pluralise, while the visible caption does.


def _graph_summary(client):
    page = client.get("/graph").text
    return re.search(r'aria-label="([^"]*)"', page).group(1), page


def test_the_graph_text_alternative_pluralises_like_the_visible_caption(client):
    """`graph.html` prints `shared hub{{ '' if length == 1 else 's' }}` -- correct. The
    `aria-label` and `<title>` come from `graph_view._alt_text`, which hardcodes the plural:

        f"An interest graph of {len(roster)} people joined by {len(shared)} shared hubs."

    So with exactly one shared hub a sighted user reads "1 shared hub" and a screen-reader
    user hears "joined by 1 shared hubs". The accessible text is both ungrammatical and in
    disagreement with the visible text describing the same picture.

    `corpus.html` pluralises correctly in the same position
    (`hub{{ '' if person.n_hubs == 1 else 's' }}`), so this is an oversight in one
    function rather than a house style.
    """
    _arrive(client, "harlow-vane", "indigo-marsh")  # share exactly company:pellmell-works
    summary, page = _graph_summary(client)

    shared_count = int(re.search(r"joined by (\d+) shared hub", summary).group(1))
    assert shared_count == 1, f"fixture no longer yields one shared hub: {summary!r}"
    assert "1 shared hubs" not in summary, f"the SVG text alternative is ungrammatical: {summary!r}"


test_the_graph_text_alternative_pluralises_like_the_visible_caption = pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN DEFECT found by TESTBROWSER: graph_view._alt_text hardcodes 'shared hubs'. "
        "Delete this marker when it pluralises."
    ),
)(test_the_graph_text_alternative_pluralises_like_the_visible_caption)


def test_the_visible_caption_pluralises_correctly_for_one_shared_hub(client):
    """The half that is already right -- kept so a fix to the half that is wrong cannot be
    made by breaking this one to match."""
    _arrive(client, "harlow-vane", "indigo-marsh")
    page = client.get("/graph").text
    lede = re.search(r'<p class="lede">(.*?)</p>', page, re.S).group(1)
    flattened = " ".join(lede.split())
    assert "1 shared hub" in flattened
    assert "1 shared hubs" not in flattened


def test_the_visible_caption_pluralises_correctly_for_two_shared_hubs(client):
    _arrive(client, "harlow-vane", "indigo-marsh", "juniper-crane")
    page = client.get("/graph").text
    lede = re.search(r'<p class="lede">(.*?)</p>', page, re.S).group(1)
    assert "2 shared hubs" in " ".join(lede.split())


# ===================================================================== DEFECT 3
# A long unbreakable token in a <code> blows the page out horizontally on a phone.


def test_a_long_dossier_path_cannot_force_horizontal_scroll_on_a_phone(client):
    """`/` and `/corpus` print `Settings.dossier_dir` inside a bare `<code>`.

    Measured in Chrome, in a real 390px and 320px viewport, with `DOSSIER_DIR` set to the
    absolute path the task packet itself documents: the roster page's document scrollWidth
    was 615px against a 390px client width -- 225px of horizontal page scroll, with the
    member column dragged off screen. The `@media (max-width: 40rem)` block fires correctly
    (body font drops to 18px, h1 to 30px); the breakpoint is not the problem. The problem is
    that `code` inherits `overflow-wrap: normal`, so a path with no spaces is one
    unbreakable token wider than the viewport.

    On the current production deploy the path is `/opt/render/project/src/data/dossiers`,
    which happens to fit at 320px with about 12px to spare -- so this is latent there and
    live for anyone whose DOSSIER_DIR is longer.

    The fix is a wrap rule on `code` (`overflow-wrap: anywhere`), not a shorter path.
    """
    css = _all_css(client.get("/").text)
    code_rules = re.findall(r"(?:^|[,{}\s])(code|pre)\s*\{([^}]*)\}", css, re.S)
    wrapping = [
        body
        for _selector, body in code_rules
        if re.search(r"overflow-wrap\s*:\s*(anywhere|break-word)", body)
        or re.search(r"word-break\s*:\s*break-(all|word)", body)
    ]
    assert wrapping, (
        "no rule lets a long unbreakable token inside <code> wrap, so any DOSSIER_DIR "
        "longer than the viewport forces the whole page to scroll sideways on a phone"
    )


test_a_long_dossier_path_cannot_force_horizontal_scroll_on_a_phone = pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN DEFECT found by TESTBROWSER: base.html gives <code> no overflow-wrap, and "
        "/ and /corpus print an absolute filesystem path in one. Delete this marker when "
        "the rule is added."
    ),
)(test_a_long_dossier_path_cannot_force_horizontal_scroll_on_a_phone)


# ===================================================== what is already right, pinned


def test_the_phone_breakpoint_exists_and_lets_tables_scroll_inside_themselves(client):
    """Measured in Chrome: at 390px the roster table's own scrollWidth equals its client
    width, i.e. it does not force the PAGE to scroll. That is this rule doing its job."""
    css = _all_css(client.get("/").text)
    media = re.search(
        rf"@media\s*\(\s*max-width:\s*{PHONE_BREAKPOINT_REM}rem\s*\)\s*\{{(.*?)\n\}}",
        css,
        re.S,
    )
    assert media is not None, "the phone breakpoint is gone"
    block = media.group(1)
    assert re.search(r"table\s*\{[^}]*overflow-x\s*:\s*auto", block), (
        "tables no longer scroll inside themselves on a phone"
    )
    assert re.search(r"\.roster td:last-child\s*\{[^}]*white-space\s*:\s*normal", block), (
        "the roster action column no longer un-nowraps on a phone, so the two buttons "
        "hold the column open and squeeze the member names"
    )


@pytest.mark.parametrize("route", ["/graph", "/corpus"])
def test_the_svg_figure_scrolls_inside_itself_rather_than_the_page(client, route):
    """Measured in Chrome at 390px: the drawing is ~620-660px wide and the page still
    reports zero horizontal overflow, because `figure` clips it."""
    _arrive(client, "harlow-vane", "indigo-marsh", "juniper-crane")
    css = _all_css(client.get(route).text)
    assert re.search(r"figure[^{}]*\{[^}]*overflow-x\s*:\s*auto", css), (
        f"{route}: the SVG figure has no horizontal scroll container, so a drawing wider "
        "than the viewport drags the whole page sideways"
    )


@pytest.mark.parametrize("route", ["/graph", "/corpus"])
def test_the_drawing_carries_a_usable_text_alternative(client, route):
    """A picture with no text alternative is announced as "image" and nothing else."""
    _arrive(client, "harlow-vane", "indigo-marsh", "juniper-crane")
    page = client.get(route).text
    svg = re.search(r"<svg\b(.*?)>(.*)</svg>", page, re.S)
    assert svg is not None, f"{route} has no <svg>"
    attrs, body = svg.group(1), svg.group(2)

    assert 'role="img"' in attrs, f"{route}: the drawing is not exposed as an image"
    label = re.search(r'aria-label="([^"]+)"', attrs)
    assert label and label.group(1).strip(), f"{route}: no aria-label on the drawing"

    title = re.search(r"<title>(.*?)</title>", body, re.S)
    assert title and title.group(1).strip(), f"{route}: no <title> inside the drawing"
    assert body.lstrip().startswith("<title>"), (
        f"{route}: <title> must be the FIRST child of <svg> or it is not the accessible name"
    )
    desc = re.search(r"<desc>(.*?)</desc>", body, re.S)
    assert desc and desc.group(1).strip(), f"{route}: no <desc> long description"
    # the long description must actually name the people, or it is decoration
    assert "Harlow Vane" in desc.group(1)


@pytest.mark.parametrize("route", ["/", "/building", "/graph", "/corpus"])
def test_every_page_has_one_h1_one_main_and_a_language(client, route):
    _arrive(client, "harlow-vane", "indigo-marsh")
    page = client.get(route).text
    assert page.count("<h1>") == 1, f"{route}: expected exactly one <h1>"
    assert page.count("<main>") == 1, f"{route}: expected exactly one <main>"
    assert page.count("<nav>") == 1, f"{route}: expected exactly one <nav>"
    assert '<html lang="en">' in page, f"{route}: no document language"
    assert '<meta name="viewport"' in page, f"{route}: no viewport meta, so no phone layout"


@pytest.mark.parametrize("route", ["/", "/graph", "/corpus"])
def test_every_table_has_a_caption_and_scoped_headers(client, route):
    _arrive(client, "harlow-vane", "indigo-marsh")
    page = client.get(route).text
    tables = re.findall(r"<table\b.*?</table>", page, re.S)
    assert tables, f"{route}: no tables found, so this test is vacuous"
    for table in tables:
        assert "<caption>" in table, f"{route}: a table with no caption"
        for attrs in re.findall(r"<th\b([^>]*)>", table):
            assert "scope=" in attrs, f"{route}: a <th> with no scope: <th{attrs}>"


def test_the_reasoning_disclosure_is_a_native_details_so_it_works_without_javascript(client):
    """Verified in Chrome: clicking the summary opened the table with no script on the page.
    A div-plus-onclick would be inert here, since the app ships no JavaScript at all."""
    _arrive(client, "harlow-vane", "indigo-marsh")
    response = client.post("/arrive", json={"person_id": "juniper-crane"})
    page = client.get(f"/digest/{response.json()['digest_id']}").text

    disclosures = re.findall(r"<details\b[^>]*>(.*?)</details>", page, re.S)
    assert disclosures, "no disclosures on a digest with a Meet row"
    for block in disclosures:
        assert "<summary>" in block, "a <details> with no <summary> cannot be operated"
    assert "<script" not in page.lower()


def test_the_nav_is_the_same_four_links_on_every_page(client):
    """A host who navigates away from a digest has no other way back."""
    _arrive(client, "harlow-vane")
    expected = ['href="/"', 'href="/building"', 'href="/graph"', 'href="/corpus"']
    for route in ("/", "/building", "/graph", "/corpus"):
        nav = re.search(r"<nav>(.*?)</nav>", client.get(route).text, re.S).group(1)
        for link in expected:
            assert link in nav, f"{route}: nav is missing {link}"
