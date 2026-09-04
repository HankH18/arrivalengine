"""T-3 acceptance 2 / SPEC C8 / DESIGN Decision 5: the citation check drops and counts.

Every test here pairs a drop with a survivor. A guard that throws everything away also
satisfies "the fabricated fact is gone", and it is the failure mode a one-sided assertion
cannot see.
"""

from __future__ import annotations

import pytest
import t3_corpus as corpus

from arrival.extract import (
    MAX_FACT_CHARS,
    CandidateFact,
    ExtractionResult,
    ExtractionStats,
    extract,
    is_cited,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")

CITED = "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team."
UNCITED = "Runa Okonkwo has been the chief executive of Quarrystone Labs since 2021."
FABRICATED = "Runa Okonkwo was appointed chief executive of Quarrystone Labs in 2021."


def _run(doc, candidates, stats=None):
    llm = LLMDouble()
    llm.queue(ExtractionResult(facts=candidates))
    return llm, extract(
        corpus.PERSON, corpus.resolution_for(doc), [doc], llm, stats=stats
    )


def _cand(doc, text, quote, **kw):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, **kw)


def test_is_cited_is_whitespace_and_case_insensitive_but_word_sensitive():
    doc = corpus.about_doc()
    assert is_cited(corpus.ABOUT_SPAN, doc)
    assert is_cited("\n  ".join(corpus.ABOUT_SPAN.upper().split()), doc)
    assert not is_cited(corpus.ABOUT_SPAN.replace("platform", "logistics"), doc)
    assert not is_cited("", doc), "an empty quote cites nothing, it does not cite everything"
    assert not is_cited("   ", doc)


async def test_a_fabricated_quote_drops_its_fact_and_is_counted():
    doc = corpus.about_doc()
    stats = ExtractionStats()
    _llm, coro = _run(
        doc,
        [_cand(doc, CITED, corpus.ABOUT_SPAN), _cand(doc, UNCITED, FABRICATED)],
        stats=stats,
    )
    facts, _hubs = await coro

    kept = [f.text for f in facts]
    assert UNCITED not in kept
    assert CITED in kept, "the cited fact must survive, or the guard is just a delete"
    assert stats.dropped_uncited == 1
    assert stats.facts_proposed == 2
    assert stats.facts_kept == 1


async def test_a_quote_reflowed_or_re_cased_still_counts_as_verbatim():
    doc = corpus.about_doc()
    reflowed = "\n   ".join(corpus.ABOUT_SPAN.upper().split())
    word_changed = corpus.ABOUT_SPAN.replace("platform team", "logistics team")
    _llm, coro = _run(
        doc,
        [
            _cand(doc, "Runa Okonkwo runs the platform team.", reflowed),
            _cand(doc, "Runa Okonkwo runs the logistics team.", word_changed),
        ],
    )
    facts, _hubs = await coro

    kept = [f.text for f in facts]
    assert "Runa Okonkwo runs the platform team." in kept
    assert "Runa Okonkwo runs the logistics team." not in kept


async def test_the_stored_quote_is_the_span_not_the_model_s_typography():
    doc = corpus.about_doc()
    _llm, coro = _run(doc, [_cand(doc, CITED, "\n  ".join(corpus.ABOUT_SPAN.split()))])
    facts, _hubs = await coro

    assert len(facts) == 1
    quote = facts[0].provenance.quote
    assert "\n" not in quote and "  " not in quote, "whitespace runs are collapsed for display"
    assert is_cited(quote, doc), "and what is stored is still verbatim in the source"


async def test_an_over_length_fact_is_dropped_and_counted_while_the_short_one_survives():
    doc = corpus.about_doc()
    long_text = (
        "Runa Okonkwo argues at considerable length that a pricing page is a moral document, "
        "that documentation is part of the product, that support is part of the product, and "
        "that a company selling to engineers is selling to people who read whatever it ships."
    )
    assert len(long_text) > MAX_FACT_CHARS
    stats = ExtractionStats()
    _llm, coro = _run(
        doc,
        [_cand(doc, CITED, corpus.ABOUT_SPAN), _cand(doc, long_text, corpus.ABOUT_SPAN)],
        stats=stats,
    )
    facts, _hubs = await coro

    assert [f.text for f in facts] == [CITED]
    assert stats.dropped_over_length == 1
    assert all(len(f.text) <= MAX_FACT_CHARS for f in facts)


async def test_a_fact_of_exactly_the_cap_survives():
    """The boundary, asserted: a `>` written as `>=` silently costs a legitimate fact."""
    doc = corpus.about_doc()
    exact = "Runa Okonkwo co-founded Quarrystone Labs. " + "x" * (MAX_FACT_CHARS - 42)
    assert len(exact) == MAX_FACT_CHARS
    _llm, coro = _run(doc, [_cand(doc, exact, corpus.ABOUT_SPAN)])
    facts, _hubs = await coro

    assert [f.text for f in facts] == [exact]


async def test_an_empty_sentence_or_an_empty_quote_is_dropped_and_counted():
    doc = corpus.about_doc()
    stats = ExtractionStats()
    _llm, coro = _run(
        doc,
        [
            _cand(doc, "", corpus.ABOUT_SPAN),
            _cand(doc, CITED, "   "),
            _cand(doc, CITED, corpus.ABOUT_SPAN),
        ],
        stats=stats,
    )
    facts, _hubs = await coro

    assert len(facts) == 1
    assert stats.dropped_empty == 2


async def test_provenance_comes_from_the_document_not_from_the_model():
    """A model that lies about the url or the source kind must not be believed."""
    doc = corpus.status_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                CandidateFact(
                    doc_id="not-a-real-doc-id",
                    text="Quarrystone Labs published a status page in 2017.",
                    quote=corpus.STATUS_SPAN,
                    confidence=4.2,
                )
            ]
        )
    )

    facts, _hubs = await extract(corpus.PERSON, corpus.resolution_for(doc), [doc], llm)

    assert len(facts) == 1, "a mistaken doc_id is repaired by the quote, not fatal"
    prov = facts[0].provenance
    assert prov.doc_id == doc.doc_id
    assert prov.url == doc.url
    assert prov.source_kind == doc.source_kind
    assert prov.published_at == doc.published_at
    assert prov.retrieved_at == doc.fetched_at
    assert prov.confidence == 1.0, "an out-of-range confidence is clamped, not stored"


async def test_a_quote_from_the_wrong_document_in_the_batch_is_attributed_to_its_real_source():
    about, status = corpus.about_doc(), corpus.status_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                # Claims the about page, but the span is only in the archived status page.
                CandidateFact(
                    doc_id=about.doc_id,
                    text="Quarrystone Labs published a status page in 2017.",
                    quote=corpus.STATUS_SPAN,
                )
            ]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON, corpus.resolution_for(about, status), [about, status], llm
    )

    assert len(facts) == 1
    assert facts[0].provenance.doc_id == status.doc_id


async def test_facts_carry_distinct_ids_even_when_the_model_reuses_one():
    doc = corpus.about_doc()
    _llm, coro = _run(
        doc,
        [
            _cand(doc, CITED, corpus.ABOUT_SPAN, fact_id="same"),
            _cand(doc, "Quarrystone Labs raised money from Foundry Seed.", corpus.FOUNDRY_SPAN,
                  fact_id="same"),
        ],
    )
    facts, _hubs = await coro

    assert len(facts) == 2
    assert len({f.fact_id for f in facts}) == 2, "duplicate ids would collapse the graph's edges"
