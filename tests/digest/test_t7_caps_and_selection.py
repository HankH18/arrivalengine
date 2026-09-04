"""T-7 acceptance 1 and 4: the caps, the display gate, and what fills each section.

Graded here: ``len(meet) <= 3``, ``len(lately) <= 3``, Lately ordered by ``published_at``
descending and holding only ``is_displayable`` facts, ``non_obvious`` chosen per the DESIGN
eligibility list or ``None``, ``who_line`` built from ``current_work`` facts, and R8's empty
Meet represented rather than padded.
"""

from __future__ import annotations

import datetime as dt

import pytest
from t7_digest_helpers import (
    fact_of,
    load,
    make_match,
    promoted_to_freshest,
    replacing,
    variant,
    with_facts,
)

from arrival import taste
from arrival.digest import LATELY_CAP, MEET_CAP, make_digest, pick_lately, pick_non_obvious
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")

OPENER = "Ask about the evaluation harness and what nine months of rubric work bought."


def _llm(line: str = OPENER, **kwargs) -> LLMDouble:
    double = LLMDouble(**kwargs)
    double.queue({"line": line})
    return double


@pytest.fixture
def alpha():
    return load("alpha")


@pytest.fixture
def peers():
    return [load("bravo"), load("charlie"), load("delta")]


def _matches(alpha, peers):
    """Four present peers against a cap of three, highest first."""
    bravo, charlie, delta = peers
    return [
        make_match(alpha, bravo, score=100.0, why="Both work on machine learning in Austin.",
                   hub_id="company:northgate-labs"),
        make_match(alpha, charlie, score=64.0, why="Both build evaluation harnesses.",
                   hub_id="technology:evaluation-harnesses"),
        make_match(alpha, delta, score=12.0, why="Both are in Austin tonight."),
        make_match(alpha, load("bravo"), score=12.0, why="Nothing in common on the record yet."),
    ]


async def test_caps_and_selection(alpha, peers):
    """The whole of acceptance 1 on the fixture dossier that carries excluded facts."""
    digest = await make_digest(alpha, _matches(alpha, peers), _llm())

    assert len(digest.meet) == MEET_CAP, "four peers were offered against a cap of three"
    assert [m.score for m in digest.meet] == sorted(
        (m.score for m in digest.meet), reverse=True
    ), "Meet is not highest-score-first"

    assert len(digest.lately) <= LATELY_CAP
    dates = [f.provenance.published_at for f in digest.lately]
    assert all(d is not None for d in dates), "an undatable fact reached a most-recent-first list"
    assert dates == sorted(dates, reverse=True), f"Lately is not most-recent-first: {dates}"
    for fact in digest.lately:
        assert taste.is_displayable(fact), f"{fact.fact_id} is shown but fails R12"

    assert digest.non_obvious is not None
    assert digest.non_obvious.fact_id == "alpha-nonobvious"
    assert digest.non_obvious not in digest.lately, "the non-obvious find doubled as a bullet"

    assert "Northgate Labs" in digest.who_line, "who_line carries no current_work material"
    assert digest.person.person_id == "alpha"
    assert digest.exclusion_policy == taste.EXCLUSION_POLICY
    assert digest.created_at.tzinfo is not None, "created_at is naive"
    assert digest.digest_id, "a digest with no id cannot be fetched back by /digest/{id}"


async def test_meet_never_exceeds_the_cap_however_many_are_present(alpha, peers):
    many = _matches(alpha, peers) * 3
    digest = await make_digest(alpha, many, _llm())
    assert len(digest.meet) == MEET_CAP


async def test_empty_building(alpha):
    """R8: nobody else present means an empty Meet, and a digest that is otherwise whole."""
    digest = await make_digest(alpha, [], _llm())

    assert digest.meet == [], "Meet was padded when nobody else was present"
    assert digest.who_line.strip(), "who_line went empty"
    assert digest.say_out_loud.strip(), "say_out_loud went empty"
    assert digest.lately, "Lately collapsed along with Meet"
    assert digest.exclusion_policy == taste.EXCLUSION_POLICY


async def test_excluded_facts_never_reach_a_section_even_when_they_are_the_freshest(alpha, peers):
    """R11: `excluded` is not survivable by being recent.

    Ordering must not be what hides the withheld material, so the two excluded fixture
    facts are re-dated to the front of the queue first.
    """
    withheld = {"alpha-excluded-address", "alpha-excluded-family"}
    promoted = promoted_to_freshest(alpha, withheld)

    digest = await make_digest(promoted, _matches(promoted, peers), _llm())

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    assert not {f.fact_id for f in shown} & withheld, "an excluded fact reached the digest"
    assert digest.lately, "positive control: nothing was shown, so nothing was proven"
    surface = " ".join([digest.who_line, digest.say_out_loud] + [f.text for f in shown])
    assert "Quarrystone Lane" not in surface
    assert "Delia Moreno-Vance" not in surface


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", 0.55),  # R12 clause 2: below the 0.7 floor
        ("source_kind", "fec"),  # R12 clause 3: off the display whitelist
    ],
)
async def test_r12_clauses_bite_independently_of_the_taste_filter(alpha, peers, field, value):
    """R12's three clauses are independent: a tasteful fact can still be undisplayable.

    The fixtures carry no low-confidence and no ``fec`` fact, so each is produced here by
    copying the freshest displayable fact and changing exactly one field. ``excluded``
    stays False throughout — that is the whole point of the check.

    The variant also gets a document of its own. ``alpha-recent`` shares
    ``b1159ac929dac1e6`` with ``alpha-hook``, so a doc-level assertion against the shared id
    would fail on a CORRECT build: the citation list is derived from facts, and the hook is
    still shown and still entitled to cite its source.
    """
    doomed = variant(alpha_recent := fact_of(alpha, "alpha-recent"), doc_id="00000000000000ff",
                     **{field: value})
    assert doomed.provenance.doc_id != alpha_recent.provenance.doc_id
    assert doomed.excluded is False
    assert not taste.is_displayable(doomed)
    dossier = replacing(alpha, {"alpha-recent": doomed})

    digest = await make_digest(dossier, _matches(dossier, peers), _llm())

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    assert "alpha-recent" not in {f.fact_id for f in shown}
    assert doomed.provenance.doc_id not in {p.doc_id for p in digest.sources}


async def test_non_obvious_is_none_when_no_eligible_fact_exists():
    """R7: "Not on the first page" is honestly empty rather than filled with a near miss."""
    bravo = load("bravo")
    assert not [f for f in bravo.facts if f.category == "non_obvious"]

    digest = await make_digest(bravo, [], _llm())

    assert digest.non_obvious is None


def test_non_obvious_requires_the_source_kind_whitelist_not_just_the_category():
    """DESIGN eligibility is category AND source kind; ``search`` is a first page."""
    alpha = load("alpha")
    from_search = variant(fact_of(alpha, "alpha-nonobvious"), source_kind="search")
    assert taste.is_displayable(from_search), "the variant must fail on eligibility, not R12"

    assert pick_non_obvious(replacing(alpha, {"alpha-nonobvious": from_search})) is None


def test_non_obvious_picks_the_highest_confidence_eligible_fact():
    alpha = load("alpha")
    weaker = variant(fact_of(alpha, "alpha-nonobvious"), confidence=0.75)
    stronger = variant(
        weaker,
        fact_id="alpha-nonobvious-2",
        confidence=0.95,
        doc_id="0000000000000001",
    )
    dossier = with_facts(alpha, [*alpha.facts, stronger])
    dossier = replacing(dossier, {"alpha-nonobvious": weaker})

    chosen = pick_non_obvious(dossier)

    assert chosen is not None
    assert chosen.fact_id == "alpha-nonobvious-2"


def test_lately_skips_facts_with_no_publication_date():
    """A most-recent-first list cannot hold a fact with no date to sort it by."""
    alpha = load("alpha")
    undated = variant(fact_of(alpha, "alpha-recent"), published_at=None)
    assert taste.is_displayable(undated)

    lately = pick_lately(replacing(alpha, {"alpha-recent": undated}))

    assert "alpha-recent" not in {f.fact_id for f in lately}


def test_lately_prefers_recent_activity_and_only_then_tops_the_list_up():
    """R7 names "most recent professional activity"; other categories only fill a gap.

    ``delta`` carries exactly one ``recent_activity`` fact, so the top-up is the only way
    its Lately reaches three - and the recent_activity fact must still lead when it is the
    newest, which here it is.
    """
    delta = load("delta")
    recent = [f for f in delta.facts if f.category == "recent_activity"]
    assert len(recent) == 1, "fixture changed: this test needs a short primary pool"

    lately = pick_lately(delta)

    assert len(lately) == LATELY_CAP, "Lately was left short while true facts remained"
    assert lately[0].fact_id == "delta-recent"
    assert [f.provenance.published_at for f in lately] == sorted(
        (f.provenance.published_at for f in lately), reverse=True
    )


def test_lately_never_repeats_a_fact_already_shown_elsewhere():
    alpha = load("alpha")
    non_obvious = pick_non_obvious(alpha)
    assert non_obvious is not None

    lately = pick_lately(alpha, exclude=[non_obvious])

    assert non_obvious.fact_id not in {f.fact_id for f in lately}


def test_lately_orders_a_same_day_tie_deterministically():
    """Two facts on one date must not reorder between runs; the tie breaks on confidence."""
    alpha = load("alpha")
    same_day = dt.date(2026, 7, 2)
    low = variant(fact_of(alpha, "alpha-recent"), published_at=same_day, confidence=0.80)
    high = variant(
        low,
        fact_id="alpha-recent-2",
        confidence=0.95,
        doc_id="0000000000000002",
    )
    dossier = with_facts(alpha, [*alpha.facts, high])
    dossier = replacing(dossier, {"alpha-recent": low})

    first = [f.fact_id for f in pick_lately(dossier)]
    second = [f.fact_id for f in pick_lately(dossier)]

    assert first == second
    assert first.index("alpha-recent-2") < first.index("alpha-recent")


async def test_meet_holds_one_row_per_person(alpha, peers):
    """R7 caps Meet at three present PEOPLE, not three Match objects.

    A duplicate row for one person would be padding with extra steps: the host reads the
    same name twice and a genuinely different peer loses the slot. The highest-scoring row
    for a person is the one kept.
    """
    bravo = peers[0]
    matches = [
        make_match(alpha, bravo, score=40.0, why="Both work on machine learning in Austin."),
        make_match(alpha, bravo, score=90.0, why="Both build evaluation harnesses."),
        make_match(alpha, peers[1], score=30.0, why="Both are in Austin tonight."),
    ]

    digest = await make_digest(alpha, matches, _llm())

    ids = [m.other.person_id for m in digest.meet]
    assert len(ids) == len(set(ids)), f"Meet lists the same person twice: {ids}"
    assert len(digest.meet) == 2, "a duplicate row displaced a genuinely different peer"
    kept = next(m for m in digest.meet if m.other.person_id == bravo.person.person_id)
    assert kept.score == 90.0, "the lower-scoring duplicate row was the one kept"
