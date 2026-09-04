"""T-8: the view models — citation numbering and the operator view's withholding reasons.

These grade two decisions this ticket owns outright, both invisible to a page-level grep:

* a citation `[n]` on the page indexes `Digest.sources[n-1]` and nothing else, and
* `/debug` reports WHICH of R12's three independent clauses withheld a fact, rather than
  calling everything "excluded".
"""

from __future__ import annotations

import datetime as dt

import pytest

from arrival.contracts import Digest, Dossier, Fact, PersonRef, Provenance
from arrival.web.render import digest_view, render, withholding_reason

pytestmark = pytest.mark.ticket("T-8")

RETRIEVED = dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.UTC)


def _fact(fact_id, *, doc="doc-a", kind="self_page", confidence=0.9, excluded=False, reason=None):
    return Fact(
        fact_id=fact_id,
        text=f"Sentence for {fact_id}.",
        category="recent_activity",
        provenance=Provenance(
            doc_id=doc,
            url=f"https://example.com/{doc}",
            source_kind=kind,
            quote=f"Sentence for {fact_id}",
            published_at=dt.date(2026, 1, 5),
            retrieved_at=RETRIEVED,
            confidence=confidence,
        ),
        excluded=excluded,
        exclusion_reason=reason,
    )


def _digest(*, lately, sources, non_obvious=None):
    return Digest(
        digest_id="0123456789abcdef",
        person=PersonRef(person_id="ada-lark", name="Ada Lark"),
        who_line="Ada Lark. Runs the platform team.",
        meet=[],
        lately=lately,
        non_obvious=non_obvious,
        say_out_loud="Ask about the status page.",
        sources=sources,
        exclusion_policy="policy",
        created_at=RETRIEVED,
    )


def _dossier(facts):
    return Dossier(
        person=PersonRef(person_id="ada-lark", name="Ada Lark"),
        resolution={
            "person_id": "ada-lark",
            "status": "resolved",
            "strong_keys": {},
            "accepted_doc_ids": [],
            "rejected": [],
            "confidence": 0.9,
        },
        facts=facts,
        hubs=[],
        built_at=RETRIEVED,
    )


def test_a_citation_number_indexes_the_position_in_digest_sources():
    """The numbering decision, pinned.

    `Digest.sources` is "deduped by doc_id, NUMBERED IN ORDER" — T-7's first-use order, which
    is NOT the page's section order. This layer cites by that position rather than
    renumbering, so `[n]` and `sources[n-1]` can never disagree. The visible cost is that a
    Meet row may carry a higher number than a Lately bullet below it; the alternative is a
    page whose footnotes do not index its own data model.
    """
    first, second = _fact("f1", doc="doc-a"), _fact("f2", doc="doc-b")
    digest = _digest(
        lately=[second], sources=[first.provenance, second.provenance]
    )
    view = digest_view(digest, None)

    assert [n for n, _ in view["sources"]] == [1, 2]
    assert view["sources"][0][1].doc_id == "doc-a"
    # The bullet on the page is f2, whose document is SECOND in sources — so it cites [2],
    # not [1], even though it is the first bullet rendered.
    assert view["lately_rows"][0]["citations"] == [2]


def test_a_fact_whose_document_is_not_in_sources_gets_no_citation():
    """The gate that keeps this layer from smuggling a withheld document back onto the page.

    T-7 decides what `sources` holds, and it applies `is_displayable` to hub evidence before
    citing it. If this module numbered by looking documents up in the dossier instead, a
    taste-withheld source would acquire a footnote.
    """
    shown, unlisted = _fact("f1", doc="doc-a"), _fact("f2", doc="doc-secret")
    digest = _digest(lately=[shown, unlisted], sources=[shown.provenance])
    view = digest_view(digest, None)

    assert view["lately_rows"][0]["citations"] == [1]
    assert view["lately_rows"][1]["citations"] == []


def test_repeated_documents_are_cited_once_each_and_keep_their_number():
    same_doc_a, same_doc_b = _fact("f1", doc="doc-a"), _fact("f2", doc="doc-a")
    digest = _digest(lately=[same_doc_a, same_doc_b], sources=[same_doc_a.provenance])
    view = digest_view(digest, None)
    assert [row["citations"] for row in view["lately_rows"]] == [[1], [1]]


def test_the_who_line_is_cited_from_the_dossier_it_was_built_from():
    """`Digest` carries the SENTENCE, not the facts behind it, so `who_line_for` is re-run.

    Pure and deterministic on the same dossier, and the only alternative is re-implementing
    T-7's Who-line selection rule in a second place, where it would drift.
    """
    current = Fact(
        fact_id="f-current",
        text="Runs the platform team at Northgate.",
        category="current_work",
        provenance=_fact("f-current", doc="doc-who").provenance,
    )
    digest = _digest(lately=[], sources=[current.provenance])
    view = digest_view(digest, _dossier([current]))
    assert view["who_citations"] == [1]


def test_withholding_reason_names_which_of_r12s_three_clauses_bit():
    """R12's clauses are independent, and the operator view has to say which one applied.

    Two facts in the frozen grading corpus exist precisely to prove that: one is kept at
    confidence 0.55 and blocked only by the display floor, the other is kept at confidence
    0.92 and blocked only because its source kind is `fec`. A debug view that reported
    "excluded" for both would be showing the operator a line that is not where the line is.
    """
    assert withholding_reason(_fact("ok")) is None
    assert withholding_reason(_fact("family", excluded=True, reason="family")) == "family"
    assert withholding_reason(_fact("quiet", confidence=0.55)) == "low_confidence"
    assert (
        withholding_reason(_fact("filing", kind="fec", confidence=0.92))
        == "source_kind_not_displayable"
    )


def test_an_excluded_fact_with_no_recorded_reason_still_reports_one():
    """`exclusion_reason` is optional on the contract; a blank cell teaches the operator nothing."""
    assert withholding_reason(_fact("bare", excluded=True)) == "excluded"


def test_an_empty_digest_still_renders_all_six_sections_and_states_each_absence():
    """R7 pins SIX sections; R8 pins that an absence is stated, never padded.

    Every fact in the frozen grading corpus carries a `published_at`, so `lately` is never
    empty there and neither this branch nor the missing-non-obvious one is exercised by the
    acceptance suite at all. A dossier whose facts are all undated yields `lately == []`
    (`digest.pick_lately` will not place an undatable sentence in a most-recent-first list),
    and that page must still be a page.
    """
    html = render("digest.html", **digest_view(_digest(lately=[], sources=[]), None))

    for anchor in (
        'id="who"',
        'id="meet"',
        'id="lately"',
        'id="not-on-the-first-page"',
        'id="say-out-loud"',
        'id="why-we-know-this"',
    ):
        assert anchor in html, f"an empty digest dropped the {anchor} section"

    assert html.index('id="who"') < html.index('id="meet"') < html.index('id="lately"')
    assert (
        html.index('id="lately"')
        < html.index('id="not-on-the-first-page"')
        < html.index('id="say-out-loud"')
        < html.index('id="why-we-know-this"')
    )

    lately = html[html.index('id="lately"') : html.index('id="not-on-the-first-page"')]
    assert "Nothing dated" in lately
    assert "<li" not in lately, "an empty Lately padded itself with a bullet"

    meet = html[html.index('id="meet"') : html.index('id="lately"')]
    assert "Nobody else is in the building" in meet

    assert "Ask about the status page." in html  # the spoken line still lands
    assert html.count("exclusion-policy") == 1  # R13 rides along on every digest
