"""T-4 acceptance 3 — ``is_displayable``: the R12 gate, three independent clauses.

The three clauses are graded SEPARATELY and each is shown to bite on its own, because the
cheapest wrong implementation of this function is ``return not fact.excluded`` and it
passes any test that only ever varies one input at a time in the excluded direction. A
``fec`` filing can be a perfectly tasteful, high-confidence, non-excluded fact and must
still never reach a screen.
"""

from __future__ import annotations

import typing

import pytest
from t4_corpus import fact_for

from arrival.contracts import SourceKind
from arrival.taste import (
    CONFIDENCE_FLOOR,
    DISPLAYABLE_KINDS,
    NEVER_DISPLAYABLE_KINDS,
    is_displayable,
)

pytestmark = pytest.mark.ticket("T-4")

#: DESIGN §Data models pins this whitelist verbatim. Transcribed here independently so the
#: test fails if the product's copy is widened, rather than agreeing with it by import.
DESIGN_WHITELIST = frozenset(
    {
        "self_page",
        "search",
        "wikidata",
        "wikipedia",
        "github",
        "edgar",
        "uspto",
        "propublica",
        "wayback",
        "hn",
        "openalex",
        "youtube",
        "podcast",
    }
)

_NEUTRAL = {
    "id": "disp-probe",
    "text": "They maintain a widely used open-source scheduler and speak about it at meetups.",
}


def _fact(*, source_kind: str = "self_page", confidence: float = 0.9, excluded: bool = False):
    fact = fact_for(_NEUTRAL, source_kind=source_kind, confidence=confidence)
    if excluded:
        return fact.model_copy(update={"excluded": True, "exclusion_reason": "health"})
    return fact


def test_a_clean_fact_is_displayable() -> None:
    """The baseline the three negative clauses are measured against."""
    assert is_displayable(_fact()) is True


def test_an_excluded_fact_is_never_displayable() -> None:
    fact = _fact(excluded=True)
    assert fact.provenance.confidence >= CONFIDENCE_FLOOR
    assert fact.provenance.source_kind in DISPLAYABLE_KINDS
    assert is_displayable(fact) is False, "clause 1 must bite with the other two satisfied"


def test_low_confidence_alone_blocks_display() -> None:
    fact = _fact(confidence=0.5)
    assert fact.excluded is False
    assert fact.provenance.source_kind in DISPLAYABLE_KINDS
    assert is_displayable(fact) is False, "clause 2 must bite with the other two satisfied"


@pytest.mark.parametrize("kind", sorted(NEVER_DISPLAYABLE_KINDS))
def test_a_non_whitelisted_source_kind_alone_blocks_display(kind: str) -> None:
    fact = _fact(source_kind=kind, confidence=1.0)
    assert fact.excluded is False
    assert is_displayable(fact) is False, (
        f"clause 3 must bite on its own: a tasteful, certain {kind} fact is still not "
        "displayable"
    )


@pytest.mark.parametrize("kind", sorted(DESIGN_WHITELIST))
def test_every_whitelisted_source_kind_is_displayable(kind: str) -> None:
    assert is_displayable(_fact(source_kind=kind)) is True


def test_the_confidence_floor_is_inclusive_at_0_7() -> None:
    """R12 says ``< 0.7`` is withheld, so 0.7 itself displays. The boundary is pinned."""
    assert CONFIDENCE_FLOOR == 0.7
    assert is_displayable(_fact(confidence=0.7)) is True
    assert is_displayable(_fact(confidence=0.6999)) is False
    assert is_displayable(_fact(confidence=0.69)) is False


def test_displayable_kinds_is_exactly_the_design_whitelist() -> None:
    assert set(DISPLAYABLE_KINDS) == set(DESIGN_WHITELIST)


def test_the_two_kind_sets_partition_every_source_kind() -> None:
    """Derived, not hand-listed: a new SourceKind lands on the safe side automatically."""
    all_kinds = set(typing.get_args(SourceKind))
    assert set(DISPLAYABLE_KINDS) | set(NEVER_DISPLAYABLE_KINDS) == all_kinds
    assert not set(DISPLAYABLE_KINDS) & set(NEVER_DISPLAYABLE_KINDS)
    assert set(NEVER_DISPLAYABLE_KINDS) == {"fec", "courtlistener"}
