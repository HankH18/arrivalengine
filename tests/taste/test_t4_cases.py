"""T-4 acceptance 1 and 2 — the corpus, and 100% agreement with it.

``test_taste_cases`` is the test the ticket names by hand: every case in T-4's own
``tests/fixtures/taste_cases.yaml``, run through ``apply_taste`` with ``LLMDouble``,
asserting the corpus' ``expect`` and its ``ExclusionReason`` exactly.

Three further tests grade the STAGING that DESIGN Decision 6 specifies, because agreement
alone does not distinguish a two-stage filter from a one-stage one:

* every ``rule_layer: deterministic`` case is settled by ``apply_taste_rules`` alone, and
  its sentence never appears in a prompt;
* every ``rule_layer: llm`` case actually REACHES the classifier — "allowed to" is not the
  same claim as "does", and a rule layer that quietly hard-codes the answer would pass the
  outcome test while making the LLM stage dead code;
* a classifier told to keep everything cannot move a deterministic exclusion, which is what
  makes the deterministic layer worth having.
"""

from __future__ import annotations

import collections

import pytest
from t4_corpus import (
    CASES,
    FIXTURE_PATH,
    SIX_CATEGORIES,
    expected_reason,
    facts_for,
    scripted_double,
)

from arrival.taste import apply_taste, apply_taste_rules, rule_verdict

pytestmark = pytest.mark.ticket("T-4")


#: Acceptance 1 names these three keeps by hand. Each is matched by a distinctive phrase
#: rather than a whole sentence, so the corpus may word them however its author likes.
REQUIRED_TRICKY_KEEPS = {
    "raised a Series B": ("series b",),
    "board of a children's hospital foundation": ("children's hospital", "childrens hospital"),
    "keynoted at SXSW": ("sxsw",),
}


def _by_id(facts) -> dict[str, object]:
    return {fact.fact_id: fact for fact in facts}


# ------------------------------------------------------------------ acceptance 1: the corpus


def test_the_corpus_meets_the_composition_the_ticket_specifies() -> None:
    """>= 30 cases, all six R11 categories, >= 10 must-keep professional facts."""
    assert len(CASES) >= 30, f"acceptance 1 wants >= 30 cases, corpus has {len(CASES)}"

    ids = [case["id"] for case in CASES]
    duplicates = [i for i, n in collections.Counter(ids).items() if n > 1]
    assert not duplicates, f"case ids must be unique; repeated: {duplicates}"

    by_category = collections.Counter(case["category"] for case in CASES)
    missing = [c for c in SIX_CATEGORIES if by_category[c] == 0]
    assert not missing, f"corpus spans no cases for R11 categories {missing}"

    keeps = [case for case in CASES if case["expect"] == "keep"]
    assert len(keeps) >= 10, f"acceptance 1 wants >= 10 must-keep facts, corpus has {len(keeps)}"

    kept_text = " ".join(case["text"] for case in keeps).lower()
    for label, spellings in REQUIRED_TRICKY_KEEPS.items():
        assert any(s in kept_text for s in spellings), (
            f"acceptance 1 names the tricky keep {label!r} and no must-keep case carries it"
        )


def test_the_corpus_is_internally_consistent() -> None:
    """A corpus that contradicts itself grades nothing. Checked before it is trusted."""
    problems: list[str] = []
    for case in CASES:
        cid = case["id"]
        if len(case["text"]) > 200:
            problems.append(f"{cid}: text is {len(case['text'])} chars, Fact.text caps at 200")
        if case["expect"] == "keep":
            if case["category"] != "keep" or case["reason"] is not None:
                problems.append(f"{cid}: a keep must be category 'keep' with reason null")
            if case.get("fail_closed"):
                problems.append(f"{cid}: a keep cannot be fail_closed")
        elif case["expect"] == "exclude":
            if case["category"] not in SIX_CATEGORIES:
                problems.append(f"{cid}: category {case['category']!r} is not an R11 category")
            if case.get("fail_closed"):
                if case["reason"] != "low_confidence":
                    problems.append(f"{cid}: a fail_closed case must carry reason low_confidence")
                if case.get("llm_returns") != "unsure":
                    problems.append(f"{cid}: a fail_closed case must script llm_returns: unsure")
            elif case["reason"] != case["category"]:
                problems.append(f"{cid}: reason {case['reason']!r} != category")
        else:
            problems.append(f"{cid}: expect must be 'keep' or 'exclude', got {case['expect']!r}")
        if case["rule_layer"] == "llm" and not case.get("llm_returns"):
            problems.append(f"{cid}: rule_layer 'llm' needs an llm_returns to script")
        if case["rule_layer"] not in ("deterministic", "llm"):
            problems.append(f"{cid}: rule_layer must be 'deterministic' or 'llm'")
        if not case.get("rationale"):
            problems.append(f"{cid}: every case needs a rationale — it is what a human reads")
    assert not problems, "corpus is internally inconsistent:\n  " + "\n  ".join(problems)


def test_the_corpus_carries_an_owner_approval_line() -> None:
    """Acceptance 1 asks for the approval line on line 1, and it must say the truth.

    The implementing agent cannot approve its own answer key, so the line records the
    review state rather than inventing a signature. What is graded here is that the line
    EXISTS, is first, and is not silently blank.
    """
    first = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# approved_by:"), (
        f"line 1 of the corpus must be '# approved_by: <name> <date>', got {first!r}"
    )
    assert first.split(":", 1)[1].strip(), "the approved_by line names nobody"


# ------------------------------------------------- acceptance 2: 100% agreement with `expect`


async def test_taste_cases() -> None:
    """EVERY case in T-4's corpus, through ``apply_taste`` with ``LLMDouble``, must agree.

    The ticket names this test. It grades the OUTCOME — ``excluded`` and
    ``exclusion_reason`` — for all six R11 categories, the must-keep professional facts and
    the fail-closed cases at once, and reports every disagreement rather than the first, so
    one run shows the whole shape of a regression.
    """
    llm = scripted_double(CASES)
    results = _by_id(await apply_taste(facts_for(CASES), llm))

    assert len(results) == len(CASES), "apply_taste dropped facts; it must return all of them"

    disagreements: list[str] = []
    for case in CASES:
        fact = results[case["id"]]
        want_excluded = case["expect"] == "exclude"
        want_reason = expected_reason(case)
        if fact.excluded is not want_excluded or fact.exclusion_reason != want_reason:
            disagreements.append(
                f"{case['id']}: want excluded={want_excluded} reason={want_reason!r}, "
                f"got excluded={fact.excluded} reason={fact.exclusion_reason!r} "
                f"-- {case['text']!r}"
            )

    agreed = len(CASES) - len(disagreements)
    assert not disagreements, (
        f"S3 demands 100% agreement with the owner-approved corpus; "
        f"got {agreed}/{len(CASES)}:\n  " + "\n  ".join(disagreements)
    )


async def test_apply_taste_preserves_every_fact_and_its_order() -> None:
    """Excluded facts are kept in the list. ``/debug`` shows them and the digest counts them."""
    facts = facts_for(CASES)
    out = await apply_taste(facts, scripted_double(CASES))
    assert [f.fact_id for f in out] == [f.fact_id for f in facts]
    assert [f.text for f in out] == [f.text for f in facts]


# ------------------------------------------------------------- DESIGN Decision 6: the staging


def test_the_rule_layer_alone_settles_every_deterministic_case() -> None:
    """No LLM in the room at all: ``apply_taste_rules`` must reach the corpus' answer."""
    deterministic = [case for case in CASES if case["rule_layer"] == "deterministic"]
    assert deterministic, "corpus has no deterministic cases to grade the rule layer with"

    results = _by_id(apply_taste_rules(facts_for(deterministic)))
    wrong: list[str] = []
    for case in deterministic:
        fact = results[case["id"]]
        want_excluded = case["expect"] == "exclude"
        if fact.excluded is not want_excluded or fact.exclusion_reason != expected_reason(case):
            wrong.append(
                f"{case['id']}: rules alone gave excluded={fact.excluded} "
                f"reason={fact.exclusion_reason!r}, corpus says excluded={want_excluded} "
                f"reason={expected_reason(case)!r} -- {case['text']!r}"
            )
    assert not wrong, "cases marked deterministic that the rule layer cannot settle:\n  " + (
        "\n  ".join(wrong)
    )


async def test_only_the_unsure_cases_reach_the_classifier() -> None:
    """Staging, graded in BOTH directions on the recorded prompts.

    A deterministic case must never cost a call, and every ``rule_layer: llm`` case must
    actually arrive — a rule layer that answered them itself would pass every outcome test
    while turning the classifier into dead code.
    """
    llm = scripted_double(CASES)
    await apply_taste(facts_for(CASES), llm)

    prompts = "\n".join(call.user for call in llm.calls)

    leaked = [c["id"] for c in CASES if c["rule_layer"] == "deterministic" and c["text"] in prompts]
    assert not leaked, f"deterministic cases were sent to the LLM anyway: {leaked}"

    llm_cases = [c for c in CASES if c["rule_layer"] == "llm"]
    assert llm_cases, "corpus has no llm-layer cases, so the second stage is never exercised"
    assert llm.calls, "no classifier call was made at all, so the LLM stage is dead code"

    unreached = [c["id"] for c in llm_cases if c["text"] not in prompts]
    assert not unreached, (
        f"cases marked rule_layer: llm never reached the classifier: {unreached}. "
        "The rule layer is answering a question the design gives to the LLM."
    )

    for case in llm_cases:
        assert rule_verdict(case["text"]).decision == "unsure", (
            f"{case['id']} is marked rule_layer: llm but rule_verdict settled it as "
            f"{rule_verdict(case['text']).decision!r} -- {case['text']!r}"
        )


async def test_a_classifier_that_keeps_everything_cannot_move_a_deterministic_exclusion() -> None:
    """The point of a deterministic layer: a wrong, cheerful model cannot open the gate."""
    deterministic_excludes = [
        c for c in CASES if c["rule_layer"] == "deterministic" and c["expect"] == "exclude"
    ]
    assert deterministic_excludes, "no deterministic exclusions to grade"

    hostile = scripted_double(CASES, override={c["id"]: "keep" for c in CASES})
    results = _by_id(await apply_taste(facts_for(deterministic_excludes), hostile))

    survived = [
        c["id"] for c in deterministic_excludes if not results[c["id"]].excluded
    ]
    assert not survived, (
        f"a classifier answering 'keep' to everything opened these R11 exclusions: {survived}"
    )
