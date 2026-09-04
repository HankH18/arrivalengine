"""The three shared primitives.

These are the ONLY copies in the repo (EXECUTION §4). A ticket that finds itself writing a
slug, a whitespace normaliser or a url hash imports from here instead of forking one — two
implementations of `slug` means two spellings of every `hub_id`, and the graph stops
joining people who should join.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = ["doc_id", "normalize_ws", "slug"]

# Apostrophes are DELETED rather than turned into a separator, so "O'Neil" -> "oneil"
# and not "o-neil". ASCII ', typographic ’, modifier ʼ, backtick and acute accent.
_APOSTROPHES = "'’ʼ`´"
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def slug(s: str) -> str:
    """Lowercase, strip accents and apostrophes, non-alphanumerics to "-", collapse, trim.

    >>> slug("Jane O'Neil-Ruiz")
    'jane-oneil-ruiz'
    >>> slug("  Foundry Seed 2019  ")
    'foundry-seed-2019'
    >>> slug("José Ángel Núñez")
    'jose-angel-nunez'
    """
    decomposed = unicodedata.normalize("NFKD", s)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    depunctuated = "".join(ch for ch in unaccented if ch not in _APOSTROPHES)
    return _NON_SLUG.sub("-", depunctuated.lower()).strip("-")


def normalize_ws(s: str) -> str:
    """Collapse every whitespace run to one space, strip, and casefold.

    This is the normalisation the citation check runs on both sides (DESIGN Decision 5):
    a quote counts as cited when `normalize_ws(quote) in normalize_ws(doc.text)`.

    >>> normalize_ws("A  b\\nC")
    'a b c'
    """
    return _WHITESPACE.sub(" ", s).strip().casefold()


def doc_id(url: str) -> str:
    """Stable document id: the first 16 hex chars of sha1(url).

    Not a security hash — it is a short, stable cache filename and join key.

    >>> doc_id("https://example.com/")
    'b559c7edd3fb6737'
    """
    return hashlib.sha1(url.encode()).hexdigest()[:16]
