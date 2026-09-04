"""TESTBROWSER: the digest's citation machinery and score arithmetic, read off the
RENDERED page rather than off the view model.

The exploratory pass clicked every citation superscript on a live digest in Chrome and
checked, for each, that it landed on the right `<li id="source-N">` and that the quote
sitting there was the one supporting that claim. It also opened every `<details>`
disclosure and re-did the arithmetic in the component table by hand. Nothing had ever
exercised any of that; the checks are reproduced here against the HTML string, because the
HTML string is what a browser gets and a view-model assertion cannot see a template that
prints the wrong variable.

Everything asserted is either a pure arithmetic identity or a structural property of the
page. `digest.py`, `graph.py`, `render.py` and the templates all sit outside this lane's
write scope, so none of these assertions grade a file this lane can change.
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

CITE_RE = re.compile(r'<sup class="cite"><a href="#source-(\d+)">\[(\d+)\]</a></sup>')
SOURCE_LI_RE = re.compile(r'<li id="source-(\d+)">')
EVIDENCE_RE = re.compile(
    r'<li class="evidence-item" id="source-(\d+)-evidence-(\d+)" data-backs="([^"]*)"'
)
COMPONENT_ROW_RE = re.compile(
    r"<tr>\s*<td>(.*?)\s*<code>(.*?)</code></td>"
    r"\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*</tr>",
    re.S,
)

#: R7's six sections, in R7's order.
R7_SECTIONS = (
    "who",
    "meet",
    "lately",
    "not-on-the-first-page",
    "say-out-loud",
    "why-we-know-this",
)


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "dossiers")


@pytest.fixture
def client(corpus, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as test_client:
        yield test_client


def _page(client, *present):
    """Arrive everyone named, and return the LAST arrival's rendered digest."""
    digest_id = None
    for person_id in present:
        response = client.post("/arrive", json={"person_id": person_id})
        assert response.status_code == 200, response.text
        digest_id = response.json()["digest_id"]
    page = client.get(f"/digest/{digest_id}")
    assert page.status_code == 200
    return page.text


ROOMS = [
    pytest.param(("harlow-vane",), id="alone"),
    pytest.param(("indigo-marsh", "harlow-vane"), id="one-shared-hub"),
    pytest.param(("juniper-crane", "indigo-marsh", "harlow-vane"), id="two-shared-hubs"),
    pytest.param(("kestrel-dow", "harlow-vane"), id="nothing-in-common"),
    pytest.param(("harlow-vane", "indigo-marsh", "lumen-tack"), id="unresolved-arrives"),
    pytest.param(
        ("harlow-vane", "indigo-marsh", "juniper-crane", "kestrel-dow", "lumen-tack"),
        id="whole-roster",
    ),
]


# --------------------------------------------------------------------- citation integrity


@pytest.mark.parametrize("room", ROOMS)
def test_every_citation_superscript_has_a_source_to_land_on(client, room):
    page = _page(client, *room)
    targets = {int(n) for n in SOURCE_LI_RE.findall(page)}
    for href_n, label_n in CITE_RE.findall(page):
        assert href_n == label_n, (
            f"citation labelled [{label_n}] links to #source-{href_n}: the number the host "
            "reads is not the number they land on"
        )
        assert int(href_n) in targets, f"citation [{href_n}] has no <li id='source-{href_n}'>"


@pytest.mark.parametrize("room", ROOMS)
def test_source_numbering_is_one_to_n_in_document_order(client, room):
    """`[n]` only means anything if it indexes `sources[n-1]`."""
    page = _page(client, *room)
    order = [int(n) for n in SOURCE_LI_RE.findall(page)]
    assert order == list(range(1, len(order) + 1)), (
        f"the numbered source list is not 1..N in document order: {order}"
    )


@pytest.mark.parametrize("room", ROOMS)
def test_every_dom_id_on_the_page_is_unique(client, room):
    """Two `id="source-3"`s would make one of the two citations unreachable."""
    page = _page(client, *room)
    ids = re.findall(r'\sid="([^"]+)"', page)
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    assert duplicated == [], f"duplicate DOM ids on the digest: {duplicated}"


@pytest.mark.parametrize("room", ROOMS)
def test_evidence_anchors_hang_under_the_source_they_name(client, room):
    page = _page(client, *room)
    sources = {int(n) for n in SOURCE_LI_RE.findall(page)}
    for source_n, _index, backs in EVIDENCE_RE.findall(page):
        assert int(source_n) in sources, (
            f"evidence anchor source-{source_n}-evidence-* names a source that is not listed"
        )
        for section in backs.split():
            assert f'id="{section}"' in page, (
                f"data-backs names section {section!r}, which is not on the page"
            )


@pytest.mark.parametrize("room", ROOMS)
def test_a_quote_is_only_shown_when_it_can_be_traced_to_a_claim(client, room):
    """The alternative -- printing `source.quote` as a consolation -- has published an
    R11-excluded sentence before. A source with no traceable evidence must say so instead."""
    page = _page(client, *room)
    section = re.search(r'<section id="why-we-know-this">(.*?)</section>', page, re.S)
    assert section is not None
    for block in re.split(r'(?=<li id="source-)', section.group(1))[1:]:
        has_evidence = 'class="evidence-item"' in block
        says_untraceable = "could be traced to a claim on this page" in block
        assert has_evidence != says_untraceable, (
            "a source list entry must either carry traced evidence or say it has none, "
            f"never both and never neither: {block[:200]!r}"
        )


# ------------------------------------------------------------------- the score arithmetic


@pytest.mark.parametrize("room", ROOMS)
def test_the_reasoning_table_arithmetic_adds_up(client, room):
    """R10's disclosure prints `weight x recency x type boost = contribution`.

    A table that does not multiply out is worse than no table: it is an audit surface that
    lies. Checked against the printed numbers only, so it holds whatever the weights are.
    """
    page = _page(client, *room)
    rows = COMPONENT_ROW_RE.findall(page)
    for label, hub_type, idf, recency, boost, contribution in rows:
        weight, rec, tb, printed = (
            float(idf),
            float(recency),
            float(boost),
            float(contribution),
        )
        product = weight * rec * tb
        # every printed figure is rounded to 4dp, so the envelope is generous but finite
        assert abs(product - printed) <= 5e-4, (
            f"hub {label.strip()!r} <{hub_type}>: "
            f"{weight} x {rec} x {tb} = {product:.6f}, but the page prints {printed}"
        )


def test_the_arithmetic_check_sees_a_table_at_all(client):
    """A guard on the test above: with no rows parsed it asserts nothing."""
    page = _page(client, "indigo-marsh", "harlow-vane")
    rows = COMPONENT_ROW_RE.findall(page)
    assert rows, "no score-component rows were parsed, so the arithmetic test is vacuous"
    assert 'data-reasoning="score-components"' in page


def test_a_meet_row_with_no_shared_hub_says_so_instead_of_printing_an_empty_table(client):
    page = _page(client, "kestrel-dow", "harlow-vane")
    assert 'data-reasoning="score-components"' in page
    assert "No shared hub at all, so there are no components to show." in page


# ----------------------------------------------------------------------- page structure


@pytest.mark.parametrize("room", ROOMS)
def test_the_six_sections_are_present_and_in_r7_order(client, room):
    page = _page(client, *room)
    positions = []
    for section in R7_SECTIONS:
        index = page.find(f'id="{section}"')
        assert index >= 0, f"missing section id={section}"
        positions.append(index)
    assert positions == sorted(positions), (
        f"sections out of R7 order: {list(zip(R7_SECTIONS, positions, strict=True))}"
    )


@pytest.mark.parametrize("room", ROOMS)
def test_every_empty_section_states_its_absence_rather_than_rendering_blank(client, room):
    """An empty state that renders as nothing reads to a host as a broken page."""
    page = _page(client, *room)
    for section in R7_SECTIONS:
        block = re.search(rf'<section id="{section}">(.*?)</section>', page, re.S).group(1)
        stripped = re.sub(r"<[^>]+>", " ", block)
        assert stripped.split(), f"section {section} rendered with no text at all"


@pytest.mark.parametrize("room", ROOMS)
def test_no_page_ships_javascript(client, room):
    """SPEC's non-goal, and the reason every disclosure is a native <details>."""
    page = _page(client, *room)
    assert "<script" not in page.lower()
    assert "onclick=" not in page.lower()
    assert "onerror=" not in page.lower()


@pytest.mark.parametrize("room", ROOMS)
def test_the_exclusion_policy_appears_exactly_once(client, room):
    page = _page(client, *room)
    assert page.count("exclusion-policy") == 1


@pytest.mark.parametrize("room", ROOMS)
def test_no_template_expression_or_none_leaks_into_the_page(client, room):
    page = _page(client, *room)
    for leak in ("{{", "}}", "{%", ">None<", "None</", "Undefined"):
        assert leak not in page, f"raw template/None leak {leak!r} in the rendered page"


# ------------------------------------------------------------ the spoken lines (R18)


@pytest.mark.parametrize("room", ROOMS)
def test_the_spoken_lines_carry_no_bracket_markers_or_urls(client, room):
    """R18: the who line, every Meet why, and the opener are read aloud. A `[3]` or an
    `https://` inside the sentence is a thing the host would have to say out loud."""
    page = _page(client, *room)
    spoken = re.findall(r'<p class="who-line">(.*?)</p>', page, re.S)
    spoken += re.findall(r'<p class="why">(.*?)</p>', page, re.S)
    spoken += re.findall(r'<p class="opener">(.*?)</p>', page, re.S)
    assert spoken, "no spoken lines were found, so this test is vacuous"
    for line in spoken:
        # citations hang OUTSIDE the sentence, in a <sup> after it
        sentence = re.sub(r"<sup class=\"cite\">.*?</sup>", "", line, flags=re.S)
        sentence = re.sub(r"<[^>]+>", "", sentence).strip()
        assert "http" not in sentence, f"a URL in a spoken line: {sentence!r}"
        assert not re.search(r"\[\d+\]", sentence), f"a [n] marker in a spoken line: {sentence!r}"
