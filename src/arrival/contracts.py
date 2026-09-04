"""Shared models and Protocols for the Arrival Engine.

FROZEN BY CONVENTION. This module is shipped by ticket T-0 and is the contract between
every other ticket. Import from here; never redefine, subclass or fork a model that lives
here. If a signature is wrong, escalate (EXECUTION §6) — do not edit it in a downstream
ticket, because nine tickets import these names.

Signatures are verbatim from DESIGN §Interfaces.

**Validators (T-054).** Field *names, types, order, requiredness and defaults* are the
frozen part; a `Field(...)` constraint rides in `Annotated` and leaves all five untouched
(`FieldInfo.annotation` stays `float`, the constraint lands in `FieldInfo.metadata`), so the
contract table in `tests/test_t0_contract_fields.py` still grades what it always graded.

A constraint earns its place here on ONE test, applied twice over:

1. **Is it a property of the VALUE, or a relation between two objects?** `recency` is a
   number in 0..1 and a `confidence` is a probability — properties, so they are declared on
   the field. `Provenance.quote` "MUST be a substring of `RawDoc.text`" is a relation, and
   it CANNOT be checked here: no model in this file holds a `RawDoc`. `Dossier` carries
   person, resolution, facts and hubs — documents are never part of it — so `Fact` has no
   route to the document its quote came from, and a field validator pretending otherwise
   would be a lie or a coupling. The enforcement stays `extract.cited_span` (DESIGN
   Decision 5), which searches `doc.text` and returns *the document's own characters*, so
   what is stored is a substring by construction. The value-level HALF of that contract is
   real and is enforced: a quote must carry text. An empty quote is a substring of every
   document, clears the guard vacuously, and renders as a citation backing nothing.

2. **Is the model PERSISTED, or derived per request?** `PersonRef`, `RawDoc`, `Verdict`,
   `Resolution`, `Provenance`, `Fact`, `Hub` and `Dossier` are written to JSON and read
   back, so a constraint on them is a gate on the corpus, checked once at load, where a
   failure is a named `DossierLoadError` an operator can act on. `HubContribution`, `Match`
   and `Digest` are BUILT PER REQUEST from data that has already been validated — a
   constraint there can only fire on a bug in `graph`/`digest` arithmetic, and firing means
   a 500 on the page a host is waiting for instead of a slightly wrong number a test
   catches. Nothing derived is constrained here.

3. **Does the bad value corrupt silently, or degrade visibly into a path the code already
   names?** Every module imports this file and `web/app.py` ends with `app = create_app()`,
   so a `Dossier` that fails validation takes the whole staff-facing app offline at import
   (via `web.store`'s `DossierLoadError`, which names the file). That is the right answer
   for a corrupt corpus and the wrong one for an odd but handled one. So: validate what
   goes WRONG quietly; tolerate what goes BLANK loudly and is already accommodated by name
   in the code. `Hub.recency` outside 0..1 is the first kind — it is consumed as a
   multiplier, it reorders who the host is told to meet, and `graph`'s final clamp to 0..100
   HIDES the distortion. An empty `Hub.label` is the second kind — `graph._identity_key`
   handles it explicitly ("An empty label leaves nothing to group by, so such a hub falls
   back to standing alone under its own id") and `tests/graph/test_t053_*` pins that
   behaviour, so it is reachable, handled and tested input, not nonsense.

One limitation, stated plainly because a guard nobody understands is worse than none:
`model_copy(update=...)` does NOT re-validate in pydantic v2, so every constraint here binds
construction, `model_validate` and `model_validate_json` — the paths a corpus arrives by —
and not an in-place update. `tests/research/test_t6_reporting.py` injects an out-of-range
`Resolution.confidence` exactly that way, and still can; `research.py`'s clamp-and-warn on
that value therefore stays reachable and stays necessary.

What is deliberately NOT constrained, and why, is recorded at each field below.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

#: A probability. Used for every `confidence` in the contract.
#:
#: `ge`/`le` also reject NaN and infinity — every comparison against NaN is False, so the
#: bound fails. That is not incidental: `taste.py`'s displayability floor is
#: `confidence < CONFIDENCE_FLOOR`, which a NaN passes, so a NaN confidence would publish a
#: fact the taste layer meant to withhold.
Probability = Annotated[float, Field(ge=0.0, le=1.0)]

#: A string that must carry at least one non-whitespace character. `min_length=1` alone
#: would admit `"   "`, which is blank everywhere it is displayed or slugged.
NonBlank = Annotated[str, Field(min_length=1, pattern=r"\S")]

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
    # The identity fields in this file (`person_id`, `doc_id`, `fact_id`) are NOT shape- or
    # blank-checked, and `hub_id` is the one exception for a reason given at `Hub`. Two
    # arguments, both against: the comment records how an id is MINTED (`slug(name)`,
    # `sha1(url)[:16]`) rather than what makes one valid, so pinning the shape would outlaw
    # every future scheme and every mnemonic test id; and the damage a bad id does is
    # COLLISION, which is a property of the SET of ids, not of one value — `DossierStore`
    # builds `{d.person.person_id: d}` and silently drops a duplicate whether the id is ""
    # or "alpha". That check belongs where the whole set is visible, not on a field.
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
    # "never empty" is enforced; "<= 20k chars" is NOT. A document with no text is not a
    # document — the sole production factory already refuses one (`connectors/base.py:237`,
    # `if not body.strip(): return None`), so this makes a written contract machine-checked
    # at zero cost, and `RawDoc` never reaches `web/app.py`, so there is no boot exposure.
    # The 20k cap is a different animal: a 20_001-character document is oversized, not
    # nonsense, and the cap is a TRUNCATION policy that `clip(..., MAX_TEXT_CHARS)` applies
    # on the way in. Enforcing it here would turn "slightly too long" into a hard failure.
    text: NonBlank  # extracted plain text, <= 20k chars, never empty
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
    confidence: Probability  # 0..1
    evidence: str  # verbatim span from doc.text supporting the verdict
    disambiguator: str  # which detail (employer/city/role/handle) decided it


class Resolution(BaseModel):
    person_id: str
    status: Literal["resolved", "unresolved"]
    # {"wikidata_qid": "Q..", "github": "..", "company_domain": "..", "sec_cik": ".."}
    strong_keys: dict[str, str] = {}
    accepted_doc_ids: list[str]
    rejected: list[Verdict]  # kept for /debug
    confidence: Probability  # 0..1, overall


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
    # (DESIGN Decision 5 — this is the hallucination guard T-3 implements).
    #
    # The substring relation is NOT checkable here: no model in this file holds a RawDoc,
    # so `extract.cited_span` remains the enforcement and this stays a comment about it.
    # `NonBlank` is the half that IS a property of the value — an empty or all-whitespace
    # quote is a substring of every document, clears the guard vacuously, and renders as a
    # citation backing nothing.
    quote: NonBlank
    published_at: date | None = None
    retrieved_at: datetime
    # DESIGN's block writes this one as a bare `float` while spelling `# 0..1` on
    # `Verdict.confidence` and `Resolution.confidence`. Read as an omission, not a licence:
    # it is the same quantity, and it is the number `taste.CONFIDENCE_FLOOR` compares
    # against to decide whether a fact may be shown at all.
    confidence: Probability


class Fact(BaseModel):
    fact_id: str
    # `<= 200 chars, one sentence` is NOT enforced. It is a TASTE rule, not a validity rule:
    # a 201-character fact is true, cited and merely too long, and `extract` already drops
    # over-length candidates (its `dropped_over_length` counter). Enforcing it here would
    # let one verbose fact in one committed dossier take the whole app offline at boot —
    # the exact trade this file must not make. "One sentence" is not machine-checkable at
    # all without a sentence splitter, which is not a contract's job.
    text: str  # <= 200 chars, one sentence
    category: FactCategory
    provenance: Provenance
    # No coherence check between these two (`excluded` implies a reason, and vice versa).
    # It reads like an intra-model invariant and is not one: `web/render.py:379` is written
    # for the reason-less shape (`return fact.exclusion_reason or "excluded"`), and BOTH a
    # project test and a FROZEN acceptance test construct it directly —
    # `tests/web/test_t8_render.py:153` ("`exclusion_reason` is optional on the contract")
    # and `.swarm-loop/acceptance/test_t4_taste.py:379`. Enforcing it would fail the frozen
    # gate. It also only ever degrades to a blank cell on `/debug`: `is_displayable` reads
    # `excluded`, never the reason.
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
    # NOT validated against `{type}:{slug(label)}`: `graph._canonical_hub_ids` documents
    # "an id whose slug has drifted from its label never has its node silently renamed" as
    # deliberate, so id and label are ALLOWED to disagree. Only blankness is nonsense.
    hub_id: NonBlank  # canonical: "wd:Q123" if Wikidata-resolved else "{type}:{slug(label)}"
    # NOT `NonBlank`, deliberately. An unlabelled hub is inert — `graph._identity_key`
    # groups by `slug(label)`, so it can never join anyone — but `graph.py` accommodates it
    # BY NAME ("An empty label leaves nothing to group by, so such a hub falls back to
    # standing alone under its own id") and `test_t053_hub_qid_identity_election` pins that
    # fallback with `label=""`. It degrades visibly (a blank chip) rather than corrupting
    # quietly, and the fallback must exist regardless: `slug("数学")` is also "", so a
    # legitimate non-Latin label takes the same branch. Rejecting `""` would not remove one
    # line of that handling; it would only convert a handled state into a boot failure.
    label: str
    type: HubType
    recency: Probability = 1.0  # 0..1, 1 = tied to current work, decays with age
    # NOT checked against the dossier's `facts`, though `Dossier` is the one model where
    # both sides exist. Measured before declining: all three readers resolve these ids with
    # a miss-tolerant lookup and skip what is absent — `web/render.py:161`
    # (`if fact_id in by_id`), `web/graph_view.py:209` and `digest.py:816` (`by_id.get`) —
    # and two of them document that skip as the DESIGNED path for evidence that must not be
    # shown (a hub whose facts were taste-excluded still scores a match). So a dangling id
    # yields a THINNER page, never a wrong one, through a branch that already runs every
    # day. Enforcing it here would trade that for the whole app failing to boot.
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
    # NOT `Probability`, though the value is one. It is `min()` of two `Hub.recency` values
    # that are ALREADY bounded, so on validated data the constraint can never fire; and on
    # unvalidated data (a hub smuggled in by `model_construct`) it fires INSIDE
    # `graph._contributions`, turning a wrong ordering into a 500 on the page a host is
    # waiting for. Measured while writing `test_t054_contract_validators.py`, which could
    # not demonstrate the reordering until this constraint came off.
    recency: float  # min(recency on A's edge, recency on B's edge)
    type_boost: float
    # NOT checked to equal `idf_weight * recency * type_boost`. It would be a float-equality
    # assertion on a product the sole producer (`graph.py`) computes in that exact form, so
    # it could only ever fire on rounding — a false positive dressed as a guard.
    contribution: float  # idf_weight * recency * type_boost


class Match(BaseModel):
    other: PersonRef
    # `0..100` is NOT enforced. The sole producer clamps it — `graph.py:541`,
    # `float(max(0, min(100, round(100 * raw / ref))))` — and an existing test asserts that
    # clamp holds under bad input. A second copy here could only fire on a graph arithmetic
    # bug, and `Match` is built per REQUEST, so firing would mean a 500 on the page a host
    # is waiting for instead of a slightly wrong number a test catches.
    score: float  # 0..100
    contributions: list[HubContribution]  # sorted desc, the exposed reasoning (R10)
    path: list[str]  # ["person:a", "hub:wd:Q1", "person:b"]
    why: str  # one sentence, names the top shared hub(s)


# --- digest ---------------------------------------------------------------


class Digest(BaseModel):
    digest_id: str
    person: PersonRef
    who_line: str
    # The `len <= 3` caps and the `sources` dedup below are DISPLAY POLICY owned by T-7,
    # not validity. A fourth match is not nonsense, and like `Match` a `Digest` is built per
    # request: a `max_length` here converts a policy slip into a 500 on a host-facing page.
    meet: list[Match]  # len <= 3
    lately: list[Fact]  # len <= 3, displayable only
    non_obvious: Fact | None  # exactly 1 when available (R7)
    say_out_loud: str
    # The dedup is NOT enforced. `digest._sources` is the sole producer and builds the list
    # from a first-seen `doc_id` order plus a one-slot-per-document winner, so it cannot emit
    # a duplicate; and no `Digest` is ever persisted or re-validated (nothing in the repo
    # holds a digest JSON). A uniqueness check here could only fire on the one function that
    # is structurally incapable of tripping it.
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
    # STILL `list[dict]`, and deliberately. Three independent reasons, any one sufficient:
    #
    # 1. The annotation is not this file's to change. `tests/test_t0_contract_fields.py`
    #    holds an independent transcription of DESIGN §Interfaces pinning `list[dict]`, and
    #    its own docstring forbids editing the table to match the code — so a real schema is
    #    an EXECUTION §6 escalation against DESIGN, not a contract edit.
    # 2. Two tests pin the schemalessness on purpose, not by accident.
    #    `tests/research/test_t6_reporting.py`'s
    #    `test_a_row_carrying_none_where_a_number_belongs_does_not_kill_the_report` passes a
    #    row of `None`s BECAUSE one bad row must not kill the whole report, and
    #    `tests/test_t0_contracts.py` passes a one-key partial row. A schema strict enough
    #    to be worth having contradicts the first of those directly.
    # 3. The rows are richer than the comment: `research.report_row` emits twelve keys and
    #    `_failed_row` a thirteenth (`error`), whose PRESENCE is the CLI's non-zero exit
    #    signal. A schema transcribed from the seven-key comment would be wrong on contact.
    #
    # The T-6 side is already guarded where the set of legal values is known:
    # `research.py:91` declares `_SOURCE_KINDS` from `get_args(SourceKind)` and `_fan_out`
    # applies it, so a typo'd source kind is dropped on the way in rather than validated
    # here. (`codebase-map.md` hazard 12 — "the one place in the contract with no schema".)
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
