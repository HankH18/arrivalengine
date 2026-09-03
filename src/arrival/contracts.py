"""Shared models and Protocols for the Arrival Engine.

FROZEN BY CONVENTION. This module is shipped by ticket T-0 and is the contract between
every other ticket. Import from here; never redefine, subclass or fork a model that lives
here. If a signature is wrong, escalate (EXECUTION §6) — do not edit it in a downstream
ticket, because nine tickets import these names.

Signatures are verbatim from DESIGN §Interfaces.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

__all__ = [
    "Budget",
    "BuildReport",
    "Connector",
    "Digest",
    "Dossier",
    "ExclusionReason",
    "Fact",
    "FactCategory",
    "Hub",
    "HubContribution",
    "HubType",
    "LLMClient",
    "LLMError",
    "Match",
    "PersonRef",
    "Provenance",
    "RawDoc",
    "Resolution",
    "SourceKind",
    "Verdict",
]


# --- errors ---------------------------------------------------------------


class LLMError(Exception):
    """Raised by an LLMClient when a structured call cannot be satisfied.

    DESIGN's LLMClient contract says `structured` "raises LLMError on invalid JSON after
    one retry". Nothing else in the design declares the type, so it is declared here with
    the Protocol that raises it. `tests.doubles.LLMDouble` raises the same type when a call
    is unscripted, so a test written against the double behaves like production.
    """


# --- identity -------------------------------------------------------------


class PersonRef(BaseModel):
    person_id: str  # slug(name) [+ "-" + slug(details[0]) on collision]
    name: str
    details: list[str] = []  # e.g. ["CEO of Acme", "Austin"]


# --- retrieval ------------------------------------------------------------

SourceKind = Literal[
    "self_page",
    "search",
    "wikidata",
    "wikipedia",
    "github",
    "edgar",
    "uspto",
    "propublica",
    "wayback",
    "hn",
    "openalex",
    "youtube",
    "podcast",
    "fec",
    "courtlistener",
]


class RawDoc(BaseModel):
    doc_id: str  # sha1(url)[:16]
    source_kind: SourceKind
    url: str
    title: str = ""
    text: str  # extracted plain text, <= 20k chars, never empty
    published_at: date | None = None
    fetched_at: datetime


@runtime_checkable
class Connector(Protocol):
    kind: SourceKind

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]: ...

    # budget = max docs to return. Must never raise on network/HTTP error: log and return [].


# --- resolution -----------------------------------------------------------


class Verdict(BaseModel):
    doc_id: str
    match: Literal["yes", "no", "unsure"]
    confidence: float  # 0..1
    evidence: str  # verbatim span from doc.text supporting the verdict
    disambiguator: str  # which detail (employer/city/role/handle) decided it


class Resolution(BaseModel):
    person_id: str
    status: Literal["resolved", "unresolved"]
    # {"wikidata_qid": "Q..", "github": "..", "company_domain": "..", "sec_cik": ".."}
    strong_keys: dict[str, str] = {}
    accepted_doc_ids: list[str]
    rejected: list[Verdict]  # kept for /debug
    confidence: float  # 0..1, overall


# --- facts ----------------------------------------------------------------

FactCategory = Literal[
    "current_work",
    "collaborator",
    "interest",
    "recent_activity",
    "hook",
    "affiliation",
    "non_obvious",
]

ExclusionReason = Literal[
    "home_or_property",
    "family",
    "health",
    "legal",
    "wealth",
    "political",
    "low_confidence",
    "source_kind_not_displayable",
]


class Provenance(BaseModel):
    doc_id: str
    url: str
    source_kind: SourceKind
    # verbatim; MUST be a substring of RawDoc.text after whitespace-normalisation
    # (DESIGN Decision 5 — this is the hallucination guard T-3 implements)
    quote: str
    published_at: date | None = None
    retrieved_at: datetime
    confidence: float


class Fact(BaseModel):
    fact_id: str
    text: str  # <= 200 chars, one sentence
    category: FactCategory
    provenance: Provenance
    excluded: bool = False
    exclusion_reason: ExclusionReason | None = None


HubType = Literal[
    "company",
    "investor",
    "school",
    "board",
    "topic",
    "city",
    "technology",
    "event",
    "cause",
    "person",
]


class Hub(BaseModel):
    hub_id: str  # canonical: "wd:Q123" if Wikidata-resolved else "{type}:{slug(label)}"
    label: str
    type: HubType
    recency: float = 1.0  # 0..1, 1 = tied to current work, decays with age
    evidence_fact_ids: list[str] = []


class Dossier(BaseModel):
    person: PersonRef
    resolution: Resolution
    facts: list[Fact]  # includes excluded facts (flag set)
    hubs: list[Hub]
    built_at: datetime
    schema_version: int = 1


# --- matching -------------------------------------------------------------


class HubContribution(BaseModel):
    # the ARRIVING person's Hub object (its evidence_fact_ids resolve in the arriving dossier)
    hub: Hub
    idf_weight: float
    recency: float  # min(recency on A's edge, recency on B's edge)
    type_boost: float
    contribution: float  # idf_weight * recency * type_boost


class Match(BaseModel):
    other: PersonRef
    score: float  # 0..100
    contributions: list[HubContribution]  # sorted desc, the exposed reasoning (R10)
    path: list[str]  # ["person:a", "hub:wd:Q1", "person:b"]
    why: str  # one sentence, names the top shared hub(s)


# --- digest ---------------------------------------------------------------


class Digest(BaseModel):
    digest_id: str
    person: PersonRef
    who_line: str
    meet: list[Match]  # len <= 3
    lately: list[Fact]  # len <= 3, displayable only
    non_obvious: Fact | None  # exactly 1 when available (R7)
    say_out_loud: str
    # every provenance referenced above, deduped by doc_id, NUMBERED IN ORDER
    # (the numbering is what R7 citation rendering and T-7 ordering depend on)
    sources: list[Provenance]
    exclusion_policy: str  # R13, constant text from taste.py
    created_at: datetime


# --- research budget / report --------------------------------------------


class Budget(BaseModel):
    docs_per_connector: int = 8
    max_docs_total: int = 40
    max_llm_calls: int = 80


class BuildReport(BaseModel):
    # {person_id, status, confidence, facts_kept, facts_excluded, hubs,
    #  zero_result_sources: [SourceKind]}
    people: list[dict]
    started_at: datetime
    finished_at: datetime


# --- LLM ------------------------------------------------------------------


@runtime_checkable
class LLMClient(Protocol):
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel: ...

    # temperature 0; returns an instance of `schema` (an instance of some OTHER model is
    # a contract violation, not a response); raises LLMError on invalid JSON after one
    # retry.
