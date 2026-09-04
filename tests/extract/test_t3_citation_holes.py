"""T-015: the three holes in the citation guard, and the four behaviours that must survive.

The guard is the whole hallucination defence (SPEC C8 / R9, DESIGN Decision 5), so every
test here is written in the refusing direction: a doubtful quote is DROPPED, never
attributed. A false negative costs one fact; a false positive puts a fabricated citation
under a sentence a host reads out loud.

Measured against the current code before the repair:

* `is_cited("a", doc)` was True against every document, and a fact carrying the quote
  `"a"` was KEPT — a degenerate quote was a universal citation;
* `is_cited("plat", doc)` was True because "platform" contains it — no word boundary;
* a span present in TWO prompted documents was attributed by BATCH ORDER: the same
  candidate came back as `search`/2026-02-11 or as `hn`/2019-03-02 depending only on which
  document was listed first, and `published_at` feeds `recency_for` and therefore the score;
* `Jane O’Neil` against a source spelling it `Jane O'Neil` was dropped as uncited.

The bottom half of the module pins what must NOT change: paraphrases, ellipsis joins,
one-word alterations and empty quotes are still refused.
"""

from __future__ import annotations

import pytest
import t3_corpus as corpus

from arrival.contracts import Provenance
from arrival.extract import (
    MIN_QUOTE_CHARS,
    MIN_QUOTE_WORDS,
    CandidateFact,
    ExtractionResult,
    ExtractionStats,
    cited_span,
    extract,
    is_cited,
)
from arrival.util import normalize_ws
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


async def _run(docs, candidates, stats=None):
    llm = LLMDouble()
    llm.queue(ExtractionResult(facts=candidates))
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm, stats=stats
    )
    return facts


def _cand(doc, text, quote, **kw):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, **kw)


# --------------------------------------------------------------------------
# hole 1: a degenerate quote was a universal citation
# --------------------------------------------------------------------------


def test_a_quote_too_short_to_be_evidence_cites_nothing():
    about, status = corpus.about_doc(), corpus.status_doc()
    for degenerate in ("a", "I", "the", "in 2016", "team there"):
        assert not is_cited(degenerate, about), f"{degenerate!r} is not evidence of anything"
        assert not is_cited(degenerate, status)
    assert is_cited(corpus.ABOUT_SPAN, about), "a real span is still cited"


def test_the_minimum_is_a_published_constant_and_a_span_at_the_boundary_survives():
    """The boundary asserted in both directions, so the floor cannot drift silently."""
    assert MIN_QUOTE_WORDS >= 3 and MIN_QUOTE_CHARS >= 12
    doc = corpus.about_doc()
    span = "I run the platform team there."
    assert len(normalize_ws(span)) >= MIN_QUOTE_CHARS
    assert len(normalize_ws(span).split()) >= MIN_QUOTE_WORDS
    assert is_cited(span, doc), "a genuine span above the floor must not be refused"


async def test_a_fact_carrying_a_degenerate_quote_is_dropped_and_counted():
    doc = corpus.about_doc()
    stats = ExtractionStats()
    facts = await _run(
        [doc],
        [
            _cand(doc, "Runa Okonkwo was fired for fraud in 2024.", "a"),
            _cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.", corpus.ABOUT_SPAN),
        ],
        stats=stats,
    )

    assert [f.text for f in facts] == ["Runa Okonkwo co-founded Quarrystone Labs."], (
        "a one-character quote certified an invented sentence"
    )
    assert stats.dropped_uncited == 1


def test_a_quote_must_land_on_word_boundaries():
    doc = corpus.about_doc()
    assert not is_cited("platform team there. I co-founded Quarry", doc), (
        "a span cut mid-word is not a span of this document"
    )
    assert not is_cited("co-founded Quarrystone Labs in 201", doc)
    assert is_cited("co-founded Quarrystone Labs in 2016", doc)


# --------------------------------------------------------------------------
# hole 2: which document the guard certifies
# --------------------------------------------------------------------------


async def test_a_span_present_in_two_prompted_documents_is_refused_not_ordered():
    """Ambiguous provenance is refused, and refused the same way whatever the batch order.

    The declared id resolves to nothing, so the guard has to guess between two documents
    that both really contain the span. It must not guess: `published_at` and `source_kind`
    come out of the chosen document, `published_at` drives `recency_for`, and recency is a
    factor of T-5's score.
    """
    first, second = corpus.trade_doc(0), corpus.trade_doc(1)
    assert first.doc_id != second.doc_id
    assert first.published_at != second.published_at
    assert is_cited(corpus.TRADE_SPAN, first) and is_cited(corpus.TRADE_SPAN, second)

    for docs in ([first, second], [second, first]):
        stats = ExtractionStats()
        facts = await _run(
            docs,
            [
                CandidateFact(
                    doc_id="a-doc-id-the-model-invented",
                    text="Foundry Seed backed eleven companies.",
                    quote=corpus.TRADE_SPAN,
                )
            ],
            stats=stats,
        )
        assert facts == [], (
            "a span two documents both contain cannot be attributed to one of them by "
            f"batch order; got {[f.provenance.doc_id for f in facts]}"
        )
        assert stats.dropped_uncited == 1


async def test_the_declared_document_still_wins_when_it_really_contains_the_span():
    """The other half: ambiguity is only a problem for the FALLBACK, never for a true claim."""
    first, second = corpus.trade_doc(0), corpus.trade_doc(1)
    facts = await _run(
        [first, second],
        [_cand(second, "Foundry Seed backed eleven companies.", corpus.TRADE_SPAN)],
    )

    assert [f.provenance.doc_id for f in facts] == [second.doc_id], (
        "the document the model named contains the span, so nothing needs repairing"
    )
    assert facts[0].provenance.published_at == second.published_at


async def test_a_single_unambiguous_fallback_still_repairs_a_mistaken_doc_id():
    """Unchanged behaviour, re-asserted so the ambiguity rule cannot swallow it."""
    about, status = corpus.about_doc(), corpus.status_doc()
    facts = await _run(
        [about, status],
        [_cand(about, "Quarrystone Labs published a status page in 2017.", corpus.STATUS_SPAN)],
    )

    assert [f.provenance.doc_id for f in facts] == [status.doc_id]
    assert facts[0].provenance.source_kind == status.source_kind
    assert facts[0].provenance.published_at == status.published_at


# --------------------------------------------------------------------------
# hole 3: typographic punctuation was a false negative
# --------------------------------------------------------------------------


def test_typographic_punctuation_is_the_same_span_and_the_document_spelling_is_returned():
    doc = corpus.punctuation_doc()
    curly = corpus.PUNCTUATION_SPAN.replace("'", "’")
    assert curly != corpus.PUNCTUATION_SPAN
    assert is_cited(curly, doc), "a curly apostrophe is typography, not a different word"
    assert cited_span(curly, doc) == corpus.PUNCTUATION_SPAN, (
        "what comes back is the DOCUMENT's own spelling, so the stored quote stays verbatim"
    )

    hyphenated = corpus.PUNCTUATION_DASH_SPAN.replace("—", "-")
    assert is_cited(hyphenated, doc), "an em dash re-typed as a hyphen is the same span"
    assert cited_span(hyphenated, doc) == corpus.PUNCTUATION_DASH_SPAN


async def test_a_curly_apostrophe_keeps_the_fact_and_stores_the_sources_own_words():
    doc = corpus.punctuation_doc()
    stats = ExtractionStats()
    facts = await _run(
        [doc],
        [_cand(doc, "Jane O’Neil ships the parser weekly.",
               corpus.PUNCTUATION_SPAN.replace("'", "’"))],
        stats=stats,
    )

    assert len(facts) == 1, "a legitimate fact was dropped over an apostrophe"
    assert stats.dropped_uncited == 0
    quote = facts[0].provenance.quote
    assert "’" not in quote
    assert normalize_ws(quote) in normalize_ws(doc.text), (
        "DESIGN Decision 5 / contracts.Provenance: the stored quote is verbatim in its source"
    )
    Provenance.model_validate(facts[0].provenance.model_dump())


# --------------------------------------------------------------------------
# what must NOT change: the guard still refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "quote"),
    [
        ("paraphrase", "I started Quarrystone Labs in 2016 and I lead the platform team"),
        ("one word altered", "I co-founded Quarrystone Labs in 2016 and I run the logistics team"),
        ("ellipsis join", "I co-founded Quarrystone Labs ... I run the platform team there"),
        ("invented", "Runa Okonkwo was appointed chief executive of Quarrystone Labs in 2021."),
        ("empty", ""),
        ("whitespace only", "   \n  "),
        ("from another document", corpus.STATUS_SPAN),
    ],
)
async def test_the_guard_still_refuses_what_it_always_refused(name, quote):
    doc = corpus.about_doc()
    assert not is_cited(quote, doc), name
    stats = ExtractionStats()
    facts = await _run([doc], [_cand(doc, "Runa Okonkwo did something.", quote)], stats=stats)
    assert facts == [], name
    assert stats.dropped_uncited + stats.dropped_empty == 1, name


def test_cited_span_returns_none_rather_than_a_guess_when_there_is_no_span():
    doc = corpus.about_doc()
    assert cited_span("Runa Okonkwo was appointed chief executive in 2021.", doc) is None
    assert cited_span("", doc) is None
    # `ABOUT_TEXT` wraps mid-sentence, so the document's own span carries the newline the
    # model's single-spaced quote does not — and it is the DOCUMENT's characters that come
    # back, which is the whole point of `cited_span`.
    span = cited_span(corpus.ABOUT_SPAN, doc)
    assert span is not None and "\n" in span
    assert span in doc.text, "what comes back is a literal slice of the source"
    assert " ".join(span.split()) == corpus.ABOUT_SPAN
