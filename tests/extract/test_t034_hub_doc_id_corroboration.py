"""T-034: `CandidateHub.doc_id` is a claim, and a claim is not a corroboration.

`_collect_hubs` used to accept the model's `doc_id` with no check at all and then attach
the WHOLE of that document's surviving facts to the hub. Three things ride on that:

* `Hub.recency` is a max over the evidence documents' dates, and it is an edge weight in
  T-5's score, so a 2017 entity evidenced by an undated page moves the score;
* the QID corroboration set is the evidence documents, so borrowed evidence decides what
  `_states_qid` is even allowed to look at;
* `research._supported_hubs` — whose entire job is to drop a hub whose evidence facts
  taste excluded (SPEC R11) — is satisfied by ANY surviving evidence id, so borrowed
  evidence from an unrelated document keeps a hub alive whose real support was withheld.

The last one is the reason this is graded against `research._supported_hubs` and not
against anything in `extract`: that module is outside this ticket's write scope, so it
cannot be edited into agreeing with the fix.
"""

from __future__ import annotations

import pytest
import t34_corpus as corpus

from arrival.extract import CandidateFact, CandidateHub, ExtractionResult, ExtractionStats, extract
from arrival.research import BuildTrace, _supported_hubs
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _cand(doc, text, quote, fact_id=""):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id)


async def _run(docs, results, stats=None):
    llm = LLMDouble()
    for result in results:
        llm.queue(result)
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm, stats=stats
    )
    return facts, hubs, llm


def _answer(kestrel, harbour, *, claimed_doc):
    """One answer whose hub is about Kestrel Yards but points at whatever we tell it to.

    The hub carries NO `evidence_fact_ids`, which is the branch under test: the model
    declined to say which facts support it, so the extractor has to decide for itself.
    """
    return ExtractionResult(
        facts=[
            _cand(kestrel, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN),
            _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN),
            _cand(harbour, corpus.HARBOUR_SENTENCE_2, corpus.HARBOUR_SPAN_2),
        ],
        hubs=[
            CandidateHub(label="Kestrel Yards", type="company", doc_id=claimed_doc.doc_id),
        ],
    )


async def test_a_hubs_evidence_comes_only_from_documents_that_mention_it():
    """The general statement: no hub may be evidenced by a document that never names it.

    Graded against the RawDoc texts themselves, which are the inputs, not an answer key.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run(
        [kestrel, harbour], [_answer(kestrel, harbour, claimed_doc=harbour)]
    )

    by_id = {fact.fact_id: fact for fact in facts}
    docs_by_id = {doc.doc_id: doc for doc in (kestrel, harbour)}
    for hub in hubs:
        for fact_id in hub.evidence_fact_ids:
            source = docs_by_id[by_id[fact_id].provenance.doc_id]
            assert hub.label in source.text, (
                f"hub {hub.hub_id!r} is evidenced by a fact from {source.url}, "
                f"a document that never mentions {hub.label!r}"
            )


async def test_the_declared_document_is_repaired_to_the_one_that_names_the_hub():
    """The model's `doc_id` is wrong; exactly one prompted document names the entity."""
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run(
        [kestrel, harbour], [_answer(kestrel, harbour, claimed_doc=harbour)]
    )

    (hub,) = [h for h in hubs if h.label == "Kestrel Yards"]
    by_id = {fact.fact_id: fact for fact in facts}
    assert {by_id[f].provenance.doc_id for f in hub.evidence_fact_ids} == {kestrel.doc_id}


async def test_a_wrongly_declared_document_does_not_move_the_hubs_recency():
    """`Hub.recency` is an edge weight in T-5's score; borrowed evidence moves it.

    `kestrel` is dated 2017 (the permanent `0.3` band) and `harbour` is undated (the fixed
    `0.5` band), so neither number depends on the wall clock.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    _facts, hubs, _llm = await _run(
        [kestrel, harbour], [_answer(kestrel, harbour, claimed_doc=harbour)]
    )

    (hub,) = [h for h in hubs if h.label == "Kestrel Yards"]
    assert hub.recency == 0.3, (
        "the hub took its recency from the undated document the model misnamed, not from "
        "the 2017 capture that actually mentions it"
    )


async def test_borrowed_evidence_does_not_survive_the_r11_support_check():
    """The privacy consequence, graded by the module that exists to prevent it.

    `research._supported_hubs` drops a hub when every evidence fact it can resolve was
    excluded by taste. A hub carrying another document's facts is supported by facts that
    were never about it, so the drop never fires and the withheld material comes back as a
    match reason.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run(
        [kestrel, harbour], [_answer(kestrel, harbour, claimed_doc=harbour)]
    )

    # Taste withholds everything the Kestrel Yards document said. Nothing else changes.
    after_taste = [
        fact.model_copy(update={"excluded": True, "exclusion_reason": "health"})
        if fact.provenance.doc_id == kestrel.doc_id
        else fact
        for fact in facts
    ]
    assert any(fact.excluded for fact in after_taste), "fixture pre-condition"

    kept = _supported_hubs(hubs, after_taste, BuildTrace())

    assert [h.label for h in kept if h.label == "Kestrel Yards"] == [], (
        "the hub outlived the exclusion of every fact that was really about it, because "
        "it was holding an unrelated document's evidence"
    )


async def test_a_hub_no_prompted_document_mentions_is_dropped_as_unsupported():
    """A label nothing in the batch names is not repairable; it is unsupported."""
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    stats = ExtractionStats()
    _facts, hubs, _llm = await _run(
        [kestrel, harbour],
        [
            ExtractionResult(
                facts=[_cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN)],
                hubs=[
                    CandidateHub(
                        label="Pemberton Trust", type="company", doc_id=harbour.doc_id
                    )
                ],
            )
        ],
        stats=stats,
    )

    assert [h.label for h in hubs] == []
    assert stats.dropped_unsupported_hubs == 1


async def test_an_ambiguous_label_is_refused_rather_than_attached_to_the_first_document():
    """Two prompted documents name the entity and the model pointed at neither.

    `_source_doc` already refuses this shape for a fact ("two documents both carrying the
    span is not a repair, it is a coin toss"); the hub fallback gets the same answer.
    """
    one = corpus.make_doc("https://example.org/a", "search", corpus.KESTREL_TEXT)
    two = corpus.make_doc("https://example.org/b", "hn", corpus.KESTREL_TEXT)
    harbour = corpus.harbour_doc()
    stats = ExtractionStats()
    _facts, hubs, _llm = await _run(
        [one, two, harbour],
        [
            ExtractionResult(
                facts=[
                    _cand(one, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN),
                    _cand(two, "Kestrel Yards ran eleven people.", corpus.KESTREL_SPAN),
                    _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN),
                ],
                hubs=[
                    CandidateHub(label="Kestrel Yards", type="company", doc_id=harbour.doc_id)
                ],
            )
        ],
        stats=stats,
    )

    assert [h.label for h in hubs if h.label == "Kestrel Yards"] == [], (
        "two documents name the entity, so no document is THE source of the hub"
    )
    assert stats.dropped_unsupported_hubs == 1


async def test_a_hub_whose_named_evidence_ids_are_unusable_is_repaired_soundly():
    """The model named only ids we cannot resolve, so the fallback decides the evidence.

    It is allowed to: the repaired evidence is not "whatever that document contained" but
    the facts of a document that NAMES the hub, restricted to the ones that name it too.
    Every citation is therefore genuinely about the entity, which is the property the
    unrepaired code did not have.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run(
        [kestrel, harbour],
        [
            ExtractionResult(
                facts=[
                    _cand(kestrel, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN),
                    _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN),
                ],
                hubs=[
                    CandidateHub(
                        label="Harbourline Systems",
                        type="company",
                        doc_id=harbour.doc_id,
                        evidence_fact_ids=["invented", "also-invented"],
                    )
                ],
            )
        ],
    )

    (hub,) = hubs
    by_id = {fact.fact_id: fact for fact in facts}
    assert {by_id[f].provenance.doc_id for f in hub.evidence_fact_ids} == {harbour.doc_id}
    for fact_id in hub.evidence_fact_ids:
        assert hub.label in by_id[fact_id].text


async def test_a_repaired_hub_still_dies_with_the_facts_that_name_it():
    """The R11 property has to survive the repair, or the repair reopened the hole.

    Same shape as the borrowed-evidence case, but reached through the dangling-id branch:
    exclude every fact that names the entity and the hub must not outlive them.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run(
        [kestrel, harbour],
        [
            ExtractionResult(
                facts=[
                    _cand(kestrel, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN),
                    _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN),
                    _cand(harbour, corpus.HARBOUR_SENTENCE_2, corpus.HARBOUR_SPAN_2),
                ],
                hubs=[
                    CandidateHub(
                        label="Harbourline Systems",
                        type="company",
                        doc_id=harbour.doc_id,
                        evidence_fact_ids=["invented"],
                    )
                ],
            )
        ],
    )
    assert hubs, "fixture pre-condition: the hub exists before taste runs"

    after_taste = [
        fact.model_copy(update={"excluded": True, "exclusion_reason": "health"})
        if "Harbourline Systems" in fact.text
        else fact
        for fact in facts
    ]
    kept = _supported_hubs(hubs, after_taste, BuildTrace())

    assert kept == [], (
        "the hub survived the exclusion of every fact naming it, so the repaired evidence "
        "was still borrowed from somewhere else"
    )


async def test_a_hub_named_by_no_surviving_fact_is_dropped_as_unsupported():
    """The document names the entity; none of its surviving facts do.

    This is the narrowing that closes the R11 hole rather than merely moving it. The
    `_supported_hubs` docstring records the leak it was built for — a `home_or_property`
    sentence excluded, and `city:pecan-street` left standing because the SAME document
    also carried innocuous facts. Corroborating the document alone would not have caught
    that: the document does name the street. Only refusing to attach facts that are not
    about the entity does.
    """
    kestrel = corpus.kestrel_doc()
    assert "Kestrel Yards" in kestrel.text, "fixture pre-condition: the document names it"
    unrelated_span = "which at the time was unusual for a company of eleven people"
    assert unrelated_span in kestrel.text and "Kestrel Yards" not in unrelated_span

    stats = ExtractionStats()
    facts, hubs, _llm = await _run(
        [kestrel],
        [
            ExtractionResult(
                # A true, cited fact from the document — but it is not about the hub.
                facts=[
                    _cand(kestrel, "The status page was an unusual move.", unrelated_span)
                ],
                hubs=[
                    CandidateHub(label="Kestrel Yards", type="company", doc_id=kestrel.doc_id)
                ],
            )
        ],
        stats=stats,
    )

    assert len(facts) == 1, "the fact itself is fine; it is the hub that is unsupported"
    assert [h.label for h in hubs] == []
    assert stats.dropped_unsupported_hubs == 1
