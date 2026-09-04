"""T-010 / T-011 / T-014: a hub's identity may not depend on what the model happened to say.

T-3 acceptance 3 promises "the same label across two docs yields ONE Hub with merged
`evidence_fact_ids`". The frozen suite only ever supplies `type="company"` in both
documents, so its green proves the same-TYPE case and nothing else. Measured before the
repair: same label + different type (`investor` vs `company`) produced TWO hubs with the
evidence split, and downstream two people who genuinely share that hub scored 0.

The same class of defect sits in `_merge_groups`: when two groups collapse onto one
`hub_id` (only reachable through a shared Wikidata QID) the FIRST group's label and type
won, and "first" is the model's output ordering. Since `type` drives T-5's `TYPE_BOOST`
(investor 1.5, city 0.5), that let LLM output order move a match score.

Every test here therefore asserts an OUTPUT THAT MUST NOT MOVE when the model's list is
permuted — the assertion the original suite could not make, because it never permuted.
"""

from __future__ import annotations

from itertools import permutations

import pytest
import t3_corpus as corpus

from arrival.extract import (
    CandidateFact,
    CandidateHub,
    ExtractionResult,
    ExtractionStats,
    extract,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")


def _cand(doc, text, quote, fact_id):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id)


async def _run(docs, results, stats=None):
    llm = LLMDouble()
    for result in results:
        llm.queue(result)
    facts, hubs = await extract(
        corpus.PERSON, corpus.resolution_for(*docs), list(docs), llm, stats=stats
    )
    return facts, hubs


def _shape(hubs):
    """The part of a hub that must be a function of the evidence, not of the ordering."""
    return sorted(
        (h.hub_id, h.label, h.type, h.recency, tuple(sorted(h.evidence_fact_ids))) for h in hubs
    )


# --------------------------------------------------------------------------
# T-011: one label, two types, still one hub
# --------------------------------------------------------------------------


async def test_one_label_typed_differently_in_two_documents_is_still_one_hub():
    """T-3 acceptance 3, the case the frozen test never supplies.

    Pre-repair measurement: two hubs, `company:foundry-seed-2019` and
    `investor:foundry-seed-2019`, one evidence fact each.
    """
    about, roadmap = corpus.about_doc(), corpus.roadmap_doc()
    facts, hubs = await _run(
        [about, roadmap],
        [
            ExtractionResult(
                facts=[
                    _cand(about, "Quarrystone Labs raised money from Foundry Seed.",
                          corpus.FOUNDRY_SPAN, "a"),
                    _cand(roadmap, "Quarrystone Labs opened its roadmap.",
                          corpus.ROADMAP_SPAN, "b"),
                ],
                hubs=[
                    CandidateHub(label="Foundry Seed 2019", type="investor",
                                 doc_id=about.doc_id, evidence_fact_ids=["a"]),
                    CandidateHub(label="Foundry Seed 2019", type="company",
                                 doc_id=roadmap.doc_id, evidence_fact_ids=["b"]),
                ],
            )
        ],
    )

    matching = [h for h in hubs if h.hub_id.endswith(":foundry-seed-2019")]
    assert len(matching) == 1, (
        "one label described with two types is one entity, not two: "
        f"got {[h.hub_id for h in hubs]}"
    )
    by_id = {f.fact_id: f for f in facts}
    evidence = matching[0].evidence_fact_ids
    assert {by_id[fid].provenance.doc_id for fid in evidence} == {about.doc_id, roadmap.doc_id}, (
        "the merged hub must keep BOTH documents' evidence, or the merge lost half of it"
    )


async def test_the_reconciled_type_is_the_majority_one_not_the_first_seen():
    """Two documents call it an investor, one calls it a company: investor wins."""
    docs = [corpus.trade_doc(n) for n in range(3)]
    types = ["investor", "company", "investor"]
    result = ExtractionResult(
        facts=[
            _cand(d, f"Foundry Seed backed a company, note {i}.", corpus.TRADE_SPAN, f"f{i}")
            for i, d in enumerate(docs)
        ],
        hubs=[
            CandidateHub(label="Foundry Seed 2019", type=t, doc_id=d.doc_id,
                         evidence_fact_ids=[f"f{i}"])
            for i, (d, t) in enumerate(zip(docs, types, strict=True))
        ],
    )
    _facts, hubs = await _run(docs, [result])

    assert [h.hub_id for h in hubs] == ["investor:foundry-seed-2019"]
    assert hubs[0].type == "investor"


async def test_the_emitted_hub_is_identical_under_every_permutation_of_the_models_hubs():
    """The general statement of T-011 and T-014: output order may not move the answer."""
    docs = [corpus.trade_doc(n) for n in range(3)]
    facts = [
        _cand(d, f"Foundry Seed backed a company, note {i}.", corpus.TRADE_SPAN, f"f{i}")
        for i, d in enumerate(docs)
    ]
    hubs_in = [
        CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=docs[0].doc_id,
                     evidence_fact_ids=["f0"]),
        CandidateHub(label="foundry seed 2019", type="company", doc_id=docs[1].doc_id,
                     evidence_fact_ids=["f1"]),
        CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=docs[2].doc_id,
                     evidence_fact_ids=["f2"]),
    ]

    shapes = set()
    for order in permutations(range(3)):
        _f, hubs = await _run(
            docs,
            [ExtractionResult(facts=facts, hubs=[hubs_in[i] for i in order])],
        )
        shapes.add(tuple(_shape(hubs)))

    assert len(shapes) == 1, (
        f"the model's hub ORDER changed the emitted hubs: {sorted(shapes)}"
    )
    (only,) = shapes
    assert len(only) == 1, f"one label is one hub, got {only}"
    assert only[0][0] == "investor:foundry-seed-2019", (
        "the majority type wins, so the id is a function of the evidence, not of the order"
    )
    assert only[0][1] == "Foundry Seed 2019", "and so is the displayed label"


# --------------------------------------------------------------------------
# T-014: the cross-id merge branch — two labels, one QID
# --------------------------------------------------------------------------


async def test_two_labels_sharing_one_qid_merge_and_the_survivor_is_order_independent():
    """The `_merge_groups` branch no test in the repo executed, and its order dependence.

    Pre-repair measurement, permuting ONLY the model's hub list: order (0,1) gave
    `wd:Q4242 label='Foundry Seed 2019' type=investor`; order (1,0) gave
    `wd:Q4242 label='Foundry Capital' type=company`.
    """
    fund = corpus.fund_doc()
    assert "Q4242" in fund.text, "fixture pre-condition: the item states its own QID"
    facts = [_cand(fund, "Foundry Seed 2019 is a venture capital fund.", corpus.FUND_SPAN, "a")]
    hubs_in = [
        CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=fund.doc_id,
                     evidence_fact_ids=["a"], qid="Q4242"),
        CandidateHub(label="Foundry Capital", type="company", doc_id=fund.doc_id,
                     evidence_fact_ids=["a"], qid="Q4242"),
    ]

    shapes = set()
    for order in ((0, 1), (1, 0)):
        _f, hubs = await _run(
            [fund],
            [ExtractionResult(facts=facts, hubs=[hubs_in[i] for i in order])],
        )
        assert [h.hub_id for h in hubs] == ["wd:Q4242"], (
            f"two labels stating one QID are one node, got {[h.hub_id for h in hubs]}"
        )
        shapes.add(tuple(_shape(hubs)))

    assert len(shapes) == 1, (
        f"the model's output order decided the merged hub's label/type: {sorted(shapes)}"
    )


async def test_a_cross_id_merge_keeps_every_evidence_fact_and_the_freshest_recency():
    """The merge branch's body, executed and asserted rather than merely reached."""
    fund = corpus.fund_doc()  # published_at unknown -> recency 0.5
    trade = corpus.trade_doc(0)  # 2026-02-11 -> recency 1.0
    facts = [
        _cand(fund, "Foundry Seed 2019 is a venture capital fund.", corpus.FUND_SPAN, "a"),
        _cand(trade, "Foundry Seed 2019 has backed eleven companies.", corpus.TRADE_SPAN, "b"),
    ]
    stats = ExtractionStats()
    _f, hubs = await _run(
        [fund, trade],
        [
            ExtractionResult(
                facts=facts,
                hubs=[
                    CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=fund.doc_id,
                                 evidence_fact_ids=["a"], qid="Q4242"),
                    # A different label for the same fund, also anchored on the Wikidata
                    # fact (so the QID is believed) but additionally evidenced by the
                    # fresher trade-press document.
                    CandidateHub(label="Foundry Capital", type="investor", doc_id=fund.doc_id,
                                 evidence_fact_ids=["a", "b"], qid="Q4242"),
                ],
            )
        ],
        stats=stats,
    )

    assert [h.hub_id for h in hubs] == ["wd:Q4242"]
    assert len(hubs[0].evidence_fact_ids) == 2, "a merge that drops evidence is not a merge"
    assert hubs[0].recency == 1.0, "the merged node keeps the FRESHEST edge, not the first"
    assert stats.hubs_kept == 1, "hubs_kept counts nodes emitted, not groups accumulated"


async def test_a_qid_only_one_of_two_documents_states_still_keys_the_whole_hub():
    """T-010(b) as far as one dossier can see it: within a person, wd: wins over the slug."""
    fund = corpus.fund_doc()
    trade = corpus.trade_doc(0)
    _f, hubs = await _run(
        [fund, trade],
        [
            ExtractionResult(
                facts=[
                    _cand(fund, "Foundry Seed 2019 is a venture capital fund.",
                          corpus.FUND_SPAN, "a"),
                    _cand(trade, "Foundry Seed 2019 has backed eleven companies.",
                          corpus.TRADE_SPAN, "b"),
                ],
                hubs=[
                    CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=trade.doc_id,
                                 evidence_fact_ids=["b"]),
                    CandidateHub(label="Foundry Seed 2019", type="investor", doc_id=fund.doc_id,
                                 evidence_fact_ids=["a"], qid="Q4242"),
                ],
            )
        ],
    )

    assert [h.hub_id for h in hubs] == ["wd:Q4242"]
    assert len(hubs[0].evidence_fact_ids) == 2
