"""T-3 acceptance 5 / SPEC R7: who gets to be `non_obvious`.

Both halves of the ticket sentence, in one place: an eligible source kind AND the model's
own flag. Neither alone. A classifier that labels everything, labels nothing, or keys on
the source kind alone fails at least one of these.
"""

from __future__ import annotations

import pytest
import t3_corpus as corpus

from arrival.extract import (
    NON_OBVIOUS_ELIGIBLE_KINDS,
    CandidateFact,
    ExtractionResult,
    ExtractionStats,
    extract,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")

FLAGGED_ELIGIBLE = "Quarrystone Labs shipped a public status page in 2017, with eleven people."
UNFLAGGED_ELIGIBLE = "Quarrystone Labs keeps its incident log public on the bad days too."
FLAGGED_INELIGIBLE = "Runa Okonkwo co-founded Quarrystone Labs in 2016 and runs its platform team."


def test_the_eligible_set_is_the_one_design_names_and_excludes_the_first_page():
    assert NON_OBVIOUS_ELIGIBLE_KINDS == {
        "edgar", "uspto", "propublica", "wayback", "github", "hn", "openalex", "wikidata",
        "podcast",
    }
    assert "self_page" not in NON_OBVIOUS_ELIGIBLE_KINDS, "an about page IS the first page"
    assert "search" not in NON_OBVIOUS_ELIGIBLE_KINDS


async def test_the_flag_and_an_eligible_source_are_both_required():
    status, about = corpus.status_doc(), corpus.about_doc()
    assert status.source_kind in NON_OBVIOUS_ELIGIBLE_KINDS
    assert about.source_kind not in NON_OBVIOUS_ELIGIBLE_KINDS
    stats = ExtractionStats()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                CandidateFact(doc_id=status.doc_id, text=FLAGGED_ELIGIBLE,
                              quote=corpus.STATUS_SPAN, category="non_obvious",
                              natural_category="recent_activity"),
                CandidateFact(doc_id=status.doc_id, text=UNFLAGGED_ELIGIBLE,
                              quote=corpus.STATUS_SPAN_2, category="recent_activity"),
                CandidateFact(doc_id=about.doc_id, text=FLAGGED_INELIGIBLE,
                              quote=corpus.ABOUT_SPAN, category="non_obvious",
                              natural_category="current_work"),
            ]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON,
        corpus.resolution_for(status, about),
        [status, about],
        llm,
        stats=stats,
    )

    by_text = {f.text: f for f in facts}
    assert set(by_text) == {FLAGGED_ELIGIBLE, UNFLAGGED_ELIGIBLE, FLAGGED_INELIGIBLE}, (
        "all three are cited and inside the cap, so none may be dropped"
    )
    assert by_text[FLAGGED_ELIGIBLE].category == "non_obvious"
    assert by_text[UNFLAGGED_ELIGIBLE].category == "recent_activity"
    assert by_text[FLAGGED_INELIGIBLE].category == "current_work", (
        "a downgraded fact falls back to the model's natural category"
    )
    assert [f.text for f in facts if f.category == "non_obvious"] == [FLAGGED_ELIGIBLE]
    assert stats.downgraded_non_obvious == 1


async def test_a_downgrade_with_no_natural_category_offered_still_leaves_the_slot():
    """A model that puts `non_obvious` in both fields must not win by repetition."""
    about = corpus.about_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                CandidateFact(doc_id=about.doc_id, text=FLAGGED_INELIGIBLE,
                              quote=corpus.ABOUT_SPAN, category="non_obvious",
                              natural_category="non_obvious")
            ]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON, corpus.resolution_for(about), [about], llm
    )

    assert len(facts) == 1
    assert facts[0].category != "non_obvious"


async def test_an_unflagged_fact_keeps_its_own_category_untouched():
    status = corpus.status_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                CandidateFact(doc_id=status.doc_id, text=UNFLAGGED_ELIGIBLE,
                              quote=corpus.STATUS_SPAN_2, category="hook",
                              natural_category="interest")
            ]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON, corpus.resolution_for(status), [status], llm
    )

    assert [f.category for f in facts] == ["hook"], (
        "natural_category is a fallback for a downgrade, never an override"
    )
