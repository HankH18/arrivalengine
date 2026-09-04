"""T-6 acceptance 1 and 2: what one person's build produces, and what it refuses to do."""

from __future__ import annotations

import pytest
from t6_corpus import EMPLOYER, PERSON, PRIVATE, docs_for, script_extraction, script_verdicts

from arrival.contracts import Budget, Dossier
from arrival.research import BuildTrace, build_dossier
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")


def _happy(*, private_index: int = 0):
    """Two sources, three documents, everything scripted to resolve and extract."""
    self_page = docs_for("self_page", 2, private_index=private_index)
    search = docs_for("search", 1)
    connectors = [
        ConnectorDouble(kind="self_page", docs=self_page),
        ConnectorDouble(kind="search", docs=search),
    ]
    llm = LLMDouble()
    script_verdicts(llm, self_page + search)
    script_extraction(llm, self_page + search)
    return connectors, llm


async def test_happy_path_keeps_facts_excludes_facts_and_records_a_hub():
    """Acceptance 1: >= 1 kept fact, >= 1 EXCLUDED fact, >= 1 hub — content, not shape.

    The excluded one matters on its own: taste is what this product is scored on, and a
    pipeline that never calls `apply_taste` produces a dossier that is structurally
    identical to one that does, right up until it reads someone's address out loud.
    """
    connectors, llm = _happy()

    dossier = await build_dossier(PERSON, connectors, llm, Budget())

    assert isinstance(dossier, Dossier)
    assert dossier.resolution.status == "resolved"
    kept = [fact for fact in dossier.facts if not fact.excluded]
    excluded = [fact for fact in dossier.facts if fact.excluded]
    assert kept, f"no fact survived the taste filter: {dossier.facts}"
    assert excluded, "the private sentence reached the dossier un-excluded"
    assert dossier.hubs, "a resolved dossier with no hubs cannot join anyone in the graph"

    private = [fact for fact in dossier.facts if PRIVATE in fact.text]
    assert private and all(fact.excluded for fact in private), private
    assert private[0].exclusion_reason == "home_or_property"
    for fact in kept:
        assert PRIVATE not in fact.text


async def test_every_kept_fact_quotes_a_document_the_resolver_accepted():
    """The dossier's citations must resolve inside the run that produced them."""
    connectors, llm = _happy()

    dossier = await build_dossier(PERSON, connectors, llm, Budget())

    accepted = set(dossier.resolution.accepted_doc_ids)
    assert accepted
    for fact in dossier.facts:
        assert fact.provenance.doc_id in accepted, (
            f"{fact.fact_id} cites {fact.provenance.doc_id}, which was never accepted"
        )
        assert fact.provenance.quote.strip()


async def test_unresolved_person_stores_nothing_and_never_calls_the_extractor():
    """Acceptance 2, asserted on the CALLS: 'kept nothing' != 'never went looking'."""
    docs = docs_for("search", 3)
    connectors = [ConnectorDouble(kind="search", docs=docs)]
    llm = LLMDouble()
    script_verdicts(llm, docs, match="no", confidence=0.95)
    script_extraction(llm, docs)  # scripted and deliberately never reached

    dossier = await build_dossier(PERSON, connectors, llm, Budget())

    assert dossier.resolution.status == "unresolved"
    assert dossier.facts == []
    assert dossier.hubs == []
    assert dossier.resolution.accepted_doc_ids == []
    schemas = {call.schema_name for call in llm.calls}
    assert schemas == {"DocVerdict"}, f"extraction ran for an unresolved person: {schemas}"


async def test_only_accepted_documents_reach_the_extractor():
    """A rejected document must not be quoted at, or even shown to, the extractor."""
    docs = docs_for("search", 3)
    rejected = docs[2]
    llm = LLMDouble()
    script_verdicts(llm, docs[:2])
    script_verdicts(llm, [rejected], match="no", confidence=0.4)
    script_extraction(llm, docs)

    dossier = await build_dossier(
        PERSON, [ConnectorDouble(kind="search", docs=docs)], llm, Budget()
    )

    assert dossier.resolution.status == "resolved"
    assert rejected.doc_id not in dossier.resolution.accepted_doc_ids
    extraction_prompts = [c.user for c in llm.calls if c.schema_name == "ExtractionResult"]
    assert extraction_prompts, "nothing was extracted, so this assertion is vacuous"
    for prompt in extraction_prompts:
        assert rejected.doc_id not in prompt


async def test_a_source_that_hangs_then_dies_is_reported_not_fatal():
    """DESIGN Decision 8. The double waits before it raises, which is how sources die."""
    docs = docs_for("self_page", 2, private_index=0)
    trace = BuildTrace()
    connectors = [
        ConnectorDouble(kind="self_page", docs=docs),
        ConnectorDouble(kind="github", raises=RuntimeError("502 from github"), delay=0.02),
        ConnectorDouble(kind="hn", docs=[]),
    ]
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)

    dossier = await build_dossier(PERSON, connectors, llm, Budget(), trace=trace)

    assert isinstance(dossier, Dossier)
    assert dossier.resolution.status == "resolved"
    assert sorted(trace.zero_result_sources) == ["github", "hn"]
    assert "self_page" not in trace.zero_result_sources
    assert "github" in trace.connector_errors
    assert "hn" not in trace.connector_errors, "an empty source is not a failing source"


async def test_a_dossier_survives_every_source_dying():
    """Nothing fetched is still a dossier — an unresolved one, with no LLM call at all."""
    trace = BuildTrace()
    connectors = [
        ConnectorDouble(kind="search", raises=RuntimeError("boom")),
        ConnectorDouble(kind="wikidata", docs=[]),
    ]
    llm = LLMDouble()

    dossier = await build_dossier(PERSON, connectors, llm, Budget(), trace=trace)

    assert dossier.resolution.status == "unresolved"
    assert llm.calls == []
    assert sorted(trace.zero_result_sources) == ["search", "wikidata"]


async def test_the_dossier_round_trips_through_json():
    """It is written to disk and read back by four other tickets; prove it survives."""
    connectors, llm = _happy()

    dossier = await build_dossier(PERSON, connectors, llm, Budget())
    again = Dossier.model_validate_json(dossier.model_dump_json())

    assert again.person.person_id == PERSON.person_id
    assert again.schema_version == 1
    assert [f.fact_id for f in again.facts] == [f.fact_id for f in dossier.facts]
    assert [h.hub_id for h in again.hubs] == [h.hub_id for h in dossier.hubs]


async def test_a_hub_whose_every_fact_was_excluded_never_reaches_the_dossier():
    """R11 across the stage boundary: `extract` evidences hubs BEFORE taste rules.

    `Hub` carries a label, T-5 joins on it and T-7 prints it in `Match.why`, so a hub left
    standing on excluded evidence reintroduces the sentence taste just removed. Reproduced
    before the guard existed: a `home_or_property` exclusion left `city:pecan-street` — the
    member's street — as a joinable node and a candidate match reason.
    """
    from arrival.extract import CandidateFact, CandidateHub, ExtractionResult

    docs = docs_for("self_page", 2, private_index=0)
    private_doc = docs[0]
    llm = LLMDouble()
    script_verdicts(llm, docs)
    llm.when(
        "ExtractionResult",
        private_doc.doc_id,
        ExtractionResult(
            facts=[
                CandidateFact(
                    doc_id=private_doc.doc_id,
                    fact_id="kept",
                    text=f"{PERSON.name} {EMPLOYER}.",
                    quote=f"{PERSON.name} {EMPLOYER}",
                    category="current_work",
                    natural_category="current_work",
                    confidence=0.9,
                ),
                CandidateFact(
                    doc_id=private_doc.doc_id,
                    fact_id="private",
                    text=f"{PERSON.name} {PRIVATE}.",
                    quote=f"{PERSON.name} {PRIVATE}",
                    category="hook",
                    natural_category="hook",
                    confidence=0.9,
                ),
            ],
            hubs=[
                CandidateHub(
                    label="Pecan Street",
                    type="city",
                    doc_id=private_doc.doc_id,
                    evidence_fact_ids=["private"],
                ),
                CandidateHub(
                    label="Quarrystone Labs",
                    type="company",
                    doc_id=private_doc.doc_id,
                    evidence_fact_ids=["kept"],
                ),
            ],
        ),
    )
    llm.when("ExtractionResult", docs[1].doc_id, ExtractionResult(facts=[], hubs=[]))
    trace = BuildTrace()

    dossier = await build_dossier(
        PERSON, [ConnectorDouble(kind="self_page", docs=docs)], llm, Budget(), trace=trace
    )

    # Positive control: the excluded fact really was produced and really was excluded.
    assert any(fact.excluded and PRIVATE in fact.text for fact in dossier.facts)

    labels = {hub.label for hub in dossier.hubs}
    assert "Pecan Street" not in labels, (
        f"a hub evidenced only by an excluded fact survived into the dossier: {labels}"
    )
    assert "Quarrystone Labs" in labels, (
        "the guard is too wide: a hub with surviving evidence was dropped too"
    )
    assert trace.hubs_dropped_unsupported == ["city:pecan-street"]


async def test_a_surviving_hub_loses_its_excluded_evidence_ids():
    """The narrower half of the same R11 leak, and the likelier one.

    A hub with one kept and one excluded evidence fact survives — correctly, it has real
    support — but `contracts.HubContribution` says its `evidence_fact_ids` "resolve in the
    arriving dossier", and the dossier keeps excluded facts by contract. An id left in the
    list is a live pointer from a displayed match reason to the withheld sentence.
    """
    from arrival.extract import CandidateFact, CandidateHub, ExtractionResult

    docs = docs_for("self_page", 2, private_index=0)
    private_doc = docs[0]
    llm = LLMDouble()
    script_verdicts(llm, docs)
    llm.when(
        "ExtractionResult",
        private_doc.doc_id,
        ExtractionResult(
            facts=[
                CandidateFact(
                    doc_id=private_doc.doc_id, fact_id="kept",
                    text=f"{PERSON.name} {EMPLOYER}.", quote=f"{PERSON.name} {EMPLOYER}",
                    category="current_work", natural_category="current_work", confidence=0.9,
                ),
                CandidateFact(
                    doc_id=private_doc.doc_id, fact_id="private",
                    text=f"{PERSON.name} {PRIVATE}.", quote=f"{PERSON.name} {PRIVATE}",
                    category="hook", natural_category="hook", confidence=0.9,
                ),
            ],
            hubs=[
                CandidateHub(
                    label="Quarrystone Labs", type="company", doc_id=private_doc.doc_id,
                    evidence_fact_ids=["kept", "private"],
                )
            ],
        ),
    )
    llm.when("ExtractionResult", docs[1].doc_id, ExtractionResult(facts=[], hubs=[]))

    dossier = await build_dossier(
        PERSON, [ConnectorDouble(kind="self_page", docs=docs)], llm, Budget()
    )

    excluded = {fact.fact_id for fact in dossier.facts if fact.excluded}
    kept = {fact.fact_id for fact in dossier.facts if not fact.excluded}
    # Positive controls: the hub really did survive, and one of its facts really was cut.
    assert excluded and kept
    hub = next(h for h in dossier.hubs if h.label == "Quarrystone Labs")
    assert hub.evidence_fact_ids, "the guard is too wide: it stripped the surviving fact too"

    assert not (set(hub.evidence_fact_ids) & excluded), (
        f"hub {hub.hub_id} still points at excluded facts: {hub.evidence_fact_ids}"
    )
    assert set(hub.evidence_fact_ids) <= kept
