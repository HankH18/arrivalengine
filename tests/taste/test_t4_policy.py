"""T-4 acceptance 4 — ``EXCLUSION_POLICY`` is a single paragraph naming all six categories.

R13 ships this string with every digest, so it is a product surface, not a docstring: it is
what a member is shown when they ask what the club looked up. Graded on three properties —
it names all six R11 categories in words a reader recognises, it is ONE paragraph (a
bulleted list is a different artefact and the acceptance says paragraph), and it is long
enough to be a real statement rather than a heading.
"""

from __future__ import annotations

import pytest

from arrival.taste import EXCLUSION_POLICY

pytestmark = pytest.mark.ticket("T-4")

#: Any one alternative satisfies its category, so the prose is not over-specified.
POLICY_WORDS = {
    "home_or_property": ("home", "address", "propert"),
    "family": ("family", "families", "spouse", "children", "relationship"),
    "health": ("health", "medical"),
    "legal": ("legal", "litigation", "court", "criminal", "divorce"),
    "wealth": ("wealth", "net worth", "compensation", "salary"),
    "political": ("political", "politics", "donation", "affiliation"),
}


def test_the_policy_names_all_six_r11_categories() -> None:
    text = EXCLUSION_POLICY.lower()
    missing = [
        category
        for category, words in POLICY_WORDS.items()
        if not any(word in text for word in words)
    ]
    assert not missing, (
        f"EXCLUSION_POLICY never names {missing}; R13 shows this paragraph to members and a "
        "category it omits is a promise the product does not make"
    )


def test_the_policy_is_a_single_paragraph() -> None:
    assert isinstance(EXCLUSION_POLICY, str)
    assert "\n" not in EXCLUSION_POLICY, "a paragraph, not a list or a multi-line block"
    assert not EXCLUSION_POLICY.strip().startswith(("-", "*", "•")), "not a bullet"
    assert EXCLUSION_POLICY == EXCLUSION_POLICY.strip(), "no leading or trailing whitespace"


def test_the_policy_is_a_real_statement() -> None:
    words = EXCLUSION_POLICY.split()
    assert 40 <= len(words) <= 250, f"one substantive paragraph, got {len(words)} words"
    assert EXCLUSION_POLICY.rstrip().endswith("."), "it is prose; it ends in a full stop"
