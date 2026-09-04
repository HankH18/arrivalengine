"""T-0 acceptance 2: the three shared primitives, with the pinned examples."""

from __future__ import annotations

import hashlib

import pytest

from arrival.util import doc_id, normalize_ws, slug

pytestmark = pytest.mark.ticket("T-0")


# --- slug -----------------------------------------------------------------


def test_slug_pinned_example():
    assert slug("Jane O'Neil-Ruiz") == "jane-oneil-ruiz"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Foundry Seed 2019", "foundry-seed-2019"),
        ("  Northgate  Labs  ", "northgate-labs"),
        ("AI", "ai"),
        ("Austin", "austin"),
        ("José Ángel Núñez", "jose-angel-nunez"),  # accents stripped, not dropped
        ("Jane O’Neil", "jane-oneil"),  # typographic apostrophe deleted, space still separates
        ("---Hello---", "hello"),  # separators trimmed at both ends
        ("R&D / Ops", "r-d-ops"),  # runs of punctuation collapse to one hyphen
        ("Ångström Labs", "angstrom-labs"),
    ],
)
def test_slug_cases(raw, expected):
    assert slug(raw) == expected


def test_slug_is_idempotent():
    once = slug("Jane O'Neil-Ruiz")
    assert slug(once) == once


def test_slug_of_empty_is_empty():
    assert slug("") == ""
    assert slug("!!!") == ""


# --- normalize_ws ---------------------------------------------------------


def test_normalize_ws_pinned_example():
    assert normalize_ws("A  b\nC") == "a b c"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  leading and trailing  ", "leading and trailing"),
        ("tabs\tand\r\nnewlines", "tabs and newlines"),
        ("MiXeD CaSe", "mixed case"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_ws_cases(raw, expected):
    assert normalize_ws(raw) == expected


def test_normalize_ws_makes_the_citation_check_work():
    """DESIGN Decision 5: a quote is cited when it is a normalised substring of the text."""
    text = "Northgate Labs released an open\n  source evaluation harness this week."
    quote = "released an OPEN source   evaluation harness"
    assert normalize_ws(quote) in normalize_ws(text)


def test_normalize_ws_is_idempotent():
    once = normalize_ws("  A  b\nC  ")
    assert normalize_ws(once) == once


# --- doc_id ---------------------------------------------------------------


def test_doc_id_is_sha1_prefix():
    url = "https://northgatelabs.example/team/teodoro-vance"
    assert doc_id(url) == hashlib.sha1(url.encode()).hexdigest()[:16]


def test_doc_id_shape_and_stability():
    d = doc_id("https://example.com/")
    assert len(d) == 16
    assert all(c in "0123456789abcdef" for c in d)
    assert d == doc_id("https://example.com/")


def test_doc_id_discriminates():
    assert doc_id("https://a.example/") != doc_id("https://b.example/")
    # not normalised: a trailing slash is a different document id
    assert doc_id("https://a.example") != doc_id("https://a.example/")


def test_doc_id_handles_non_ascii_urls():
    assert len(doc_id("https://example.com/José")) == 16
