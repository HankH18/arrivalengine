"""T-0 acceptance 1: contracts.py matches DESIGN §Interfaces and survives a JSON round trip."""

from __future__ import annotations

import inspect
import typing
from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel

from arrival.contracts import (
    Budget,
    BuildReport,
    Connector,
    Digest,
    Dossier,
    ExclusionReason,
    Fact,
    FactCategory,
    Hub,
    HubContribution,
    HubType,
    LLMClient,
    LLMError,
    Match,
    PersonRef,
    Provenance,
    RawDoc,
    Resolution,
    SourceKind,
    Verdict,
)

pytestmark = pytest.mark.ticket("T-0")

NOW = datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC)


def _provenance(doc_id: str = "d1", confidence: float = 0.9) -> Provenance:
    return Provenance(
        doc_id=doc_id,
        url=f"https://northgatelabs.example/{doc_id}",
        source_kind="self_page",
        quote="a verbatim span",
        published_at=date(2026, 6, 14),
        retrieved_at=NOW,
        confidence=confidence,
    )


def _dossier() -> Dossier:
    """A Dossier with every optional field populated and every list non-empty."""
    kept = Fact(
        fact_id="f-kept",
        text="She founded a company that makes code review legible.",
        category="current_work",
        provenance=_provenance("d1"),
    )
    excluded = Fact(
        fact_id="f-excluded",
        text="She owns a house on a named street.",
        category="affiliation",
        provenance=_provenance("d2", 0.95),
        excluded=True,
        exclusion_reason="home_or_property",
    )
    return Dossier(
        person=PersonRef(person_id="alpha", name="Teodoro Vance", details=["CTO", "Austin"]),
        resolution=Resolution(
            person_id="alpha",
            status="resolved",
            strong_keys={"wikidata_qid": "Q1", "github": "tvance-ng"},
            accepted_doc_ids=["d1", "d2"],
            rejected=[
                Verdict(
                    doc_id="d3",
                    match="no",
                    confidence=0.95,
                    evidence="a luthier in Corpus Christi",
                    disambiguator="occupation",
                )
            ],
            confidence=0.92,
        ),
        facts=[kept, excluded],
        hubs=[
            Hub(
                hub_id="investor:foundry-seed-2019",
                label="Foundry Seed 2019",
                type="investor",
                recency=1.0,
                evidence_fact_ids=["f-kept"],
            ),
            Hub(hub_id="wd:Q42", label="Austin", type="city"),
        ],
        built_at=NOW,
        schema_version=1,
    )


def test_contracts_roundtrip():
    """A fully populated Dossier survives model_dump_json -> model_validate_json unchanged."""
    original = _dossier()
    restored = Dossier.model_validate_json(original.model_dump_json())
    assert restored == original
    # and the round trip is stable, not merely equal on the first hop
    assert restored.model_dump_json() == original.model_dump_json()


def test_roundtrip_preserves_the_details_that_matter():
    restored = Dossier.model_validate_json(_dossier().model_dump_json())
    assert restored.facts[1].excluded is True
    assert restored.facts[1].exclusion_reason == "home_or_property"
    assert restored.resolution.rejected[0].match == "no"
    assert restored.hubs[1].recency == 1.0  # default survived
    assert restored.hubs[1].evidence_fact_ids == []
    assert restored.facts[0].provenance.published_at == date(2026, 6, 14)


def test_mutable_defaults_are_not_shared():
    """Field(default_factory=...) — two instances must not share one list/dict."""
    a, b = PersonRef(person_id="a", name="A"), PersonRef(person_id="b", name="B")
    a.details.append("leaked?")
    assert b.details == []

    kwargs = {"status": "unresolved", "accepted_doc_ids": [], "rejected": [], "confidence": 0.0}
    ra = Resolution(person_id="a", **kwargs)
    rb = Resolution(person_id="b", **kwargs)
    ra.strong_keys["github"] = "leaked?"
    assert rb.strong_keys == {}

    ha = Hub(hub_id="topic:ai", label="AI", type="topic")
    hb = Hub(hub_id="city:austin", label="Austin", type="city")
    ha.evidence_fact_ids.append("leaked?")
    assert hb.evidence_fact_ids == []


def test_llm_error_is_an_exception():
    assert issubclass(LLMError, Exception)
    with pytest.raises(LLMError):
        raise LLMError("boom")


def test_both_protocols_are_runtime_checkable():
    """Downstream conformance tests use isinstance(); that needs runtime_checkable."""
    assert getattr(Connector, "_is_runtime_protocol", False)
    assert getattr(LLMClient, "_is_runtime_protocol", False)


def test_llm_client_structured_signature_is_keyword_only():
    signature = inspect.signature(LLMClient.structured)
    keyword_only = [
        name
        for name, p in signature.parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    ]
    assert keyword_only == ["system", "user", "schema", "max_tokens", "cache_prefix"]
    assert signature.parameters["max_tokens"].default == 2000
    assert signature.parameters["cache_prefix"].default is True

    hints = typing.get_type_hints(LLMClient.structured)
    assert hints["schema"] == type[BaseModel]
    assert hints["max_tokens"] is int
    assert hints["cache_prefix"] is bool


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        (
            SourceKind,
            {
                "self_page", "search", "wikidata", "wikipedia", "github", "edgar", "uspto",
                "propublica", "wayback", "hn", "openalex", "youtube", "podcast", "fec",
                "courtlistener",
            },
        ),
        (
            FactCategory,
            {
                "current_work", "collaborator", "interest", "recent_activity", "hook",
                "affiliation", "non_obvious",
            },
        ),
        (
            ExclusionReason,
            {
                "home_or_property", "family", "health", "legal", "wealth", "political",
                "low_confidence", "source_kind_not_displayable",
            },
        ),
        (
            HubType,
            {
                "company", "investor", "school", "board", "topic", "city", "technology",
                "event", "cause", "person",
            },
        ),
    ],
)
def test_literal_members_are_verbatim(alias, expected):
    """A wrong Literal member here costs nine tickets, so pin every one of them."""
    assert set(typing.get_args(alias)) == expected


def test_budget_defaults():
    b = Budget()
    assert (b.docs_per_connector, b.max_docs_total, b.max_llm_calls) == (8, 40, 80)


def test_every_designed_model_is_exported_and_constructible():
    """Smoke-construct the models the round trip does not reach."""
    prov = _provenance()
    fact = Fact(fact_id="f", text="t", category="hook", provenance=prov)
    hub = Hub(hub_id="topic:ai", label="AI", type="topic")
    contribution = HubContribution(
        hub=hub, idf_weight=0.2877, recency=1.0, type_boost=1.5, contribution=0.4315
    )
    match = Match(
        other=PersonRef(person_id="delta", name="Hollis Trent"),
        score=100.0,
        contributions=[contribution],
        path=["person:charlie", "hub:investor:foundry-seed-2019", "person:delta"],
        why="Both were seeded by the same fund.",
    )
    digest = Digest(
        digest_id="dg1",
        person=PersonRef(person_id="charlie", name="Selin Ardahan"),
        who_line="She founded Quillmark in Austin.",
        meet=[match],
        lately=[fact],
        non_obvious=None,
        say_out_loud="Ask about reading four hundred code reviews out loud.",
        sources=[prov],
        exclusion_policy="We never surface …",
        created_at=NOW,
    )
    report = BuildReport(people=[{"person_id": "charlie"}], started_at=NOW, finished_at=NOW)
    doc = RawDoc(
        doc_id="d1",
        source_kind="wayback",
        url="https://web.archive.example/x",
        text="some text",
        fetched_at=NOW,
    )
    assert digest.non_obvious is None
    assert report.people[0]["person_id"] == "charlie"
    assert doc.title == ""
    assert doc.published_at is None
