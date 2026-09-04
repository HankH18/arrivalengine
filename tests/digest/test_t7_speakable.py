"""T-7 acceptance 5 (R18): the three spoken lines read aloud as written.

``who_line``, every ``meet[*].why`` and ``say_out_loud`` carry no ``http``, no ``[n]``
citation marker, no parentheses, no digits used as a score, and at most thirty words.

The two halves of that are handled differently on purpose, and this module grades the
difference: a ``why`` arrives from the matcher and is REPAIRED on a copy, while a
``Fact`` is shown verbatim or skipped — never edited — because a sentence quietly reworded
next to a citation is no longer the thing the citation vouches for.
"""

from __future__ import annotations

import pytest
from t7_digest_helpers import fact_of, load, make_match, replacing, variant

from arrival.digest import SPOKEN_WORD_CAP, is_speakable, make_digest, speakable, who_line_for
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")

OPENER = "Ask about the nine months of rubric work before the first line of code."


def _llm() -> LLMDouble:
    double = LLMDouble()
    double.queue({"line": OPENER})
    return double


def _spoken(digest):
    return [digest.who_line] + [m.why for m in digest.meet] + [digest.say_out_loud]


@pytest.fixture
def alpha():
    return load("alpha")


async def test_speakable(alpha):
    """Every spoken line on a normally-built digest satisfies R18."""
    matches = [
        make_match(alpha, load("bravo"), score=100.0, hub_id="company:northgate-labs",
                   why="Both work on machine learning in Austin."),
        make_match(alpha, load("charlie"), score=40.0, why="Both build evaluation harnesses."),
    ]

    digest = await make_digest(alpha, matches, _llm())

    lines = _spoken(digest)
    assert len(lines) >= 3, "positive control: there is nothing to read aloud"
    for line in lines:
        assert line.strip(), "a spoken line is empty"
        assert is_speakable(line), f"not speakable as written: {line!r}"


async def test_an_unspeakable_why_is_repaired_without_mutating_the_match(alpha):
    """R18 binds on a Meet row even when the matcher was careless.

    T-5 owns ``why``; T-7 owns what reaches the host's mouth. The repair is a copy, so the
    caller's ``Match`` — and anything else holding it — is untouched.
    """
    rough = (
        "Both came up through Foundry Seed (the 2019 cohort) [1], see "
        "https://example.com/foundry, and both score 67."
    )
    match = make_match(alpha, load("bravo"), score=100.0, why=rough)

    digest = await make_digest(alpha, [match], _llm())

    assert digest.meet, "the Meet row was dropped instead of repaired"
    repaired = digest.meet[0].why
    assert is_speakable(repaired), f"still unspeakable: {repaired!r}"
    assert "Foundry Seed" in repaired, "the repair threw away the reasoning as well as the noise"
    assert match.why == rough, "the incoming Match was mutated in place"


async def test_a_speakable_why_is_passed_through_untouched(alpha):
    """R10's exposed reasoning survives: nothing is rewritten that did not need it."""
    why = "Both came up through the Foundry Seed 2019 fund."
    match = make_match(alpha, load("bravo"), score=100.0, why=why)

    digest = await make_digest(alpha, [match], _llm())

    assert digest.meet[0].why == why


async def test_a_why_that_repairs_to_nothing_still_yields_a_spoken_line(alpha):
    match = make_match(alpha, load("bravo"), score=100.0, why="(see https://example.com/x) [1]")

    digest = await make_digest(alpha, [match], _llm())

    assert digest.meet[0].why.strip(), "a Meet row was left with nothing to say"
    assert is_speakable(digest.meet[0].why)


def test_who_line_skips_an_unspeakable_fact_rather_than_editing_it(alpha):
    """Facts are shown verbatim. A fact R18 cannot accept is dropped, never reworded."""
    rough_text = "He is CTO of Northgate Labs (an Austin lab) and posts at https://example.com."
    rough = variant(fact_of(alpha, "alpha-work"), text=rough_text)
    dossier = replacing(alpha, {"alpha-work": rough})

    line, used = who_line_for(dossier)

    assert is_speakable(line), f"an unspeakable fact reached who_line: {line!r}"
    assert "Northgate Labs (an Austin lab)" not in line, "the fact was edited instead of skipped"
    assert "alpha-work" not in {f.fact_id for f in used}
    assert line.strip(), "who_line went empty rather than falling back"


def test_who_line_falls_back_to_the_roster_when_no_current_work_fact_is_usable(alpha):
    """R8's "still a digest" property: the Who line is never blank."""
    from t7_digest_helpers import with_facts

    dossier = with_facts(alpha, [f for f in alpha.facts if f.category != "current_work"])

    line, used = who_line_for(dossier)

    assert used == [], "a fact was cited that is not a current_work fact"
    assert alpha.person.name in line
    assert is_speakable(line)


def test_who_line_stays_inside_the_word_cap_by_selecting_not_truncating(alpha):
    """A second current_work fact is included only when it fits whole."""
    long_text = " ".join(["harnesses"] * (SPOKEN_WORD_CAP + 5)) + "."
    bulky = variant(fact_of(alpha, "alpha-work"), fact_id="alpha-work-2", text=long_text,
                    doc_id="00000000000000ab")
    from t7_digest_helpers import with_facts

    dossier = with_facts(alpha, [*alpha.facts, bulky])

    line, used = who_line_for(dossier)

    assert len(line.split()) <= SPOKEN_WORD_CAP
    assert "alpha-work-2" not in {f.fact_id for f in used}
    assert long_text[:40] not in line, "an over-long fact was truncated into the line"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "See https://example.com/x for more.",
        "Both build harnesses [2].",
        "Both build harnesses (the open source kind).",
        "Both build harnesses, scoring 67.",
        " ".join(["word"] * (SPOKEN_WORD_CAP + 1)),
    ],
)
def test_is_speakable_rejects_what_r18_forbids(line):
    assert not is_speakable(line)


@pytest.mark.parametrize(
    "line",
    [
        "Both came up through the Foundry Seed 2019 fund.",
        "Co-founded Quarrystone Labs in 2016 and runs its platform team.",
        "Ask about the sixty-three commits that landed last week.",
        " ".join(["word"] * SPOKEN_WORD_CAP),
    ],
)
def test_is_speakable_accepts_ordinary_prose_including_years(line):
    """A year is not a score. R18 bans numbers-as-scores, not every digit."""
    assert is_speakable(line)


@pytest.mark.parametrize(
    "rough",
    [
        "Both build harnesses (the open source kind) [3] at https://example.com.",
        "Both build harnesses, scoring 67 tonight.",
        " ".join(["word"] * (SPOKEN_WORD_CAP + 20)),
    ],
)
def test_speakable_repairs_anything_is_speakable_rejects(rough):
    repaired = speakable(rough)
    assert repaired.strip()
    assert is_speakable(repaired), f"repair left it unspeakable: {repaired!r}"


@pytest.mark.parametrize(
    "line",
    [
        "Both shipped 12 open-source developer tools together.",
        "Both sit on 2 nonprofit boards.",
        "Both deep in Web 2 standards work.",
        "Both came up through the Foundry Seed 2019 fund.",
    ],
)
def test_a_count_is_not_a_score(line):
    """R18 bans a number used AS A SCORE, which is narrower than "a number".

    An earlier rule here treated every bare integer in 0..100 as a score, which deleted
    content from the matcher's own exposed reasoning (R10): "Both shipped 12 open-source
    developer tools together" came out as "Both shipped open-source developer tools
    together". A count, a board seat and a standards version are prose.
    """
    assert is_speakable(line)
    assert speakable(line) == line, "a legitimate number was stripped out of a Meet why"


@pytest.mark.parametrize(
    "line",
    [
        "Both build harnesses, scoring 67 tonight.",
        "Both rated 88 on the shared-hub ranking.",
        "Both matched at 88/100 tonight.",
        "Both scored 100 percent on the same hub.",
    ],
)
def test_a_number_that_announces_itself_as_a_score_is_still_rejected(line):
    """The narrowing must not turn the rule off: score vocabulary next to a digit bites."""
    assert not is_speakable(line)
    assert is_speakable(speakable(line))


def test_repairing_a_line_does_not_strand_its_punctuation():
    """A removed token used to leave "Y Combinator ." — a stumble for the host."""
    repaired = speakable("Both came through Y Combinator (W16).")
    assert repaired == "Both came through Y Combinator."


def test_who_line_leads_with_the_most_confident_current_work_fact():
    """Two current_work sentences are ordered by confidence, not by fixture order."""
    from t7_digest_helpers import with_facts

    alpha = load("alpha")
    strong = fact_of(alpha, "alpha-work")
    weak = variant(
        strong,
        fact_id="alpha-work-weak",
        text="He also keeps the Northgate build pipeline green.",
        confidence=0.71,
        doc_id="00000000000000c1",
    )
    dossier = with_facts(alpha, [weak, *alpha.facts])

    line, used = who_line_for(dossier)

    assert [f.fact_id for f in used] == ["alpha-work", "alpha-work-weak"]
    assert line.index(strong.text) < line.index(weak.text)


def test_who_line_is_never_empty_even_for_an_empty_record():
    """R8's "still a digest" property survives a record with no name and no details."""
    from t7_digest_helpers import with_facts

    from arrival.digest import WHO_OF_LAST_RESORT

    alpha = load("alpha")
    nameless = with_facts(alpha, []).model_copy(
        update={
            "person": alpha.person.model_copy(
                update={"name": "  ", "details": [], "person_id": ""}
            )
        }
    )

    line, used = who_line_for(nameless)

    assert used == []
    assert line == WHO_OF_LAST_RESORT
    assert is_speakable(line)


def test_the_roster_fallback_is_sentence_cased():
    """A roster reads "CTO of Northgate Labs"; a lowercase detail is a stumble on the page."""
    from t7_digest_helpers import with_facts

    alpha = load("alpha")
    dossier = with_facts(alpha, []).model_copy(
        update={"person": alpha.person.model_copy(update={"details": ["cto of northgate labs"]})}
    )

    line, used = who_line_for(dossier)

    assert used == []
    assert line == "Teodoro Vance. Cto of northgate labs."


def test_a_repaired_why_states_an_absence_rather_than_inventing_a_reason():
    """R7 wants a why that names the shared thing; when none survives, none is invented."""
    from arrival.digest import WHY_OF_LAST_RESORT

    assert is_speakable(WHY_OF_LAST_RESORT)
    assert "shared" not in WHY_OF_LAST_RESORT.casefold()
