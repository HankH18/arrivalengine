"""T-3 acceptance 1: the call shape, the batching, and what comes back.

`test_extract_shapes` in the ticket text, expanded: `extract` calls `llm.structured` with
its own `ExtractionResult` schema once per batch of at most three ACCEPTED documents, and
returns `(facts, hubs)` that really are `contracts` models — proved by building a
`Dossier` out of them rather than by an `isinstance` that a near-miss would also pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import t3_corpus as corpus

from arrival.contracts import Dossier, Fact, Hub, LLMError
from arrival.extract import (
    MAX_DOCS_PER_CALL,
    CandidateFact,
    CandidateHub,
    ExtractionResult,
    ExtractionStats,
    extract,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _fact(doc, text, quote, *, fact_id="f", category="current_work"):
    return CandidateFact(
        doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id, category=category
    )


def _six_docs():
    """Six documents whose prose is the about page, so every quote is verbatim in each."""
    return [
        corpus.make_doc(f"https://example.com/note/{n}", "search", corpus.ABOUT_TEXT)
        for n in range(6)
    ]


async def test_llm_is_called_once_per_batch_of_at_most_three_accepted_documents():
    docs = _six_docs()
    llm = LLMDouble()
    for group in (docs[:3], docs[3:]):
        llm.queue(
            ExtractionResult(
                facts=[
                    _fact(d, f"Runa Okonkwo works on {d.doc_id}.", corpus.ABOUT_SPAN)
                    for d in group
                ]
            )
        )

    facts, _hubs = await extract(corpus.PERSON, corpus.resolution_for(*docs), docs, llm)

    assert MAX_DOCS_PER_CALL == 3
    assert llm.call_count == 2, "six documents in batches of three is two calls"
    assert [call.schema_name for call in llm.calls] == ["ExtractionResult", "ExtractionResult"]
    assert all(call.cache_prefix for call in llm.calls), "the system prefix is cacheable"
    assert all(call.system for call in llm.calls), "the instructions belong in the system prompt"
    assert len(facts) == 6

    # Each document is named in exactly one prompt: attribution is impossible otherwise.
    for index, doc in enumerate(docs):
        wanted = 0 if index < 3 else 1
        assert doc.doc_id in llm.calls[wanted].user
        assert doc.doc_id not in llm.calls[1 - wanted].user


async def test_documents_the_resolver_did_not_accept_are_never_even_prompted():
    accepted = corpus.about_doc()
    rejected = corpus.roadmap_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[_fact(accepted, "Runa Okonkwo co-founded Quarrystone Labs.", corpus.ABOUT_SPAN)]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON,
        corpus.resolution_for(accepted, rejected, accepted=[accepted.doc_id]),
        [accepted, rejected],
        llm,
    )

    assert llm.call_count == 1
    assert rejected.doc_id not in llm.calls[0].user
    assert rejected.text not in llm.calls[0].user
    assert {f.provenance.doc_id for f in facts} == {accepted.doc_id}


async def test_an_accepted_id_with_no_matching_document_is_skipped_not_fatal():
    doc = corpus.about_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[_fact(doc, "Runa Okonkwo co-founded Quarrystone Labs.", corpus.ABOUT_SPAN)]
        )
    )

    facts, _hubs = await extract(
        corpus.PERSON,
        corpus.resolution_for(doc, accepted=[doc.doc_id, "0000000000000000"]),
        [doc],
        llm,
    )

    assert llm.call_count == 1
    assert len(facts) == 1


async def test_nothing_accepted_means_no_llm_call_at_all():
    doc = corpus.about_doc()
    llm = LLMDouble()  # unscripted: any call at all raises LLMError

    facts, hubs = await extract(corpus.PERSON, corpus.resolution_for(doc, accepted=[]), [doc], llm)

    assert (facts, hubs) == ([], [])
    assert llm.call_count == 0


async def test_one_failing_batch_does_not_cost_the_other_batches():
    docs = _six_docs()
    llm = LLMDouble()
    llm.queue(LLMError("the model fell over"))
    llm.queue(
        ExtractionResult(
            facts=[
                _fact(d, f"Runa Okonkwo works on {d.doc_id}.", corpus.ABOUT_SPAN) for d in docs[3:]
            ]
        )
    )
    stats = ExtractionStats()

    facts, _hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), docs, llm, stats=stats
    )

    assert llm.call_count == 2
    assert stats.llm_failures == 1
    assert {f.provenance.doc_id for f in facts} == {d.doc_id for d in docs[3:]}


class _WrongModelClient:
    """A client that answers with some OTHER model. Written here rather than scripted
    into `LLMDouble`, which refuses to hand back a foreign model at all — the branch
    under test only exists for a REAL client that misbehaves."""

    async def structured(
        self, *, system: str, user: str, schema, max_tokens: int = 2000, cache_prefix: bool = True
    ):
        return corpus.PERSON  # a PersonRef, not an ExtractionResult


async def test_a_client_returning_the_wrong_model_loses_its_batch_and_invents_nothing():
    """The LLMClient contract calls a foreign model a violation, not a response."""
    doc = corpus.about_doc()
    stats = ExtractionStats()

    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(doc), [doc], _WrongModelClient(), stats=stats
    )

    assert (facts, hubs) == ([], [])
    assert stats.llm_failures == 1


async def test_what_comes_back_is_contract_shaped_enough_to_build_a_dossier():
    doc = corpus.about_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                _fact(
                    doc,
                    "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team.",
                    corpus.ABOUT_SPAN,
                    fact_id="c1",
                )
            ],
            hubs=[
                CandidateHub(
                    label="Quarrystone Labs",
                    type="company",
                    doc_id=doc.doc_id,
                    evidence_fact_ids=["c1"],
                )
            ],
        )
    )

    facts, hubs = await extract(corpus.PERSON, corpus.resolution_for(doc), [doc], llm)

    assert all(isinstance(f, Fact) for f in facts)
    assert all(isinstance(h, Hub) for h in hubs)
    dossier = Dossier(
        person=corpus.PERSON,
        resolution=corpus.resolution_for(doc),
        facts=facts,
        hubs=hubs,
        built_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
    )
    assert Dossier.model_validate_json(dossier.model_dump_json()) == dossier


async def test_the_extractor_makes_no_taste_decision():
    """T-4 owns `excluded` / `exclusion_reason`; every fact leaves here untouched."""
    doc = corpus.status_doc()
    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                _fact(doc, "Quarrystone Labs published a status page in 2017.", corpus.STATUS_SPAN)
            ]
        )
    )

    facts, _hubs = await extract(corpus.PERSON, corpus.resolution_for(doc), [doc], llm)

    assert facts
    assert all(f.excluded is False and f.exclusion_reason is None for f in facts)
