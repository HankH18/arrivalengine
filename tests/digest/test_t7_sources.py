"""T-7 acceptance 2 (R9, S6): "Why we know this" covers exactly what the page shows.

``sources`` must carry every ``Provenance`` behind the facts in ``who_line``, ``lately``
and ``non_obvious``, plus — for each Meet row — the ARRIVING person's facts named in
``contributions[*].hub.evidence_fact_ids``; deduped by ``doc_id``, in first-use order.
"""

from __future__ import annotations

import pytest
from t7_digest_helpers import fact_of, load, make_match, replacing, variant

from arrival.digest import make_digest
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")


def _llm() -> LLMDouble:
    double = LLMDouble()
    double.queue({"line": "Ask about the evaluation harness."})
    return double


@pytest.fixture
def alpha():
    return load("alpha")


@pytest.fixture
def matches(alpha):
    """Two Meet rows whose hubs name different evidence facts of alpha's."""
    return [
        make_match(alpha, load("bravo"), score=100.0, hub_id="company:northgate-labs",
                   why="Both work on machine learning in Austin."),
        make_match(alpha, load("charlie"), score=40.0, hub_id="technology:evaluation-harnesses",
                   why="Both build evaluation harnesses."),
    ]


async def test_sources_cover_all(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    doc_ids = [p.doc_id for p in digest.sources]
    assert doc_ids, "nothing is citable"
    assert len(doc_ids) == len(set(doc_ids)), f"sources are not deduped by doc_id: {doc_ids}"

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    assert shown, "positive control: nothing shown, so coverage is vacuous"
    for fact in shown:
        assert fact.provenance.doc_id in doc_ids, f"{fact.fact_id} is shown with no citation"

    # who_line is built from current_work, so its document is cited too.
    assert fact_of(alpha, "alpha-work").provenance.doc_id in doc_ids

    # Every Meet row's hub evidence, which is the arriving person's own facts.
    for match in digest.meet:
        for contribution in match.contributions:
            for fact_id in contribution.hub.evidence_fact_ids:
                assert fact_of(alpha, fact_id).provenance.doc_id in doc_ids, (
                    f"the hub behind {match.other.person_id} cites {fact_id}, which is not "
                    "in 'Why we know this'"
                )

    # Nothing is cited that no shown fact stands behind.
    corpus = {f.provenance.doc_id for f in alpha.facts}
    assert set(doc_ids) <= corpus


async def test_sources_are_in_first_use_order(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    doc_ids = [p.doc_id for p in digest.sources]
    first_use = []
    for fact in list(digest.lately) + (
        [digest.non_obvious] if digest.non_obvious else []
    ):
        if fact.provenance.doc_id not in first_use:
            first_use.append(fact.provenance.doc_id)
    positions = [doc_ids.index(d) for d in first_use]
    assert positions == sorted(positions), (
        f"sources are not in first-use order: {first_use} land at {positions} in {doc_ids}"
    )


async def test_a_hub_whose_evidence_was_taste_excluded_is_never_cited(alpha):
    """The digest, not the graph, is where a withheld hub stops being citable.

    ``graph.py`` deliberately does not filter hubs — matching is not display — so a hub can
    legitimately score a match on evidence the host must never see. R12 has to bite here or
    the exclusion leaks into "Why we know this" through the back door.
    """
    tainted = variant(fact_of(alpha, "alpha-work"), excluded=True, exclusion_reason="family")
    dossier = replacing(alpha, {"alpha-work": tainted})
    match = make_match(dossier, load("bravo"), score=100.0, hub_id="company:northgate-labs",
                       why="Both work on machine learning in Austin.")
    assert "alpha-work" in match.contributions[0].hub.evidence_fact_ids

    digest = await make_digest(dossier, [match], _llm())

    assert digest.meet, "positive control: the match was dropped, so nothing was proven"
    assert tainted.provenance.doc_id not in {p.doc_id for p in digest.sources}, (
        "an excluded fact's document was cited because a hub named it as evidence"
    )


async def test_a_document_behind_two_shown_facts_is_cited_once(alpha):
    """S6 dedupes the SOURCE list by doc_id; it never dedupes the bullets."""
    digest = await make_digest(alpha, [], _llm())

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    shared = [f for f in shown if f.provenance.doc_id == "b1159ac929dac1e6"]
    assert len(shared) >= 2, (
        "fixture changed: this test needs two shown facts extracted from one document"
    )
    doc_ids = [p.doc_id for p in digest.sources]
    assert doc_ids.count("b1159ac929dac1e6") == 1


async def test_sources_are_empty_of_documents_behind_nothing_shown(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    cited = {p.doc_id for p in digest.sources}
    shown_docs = {f.provenance.doc_id for f in digest.lately}
    if digest.non_obvious is not None:
        shown_docs.add(digest.non_obvious.provenance.doc_id)
    shown_docs.add(fact_of(alpha, "alpha-work").provenance.doc_id)
    for match in digest.meet:
        for contribution in match.contributions:
            for fact_id in contribution.hub.evidence_fact_ids:
                shown_docs.add(fact_of(alpha, fact_id).provenance.doc_id)

    assert cited <= shown_docs, f"cited with nothing behind it: {sorted(cited - shown_docs)}"
