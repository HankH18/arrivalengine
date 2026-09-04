"""The "fully cited" gate grades a quote against an ABRIDGED capture, not against the page.

``tests/test_t9_committed_dossiers.py`` calls its citation check "the citation the whole
product rests on": for every displayable fact, ``normalize_ws(fact.provenance.quote)``
must be a substring of ``normalize_ws(doc.text)`` for the ``RawDoc`` committed at
``data/docs/{doc_id}.json``. That check is green today, and it proves less than it looks
like it proves — because the ``RawDoc`` and the ``Fact`` came out of the same pipeline
run. The document is not independent evidence for the quote; it is the other half of the
same output. The gate therefore establishes internal consistency and **not** that the
sentence appears on the page the citation points a host at.

**The evidence that this is not theoretical, and it is checkable offline.** 44 of the 127
committed ``RawDoc``s carry a literal ``[...]`` elision marker inside ``text`` —
``https://startupsusa.org/people/brad-feld`` reads "…the next generation of venture fund
managers. **[...]** Brad has been an early stage investor…". ``RawDoc.text`` is
contractually "extracted plain text" (``contracts.py``), the only truncation policy in the
codebase is ``http.extract.clip``, which trims the TAIL on a word boundary and never
inserts anything, and no code anywhere under ``src/`` writes ``[...]``. So those documents
were abridged after extraction, and the quotes checked against them inherit the
abridgement: ``brad-feld`` fact ``2b1b45dbf1d31438-f8`` cites, as a verbatim quote, the
string "on the boards of Path Forward, the Kauffman Fellows, and **[...]** Fellows, and
Defy Ventures" — a "verbatim" citation containing an ellipsis of its own, which passes the
substring gate because the abridged document contains the same ellipsis.

Median committed ``RawDoc.text`` length is 318 characters and 117 of 127 are under 2,000.
A fetched profile page is not 318 characters of text.

This module does not need the network, and it is not a fact-check: it asserts a property
of the committed corpus that follows from the product's own contract. On the strict-xfail
marker, see the module docstring of ``test_tadv_r11_hub_label_bypass.py``.
"""

from __future__ import annotations

import pytest

from arrival.contracts import RawDoc
from arrival.http.extract import MAX_TEXT_CHARS, clip
from tadv_corpus import REPO_ROOT

pytestmark = pytest.mark.ticket("TESTADVERSARY")

DOCS_DIR = REPO_ROOT / "data" / "docs"

#: What a summariser writes when it drops material. A fetched page's extracted text does
#: not contain these at a sentence join; an abridgement does.
ELISION_MARKERS = ("[...]", "[…]")


def committed_docs() -> list[tuple[str, RawDoc]]:
    return sorted(
        (path.stem, RawDoc.model_validate_json(path.read_text(encoding="utf-8")))
        for path in DOCS_DIR.glob("*.json")
    )


# --------------------------------------------------------------------------- premises

def test_the_docs_directory_is_populated():
    """Positive control: the assertion below is vacuous on an empty directory."""
    docs = committed_docs()
    assert len(docs) >= 100, len(docs)


def test_clip_is_the_only_truncation_policy_and_it_inserts_nothing():
    """The premise the finding rests on: no production path can produce an interior [...].

    ``clip`` trims the TAIL on a word boundary. If a future edit taught it to elide in the
    middle, the assertion below would stop being evidence of abridgement and this test is
    what says so.
    """
    long_text = ("word " * (MAX_TEXT_CHARS // 2)).strip()
    clipped = clip(long_text)
    assert len(clipped) <= MAX_TEXT_CHARS
    assert all(marker not in clipped for marker in ELISION_MARKERS)
    assert long_text.startswith(clipped[:200])
    # Short text is returned untouched, so nothing is inserted on that path either.
    assert clip("a short document.") == "a short document."


# --------------------------------------------------------------------------- the finding

@pytest.mark.xfail(
    strict=True,
    reason="OPEN DATA DEFECT: 44 of 127 committed RawDocs carry a literal elision marker "
    "in `text`, so they are abridgements rather than extracted page text, and the T-9 "
    "citation gate validates quotes against them. Remove this marker when it is fixed.",
)
def test_no_committed_rawdoc_is_an_abridgement():
    """`RawDoc.text` is "extracted plain text"; an elision marker means it was edited."""
    offenders = [
        f"{doc_id}  {doc.url}"
        for doc_id, doc in committed_docs()
        if any(marker in doc.text for marker in ELISION_MARKERS)
    ]
    assert offenders == [], (
        f"\n{len(offenders)} of {len(committed_docs())} RawDocs contain an elision "
        "marker:\n" + "\n".join(offenders)
    )


@pytest.mark.xfail(
    strict=True,
    reason="OPEN DATA DEFECT: a Provenance.quote committed with an elision marker inside "
    "it is not a verbatim citation. Remove this marker when it is fixed.",
)
def test_no_committed_quote_contains_an_elision_marker():
    """A quote is shown to a host as the sentence that proves a claim. It cannot elide."""
    from arrival.taste import is_displayable
    from tadv_corpus import committed_dossiers

    offenders = [
        f"{dossier.person.person_id} {fact.fact_id}: {fact.provenance.quote}"
        for dossier in committed_dossiers()
        for fact in dossier.facts
        if is_displayable(fact)
        and any(marker in fact.provenance.quote for marker in ELISION_MARKERS)
    ]
    assert offenders == [], "\n" + "\n".join(offenders)
