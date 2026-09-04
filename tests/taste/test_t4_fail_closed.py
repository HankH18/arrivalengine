"""T-4 acceptance 5 — fail closed, proven from every direction the stage can shrug.

DESIGN Decision 6's last clause is the one that makes the filter trustworthy: a fact the
rules could not settle and the classifier would not settle is EXCLUDED, with reason
``low_confidence``. Not kept, not guessed a category for, not dropped from the list.

The four ways the second stage can fail to answer are graded separately, because they take
different code paths and only one of them is the literal wording of the acceptance
criterion:

1. the classifier returns ``unsure``                      (the criterion);
2. no classifier was supplied at all (``llm=None``);
3. the classifier raises ``LLMError``;
4. the classifier answers, but about some other fact id.

A fifth test grades the same rule at the rules-only entry point: ``apply_taste_rules`` is
usable on its own, so it must fail closed on its own too.
"""

from __future__ import annotations

import pytest
from t4_corpus import CASES, fact_for, facts_for, scripted_double

from arrival.taste import TasteRuling, TasteRulings, apply_taste, apply_taste_rules, rule_verdict
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-4")


#: A sentence whose only content is an unexplained absence. It is invented here rather than
#: read from the corpus so the test states its own premise: nothing on the surface says
#: whether this is a sabbatical, a medical leave, a lawsuit or a parental leave, and
#: guessing the category would invent the very thing R11 protects.
UNRESOLVED = {
    "id": "fc-probe-unexplained-gap",
    "text": (
        "They stepped back from the company for eight months in 2023 "
        "and no public source says why."
    ),
    "category": "health",
    "expect": "exclude",
    "reason": "low_confidence",
    "rule_layer": "llm",
    "llm_returns": "unsure",
    "fail_closed": True,
    "rationale": "The sentence resolves nothing; the correct answer is to withhold it.",
}


def _fail_closed_cases() -> list[dict]:
    return [case for case in CASES if case.get("fail_closed")]


def test_the_probe_sentence_is_genuinely_unsettled_by_the_rules() -> None:
    """Premise of every test below: the rule layer must hand this one to the classifier.

    If the rules settled it, the tests that follow would be grading the rule layer while
    claiming to grade the fail-closed path — the shape of vacuous green rule 7 warns about.
    """
    assert rule_verdict(UNRESOLVED["text"]).decision == "unsure"


async def test_an_unsure_classifier_verdict_excludes_with_low_confidence() -> None:
    """Acceptance 5, literally: script the double to return unsure, assert the exclusion."""
    llm = scripted_double([UNRESOLVED])
    (fact,) = await apply_taste([fact_for(UNRESOLVED)], llm)

    assert llm.calls, "the fact never reached the classifier, so nothing was proven"
    assert fact.excluded is True
    assert fact.exclusion_reason == "low_confidence"


async def test_every_fail_closed_case_in_the_corpus_excludes_with_low_confidence() -> None:
    """The same rule across every fail-closed case the owner approved."""
    cases = _fail_closed_cases()
    assert len(cases) >= 1, "the corpus must carry at least one fail-closed case"

    llm = scripted_double(cases)
    results = {f.fact_id: f for f in await apply_taste(facts_for(cases), llm)}
    for case in cases:
        fact = results[case["id"]]
        assert fact.excluded is True, f"{case['id']} was kept after an unsure verdict"
        assert fact.exclusion_reason == "low_confidence", (
            f"{case['id']} was excluded as {fact.exclusion_reason!r}; an unsure verdict must "
            "never be turned into a guessed R11 category"
        )


async def test_a_missing_classifier_fails_closed() -> None:
    """``llm=None`` is a degraded pipeline, not a permissive one."""
    (fact,) = await apply_taste([fact_for(UNRESOLVED)], None)
    assert fact.excluded is True
    assert fact.exclusion_reason == "low_confidence"


async def test_a_classifier_that_raises_fails_closed() -> None:
    """An unscripted ``LLMDouble`` raises ``LLMError``; the fact must still be withheld."""
    llm = LLMDouble()  # no rules, no queue: any call raises
    (fact,) = await apply_taste([fact_for(UNRESOLVED)], llm)

    assert llm.calls, "the classifier was never called, so the error path was not exercised"
    assert fact.excluded is True
    assert fact.exclusion_reason == "low_confidence"


async def test_a_ruling_about_a_different_fact_does_not_release_this_one() -> None:
    """Rulings are matched by ``fact_id``. An answer about someone else is not an answer."""
    llm = LLMDouble()
    llm.when(
        TasteRulings.__name__,
        "",
        TasteRulings(rulings=[TasteRuling(fact_id="some-other-fact", verdict="keep")]),
    )
    (fact,) = await apply_taste([fact_for(UNRESOLVED)], llm)
    assert fact.excluded is True
    assert fact.exclusion_reason == "low_confidence"


def test_the_rules_only_entry_point_fails_closed_too() -> None:
    """``apply_taste_rules`` is public and callable alone, so it carries the same guarantee."""
    (fact,) = apply_taste_rules([fact_for(UNRESOLVED)])
    assert fact.excluded is True
    assert fact.exclusion_reason == "low_confidence"


async def test_a_kept_fact_carries_no_exclusion_reason() -> None:
    """The other half of the invariant: a keep must be clean, not merely un-excluded."""
    keeps = [case for case in CASES if case["expect"] == "keep"]
    results = {f.fact_id: f for f in await apply_taste(facts_for(keeps), scripted_double(keeps))}
    dirty = [
        cid for cid, fact in results.items() if fact.excluded or fact.exclusion_reason is not None
    ]
    assert not dirty, f"kept facts carrying an exclusion state: {dirty}"
