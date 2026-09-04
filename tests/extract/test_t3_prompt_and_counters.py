"""T-012: the two things about `extract` that no gate anywhere was grading.

Both are stub-blindness, not bugs — the code was right and nothing asserted it:

1. `_document_block` could be replaced by `return ""` — sending the model ZERO document
   text — and the ten frozen T-3 criteria plus all 46 `tests/extract` tests stayed green,
   because the scripted doubles key on document IDs appearing in the prompt rather than on
   the corpus prose. (Measured here: the frozen T-6 pipeline criterion *does* catch it,
   because its stub keys on a sentinel that lives inside the document text; the frozen T-3
   suite and the whole 815-test project suite do not.)
2. Acceptance 2's "*and counted*" clause: the frozen tests never pass `stats=`, and
   `hubs_proposed` was asserted by no test in the repo at all.

So this module asserts the INPUT the model is actually shown, and pins every counter of
one scripted run at once — a counter replaced by a constant, dropped, or incremented on
the wrong branch has to fail here.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
import t3_corpus as corpus

from arrival.extract import (
    MAX_TOKENS_PER_CALL,
    CandidateFact,
    CandidateHub,
    ExtractionResult,
    ExtractionStats,
    extract,
)
from arrival.util import normalize_ws
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _cand(doc, text, quote, fact_id="", **kw):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id, **kw)


async def _run(docs, results, stats=None):
    llm = LLMDouble()
    for result in results:
        llm.queue(result)
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm, stats=stats
    )
    return facts, hubs, llm


# --------------------------------------------------------------------------
# the extractor's input actually reaches the model
# --------------------------------------------------------------------------


async def test_the_prompt_carries_the_full_text_of_every_document_in_the_batch():
    """The sabotage this closes: `_document_block` -> `return ""`, every suite still green."""
    docs = [corpus.about_doc(), corpus.status_doc(), corpus.wikidata_doc()]
    _facts, _hubs, llm = await _run(
        docs,
        [ExtractionResult(facts=[_cand(docs[0], "Runa Okonkwo co-founded Quarrystone Labs.",
                                       corpus.ABOUT_SPAN)])],
    )

    assert llm.call_count == 1, "three documents is one batch"
    prompt = llm.calls[0].user
    for doc in docs:
        assert doc.text in prompt, (
            f"the model was never shown the text of {doc.doc_id}; it cannot quote what it "
            "cannot read, and every downstream citation check would then be grading noise"
        )
    # And the spans the tests quote are really in there, not merely the whole blob.
    # `ABOUT_TEXT` wraps mid-sentence, so compare the way the citation guard does.
    for span in (corpus.ABOUT_SPAN, corpus.STATUS_SPAN, corpus.WIKIDATA_SPAN):
        assert normalize_ws(span) in normalize_ws(prompt)


async def test_the_prompt_carries_each_documents_identity_alongside_its_text():
    doc = corpus.status_doc()
    _facts, _hubs, llm = await _run(
        [doc],
        [ExtractionResult(facts=[_cand(doc, "Quarrystone Labs published a status page.",
                                       corpus.STATUS_SPAN)])],
    )

    prompt = llm.calls[0].user
    assert doc.doc_id in prompt
    assert doc.url in prompt
    assert doc.source_kind in prompt
    assert doc.published_at.isoformat() in prompt
    assert corpus.PERSON.name in prompt
    assert llm.calls[0].max_tokens == MAX_TOKENS_PER_CALL


async def test_a_second_batch_is_shown_its_own_documents_and_not_the_first_batchs():
    docs = [
        corpus.make_doc(f"https://example.com/note/{n}", "search",
                        f"Note {n}. " + corpus.ABOUT_TEXT)
        for n in range(4)
    ]
    results = [
        ExtractionResult(facts=[_cand(d, f"Runa Okonkwo wrote note {i}.", corpus.ABOUT_SPAN)])
        for i, d in enumerate((docs[0], docs[3]))
    ]
    _facts, _hubs, llm = await _run(docs, results)

    assert llm.call_count == 2
    first, second = llm.calls[0].user, llm.calls[1].user
    for doc in docs[:3]:
        assert doc.text in first and doc.doc_id in first
    assert docs[3].text in second and docs[3].doc_id in second
    assert docs[3].doc_id not in first
    assert "Note 3." not in first, "a batch must not be shown a document it was not given"


# --------------------------------------------------------------------------
# acceptance 2's "and counted" clause
# --------------------------------------------------------------------------


async def test_every_extraction_counter_is_the_real_number_for_one_scripted_run():
    """One run, one exact expectation per counter, so no counter is graded by nothing."""
    about, status = corpus.about_doc(), corpus.status_doc()
    long_text = "Runa Okonkwo " + "argues at length " * 15
    assert len(long_text) > 200

    stats = ExtractionStats()
    facts, hubs, _llm = await _run(
        [about, status],
        [
            ExtractionResult(
                facts=[
                    # kept
                    _cand(about, "Runa Okonkwo co-founded Quarrystone Labs.",
                          corpus.ABOUT_SPAN, "a"),
                    # kept, and its non_obvious flag survives (wayback is eligible)
                    _cand(status, "Quarrystone Labs shipped a public status page.",
                          corpus.STATUS_SPAN, "b", category="non_obvious"),
                    # kept, but downgraded: self_page is not an eligible non-obvious source
                    _cand(about, "Quarrystone Labs raised money from Foundry Seed.",
                          corpus.FOUNDRY_SPAN, "c", category="non_obvious",
                          natural_category="affiliation"),
                    # dropped: uncited
                    _cand(about, "Runa Okonkwo became chief executive in 2021.",
                          "Runa Okonkwo became chief executive in 2021."),
                    # dropped: empty quote
                    _cand(about, "Runa Okonkwo did something.", "   "),
                    # dropped: over length
                    _cand(about, long_text, corpus.ABOUT_SPAN),
                ],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company",
                                 doc_id=about.doc_id, evidence_fact_ids=["a"]),
                    CandidateHub(label="Foundry Seed 2019", type="investor",
                                 doc_id=about.doc_id, evidence_fact_ids=["c"]),
                    # dropped: a stop LABEL
                    CandidateHub(label="Startup", type="topic", doc_id=about.doc_id,
                                 evidence_fact_ids=["a"]),
                    # dropped: every evidence id is dangling and the document has no
                    # surviving facts to fall back to
                    CandidateHub(label="Phantom Holdings", type="company",
                                 doc_id="not-a-document", evidence_fact_ids=["nope"]),
                ],
            )
        ],
        stats=stats,
    )

    assert len(facts) == 3 and len(hubs) == 2
    expected = {
        "documents_prompted": 2,
        "llm_calls": 1,
        "llm_failures": 0,
        "facts_proposed": 6,
        "facts_kept": 3,
        "dropped_uncited": 1,
        "dropped_over_length": 1,
        "dropped_empty": 1,
        "downgraded_non_obvious": 1,
        "hubs_proposed": 4,
        "hubs_kept": 2,
        "dropped_stop_hubs": 1,
        "dropped_unsupported_hubs": 1,
    }
    actual = {name: getattr(stats, name) for name in expected}
    assert actual == expected

    named = {f.name for f in fields(ExtractionStats)}
    assert named >= set(expected), f"a counter vanished: {set(expected) - named}"
    ungraded = named - set(expected)
    assert not ungraded, (
        f"every counter must be pinned by this test; {sorted(ungraded)} is graded by nothing"
    )


async def test_a_fresh_stats_object_starts_at_zero_and_only_accumulates_what_happened():
    """A counter wired to a constant, or seeded non-zero, cannot survive this."""
    stats = ExtractionStats()
    assert all(getattr(stats, f.name) == 0 for f in fields(ExtractionStats))

    doc = corpus.about_doc()
    await _run(
        [doc],
        [ExtractionResult(facts=[_cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.",
                                       corpus.ABOUT_SPAN)])],
        stats=stats,
    )
    assert stats.facts_proposed == 1 and stats.facts_kept == 1
    assert stats.hubs_proposed == 0 and stats.hubs_kept == 0
    assert stats.dropped_uncited == 0 and stats.dropped_empty == 0

    # The same object across a second person accumulates rather than resets.
    await _run(
        [doc],
        [ExtractionResult(facts=[_cand(doc, "Runa Okonkwo runs the platform team.",
                                       corpus.ABOUT_SPAN)])],
        stats=stats,
    )
    assert stats.facts_proposed == 2 and stats.documents_prompted == 2 and stats.llm_calls == 2


async def test_hubs_proposed_counts_the_dropped_candidates_too():
    """The one counter no test in the repo asserted before this module existed."""
    doc = corpus.about_doc()
    stats = ExtractionStats()
    _facts, hubs, _llm = await _run(
        [doc],
        [
            ExtractionResult(
                facts=[_cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.",
                             corpus.ABOUT_SPAN, "a")],
                hubs=[
                    CandidateHub(label="AI", type="topic", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="Texas", type="city", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                ],
            )
        ],
        stats=stats,
    )

    assert stats.hubs_proposed == 3, "proposals are counted before any of them is judged"
    assert stats.dropped_stop_hubs == 2
    assert stats.hubs_kept == len(hubs) == 1
