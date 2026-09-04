"""T-037: an id two facts share has not identified either of them.

`CandidateFact.fact_id` asks for uniqueness in prose only ("Any string will do"), so a
model that emits fifteen facts and reuses `"f1"` is ordinary input rather than an attack.
`_collect_facts` resolved the collision with `setdefault`, which is first-wins, which is
the model's output order — and the losing fact's hub silently claimed the winner's
sentence as its evidence. Those ids are printed as citations by `digest._hub_evidence` and
`web.render._hub_evidence`, so the visible consequence is a "Why we know this" footnote
under a sentence it does not support.

`taste._positions` settled this shape already: an id carried by two facts in one prompt is
DELETED rather than resolved, because no answer mentioning it can be attributed to one of
them rather than the other.

Graded against the input documents' own text and against permutation invariance — neither
is a value this ticket can edit into agreement.
"""

from __future__ import annotations

import pytest
import t34_corpus as corpus

from arrival.extract import CandidateFact, CandidateHub, ExtractionResult, extract
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _cand(doc, text, quote, fact_id=""):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id)


async def _run(docs, results):
    llm = LLMDouble()
    for result in results:
        llm.queue(result)
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm
    )
    return facts, hubs, llm


def _answer(kestrel, harbour, order):
    """Two facts from two documents, both claiming the id `"f1"`, in the given order.

    The hub is about Harbourline Systems, which only the `harbour` document mentions, so
    a first-wins resolution of `"f1"` cites a quote about a different company entirely.
    """
    facts = [
        _cand(kestrel, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN, "f1"),
        _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN, "f1"),
    ]
    return ExtractionResult(
        facts=[facts[i] for i in order],
        hubs=[
            CandidateHub(
                label="Harbourline Systems",
                type="company",
                doc_id=harbour.doc_id,
                evidence_fact_ids=["f1"],
            )
        ],
    )


async def test_a_shared_id_never_cites_a_document_that_does_not_mention_the_hub():
    """The visible consequence: a citation printed under a sentence it does not support."""
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run([kestrel, harbour], [_answer(kestrel, harbour, (0, 1))])

    by_id = {fact.fact_id: fact for fact in facts}
    docs_by_id = {doc.doc_id: doc for doc in (kestrel, harbour)}
    (hub,) = hubs
    assert hub.evidence_fact_ids, "the hub lost its evidence entirely"
    for fact_id in hub.evidence_fact_ids:
        source = docs_by_id[by_id[fact_id].provenance.doc_id]
        assert hub.label in source.text, (
            f"hub {hub.hub_id!r} cites {source.url}, which never mentions {hub.label!r}: "
            "the shared id resolved to whichever fact the model happened to list first"
        )


async def test_the_hub_resolves_to_the_fact_the_shared_id_could_only_have_meant():
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, hubs, _llm = await _run([kestrel, harbour], [_answer(kestrel, harbour, (0, 1))])

    (hub,) = hubs
    by_id = {fact.fact_id: fact for fact in facts}
    assert {by_id[f].provenance.doc_id for f in hub.evidence_fact_ids} == {harbour.doc_id}


async def test_a_shared_id_makes_the_hubs_evidence_independent_of_the_models_order():
    """Permuting only the model's fact list may not move which sentence is cited.

    Compared on `(provenance.doc_id, text)` rather than on `fact_id`, because permuting
    the candidates legitimately renumbers the ids we assign.
    """
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()

    shapes = set()
    for order in ((0, 1), (1, 0)):
        facts, hubs, _llm = await _run([kestrel, harbour], [_answer(kestrel, harbour, order)])
        by_id = {fact.fact_id: fact for fact in facts}
        (hub,) = hubs
        shapes.add(
            tuple(
                sorted(
                    (by_id[f].provenance.doc_id, by_id[f].text) for f in hub.evidence_fact_ids
                )
            )
        )

    assert len(shapes) == 1, (
        f"the model's fact ORDER decided which sentence the hub cites: {sorted(shapes)}"
    )


async def test_facts_sharing_an_id_still_survive_as_facts():
    """Dropping the ID is not dropping the FACTS — both sentences are still cited."""
    kestrel, harbour = corpus.kestrel_doc(), corpus.harbour_doc()
    facts, _hubs, _llm = await _run([kestrel, harbour], [_answer(kestrel, harbour, (0, 1))])

    assert len(facts) == 2
    assert len({fact.fact_id for fact in facts}) == 2
    assert {fact.text for fact in facts} == {corpus.KESTREL_SENTENCE, corpus.HARBOUR_SENTENCE}
