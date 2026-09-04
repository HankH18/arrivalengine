"""T-086/T-088: the R11 verdict follows the PREDICATE, not the spelling of the subject.

The acceptance criteria for this fix live in ``tests/test_tadv_r11_named_subject_bypass.py``
and ``tests/test_tadv_r11_unscreened_quote.py``, written by a lane that was forbidden to
touch ``src/``. This module does not restate them. It pins the three properties that fix
could not express, because each is about a DIRECTION of error rather than about a verdict:

1. **Spelling-independence as an invariant, not a case list.** Every predicate is run
   against four spellings of its subject — pronoun, proper name, possessive proper name,
   and no subject at all — and the verdicts must agree. A cue repaired for one spelling and
   not the others is the defect coming back in a new dress.
2. **The over-block direction.** The T-069 lane spent a whole ticket ensuring "They founded
   a cancer-imaging startup" survives; this asserts the surviving-keep count over
   ``tests/fixtures/taste_cases.yaml``, which this lane does not own and cannot write.
3. **Which gate ran when.** ``is_displayable`` re-checks the rule layer, and it matters a
   great deal that it re-checks ``exclude`` and NOT ``unsure`` — the second would overturn
   every ruling the classifier ever made and empty the product.
"""

from __future__ import annotations

import pytest
from t4_corpus import load_cases

from arrival.taste import (
    QUOTE_WITHHELD,
    R11_CATEGORIES,
    apply_taste_rules,
    is_displayable,
    rule_verdict,
    screen_quote,
)

pytestmark = pytest.mark.ticket("T-4")


# --------------------------------------------------------------- 1. spelling-independence

#: ``(predicate spelled four ways)``. Each tuple makes the SAME claim about the same person
#: and differs only in how the sentence names its subject. A fact is about the member by
#: construction, so all four must reach the same side of the line.
SUBJECT_SPELLINGS = [
    pytest.param(
        [
            "He lives in Boulder, Colorado.",
            "Brad Feld lives in Boulder, Colorado.",
            "Currently lives in Boulder, Colorado.",
        ],
        id="residence",
    ),
    pytest.param(
        [
            "He owns a home in Aspen.",
            "Brad Feld owns a home in Aspen.",
            "Owns homes in Aspen, Tucson, Boulder, and Basalt.",
        ],
        id="dwelling-ownership",
    ),
    pytest.param(
        [
            "His wife runs the foundation.",
            "Brad Feld's wife runs the foundation.",
            "Co-founded the studio with wife Ada Perrin.",
        ],
        id="spouse",
    ),
    pytest.param(
        [
            "His son was born last year.",
            "Brad Feld's son was born last year.",
            "Resigned as CEO due to the birth of his 9 month old son.",
        ],
        id="child",
    ),
    pytest.param(
        [
            "He is friends with Phil Weiser.",
            "Brad Feld is friends with Phil Weiser and Michael Bennet.",
            "Is friends with Phil Weiser.",
        ],
        id="friendship",
    ),
]


@pytest.mark.parametrize("spellings", SUBJECT_SPELLINGS)
def test_no_spelling_of_the_subject_earns_an_affirmative_clearance(spellings):
    """R11 protects the PERSON, so the grammar of the subject cannot decide the verdict.

    The bar is "not ``keep``" rather than "``exclude``", exactly as the adversarial module
    sets it: ``unsure`` fails closed into ``low_confidence`` and also withholds the fact.
    What R11 forbids is the affirmative clearance, which is final — ``apply_taste`` sends
    only ``unsure`` facts to the classifier, so a ``keep`` never gets a second opinion.
    """
    for sentence in spellings:
        verdict = rule_verdict(sentence)
        assert verdict.decision != "keep", (sentence, verdict)
        if verdict.decision == "exclude":
            assert verdict.reason in R11_CATEGORIES, (sentence, verdict)


@pytest.mark.parametrize("spellings", SUBJECT_SPELLINGS)
def test_every_spelling_of_one_claim_is_ruled_the_same_way(spellings):
    """The invariant behind the fix, asserted as an invariant.

    Not "each of these is withheld" — that is the test above — but "these agree with each
    other". A future edit that repairs the pronoun form of a cue and forgets the named form
    fails here even if it happens to leave both on the withheld side by luck.
    """
    verdicts = {rule_verdict(sentence).decision for sentence in spellings}
    assert len(verdicts) == 1, [(s, rule_verdict(s)) for s in spellings]


def test_a_residence_cue_needs_a_place_and_does_not_fire_on_a_relative_clause():
    """The guard that keeps the residence rule from swallowing a T-069 specimen.

    "…the converted grain silo they live in and the acre of pasture behind it" is a case the
    independent corpus marks ``rule_layer: llm`` — the rule layer must DEFER it, so the
    classifier can read the framing. It differs from "lives in Boulder" only in having no
    object after the preposition, which is why the strong cue requires one.
    """
    silo = (
        "A profile in the Vellacott Review described the converted grain silo they live in "
        "and the acre of pasture behind it."
    )
    assert rule_verdict(silo).decision == "unsure", rule_verdict(silo)
    assert rule_verdict("Brad Feld lives in Boulder, Colorado.").decision == "exclude"


# ------------------------------------------------------------------ 2. the over-block side


def test_the_independent_corpus_still_keeps_every_professional_sentence_it_marks_keep():
    """The direction of error T-069 defended, measured on a corpus this lane cannot write.

    ``tests/fixtures/taste_cases.yaml`` is outside this ticket's ownership and carries a
    ``provenance`` label on every case saying who wrote it. A repair that buys R11 coverage
    by withholding professional facts shows up here as a drop in the surviving count, not as
    a subtle regression somewhere in the product.
    """
    keeps = [case for case in load_cases() if case["expect"] == "keep"]
    assert len(keeps) >= 20, f"only {len(keeps)} keep cases; the corpus is not the one built"

    over_blocked = [
        f"{case['id']}: {case['text']}"
        for case in keeps
        if case["rule_layer"] == "deterministic" and rule_verdict(case["text"]).decision != "keep"
    ]
    assert over_blocked == [], (
        "professional sentences the independent corpus rules KEEP are now withheld:\n"
        + "\n".join(over_blocked)
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "They founded a cancer-imaging startup.",
        "Brad Feld built Overwatch, a home automation dashboard.",
        "They earned a master's in materials science.",
        "Their company purchased a warehouse estate.",
        "A partner at Marram Ventures since 2019.",
        "Brad Feld is a partner at Foundry Group.",
        "The company owns the building its studio occupies.",
        "Brad Feld's homepage lists every talk he has given.",
        "Melanie Perkins's company shipped a design tool for schools.",
    ],
)
def test_the_professional_readings_the_new_cues_could_have_eaten_are_still_kept(sentence):
    """Each of these sits one word away from a cue this ticket added or widened.

    ``home`` inside a product name, ``owns`` with a corporate subject, ``partner`` as a job
    title, a compensation word as the topic of somebody's writing. Principle 3 is what
    separates them, and this is the pin that says it still does.
    """
    assert rule_verdict(sentence).decision == "keep", (sentence, rule_verdict(sentence))


# ------------------------------------------------------- 3. which gate ran, and when


def _fact(text: str, *, excluded: bool = False, quote: str | None = None):
    from arrival.contracts import Fact, Provenance

    return Fact(
        fact_id="p-f0",
        person_id="p",
        text=text,
        category="affiliation",
        excluded=excluded,
        exclusion_reason=None,
        provenance=Provenance(
            doc_id="p-d0",
            url="https://example.invalid/p",
            source_kind="self_page",
            quote=text if quote is None else quote,
            confidence=0.9,
            retrieved_at="2026-09-01T00:00:00Z",
        ),
    )


def test_is_displayable_re_checks_an_exclude_even_when_the_stored_flag_says_otherwise():
    """The clause that makes this fix reach the corpus already committed to the repository.

    ``Fact.excluded`` is frozen into JSON at build time. A dossier written before the cues
    were repaired carries ``excluded: false`` on a sentence that is now a named R11
    violation, and without the re-check it stays on the page until somebody rebuilds.
    """
    stale = _fact("Brad Feld lives in Boulder, Colorado.")
    assert stale.excluded is False, "the premise: the stored flag says this is fine"
    assert rule_verdict(stale.text).decision == "exclude"
    assert is_displayable(stale) is False


def test_is_displayable_does_not_overturn_a_classifier_keep_on_an_unsure_sentence():
    """The other half, and the one that would empty the product if it were wrong.

    An ``unsure`` verdict is the rule layer declining to answer; ``apply_taste`` exists so
    the classifier can then clear the fact with the whole framing in view. Re-deriving
    ``unsure`` at display time would silently discard every one of those rulings.
    """
    rescued = _fact("They spent the year recovering the platform team's velocity.")
    assert rule_verdict(rescued.text).decision == "unsure", rule_verdict(rescued.text)
    assert rescued.excluded is False, "the premise: the classifier cleared it"
    assert is_displayable(rescued) is True


# ------------------------------------------------------------------- T-088: the quote


def test_a_clean_fact_carrying_a_dirty_quote_keeps_the_fact_and_loses_the_quote():
    """T-088. The fact was ruled on its own merits and survives; only the citation goes.

    Suppressing the fact instead would discard a professional claim over a defect in the
    sentence we happened to quote for it.
    """
    dirty = (
        "The psychiatrist treating Ann One told the podcast she was treated for depression "
        "in 2019."
    )
    fact = _fact("Ann One published a guide to engineering management.", quote=dirty)

    assert rule_verdict(fact.text).decision == "keep"
    assert rule_verdict(dirty).decision == "exclude"

    screened = screen_quote(fact)
    assert screened.text == fact.text
    assert screened.provenance.quote == QUOTE_WITHHELD
    assert dirty not in screened.provenance.quote
    assert is_displayable(screened) is True, "the fact itself is untouched"


def test_screening_a_clean_quote_makes_no_copy_at_all():
    """The common case is every fact in the corpus, so it must not allocate a new model."""
    fact = _fact("Ann One published a guide to engineering management.")
    assert screen_quote(fact) is fact


def test_the_taste_layer_still_rules_on_the_fact_text_alone():
    """`screen_quote` is a separate stage on purpose: a dirty quote is not a dirty fact."""
    dirty = "Their psychiatrist of nine years spoke to a podcast about the diagnosis."
    (decided,) = apply_taste_rules([_fact("Ann One ships a scheduling tool.", quote=dirty)])
    assert decided.excluded is False
    assert decided.provenance.quote == dirty, "apply_taste_rules does not redact; it rules"
