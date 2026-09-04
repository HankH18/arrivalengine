"""T-2 acceptance 4: the ticket's own synthetic resolve corpus.

Five invented cases in `tests/fixtures/resolve_cases/`, every person, company and place
fictional (SPEC §5): a same-name decoy in the SPEC Q1 pattern (same name, different
profession, one deceased), a case that must come out `unresolved` because its two `yes`
verdicts corroborate ONE attribute, a GitHub profile confirmed on name AND company that
carries a resolution alone, a hallucinated span that must be downgraded, and an SEC filing
matched on name AND company.

These are this ticket's own fixtures and are deliberately NOT the frozen corpus: the frozen
suite grades against `.swarm-loop/acceptance/fixtures/`, which no worker may write, and a
suite that grades a gradee against files the gradee owns measures nothing. What these buy
is the other direction — the cases the frozen corpus does not contain (a CONFIRMED github
handle, a CONFIRMED CIK), so the strong-key arm is exercised positively as well as
negatively.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from doubles import LLMDouble

from arrival.contracts import PersonRef, RawDoc
from arrival.resolve import DocVerdict, resolve
from arrival.util import normalize_ws

pytestmark = pytest.mark.ticket("T-2")

CASE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "resolve_cases"
CASE_IDS = sorted(path.stem for path in CASE_DIR.glob("*.json"))


def load_case(case_id: str) -> dict:
    return json.loads((CASE_DIR / f"{case_id}.json").read_text(encoding="utf-8"))


def scripted_llm(case: dict) -> LLMDouble:
    """An `LLMDouble` that answers with the case's verdict for whichever doc is named."""
    llm = LLMDouble()
    for scripted in case["scripted_verdicts"]:
        llm.when("DocVerdict", scripted["doc_id"], DocVerdict(**scripted))
    return llm


def run_case(case: dict, llm: LLMDouble | None = None):
    import asyncio

    person = PersonRef.model_validate(case["person"])
    docs = [RawDoc.model_validate(doc) for doc in case["docs"]]
    client = llm if llm is not None else scripted_llm(case)
    return asyncio.run(resolve(person, docs, client)), client, docs


def test_the_case_corpus_is_present_and_well_formed():
    """A corpus that half-loads is a broken measuring stick, not a passing suite."""
    assert len(CASE_IDS) >= 3, f"T-2 acceptance 4 wants >= 3 synthetic cases, found {CASE_IDS}"
    assert "decoy-retired-namesake" in CASE_IDS, "the same-name decoy case is missing"
    statuses = set()
    for case_id in CASE_IDS:
        case = load_case(case_id)
        assert case["case_id"] == case_id
        for key in ("person", "docs", "scripted_verdicts", "expect"):
            assert key in case, f"{case_id}: missing {key!r}"
        doc_ids = [doc["doc_id"] for doc in case["docs"]]
        assert doc_ids and len(set(doc_ids)) == len(doc_ids), f"{case_id}: bad doc ids"
        for doc in case["docs"]:
            expected = hashlib.sha1(doc["url"].encode()).hexdigest()[:16]
            assert doc["doc_id"] == expected, f"{case_id}: doc_id is not sha1(url)[:16]"
            assert doc["text"].strip(), f"{case_id}: empty document text"
        assert {v["doc_id"] for v in case["scripted_verdicts"]} == set(doc_ids)
        statuses.add(case["expect"]["status"])
        if case["expect"]["status"] == "unresolved":
            assert case["expect"]["accepted_doc_ids"] == []
    assert statuses == {"resolved", "unresolved"}, (
        "the corpus must contain at least one case of each outcome, or it only measures one "
        f"half of DESIGN Decision 4: {sorted(statuses)}"
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_resolver_reproduces_each_synthetic_case(case_id):
    case = load_case(case_id)
    expect = case["expect"]
    resolution, llm, docs = run_case(case)

    assert resolution.person_id == case["person"]["person_id"]
    assert resolution.status == expect["status"], expect["note"]
    assert sorted(resolution.accepted_doc_ids) == sorted(expect["accepted_doc_ids"]), expect["note"]
    assert sorted(resolution.strong_keys) == sorted(expect["strong_keys_present"]), expect["note"]

    # Every document was put to the model: a document nobody asked about was assumed.
    asked = {call.user for call in llm.calls_for("DocVerdict")}
    for doc in docs:
        assert any(doc.doc_id in prompt for prompt in asked), (
            f"{case_id}: {doc.doc_id} never reached the model"
        )
    assert llm.call_count == len(docs), f"{case_id}: one verdict per document, no more"


def test_the_decoy_documents_stay_out_and_the_target_documents_come_in():
    """SPEC S4, both halves: rejecting everything is abstention, not resolution."""
    case = load_case("decoy-retired-namesake")
    decoy_ids = [v["doc_id"] for v in case["scripted_verdicts"] if v["match"] == "no"]
    target_ids = list(case["expect"]["accepted_doc_ids"])
    assert decoy_ids and target_ids

    resolution, _llm, _docs = run_case(case)
    accepted = set(resolution.accepted_doc_ids)
    rejected = {verdict.doc_id for verdict in resolution.rejected}
    for doc_id in decoy_ids:
        assert doc_id not in accepted
        assert doc_id in rejected, "a vetoed document must survive in Resolution.rejected for /debug"
    for doc_id in target_ids:
        assert doc_id in accepted


def test_the_decoy_wikidata_item_matches_a_detail_and_still_earns_no_key():
    """The veto is what stands between a name+detail QID match and a wrong strong key.

    The decoy's Wikidata item names the target's city (her drawings are archived in
    Dunedin), so it satisfies the name-AND-detail test a QID key needs. It earns no key
    only because its verdict is a `no` asserting a conflicting employer and city.
    """
    case = load_case("decoy-retired-namesake")
    docs = [RawDoc.model_validate(doc) for doc in case["docs"]]
    person = PersonRef.model_validate(case["person"])
    item = next(doc for doc in docs if doc.source_kind == "wikidata")

    # Fixture pre-condition: the item really would key if it were admitted.
    from arrival.resolve import strong_keys_for

    assert strong_keys_for(person, [item]) == {"wikidata_qid": "Q900000731"}, (
        "this case stops proving anything if the decoy item no longer matches name + detail"
    )
    assert normalize_ws("Dunedin") in normalize_ws(item.text)

    resolution, _llm, _docs = run_case(case)
    assert dict(resolution.strong_keys) == {}
