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
import re

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

    # STRENGTHENED (T-070/F4). This counted every case, including the five fail-closed
    # ones -- and a fail-closed case's ruling is `low_confidence`, so its `category` was
    # never a claim the corpus could stand behind. `cover-fail-closed-declined-to-describe`
    # said so in its own rationale ("inferring a category from a refusal is precisely the
    # invention the rule forbids") while its `category` field read `health`. Under the old
    # count, a corpus whose ONLY health case was a fail-closed one satisfied "spans all six
    # R11 categories" on the strength of an invented label. Now the span must be carried by
    # cases that actually assert a category, which is strictly harder to satisfy.
    asserting = [case for case in CASES if not case.get("fail_closed")]
    by_category = collections.Counter(case["category"] for case in asserting)
    missing = [c for c in SIX_CATEGORIES if by_category[c] == 0]
    assert not missing, (
        f"corpus spans no ruled (non-fail-closed) cases for R11 categories {missing}"
    )

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
            # STRENGTHENED (T-070/F4). This branch required EVERY exclude's category to be
            # one of the six -- including the fail-closed ones, whose whole point is that
            # the sentence does not settle a category. So the corpus was obliged to write
            # down a category it had just declared unknowable, and four of the five did:
            # an eight-month absence labelled `health`, an unexplained departure labelled
            # `legal`. The allowed set for a fail-closed case is now the single value
            # `unresolved`, which is narrower than SIX_CATEGORIES, not wider: the corpus
            # may no longer name a category on a case it cannot rule.
            if case.get("fail_closed"):
                if case["category"] != "unresolved":
                    problems.append(
                        f"{cid}: a fail_closed case must carry category 'unresolved'; naming "
                        f"an R11 category for a sentence the corpus cannot rule is the "
                        f"invention R11 forbids, got {case['category']!r}"
                    )
                if case["reason"] != "low_confidence":
                    problems.append(f"{cid}: a fail_closed case must carry reason low_confidence")
                if case.get("llm_returns") != "unsure":
                    problems.append(f"{cid}: a fail_closed case must script llm_returns: unsure")
            else:
                if case["category"] not in SIX_CATEGORIES:
                    problems.append(f"{cid}: category {case['category']!r} is not an R11 category")
                if case["reason"] != case["category"]:
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


#: The corpus has NOT been reviewed yet, and says so in as many words. Anchored, so that
#: "NOT APPROVED", "PENDING", "TBD" and free text are not silently accepted as this.
_AWAITING_REVIEW = re.compile(r"^PENDING OWNER REVIEW\b")

#: A signature: a name of at least two parts, then an ISO date. Rejects the literal
#: placeholder `<name> <date>`, a single letter, and a bare role like "the T-4 lane".
_SIGNATURE = re.compile(
    r"^[^\W\d_][\w.'’-]*(?:[\s-]+[^\W\d_][\w.'’-]*)+\s+\d{4}-\d{2}-\d{2}\s*$"
)


def test_the_corpus_carries_an_owner_approval_line() -> None:
    """T-4 acceptance 1: line 1 declares the review state, unambiguously, in one of two ways.

    **THE APPROVAL ITSELF IS A HUMAN ACT AND THIS SUITE HAS NO BACKSTOP FOR IT.** Say it
    plainly rather than implying otherwise, because the alternative reading is what went
    wrong here. `# approved_by: Ada Okoro 2026-09-04` satisfies any regex anybody can
    write, and an agent can type it; no test can tell a real signature from a fabricated
    one. Tightening the shape and stopping there would move the failure from "obviously
    unapproved passes" to "fabricated signature passes", which is strictly worse -- the
    first at least tells the truth on its face.

    STRENGTHENED (T-071). The previous assertion was ``startswith("# approved_by:")`` plus
    "something non-whitespace after the colon". Measured, that PASSES on every one of:

        PENDING OWNER REVIEW ...      <name> <date>      NOT APPROVED
        the T-4 lane                  x

    -- so `pytest -q` was green over a corpus whose own first line says nobody has
    approved it, and a reader took that green as the acceptance criterion being met. That
    is the defect: not that the line was unsigned, but that the suite reported nothing.

    What IS checkable is that the line declares one of exactly two states and nothing
    else: still awaiting review, or signed with a name and a date. Free text, a
    placeholder and a one-character name are now all failures, in both directions.
    """
    first = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# approved_by:"), (
        f"line 1 of the corpus must be '# approved_by: <name> <date>', got {first!r}"
    )
    declared = first.split(":", 1)[1].strip()
    assert declared, "the approved_by line names nobody"
    assert _AWAITING_REVIEW.match(declared) or _SIGNATURE.match(declared), (
        f"the approved_by line says {declared!r}, which is neither an owner signature "
        f"('<Name Surname> <YYYY-MM-DD>') nor the literal review state "
        f"'PENDING OWNER REVIEW'. A line that is neither leaves the corpus' approval "
        f"state unreadable while the suite stays green -- which is the exact failure "
        f"this assertion replaced."
    )


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


# ------------------------------------------------ T-070 / T-071: what the corpus CLAIMS


#: T-071's reproduction, kept as data. Every one of these satisfied the previous
#: `startswith("# approved_by:") and non-blank` assertion, so `pytest -q` was green over a
#: corpus whose own first line said nobody had approved it.
NOT_AN_APPROVAL = (
    "PENDING",
    "NOT APPROVED",
    "<name> <date>",
    "the T-4 lane",
    "x",
    "TBD",
    "TODO",
    "n/a",
    "-",
    "approved",
    "Ada",
    "Ada Okoro",
    "2026-09-04",
)

#: Shapes a real signature can take. The suite cannot tell a real one from a typed one --
#: see the docstring on the approval test -- so what is graded is only that the line is
#: readable as a signature at all.
LOOKS_LIKE_A_SIGNATURE = (
    "Ada Okoro 2026-09-04",
    "Ada N. Okoro 2026-09-04",
    "Jean-Luc de Vries 2026-12-31",
)


def test_the_approval_matcher_rejects_the_lines_that_used_to_pass() -> None:
    """T-071's positive control: without it, the tightening above is unmeasured.

    A matcher can only be trusted in the direction it rejects. These strings are the
    measured false positives of the assertion this replaced.
    """
    accepted = [
        line
        for line in NOT_AN_APPROVAL
        if _AWAITING_REVIEW.match(line) or _SIGNATURE.match(line)
    ]
    assert not accepted, (
        f"these are not approvals and the matcher accepts them: {accepted}. Each one "
        "passed the previous assertion, which is why it was replaced."
    )

    rejected = [line for line in LOOKS_LIKE_A_SIGNATURE if not _SIGNATURE.match(line)]
    assert not rejected, (
        f"a legitimate signature must not be blocked over formatting: {rejected}"
    )


def test_the_corpus_declares_how_every_case_was_written() -> None:
    """T-070/F8: the owner's signature must not read as approval of 104 independent judgments.

    Only the first block of cases was written by an agent that had never seen the
    implementation or the frozen corpus. The REGRESSION COVERAGE block was written FROM
    ``taste.py``'s marker list, and the two AXIS blocks were written by the lane that also
    repaired the filter. Both of those are legitimate cases and neither is an independent
    judgment, so each carries a ``provenance`` saying which it is.
    """
    allowed = {"derived_from_implementation", "same_author_as_filter"}
    bad = [
        f"{c['id']}: {c['provenance']!r}"
        for c in CASES
        if c.get("provenance") is not None and c["provenance"] not in allowed
    ]
    assert not bad, f"unknown provenance values: {bad}"

    independent = [c["id"] for c in CASES if not c.get("provenance")]
    assert len(independent) >= 30, (
        f"only {len(independent)} independently authored cases remain; the corpus' value "
        "rests on those and a corpus of derived cases grades the implementation against "
        "itself"
    )

    # Every case whose id marks it as one of the two derived blocks must SAY so. Without
    # this the field is decorative: a new derived case could be added with no marker and
    # would be counted among the independent ones.
    unmarked = [
        c["id"]
        for c in CASES
        if (c["id"].startswith("cover-") or c["id"].startswith("axis-"))
        and not c.get("provenance")
    ]
    assert not unmarked, f"derived cases with no provenance marker: {unmarked}"


def test_the_corpus_covers_professional_facts_whose_subject_matter_is_an_r11_category() -> None:
    """T-070/F5. SPEC Q4's roster is investors and writers, so R11's six nouns are their
    market vocabulary. A corpus with no case on this axis cannot see an over-block at all.

    Ten of these twelve sentences were DETERMINISTICALLY excluded before T-069 -- the class
    of error a classifier cannot reopen -- so the axis is not decorative.
    """
    axis = [c for c in CASES if c["id"].startswith("axis-keep-")]
    assert len(axis) >= 8, (
        f"the subject-matter axis has {len(axis)} cases; it is the axis the corpus was "
        "blind to and a handful of examples does not cover a whole vocabulary"
    )
    not_keeps = [c["id"] for c in axis if c["expect"] != "keep"]
    assert not not_keeps, f"a subject-matter case is a professional fact and a keep: {not_keeps}"

    # Each must actually carry the forbidden vocabulary, or it is not testing the axis.
    r11_words = (
        "cancer", "medical", "mortgage", "equity", "portfolio", "divorce", "children",
        "salary",
        "sister", "registered independent", "charged with", "court records", "estate",
    )
    toothless = [
        c["id"] for c in axis if not any(w in c["text"].lower() for w in r11_words)
    ]
    assert not toothless, (
        f"these subject-matter cases carry no R11 vocabulary at all, so they would pass "
        f"against a filter with no rules in it: {toothless}"
    )


def test_the_corpus_covers_excludes_that_do_not_name_their_own_category() -> None:
    """T-070/F6. R11 names CATEGORIES, not words.

    Almost every exclude in the original corpus announced itself -- the divorce case says
    "divorce", the salary case says "salary". A corpus made only of those grades a
    dictionary, and every sentence below was DISPLAYED by the filter before T-069.
    """
    axis = [
        c
        for c in CASES
        if c["id"].startswith("axis-")
        and not c["id"].startswith("axis-guard-")
        and c["expect"] == "exclude"
    ]
    assert len(axis) >= 8, f"the unnamed-category axis has only {len(axis)} cases"

    #: The words that would make an exclude self-announcing, per category.
    announces = {
        "health": ("health", "medical", "diagnos", "hospital", "treatment", "illness"),
        "family": ("family", "spouse", "married", "marriage", "daughter", "son", "child"),
        "legal": ("legal", "court", "criminal", "charge", "convict", "lawsuit", "divorce"),
        "home_or_property": ("home", "house", "address", "property", "deed", "residence"),
        "wealth": ("wealth", "net worth", "salary", "compensation", "pay", "stake"),
        "political": ("political", "donation", "party", "campaign", "candidate", "pac"),
    }
    self_announcing = [
        f"{c['id']} ({c['category']})"
        for c in axis
        if any(w in c["text"].lower() for w in announces[c["category"]])
    ]
    assert not self_announcing, (
        f"these cases name their own category in the sentence, so they do not test the "
        f"axis they were added for: {self_announcing}"
    )

    categories = {c["category"] for c in axis}
    assert len(categories) >= 5, (
        f"the unnamed-category axis reaches only {sorted(categories)}; a hole in one "
        "category is a hole in the product"
    )


def test_the_corpus_guards_the_subject_matter_test_against_being_widened() -> None:
    """T-069 sibling sweep. Every neutralising mechanism is one somebody can widen.

    The rule that lets "their children's-media studio" through is the rule that would let
    "their daughter's company" through; the rule that lets "to track court records" through
    is the one that would let "tracked their court records" through. All six sentences
    below were measured LEAKING against the first version of that mechanism, in a sweep
    written after the fix and aimed at it. They are the reason a later widening breaks a
    test instead of a member's trust.
    """
    guards = [c for c in CASES if c["id"].startswith("axis-guard-")]
    assert len(guards) >= 5, f"only {len(guards)} guard cases; the mechanism is unprotected"

    excludes = [c for c in guards if c["expect"] == "exclude"]
    assert len(excludes) >= 5, "a guard case that is not an exclude guards nothing"

    # Each guard must be settled by the RULE layer. A guard that merely defers proves
    # nothing: a widened mechanism would turn it into a keep and the corpus would agree,
    # because `unsure` and `keep` are the same answer once a cooperative classifier speaks.
    deferred = [c["id"] for c in guards if c["rule_layer"] != "deterministic"]
    assert not deferred, (
        f"guard cases must be deterministic or they do not guard the rule layer: {deferred}"
    )

    # The minimal pair. One word apart, opposite rulings, and the corpus must carry both
    # or the trade-off it records is invisible.
    ids = {c["id"] for c in CASES}
    assert {"axis-guard-salary-data-breach", "axis-keep-comp-benchmarking-note"} <= ids, (
        "the salary minimal pair is what records where this filter draws an undecidable "
        "line; removing either half hides the trade-off rather than resolving it"
    )
