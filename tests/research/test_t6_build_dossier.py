"""T-6 acceptance 1 and 2: what one person's build produces, and what it refuses to do."""

from __future__ import annotations

import pytest
from t6_corpus import PERSON, PRIVATE, docs_for, script_extraction, script_verdicts

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
