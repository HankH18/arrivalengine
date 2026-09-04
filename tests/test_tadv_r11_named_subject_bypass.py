"""R11 route: the rule layer's home/family cues are anchored to PRONOUNS, and this
corpus writes every fact with a PROPER NAME.

**The mechanism.** ``taste.rule_verdict`` decides a sentence in three passes: a strong R11
marker excludes, a defer cue makes it ``unsure``, a weak cue makes it ``unsure``, and a
sentence carrying none of the three is ``keep``. ``apply_taste`` then sends only the
``unsure`` facts to the classifier — so a ``keep`` is a FINAL, affirmative decision that
never gets a second opinion, and the documented "fails closed" property does not apply to
it.

Two of the six categories spell their cues with a pronoun in them. ``home_or_property``
matches ``\\b(?:they|he|she)\\s+(?:live|lives|lived)\\s+in\\b``; ``family`` matches
possessives like ``their wife``, ``his son``. Health, legal, wealth and political are
anchored on nouns instead (``cancer``, ``divorce``, ``net worth``, ``registered
Democrat``) and are unaffected — which is exactly why this went unnoticed.

But an extractor writing facts about a named public figure produces "Brad Feld lives in
Boulder, Colorado and Homer, Alaska.", not "He lives in…". Swapping the pronoun for the
member's own name turns a hard ``exclude`` into a ``keep`` in every home/family pair
below. The two categories where the shipped corpus actually contains violations are
precisely the two the corpus's own house style defeats.

**It is not hypothetical.** :func:`test_no_displayable_fact_states_where_a_member_lives`
and :func:`test_no_displayable_fact_names_a_members_partner_child_or_friend` run these
shapes over the ten REAL dossiers at ``data/dossiers/`` and find eight displayable facts
today, among them a sentence naming the two towns a living person lives in (which also
mints the ``city:`` hubs published on ``/graph`` and ``/corpus``) and one naming a
member's four homes.

On the strict-xfail markers, see the module docstring of
``test_tadv_r11_hub_label_bypass.py``: nothing existing is relaxed here, and a fix turns
these RED so the marker has to go.
"""

from __future__ import annotations

import re

import pytest

from arrival.taste import R11_CATEGORIES, is_displayable, rule_verdict
from tadv_corpus import committed_dossiers

pytestmark = pytest.mark.ticket("TESTADVERSARY")


#: ``(category, pronoun form, proper-name form)``. Both members of a pair make the same
#: claim about the same person; only the subject's spelling differs.
PRONOUN_AND_NAME_PAIRS = [
    ("home_or_property", "He lives in Boulder, Colorado.", "Brad Feld lives in Boulder, Colorado."),
    ("home_or_property", "Their home is in Aspen.", "Brad Feld's home is in Aspen."),
    ("home_or_property", "He owns a home in Aspen.", "Brad Feld owns a home in Aspen."),
    ("family", "His wife runs the foundation.", "Brad Feld's wife runs the foundation."),
    (
        "family",
        "Her husband runs the foundation.",
        "Melanie Perkins's husband runs the foundation.",
    ),
    ("family", "His son was born last year.", "Brad Feld's son was born last year."),
]

#: The same pairs for the four categories that are anchored on nouns. These must survive
#: the rename — they are the control that shows the defect is about the CUE SPELLING and
#: not about proper names being unrecognisable in general.
NOUN_ANCHORED_PAIRS = [
    ("health", "He was treated for depression.", "Brad Feld was treated for depression."),
    ("legal", "He filed for divorce in 2021.", "Brad Feld filed for divorce in 2021."),
    ("wealth", "His net worth is $3.1 billion.", "Brad Feld's net worth is $3.1 billion."),
    ("political", "He is a registered Democrat.", "Brad Feld is a registered Democrat."),
]


# --------------------------------------------------------------------------- what works

@pytest.mark.parametrize(
    ("category", "pronoun_form"),
    [(c, p) for c, p, _ in PRONOUN_AND_NAME_PAIRS + NOUN_ANCHORED_PAIRS],
)
def test_the_pronoun_form_is_withheld(category, pronoun_form):
    """Locks the cue set that DOES work, so a later edit cannot quietly erode it.

    The bar is "not ``keep``", not "``exclude``". Both of the other verdicts withhold the
    fact — ``unsure`` fails closed into ``low_confidence`` — and which one a given cue
    earns is the rule layer's business. What R11 forbids is the AFFIRMATIVE clearance.
    """
    verdict = rule_verdict(pronoun_form)
    assert verdict.decision != "keep", (pronoun_form, verdict)
    if verdict.decision == "exclude":
        assert verdict.reason == category, (pronoun_form, verdict)
        assert verdict.reason in R11_CATEGORIES


@pytest.mark.parametrize(("category", "_pronoun", "name_form"), NOUN_ANCHORED_PAIRS)
def test_noun_anchored_categories_survive_a_proper_name_subject(category, _pronoun, name_form):
    """The control. Health, legal, wealth and political do not depend on a pronoun."""
    verdict = rule_verdict(name_form)
    assert verdict.decision == "exclude", (name_form, verdict)
    assert verdict.reason == category, (name_form, verdict)


def test_a_keep_is_final_and_never_reaches_the_classifier():
    """Why a `keep` is expensive: `apply_taste` only sends `unsure` facts to the LLM.

    Pinning the premise the two xfails below rest on — that these sentences are not merely
    unreviewed by the rules but affirmatively cleared, with the fail-closed path bypassed.
    """
    assert rule_verdict("Brad Feld lives in Boulder, Colorado.").decision == "keep"
    assert rule_verdict("He lives in Boulder, Colorado.").decision != "keep"


# --------------------------------------------------------------------------- what leaks

@pytest.mark.xfail(
    strict=True,
    reason="OPEN R11 DEFECT: home_or_property and family cues are pronoun-anchored, so "
    "naming the member defeats them. Remove this marker when it is fixed.",
)
@pytest.mark.parametrize(
    ("category", "pronoun_form", "name_form"),
    PRONOUN_AND_NAME_PAIRS,
    ids=[f"{c}-{i}" for i, (c, _, _) in enumerate(PRONOUN_AND_NAME_PAIRS)],
)
def test_naming_the_member_does_not_change_the_r11_verdict(category, pronoun_form, name_form):
    """R11 protects the PERSON. How the sentence spells its subject cannot matter."""
    assert rule_verdict(pronoun_form).decision != "keep", "premise: pronoun form is withheld"
    verdict = rule_verdict(name_form)
    assert verdict.decision != "keep", (category, name_form, verdict)


#: Sentence shapes that can only be R11 material, whatever spells the subject. Each is
#: kept narrow enough that a professional sentence cannot match it: "lives in" is a
#: residence claim, "owns a home/houses" is a property claim, and the family shapes name a
#: specific relation rather than the word "family".
WHERE_THEY_LIVE = [
    (r"\blives?\s+in\b", "states where the member lives"),
    (
        r"\bowns?\s+(?:a\s+)?(?:home|homes|house|houses|apartment|condo|ranch|estate)\b",
        "states what residential property the member owns",
    ),
    (
        r"\b(?:for|across|in)\s+multiple\s+(?:homes|houses)\b",
        "discloses that the member keeps several homes",
    ),
]

WHO_THEY_ARE_CLOSE_TO = [
    (r"\bbirth\s+of\s+(?:his|her|their|my)\b", "names the birth of the member's child"),
    (
        r"\b(?:boyfriend|girlfriend|fianc[ée]e?|spouse|wife|husband)\b",
        "names the member's partner",
    ),
    (r"\bis\s+friends\s+with\b", "names who the member's friends are"),
]


def _offenders(shapes):
    found = []
    for dossier in committed_dossiers():
        for fact in dossier.facts:
            if not is_displayable(fact):
                continue
            for pattern, description in shapes:
                if re.search(pattern, fact.text, re.IGNORECASE):
                    found.append(f"{dossier.person.person_id} [{description}]: {fact.text}")
    return found


def test_the_committed_corpus_is_not_empty():
    """Positive control: the two corpus checks below would pass vacuously on no data."""
    dossiers = committed_dossiers()
    assert len(dossiers) >= 10, dossiers
    assert any(is_displayable(f) for d in dossiers for f in d.facts)


@pytest.mark.xfail(
    strict=True,
    reason="OPEN R11 DEFECT: displayable facts in data/dossiers/ state where living "
    "members live and what property they own. Remove this marker when it is fixed.",
)
def test_no_displayable_fact_states_where_a_member_lives():
    """R11: "their home address, property records or where they live" — never displayed."""
    offenders = _offenders(WHERE_THEY_LIVE)
    assert offenders == [], "\n" + "\n".join(offenders)


@pytest.mark.xfail(
    strict=True,
    reason="OPEN R11 DEFECT: displayable facts in data/dossiers/ name a member's partner, "
    "child and friends. Remove this marker when it is fixed.",
)
def test_no_displayable_fact_names_a_members_partner_child_or_friend():
    """R11: "their family, spouse, children or personal relationships" — never displayed."""
    offenders = _offenders(WHO_THEY_ARE_CLOSE_TO)
    assert offenders == [], "\n" + "\n".join(offenders)
