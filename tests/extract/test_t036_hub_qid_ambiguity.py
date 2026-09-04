"""T-036: a hub's QID is the graph-wide join key, so it is ranked on evidence or refused.

`hub_id` beginning `wd:` wins `graph._canonical_hub_ids`' identity election outright, and
`graph.build_graph` then puts every hub electing that id on ONE node — pooling their
labels, their types and their `evidence_fact_ids`. A hub that takes the wrong QID
therefore does not merely mis-name itself: it fabricates a join between two entities that
are not the same entity, for every person in the graph, and the union of their evidence is
what `digest._hub_evidence` prints under "Why we know this".

The check `_states_qid` performs is real but it was applied with `any()` over the hub's
evidence documents, which asks only "did SOME document we are holding mention this string"
— and a person's own Wikidata item mentions their employer, so it answers yes for a hub it
has nothing to do with. `resolve._best` had the same shape and the same answer: rank the
candidates on evidence, and refuse when the top two tie.

Graded against `graph.canonical_hub_ids`-visible ids and literals; nothing here compares
against a value this ticket could edit into agreement.
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


async def test_a_qid_stated_only_by_an_unrelated_item_does_not_key_the_hub():
    """The person's own item states her employer, so `any(...)` confirms the wrong QID.

    The hub is the FUND. The QID the model names is the PERSON's. Both documents are in
    the hub's evidence set, so the pre-repair check found a Wikidata document carrying the
    string and believed it.
    """
    person_item, fund_item = corpus.person_item_doc(), corpus.fund_item_doc()
    assert "Q900000411" in person_item.text and "Foundry Seed 2019" not in person_item.text
    assert "Q4242" in fund_item.text and "Foundry Seed 2019" in fund_item.text

    _facts, hubs, _llm = await _run(
        [person_item, fund_item],
        [
            ExtractionResult(
                facts=[
                    _cand(person_item, "Runa Okonkwo is a human.", corpus.PERSON_ITEM_SPAN, "a"),
                    _cand(
                        fund_item,
                        "Foundry Seed 2019 is a venture capital fund.",
                        corpus.FUND_ITEM_SPAN,
                        "b",
                    ),
                ],
                hubs=[
                    CandidateHub(
                        label="Foundry Seed 2019",
                        type="investor",
                        doc_id=fund_item.doc_id,
                        evidence_fact_ids=["a", "b"],
                        qid="Q900000411",
                    )
                ],
            )
        ],
    )

    (hub,) = [h for h in hubs if h.label == "Foundry Seed 2019"]
    assert hub.hub_id != "wd:Q900000411", (
        "the fund took the PERSON's Wikidata id as its graph-wide join key, which merges "
        "the two entities on one node for every person in the graph"
    )
    assert hub.hub_id == "investor:foundry-seed-2019"


async def test_the_best_evidenced_qid_is_the_only_one_that_can_be_confirmed():
    """The same corpus, with the model naming the RIGHT id: it must still be believed."""
    person_item, fund_item = corpus.person_item_doc(), corpus.fund_item_doc()
    _facts, hubs, _llm = await _run(
        [person_item, fund_item],
        [
            ExtractionResult(
                facts=[
                    _cand(person_item, "Runa Okonkwo is a human.", corpus.PERSON_ITEM_SPAN, "a"),
                    _cand(
                        fund_item,
                        "Foundry Seed 2019 is a venture capital fund.",
                        corpus.FUND_ITEM_SPAN,
                        "b",
                    ),
                ],
                hubs=[
                    CandidateHub(
                        label="Foundry Seed 2019",
                        type="investor",
                        doc_id=fund_item.doc_id,
                        evidence_fact_ids=["a", "b"],
                        qid="Q4242",
                    )
                ],
            )
        ],
    )

    (hub,) = [h for h in hubs if h.label == "Foundry Seed 2019"]
    assert hub.hub_id == "wd:Q4242", (
        "refusing an ambiguous QID must not degrade into refusing every QID"
    )


async def test_two_mirrors_of_one_entity_under_different_qids_are_refused_not_ranked_by_spelling():
    """Two Wikidata mirrors of the same fund, each stating a different id.

    `_most_common` broke this tie lexicographically, so `Q4242` beat `Q7777` for no reason
    connected to the evidence. An arbitrary winner here is a graph-wide join key.
    """
    fund_item, fund_mirror = corpus.fund_item_doc(), corpus.fund_mirror_doc()
    _facts, hubs, _llm = await _run(
        [fund_item, fund_mirror],
        [
            ExtractionResult(
                facts=[
                    _cand(
                        fund_item,
                        "Foundry Seed 2019 is a venture capital fund.",
                        corpus.FUND_ITEM_SPAN,
                        "a",
                    ),
                    _cand(
                        fund_mirror,
                        "Foundry Seed 2019 is recorded in a second catalogue.",
                        corpus.FUND_MIRROR_SPAN,
                        "b",
                    ),
                ],
                hubs=[
                    CandidateHub(
                        label="Foundry Seed 2019",
                        type="investor",
                        doc_id=fund_item.doc_id,
                        evidence_fact_ids=["a"],
                        qid="Q4242",
                    ),
                    CandidateHub(
                        label="Foundry Seed 2019",
                        type="investor",
                        doc_id=fund_mirror.doc_id,
                        evidence_fact_ids=["b"],
                        qid="Q7777",
                    ),
                ],
            )
        ],
    )

    (hub,) = hubs
    assert hub.hub_id == "investor:foundry-seed-2019", (
        f"two equally evidenced QIDs cannot elect one of themselves; got {hub.hub_id!r}"
    )
    assert len(hub.evidence_fact_ids) == 2, "refusing the QID must not lose the evidence"


@pytest.mark.parametrize("named", ["Q4242", "Q7777"])
async def test_the_refusal_does_not_depend_on_which_mirror_the_model_named_first(named):
    """Whichever id the model picks, the answer is the same — that is what refusal means."""
    fund_item, fund_mirror = corpus.fund_item_doc(), corpus.fund_mirror_doc()
    _facts, hubs, _llm = await _run(
        [fund_item, fund_mirror],
        [
            ExtractionResult(
                facts=[
                    _cand(
                        fund_item,
                        "Foundry Seed 2019 is a venture capital fund.",
                        corpus.FUND_ITEM_SPAN,
                        "a",
                    ),
                    _cand(
                        fund_mirror,
                        "Foundry Seed 2019 is recorded in a second catalogue.",
                        corpus.FUND_MIRROR_SPAN,
                        "b",
                    ),
                ],
                hubs=[
                    CandidateHub(
                        label="Foundry Seed 2019",
                        type="investor",
                        doc_id=fund_item.doc_id,
                        evidence_fact_ids=["a", "b"],
                        qid=named,
                    )
                ],
            )
        ],
    )

    (hub,) = hubs
    assert hub.hub_id == "investor:foundry-seed-2019"
