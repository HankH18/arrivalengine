"""T-035: the model may not write into the id namespace we assign from.

Our fact ids are `f"{doc.doc_id}-f{index}"` and the prompt prints every `doc_id`, so
`"<doc_id>-f1"` is a string the model can simply guess. `_collect_facts` used to fold the
model's claimed ids and our own into ONE mapping with `setdefault`, in the order the facts
happened to be built, so a claim staked on `"<doc_id>-f1"` by an earlier candidate was
already in the map by the time fact #1 existed — and `setdefault` then declined to record
the real one. Every hub naming that id, including a hub that named it CORRECTLY, resolved
to the wrong fact.

Graded against literals and against `Fact.provenance`, both outside this ticket's scope to
rewrite: the id `"{doc_id}-f1"` is ours by construction, and which sentence it belongs to
is settled by the order the surviving facts were built in, not by anything in `extract`.
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


async def test_a_claimed_id_that_squats_on_one_of_ours_does_not_displace_it():
    """Candidate #0 claims the id fact #1 is about to be given."""
    harbour = corpus.harbour_doc()
    squatted = f"{harbour.doc_id}-f1"

    facts, hubs, _llm = await _run(
        [harbour],
        [
            ExtractionResult(
                facts=[
                    _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN, squatted),
                    _cand(harbour, corpus.HARBOUR_SENTENCE_2, corpus.HARBOUR_SPAN_2, "b"),
                ],
                hubs=[
                    CandidateHub(
                        label="Harbourline Systems",
                        type="company",
                        doc_id=harbour.doc_id,
                        evidence_fact_ids=[squatted],
                    )
                ],
            )
        ],
    )

    assert len(facts) == 2
    assert facts[1].fact_id == squatted, "fixture pre-condition: the id really is guessable"
    assert facts[1].text == corpus.HARBOUR_SENTENCE_2

    (hub,) = hubs
    assert hub.evidence_fact_ids == [squatted], (
        "the model's claim on one of our own ids outranked the fact we actually gave it to"
    )


async def test_our_own_ids_always_resolve_to_the_fact_that_carries_them():
    """The general invariant: `id_map[our_id] is our_id`, whatever the model claimed.

    Stated over every surviving fact so that no single squatted id has to be guessed for
    the property to be tested.
    """
    harbour, kestrel = corpus.harbour_doc(), corpus.kestrel_doc()
    claims = [f"{harbour.doc_id}-f{n}" for n in range(3)]

    facts, hubs, _llm = await _run(
        [harbour, kestrel],
        [
            ExtractionResult(
                facts=[
                    _cand(harbour, corpus.HARBOUR_SENTENCE, corpus.HARBOUR_SPAN, claims[2]),
                    _cand(harbour, corpus.HARBOUR_SENTENCE_2, corpus.HARBOUR_SPAN_2, claims[0]),
                    _cand(kestrel, corpus.KESTREL_SENTENCE, corpus.KESTREL_SPAN, claims[1]),
                ],
                # One hub per real fact id, so every id is exercised through the map the
                # hubs are translated with.
                hubs=[
                    CandidateHub(
                        label="Harbourline Systems",
                        type="company",
                        doc_id=harbour.doc_id,
                        evidence_fact_ids=[f"{harbour.doc_id}-f0"],
                    ),
                    CandidateHub(
                        label="Kestrel Yards",
                        type="company",
                        doc_id=kestrel.doc_id,
                        evidence_fact_ids=[f"{kestrel.doc_id}-f0"],
                    ),
                ],
            )
        ],
    )

    by_id = {fact.fact_id: fact for fact in facts}
    assert f"{harbour.doc_id}-f0" in by_id and f"{kestrel.doc_id}-f0" in by_id

    for hub in hubs:
        for fact_id in hub.evidence_fact_ids:
            assert fact_id in by_id
            source_doc = by_id[fact_id].provenance.doc_id
            assert fact_id.startswith(f"{source_doc}-f"), (
                f"hub {hub.hub_id!r} resolved {fact_id!r} to a fact from a different document"
            )

    harbour_hub = next(h for h in hubs if h.label == "Harbourline Systems")
    assert harbour_hub.evidence_fact_ids == [f"{harbour.doc_id}-f0"], (
        "a hub naming one of our own ids must reach the fact that id was assigned to"
    )
    assert by_id[f"{harbour.doc_id}-f0"].text == corpus.HARBOUR_SENTENCE
