"""T-057: `/debug`'s rejected table shows the attribute the RESOLVER counted.

`debug.html` used to render `verdict.disambiguator` raw — the model's own free-text word,
the one thing `resolve` spent two tickets refusing to trust. `resolve.verdict_attribute`
asks the EVIDENCE SPAN which of the person's own details it actually names and consults the
label only when the span names none; that is the value an operator debugging a resolution
needs, and it had no product caller at all while the one surface that exists to explain a
verdict showed the word the resolver declined to count.

The label is still on the page, beside the resolved attribute rather than in place of it.
That is the design call this module pins: on the page whose whole purpose is showing the
system's reasoning, the two DISAGREEING is the reasoning, and it is unshowable if only one
of them is rendered.

**What every assertion here grades against, none of it writable by this ticket:**

* `arrival.resolve.verdict_attribute` / `verdict_attributes` — the answer key for every
  expected attribute value. This module never writes down what the page "should" say; it
  asks the resolver and requires the page to agree.
* the orchestrator-owned frozen corpus at `.swarm-loop/acceptance/fixtures/dossiers*/`, and
  `tests/fixtures/dossiers/`, which this ticket does not own either.
* `arrival.contracts` for the shape of a `Verdict` and a `Dossier`.

No assertion compares against `render.py` or `debug.html`, the two files this ticket owns.
Their markup appears only as a LOCATOR (find the rejected table, find a row by its doc id);
every value checked inside it comes from one of the sources above.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arrival.config import get_settings
from arrival.contracts import Dossier, PersonRef, Resolution, Verdict
from arrival.resolve import verdict_attribute, verdict_attributes
from arrival.web.app import create_app
from arrival.web.render import debug_view, render
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-8")

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / ".swarm-loop" / "acceptance" / "fixtures"
PROJECT_DOSSIERS = REPO / "tests" / "fixtures" / "dossiers"

#: A person whose two identifying details are exactly the two `_CORROBORABLE` families, so a
#: constructed span can name one, the other, or both. The name and the employer are invented
#: here rather than taken from a fixture: these verdicts exercise a disagreement no committed
#: corpus happens to contain, and constructing it is the only way to SEE the fix work.
PERSON = PersonRef(
    person_id="dana-whitfield",
    name="Dana Whitfield",
    details=["engineer, Harrowgate Systems", "Austin"],
)

#: The label says `handle` and the span quotes the EMPLOYER. `resolve.verdict_attributes`'
#: own docstring names this shape as the attack it closed: the label is free to the model,
#: the span is checked against the document, so the span wins.
LABEL_OVERRULED = Verdict(
    doc_id="aaaaaaaaaaaaaaaa",
    match="no",
    confidence=0.91,
    evidence="github.com/dwhitfield - Harrowgate Systems",
    disambiguator="handle",
)

#: One span naming BOTH details. `verdict_attribute` can only report one; `verdict_attributes`
#: keeps both, and two distinct attributes is the number Decision 4's second arm counts.
TWO_ATTRIBUTES = Verdict(
    doc_id="bbbbbbbbbbbbbbbb",
    match="unsure",
    confidence=0.40,
    evidence="Dana Whitfield, Harrowgate Systems, Austin",
    disambiguator="company",
)

#: The model named no attribute at all, and the span corroborates nothing.
NOTHING_NAMED = Verdict(
    doc_id="cccccccccccccccc",
    match="unsure",
    confidence=0.10,
    evidence="A different Dana entirely, with no company and no city.",
    disambiguator="",
)


def _dossier(*rejected: Verdict) -> Dossier:
    return Dossier(
        person=PERSON,
        resolution=Resolution(
            person_id=PERSON.person_id,
            status="unresolved",
            strong_keys={},
            accepted_doc_ids=[],
            rejected=list(rejected),
            confidence=0.0,
        ),
        facts=[],
        hubs=[],
        built_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


def _rejected_section(html: str) -> str:
    """The rejected-candidates block of a rendered `/debug` page.

    Located by the heading text, which predates this ticket, so a row's contents can be
    checked without the assertion itself depending on the markup around it.
    """
    start = html.index("Rejected candidate documents")
    rest = html[start:]
    end = rest.find("<h2>")
    return rest[:end] if end >= 0 else rest


def _row_for(html: str, doc_id: str) -> str:
    """The one `<tr>` of the rejected table that reports `doc_id`."""
    rows = [row for row in re.split(r"<tr>", _rejected_section(html)) if doc_id in row]
    assert len(rows) == 1, f"expected exactly one rejected row for {doc_id}, found {len(rows)}"
    return rows[0]


def _debug_html(dossier: Dossier) -> str:
    return render("debug.html", **debug_view(dossier))


# --------------------------------------------------------------------------- the fix itself


def test_the_cell_reports_the_resolved_attribute_not_the_models_word():
    """The span quotes the employer while the label says `handle`; the page must say employer."""
    resolved = verdict_attribute(PERSON, LABEL_OVERRULED)
    assert resolved == "employer", (
        "the answer key moved: resolve.verdict_attribute no longer reads the employer out "
        f"of a span that quotes it, returning {resolved!r}"
    )
    assert resolved != LABEL_OVERRULED.disambiguator, (
        "this case exists to make the label and the evidence DISAGREE; if they agree the "
        "assertions below cannot distinguish the fix from the defect"
    )

    row = _row_for(_debug_html(_dossier(LABEL_OVERRULED)), LABEL_OVERRULED.doc_id)
    assert f"<strong>{resolved}</strong>" in row, (
        "/debug still does not report the attribute the resolver counted; the operator view "
        f"shows {LABEL_OVERRULED.disambiguator!r} where the evidence bore out {resolved!r}"
    )


def test_the_models_label_survives_beside_it_and_is_marked_as_overruled():
    """Both values, because their disagreement IS what this page exists to show."""
    row = _row_for(_debug_html(_dossier(LABEL_OVERRULED)), LABEL_OVERRULED.doc_id)
    assert LABEL_OVERRULED.disambiguator in row, (
        "the raw label was dropped; an operator can no longer see that the model's word was "
        "refused, nor that the prompt produced that word at all"
    )
    assert "model said" in row, "the raw label is on the page but not attributed to the model"
    assert "not taken" in row, (
        "the label and the resolved attribute disagree and the page does not say so, so the "
        "one interesting row on this table reads like every other row"
    )


def test_a_span_naming_two_details_keeps_both_because_that_is_what_resolve_counts():
    attributes = verdict_attributes(PERSON, TWO_ATTRIBUTES)
    assert attributes == frozenset({"employer", "city"}), (
        f"the answer key moved: a span naming both details corroborates {sorted(attributes)}"
    )
    row = _row_for(_debug_html(_dossier(TWO_ATTRIBUTES)), TWO_ATTRIBUTES.doc_id)
    for attribute in sorted(attributes):
        assert attribute in row, (
            f"{attribute!r} is corroborated by this span and `resolve` counts it toward "
            "Decision 4's second arm, but the operator view does not show it"
        )


def test_a_verdict_that_corroborates_nothing_says_so_rather_than_inventing_an_attribute():
    assert verdict_attribute(PERSON, NOTHING_NAMED) == "", (
        "the answer key moved: a verdict with no label and an uncorroborating span now "
        "names an attribute"
    )
    row = _row_for(_debug_html(_dossier(NOTHING_NAMED)), NOTHING_NAMED.doc_id)
    assert "<strong>" not in row, (
        "the page asserts a decided-by attribute for a verdict whose span corroborates "
        "nothing and whose label is empty — R2 says that is exactly when to refuse"
    )
    assert "absent" in row, "the empty case is rendered as blank rather than as an absence"


def test_all_three_rows_render_together_without_bleeding_into_each_other():
    html = _debug_html(_dossier(LABEL_OVERRULED, TWO_ATTRIBUTES, NOTHING_NAMED))
    assert "not taken" in _row_for(html, LABEL_OVERRULED.doc_id)
    assert "not taken" not in _row_for(html, TWO_ATTRIBUTES.doc_id), (
        "`company` canonicalises to `employer`, which is what the span corroborates, so this "
        "row is an agreement and must not be flagged as a refusal"
    )


# ------------------------------------------------------- every committed corpus, swept


def _every_dossier() -> list[tuple[str, Dossier]]:
    roots = (
        FROZEN / "dossiers",
        FROZEN / "dossiers_unresolved",
        PROJECT_DOSSIERS,
    )
    found = []
    for root in roots:
        for path in sorted(root.glob("*.json")):
            dossier = Dossier.model_validate_json(path.read_text())
            found.append((f"{root.name}/{path.name}", dossier))
    assert found, "no committed dossiers were found; this sweep would prove nothing"
    return found


def test_every_committed_rejected_verdict_renders_the_attribute_resolve_derives():
    """The page agrees with `resolve` on every rejected verdict any corpus actually holds."""
    checked = 0
    disagreements = 0
    for name, dossier in _every_dossier():
        if not dossier.resolution.rejected:
            continue
        html = _debug_html(dossier)
        for verdict in dossier.resolution.rejected:
            resolved = verdict_attribute(dossier.person, verdict)
            row = _row_for(html, verdict.doc_id)
            if resolved:
                # `in row` is not enough: several of these labels are whole sentences that
                # CONTAIN the resolved attribute as a word, so a page still printing the raw
                # label would satisfy a substring check. The decided-by slot is what is read.
                assert f"<strong>{resolved}</strong>" in row, (
                    f"{name}: `resolve.verdict_attribute` says {verdict.doc_id} turned on "
                    f"{resolved!r}, and /debug does not report it as the decision"
                )
            checked += 1
            if resolved != verdict.disambiguator:
                disagreements += 1
    assert checked >= 3, f"only {checked} committed rejected verdicts were swept"
    assert disagreements >= 1, (
        "no committed corpus contains a verdict whose label differs from its resolved "
        "attribute, so this sweep cannot tell the fix from the defect"
    )


def test_the_committed_corpus_disagreement_is_visible_on_the_page():
    """The concrete one: `charlie`'s label is a whole sentence and resolves to `city`."""
    dossier = Dossier.model_validate_json((PROJECT_DOSSIERS / "charlie.json").read_text())
    verdict = dossier.resolution.rejected[0]
    resolved = verdict_attribute(dossier.person, verdict)
    assert resolved == "city" and verdict.disambiguator != "city", (
        f"charlie's rejected verdict no longer disagrees: {verdict.disambiguator!r} -> {resolved!r}"
    )
    row = _row_for(_debug_html(dossier), verdict.doc_id)
    assert f"<strong>{resolved}</strong>" in row
    assert verdict.disambiguator in row


def test_a_person_with_nothing_rejected_still_says_so():
    """The gate moved from `resolution.rejected` to the computed rows; the empty state stands."""
    dossier = Dossier.model_validate_json((FROZEN / "dossiers" / "jem-arrowood.json").read_text())
    assert not dossier.resolution.rejected, "this fixture is no longer the empty-rejected case"
    assert "No candidate document was rejected" in _debug_html(dossier)


# ------------------------------------------------------------------ served, and only here


@pytest.fixture
def corpus(tmp_path):
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(PROJECT_DOSSIERS.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def test_the_route_actually_serves_the_resolved_attribute(monkeypatch, corpus):
    """Through the real app, not through `render` — a view model nothing serves is not a fix."""
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    dossier = Dossier.model_validate_json((PROJECT_DOSSIERS / "charlie.json").read_text())
    verdict = dossier.resolution.rejected[0]
    resolved = verdict_attribute(dossier.person, verdict)

    with TestClient(create_app(dossier_dir=corpus, llm=LLMDouble())) as client:
        page = client.get("/debug/charlie")
    assert page.status_code == 200, page.text[:400]
    row = _row_for(page.text, verdict.doc_id)
    assert f"<strong>{resolved}</strong>" in row
    assert "<script" not in page.text.lower(), "SPEC non-goals: no JS on any page"


def test_the_operator_copy_stays_off_every_host_facing_page(monkeypatch, corpus):
    """R11/R15: the resolver's reasoning is operator material and must not leak to a host."""
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    llm = LLMDouble().when(
        "SayOutLoud", "Member:", {"line": "Ask about the harness they open-sourced."}
    )
    with TestClient(create_app(dossier_dir=corpus, llm=llm)) as client:
        assert client.post("/arrive", json={"person_id": "delta"}).status_code == 200
        arrival = client.post("/arrive", json={"person_id": "charlie"})
        assert arrival.status_code == 200, arrival.text[:400]
        digest = client.get(arrival.json()["digest_url"])
        assert digest.status_code == 200, digest.text[:400]
        index = client.get("/")

    for page, where in ((digest.text, "the digest"), (index.text, "the roster")):
        low = page.lower()
        assert "model said" not in low, f"{where} carries the operator view's label copy"
        assert "decided by" not in low, f"{where} carries the operator view's verdict table"
        assert "rejected candidate documents" not in low, (
            f"{where} shows the rejected candidate documents, which are operator-only"
        )
