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

    TOTAL ON PURPOSE (T-067).  This used to be a bare `url.encode()`, which is utf-8 in
    STRICT mode and therefore raises `UnicodeEncodeError` on a lone surrogate.  That is
    not a theoretical input: a JSON body containing the escape `"\\ud800"` is decoded by
    `json.loads` into a real lone surrogate, and a url read out of such a payload — or
    out of scraped page text — reaches here.  Three of this function's four call paths
    were unguarded (`http/cache.py:cache_path`, `http/cache.py:write_record` and
    `http/client.py:fetch_text` all call it OUTSIDE their own try blocks), so the raise
    escaped `fetch_text`, which DESIGN Decision 8 says degrades and never raises.

    `surrogatepass` is chosen over hardening those three call sites because it changes
    NOTHING about this function's answer: it differs from strict only on surrogate code
    points, which strict refuses outright, so every id this function has ever returned it
    still returns byte for byte.  It only DEFINES a return where there used to be an
    exception, which is what a join key for the whole pipeline has to have.  Hardening
    the callers instead would leave the fourth (and any future) caller broken and would
    give one url two different ids depending on who asked.

    >>> doc_id("https://example.com/")
    'b559c7edd3fb6737'
    """
    return hashlib.sha1(url.encode("utf-8", "surrogatepass")).hexdigest()[:16]
