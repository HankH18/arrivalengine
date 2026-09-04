"""T-3 acceptance 3 and 4: canonical hub ids, one node per label, stop hubs, recency.

Two behaviours here are NOT discriminated by the frozen acceptance suite, and are the
reason this module exists rather than leaning on it:

* the frozen script happens to hand back fact ids in exactly the shape the extractor
  assigns, so passing `evidence_fact_ids` straight through looks identical to translating
  them. A real model names its own ids, and an untranslated id is a dangling edge;
* the only document in the frozen corpus that carries a QID is a Wikidata document, so
  believing a QID from any source at all also looks identical. A QID taken from a blog
  post merges two different people into one graph node.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import t3_corpus as corpus

from arrival.extract import (
    STOP_HUB_LABELS,
    CandidateFact,
    CandidateHub,
    ExtractionResult,
    ExtractionStats,
    canonical_hub_id,
    extract,
    recency_for,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _cand(doc, text, quote, fact_id):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id)


async def _run(docs, result_by_call, stats=None):
    llm = LLMDouble()
    for result in result_by_call:
        llm.queue(result)
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm, stats=stats
    )
    return facts, hubs, llm


# --------------------------------------------------------------------------
# recency (T-3 acceptance 4), including both band boundaries
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(0, 1.0), (364, 1.0), (365, 1.0), (366, 0.6), (1094, 0.6), (1095, 0.6), (1096, 0.3),
     (4000, 0.3)],
)
def test_recency_bands_including_their_boundaries(age_days, expected):
    today = date(2026, 2, 20)
    assert recency_for(today - timedelta(days=age_days), today=today) == expected


def test_an_unknown_date_scores_above_an_ancient_one():
    assert recency_for(None) == 0.5
    assert recency_for(date(2000, 1, 1)) == 0.3


def test_a_future_date_is_treated_as_current_rather_than_ancient():
    today = date(2026, 2, 20)
    assert recency_for(today + timedelta(days=30), today=today) == 1.0


async def test_hub_recency_comes_from_the_age_of_its_source_document():
    old = corpus.status_doc()  # published 2017
    recent = corpus.roadmap_doc()  # published 2026-02-11
    facts, hubs, _llm = await _run(
        [old, recent],
        [
            ExtractionResult(
                facts=[
                    _cand(old, "Quarrystone Labs published a status page.", corpus.STATUS_SPAN,
                          "a"),
                    _cand(recent, "Quarrystone Labs opened its roadmap.", corpus.ROADMAP_SPAN,
                          "b"),
                ],
                hubs=[
                    CandidateHub(label="Kestrel Yards", type="company", doc_id=old.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="Harbourline Systems", type="company",
                                 doc_id=recent.doc_id, evidence_fact_ids=["b"]),
                ],
            )
        ],
    )

    assert len(facts) == 2
    by_label = {h.label: h for h in hubs}
    assert by_label["Kestrel Yards"].recency == 0.3
    assert by_label["Harbourline Systems"].recency == 1.0


# --------------------------------------------------------------------------
# canonical ids (T-3 acceptance 3)
# --------------------------------------------------------------------------


def test_canonical_hub_id_uses_the_shared_slug():
    assert canonical_hub_id("investor", "Foundry Seed 2019") == "investor:foundry-seed-2019"
    assert canonical_hub_id("technology", "Developer platform") == "technology:developer-platform"
    assert canonical_hub_id("person", "Jane O'Neil-Ruiz") == "person:jane-oneil-ruiz"
    assert canonical_hub_id("company", "Belmarch Optics", "Q42") == "wd:Q42"


async def test_a_wikidata_document_that_states_the_qid_keys_the_hub_by_it():
    wiki = corpus.wikidata_doc()
    assert "Q900000411" in wiki.text
    _facts, hubs, _llm = await _run(
        [wiki],
        [
            ExtractionResult(
                facts=[_cand(wiki, "Runa Okonkwo works at Quarrystone Labs.",
                             corpus.WIKIDATA_SPAN, "a")],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=wiki.doc_id,
                                 evidence_fact_ids=["a"], qid="Q900000411")
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["wd:Q900000411"]


async def test_a_qid_offered_by_a_non_wikidata_document_is_not_believed():
    """The gap the frozen suite cannot see: a QID from a blog post would merge two people."""
    about = corpus.about_doc()
    _facts, hubs, _llm = await _run(
        [about],
        [
            ExtractionResult(
                facts=[_cand(about, "Runa Okonkwo co-founded Quarrystone Labs.",
                             corpus.ABOUT_SPAN, "a")],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=about.doc_id,
                                 evidence_fact_ids=["a"], qid="Q900000411")
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["company:quarrystone-labs"]


async def test_a_qid_a_wikidata_document_does_not_state_is_not_believed():
    wiki = corpus.make_doc(
        "https://example.org/wikidata/mirror-without-an-id",
        "wikidata",
        corpus.WIKIDATA_TEXT.replace("Q900000411", "no identifier recorded"),
    )
    assert "Q" not in wiki.text.replace("Quarrystone", "")
    _facts, hubs, _llm = await _run(
        [wiki],
        [
            ExtractionResult(
                facts=[_cand(wiki, "Runa Okonkwo works at Quarrystone Labs.",
                             corpus.WIKIDATA_SPAN, "a")],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=wiki.doc_id,
                                 evidence_fact_ids=["a"], qid="Q123456")
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["company:quarrystone-labs"]


async def test_a_malformed_qid_is_ignored():
    wiki = corpus.wikidata_doc()
    _facts, hubs, _llm = await _run(
        [wiki],
        [
            ExtractionResult(
                facts=[_cand(wiki, "Runa Okonkwo works at Quarrystone Labs.",
                             corpus.WIKIDATA_SPAN, "a")],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=wiki.doc_id,
                                 evidence_fact_ids=["a"], qid="not-a-qid")
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["company:quarrystone-labs"]


# --------------------------------------------------------------------------
# merging and evidence
# --------------------------------------------------------------------------


async def test_one_label_across_two_documents_is_one_hub_evidenced_by_both():
    about, roadmap = corpus.about_doc(), corpus.roadmap_doc()
    facts, hubs, _llm = await _run(
        [about, roadmap],
        [
            ExtractionResult(
                facts=[
                    _cand(about, "Runa Okonkwo co-founded Quarrystone Labs.",
                          corpus.ABOUT_SPAN, "a"),
                    _cand(roadmap, "Quarrystone Labs opened its roadmap.",
                          corpus.ROADMAP_SPAN, "b"),
                ],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=about.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="quarrystone labs", type="company", doc_id=roadmap.doc_id,
                                 evidence_fact_ids=["b"]),
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["company:quarrystone-labs"]
    by_id = {f.fact_id: f for f in facts}
    evidence = hubs[0].evidence_fact_ids
    assert len(evidence) == 2
    assert {by_id[fid].provenance.doc_id for fid in evidence} == {about.doc_id, roadmap.doc_id}


async def test_a_label_seen_in_two_separate_calls_still_becomes_one_hub():
    """Merging must survive the batch boundary, not just work inside one answer."""
    docs = [
        corpus.make_doc(f"https://example.com/note/{n}", "search", corpus.ABOUT_TEXT)
        for n in range(4)
    ]
    results = []
    for offset, group in ((0, docs[:3]), (3, docs[3:])):
        results.append(
            ExtractionResult(
                facts=[
                    _cand(d, f"Runa Okonkwo worked on item {offset + i}.", corpus.ABOUT_SPAN,
                          f"c{offset + i}")
                    for i, d in enumerate(group)
                ],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=d.doc_id,
                                 evidence_fact_ids=[f"c{offset + i}"])
                    for i, d in enumerate(group)
                ],
            )
        )

    facts, hubs, llm = await _run(docs, results)

    assert llm.call_count == 2
    assert [h.hub_id for h in hubs] == ["company:quarrystone-labs"]
    assert len(hubs[0].evidence_fact_ids) == len(facts) == 4


async def test_evidence_ids_are_translated_from_whatever_the_model_called_them():
    """The gap the frozen suite cannot see: the model names its own ids, we name ours."""
    doc = corpus.about_doc()
    facts, hubs, _llm = await _run(
        [doc],
        [
            ExtractionResult(
                facts=[_cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.",
                             corpus.ABOUT_SPAN, "the-models-own-name-for-it")],
                hubs=[
                    CandidateHub(label="Quarrystone Labs", type="company", doc_id=doc.doc_id,
                                 evidence_fact_ids=["the-models-own-name-for-it", "invented"])
                ],
            )
        ],
    )

    assert len(facts) == 1
    ids = {f.fact_id for f in facts}
    assert "the-models-own-name-for-it" not in ids, "the extractor assigns its own fact ids"
    assert hubs[0].evidence_fact_ids == [facts[0].fact_id]
    assert "invented" not in hubs[0].evidence_fact_ids, "a dangling edge is dropped, not kept"


async def test_a_hub_whose_every_evidence_fact_failed_the_citation_check_is_dropped():
    doc = corpus.about_doc()
    stats = ExtractionStats()
    facts, hubs, _llm = await _run(
        [doc],
        [
            ExtractionResult(
                facts=[
                    CandidateFact(
                        doc_id=doc.doc_id,
                        text="Runa Okonkwo was appointed chief executive in 2021.",
                        quote="Runa Okonkwo was appointed chief executive in 2021.",
                        fact_id="a",
                    )
                ],
                hubs=[
                    CandidateHub(label="Phantom Holdings", type="company", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"])
                ],
            )
        ],
        stats=stats,
    )

    assert facts == []
    assert hubs == [], "a hub evidenced only by a hallucination is itself a hallucination"
    assert stats.dropped_unsupported_hubs == 1


async def test_a_hub_with_no_evidence_ids_falls_back_to_its_document_s_surviving_facts():
    doc = corpus.about_doc()
    facts, hubs, _llm = await _run(
        [doc],
        [
            ExtractionResult(
                facts=[_cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.",
                             corpus.ABOUT_SPAN, "a")],
                hubs=[CandidateHub(label="Quarrystone Labs", type="company", doc_id=doc.doc_id)],
            )
        ],
    )

    assert [h.evidence_fact_ids for h in hubs] == [[facts[0].fact_id]]


# --------------------------------------------------------------------------
# stop hubs (DESIGN Decision 3)
# --------------------------------------------------------------------------


def test_the_stop_list_is_the_one_design_decision_3_names():
    assert STOP_HUB_LABELS == {
        "texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor",
    }


async def test_stop_labels_are_dropped_while_rare_hubs_naming_them_in_their_id_survive():
    doc = corpus.about_doc()
    stats = ExtractionStats()
    _facts, hubs, _llm = await _run(
        [doc],
        [
            ExtractionResult(
                facts=[
                    _cand(doc, "Runa Okonkwo co-founded Quarrystone Labs.",
                          corpus.ABOUT_SPAN, "a"),
                    _cand(doc, "Quarrystone Labs raised money from Foundry Seed.",
                          corpus.FOUNDRY_SPAN, "b"),
                ],
                hubs=[
                    # Rare, high-signal hubs whose canonical id CONTAINS a stop word.
                    CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=doc.doc_id,
                                 evidence_fact_ids=["b"]),
                    CandidateHub(label="Developer platform", type="technology",
                                 doc_id=doc.doc_id, evidence_fact_ids=["a"]),
                    # The same words as LABELS: these are the real stop hubs.
                    CandidateHub(label="Investor", type="topic", doc_id=doc.doc_id,
                                 evidence_fact_ids=["b"]),
                    CandidateHub(label="  TECHNOLOGY  ", type="topic", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="AI", type="topic", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                    CandidateHub(label="Texas", type="city", doc_id=doc.doc_id,
                                 evidence_fact_ids=["a"]),
                ],
            )
        ],
        stats=stats,
    )

    ids = {h.hub_id for h in hubs}
    assert "investor:foundry-seed-2019" in ids
    assert "technology:developer-platform" in ids
    assert not {h.label.strip().casefold() for h in hubs} & STOP_HUB_LABELS
    assert not ids & {"topic:investor", "topic:technology", "topic:ai", "city:texas"}
    assert stats.dropped_stop_hubs == 4, "a stop label is dropped however it was cased or padded"
    assert stats.hubs_kept == 2
