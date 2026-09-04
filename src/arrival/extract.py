"""Fact and hub extraction — the mechanical citation check is the hallucination guard.

`extract` turns the documents the resolver accepted into `Fact`s every downstream surface
can trust, plus the canonical `Hub`s T-5 joins people on.

The model proposes; this module disposes, and every disposal is MECHANICAL (DESIGN
Decision 5 — the citation check is not an LLM judgement):

* a fact whose `quote` is not a `normalize_ws` substring of its own source document is
  DROPPED and counted. Never repaired, never softened, never shown (SPEC C8 / R9).
* every provenance field except the quote is copied from the `RawDoc` — `url`,
  `source_kind`, `published_at`, `retrieved_at`. The model is never the source of a
  citation's metadata, only of the sentence and the span it claims to be quoting.
* `hub_id` is computed here from the type and the label (or from a Wikidata QID the
  document itself states), so two dossiers built months apart still join on one node.
* `category="non_obvious"` survives only on the source kinds R7 makes eligible. A
  subject's own about page IS the first page, so nothing on it is "not on the first page"
  however the model labelled it.

Nothing here makes a taste decision: T-4 owns `excluded` / `exclusion_reason`, and every
`Fact` leaves this module with the defaults (`excluded=False`, `exclusion_reason=None`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from itertools import batched

from pydantic import BaseModel, Field

from arrival.contracts import (
    Fact,
    FactCategory,
    Hub,
    HubType,
    LLMClient,
    LLMError,
    PersonRef,
    Provenance,
    RawDoc,
    Resolution,
    SourceKind,
)
from arrival.util import normalize_ws, slug

__all__ = [
    "MAX_DOCS_PER_CALL",
    "MAX_FACT_CHARS",
    "NON_OBVIOUS_ELIGIBLE_KINDS",
    "STOP_HUB_LABELS",
    "CandidateFact",
    "CandidateHub",
    "ExtractionResult",
    "ExtractionStats",
    "canonical_hub_id",
    "extract",
    "is_cited",
    "recency_for",
]

log = logging.getLogger(__name__)

#: `Fact.text` is one sentence of at most this many characters (DESIGN §Interfaces).
MAX_FACT_CHARS = 200

#: Documents per `llm.structured` call. The ticket allows one call per accepted document
#: or a batch of at most three; batching keeps a 40-document build inside `Budget`'s
#: 80-call ceiling while still leaving each document its own labelled block in the prompt.
MAX_DOCS_PER_CALL = 3

#: Output ceiling per call. Three documents of facts and hubs do not fit in the protocol
#: default of 2000, and a truncated JSON body costs the whole batch.
MAX_TOKENS_PER_CALL = 4000

# DESIGN Decision 3, verbatim: never nodes, after lower-casing.
#
# These are hub LABELS. They are matched against `Hub.label`, NEVER against `Hub.type` and
# NEVER against the `{type}:` prefix of a canonical `hub_id` — `investor` and `technology`
# are each simultaneously a stop word and a `HubType`, so a prefix/type matcher deletes
# `investor:foundry-seed-2019` and `technology:developer-platform`, which are exactly the
# rare, high-signal nodes T-5's IDF-weighted score is built on.
STOP_HUB_LABELS: frozenset[str] = frozenset(
    {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}
)

#: R7 / DESIGN §Data models: the source kinds whose facts may carry `non_obvious`.
#: `self_page` and `search` are deliberately absent — the subject's own about page and the
#: first page of search results are the definition of "obvious".
NON_OBVIOUS_ELIGIBLE_KINDS: frozenset[SourceKind] = frozenset(
    {
        "edgar",
        "uspto",
        "propublica",
        "wayback",
        "github",
        "hn",
        "openalex",
        "wikidata",
        "podcast",
    }
)

#: The category a flagged fact falls back to when its source kind is not R7-eligible and
#: the model offered no second choice.
FALLBACK_CATEGORY: FactCategory = "current_work"

# Recency bands (T-3 acceptance 4): 1.0 within 12 months, 0.6 within 3 years, 0.3 older.
_RECENCY_BANDS: tuple[tuple[int, float], ...] = ((365, 1.0), (1095, 0.6))
_RECENCY_OLDER = 0.3
_RECENCY_UNKNOWN = 0.5

_QID = re.compile(r"Q[1-9][0-9]*")


# --------------------------------------------------------------------------
# the internal extraction schema
#
# Internal on purpose: nothing outside this module may depend on the shape we ask the
# model for. `contracts.Fact` and `contracts.Hub` are what leave here, and they are built
# from the RawDoc plus the checked parts of the model's answer, never validated straight
# out of it.
# --------------------------------------------------------------------------


class CandidateFact(BaseModel):
    """One fact the model proposes, before any of it has been believed."""

    doc_id: str = Field(description="id of the document this fact came from, copied verbatim")
    text: str = Field(
        description=(
            f"one sentence about the person, at most {MAX_FACT_CHARS} characters. "
            "Longer sentences are discarded, so keep it short."
        )
    )
    quote: str = Field(
        description=(
            "a span copied WORD FOR WORD out of that document's text that supports the "
            "sentence. Invented or paraphrased quotes are detected and the fact is thrown "
            "away, so copy, never summarise."
        )
    )
    category: FactCategory = Field(
        default=FALLBACK_CATEGORY,
        description=(
            "use 'non_obvious' only for something a well-prepared person would NOT already "
            "know from the subject's own bio or the first page of search results"
        ),
    )
    natural_category: FactCategory = Field(
        default=FALLBACK_CATEGORY,
        description="the best category for this fact IGNORING whether it is non-obvious",
    )
    fact_id: str = ""
    confidence: float = 0.5


class CandidateHub(BaseModel):
    """One entity the model proposes as a shared node in the graph."""

    label: str = Field(description="the entity's name as written, e.g. 'Foundry Seed'")
    type: HubType = Field(default="topic", description="what kind of entity this is")
    doc_id: str = ""
    evidence_fact_ids: list[str] = Field(
        default_factory=list, description="fact_ids from this same answer that evidence the hub"
    )
    qid: str | None = Field(
        default=None,
        description=(
            "the Wikidata QID (e.g. 'Q42') ONLY when the document is a Wikidata item that "
            "states it; otherwise leave this out"
        ),
    )


class ExtractionResult(BaseModel):
    """The whole answer to one extraction call."""

    facts: list[CandidateFact] = Field(default_factory=list)
    hubs: list[CandidateHub] = Field(default_factory=list)


@dataclass
class ExtractionStats:
    """What extraction did and what it threw away.

    The citation guard is only credible if its drops are visible, so every discard is
    counted here as well as logged. Pass one in to `extract` when you want the numbers —
    T-6's `BuildReport` is the intended reader.
    """

    documents_prompted: int = 0
    llm_calls: int = 0
    llm_failures: int = 0
    facts_proposed: int = 0
    facts_kept: int = 0
    dropped_uncited: int = 0
    dropped_over_length: int = 0
    dropped_empty: int = 0
    downgraded_non_obvious: int = 0
    hubs_proposed: int = 0
    hubs_kept: int = 0
    dropped_stop_hubs: int = 0
    dropped_unsupported_hubs: int = 0


@dataclass
class _HubGroup:
    """Every candidate that canonicalises to one node, accumulated across documents."""

    label: str
    type: HubType
    key: str
    qid: str | None = None
    recency: float = 0.0
    evidence: list[str] = field(default_factory=list)

    def add_evidence(self, fact_ids: list[str]) -> None:
        for fact_id in fact_ids:
            if fact_id not in self.evidence:
                self.evidence.append(fact_id)


# --------------------------------------------------------------------------
# the mechanical checks, each usable on its own
# --------------------------------------------------------------------------


def is_cited(quote: str, doc: RawDoc) -> bool:
    """DESIGN Decision 5: a quote counts as cited when it is verbatim after normalisation.

    `normalize_ws` collapses whitespace runs and case-folds BOTH sides, so a quote reflowed
    by the extractor or re-cased by the model still passes, while a quote that differs by
    one WORD does not. That asymmetry is the whole guard: it is forgiving about typography
    and unforgiving about content.
    """
    text = normalize_ws(quote)
    return bool(text) and text in normalize_ws(doc.text)


def recency_for(published_at: date | None, *, today: date | None = None) -> float:
    """T-3 acceptance 4: 1.0 within 12 months, 0.6 within 3 years, 0.3 older, 0.5 unknown.

    An unknown date is 0.5 rather than 0.3 on purpose: "we do not know when this happened"
    is genuinely less informative than "this happened, and it was a long time ago", and
    burying an undated hub below a decade-old one would be the wrong bias.
    """
    if published_at is None:
        return _RECENCY_UNKNOWN
    age_days = ((today or date.today()) - published_at).days
    for limit, value in _RECENCY_BANDS:
        if age_days <= limit:
            return value
    return _RECENCY_OLDER


def canonical_hub_id(hub_type: str, label: str, qid: str | None = None) -> str:
    """`wd:Q…` when a Wikidata QID is known, else `{type}:{slug(label)}`.

    `slug` comes from `arrival.util` and is never re-spelled here: two spellings of `slug`
    means two spellings of every `hub_id`, and the graph stops joining people who should
    join.
    """
    if qid:
        return f"wd:{qid}"
    return f"{hub_type}:{slug(label)}"


def _normalise_sentence(text: str) -> str:
    """Collapse whitespace runs WITHOUT case-folding — this text is displayed to a human."""
    return " ".join(text.split())


def _valid_qid(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip().upper()
    return candidate if _QID.fullmatch(candidate) else None


def _states_qid(doc: RawDoc, qid: str) -> bool:
    """The QID is believed only when the Wikidata document itself carries it.

    The same mechanical discipline as the quote check, applied to the one identifier that
    outranks every other join key: a QID the model produced from memory rather than from
    the item in front of it would silently merge two different people into one node.
    """
    if doc.source_kind != "wikidata":
        return False
    needle = normalize_ws(qid)
    return needle in normalize_ws(doc.text) or needle in normalize_ws(doc.url)


# --------------------------------------------------------------------------
# prompting
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""\
You read documents about one person and pull out short, checkable facts about them, plus \
the notable entities those facts connect them to.

Rules, in order of importance:

1. QUOTE VERBATIM. Every fact carries a `quote` copied word for word from the text of the \
document you took it from. The quote is checked against the document mechanically and any \
fact whose quote is not found is thrown away, so never paraphrase, never tidy, never join \
two spans with an ellipsis. Copying a slightly longer span is always safer than editing one.
2. ONE SENTENCE, AT MOST {MAX_FACT_CHARS} CHARACTERS. Longer facts are discarded outright.
3. STAY WITH THE DOCUMENT. Set `doc_id` to the id of the block the fact came from. Do not \
merge two documents into one fact and do not use anything you know from outside them.
4. FACTS ABOUT THE PERSON. Something about the company is a fact when it says something \
about this person's work, affiliations, interests or recent activity.
5. CATEGORY. Use `non_obvious` only for material a well-prepared person would NOT already \
have from the subject's own bio or the first page of search results. Always also fill \
`natural_category` with the best category ignoring non-obviousness.
6. HUBS are the entities worth joining two people on: companies, investors, schools, \
boards, events, causes, cities, technologies, named topics, named people. Give each one \
the `evidence_fact_ids` of the facts in this same answer that support it. Skip generic \
labels that would connect everybody — {", ".join(sorted(STOP_HUB_LABELS))} and the like. \
Set `qid` only when the document is a Wikidata item that states the QID.

Return nothing you cannot point at in the text.\
"""


def _document_block(doc: RawDoc) -> str:
    published = doc.published_at.isoformat() if doc.published_at else "unknown"
    return (
        f"<document doc_id=\"{doc.doc_id}\" source_kind=\"{doc.source_kind}\" "
        f"published_at=\"{published}\">\n"
        f"url: {doc.url}\n"
        f"title: {doc.title}\n\n"
        f"{doc.text}\n"
        f"</document>"
    )


def _user_prompt(person: PersonRef, docs: tuple[RawDoc, ...]) -> str:
    details = "; ".join(person.details) if person.details else "no further details"
    ids = ", ".join(doc.doc_id for doc in docs)
    blocks = "\n\n".join(_document_block(doc) for doc in docs)
    return (
        f"Person: {person.name}\n"
        f"Known details: {details}\n\n"
        f"Documents ({len(docs)}), whose doc_id values are {ids}:\n\n"
        f"{blocks}\n\n"
        f"Extract the facts and hubs supported by these documents. "
        f"Every fact's doc_id must be one of: {ids}."
    )


async def _ask(
    llm: LLMClient, person: PersonRef, docs: tuple[RawDoc, ...], tally: ExtractionStats
) -> ExtractionResult | None:
    """One structured call. A failing batch costs its own documents, never the build."""
    tally.llm_calls += 1
    try:
        answer = await llm.structured(
            system=_SYSTEM_PROMPT,
            user=_user_prompt(person, docs),
            schema=ExtractionResult,
            max_tokens=MAX_TOKENS_PER_CALL,
            cache_prefix=True,
        )
    except LLMError:
        tally.llm_failures += 1
        log.warning(
            "extraction call failed for %d document(s): %s",
            len(docs),
            ", ".join(doc.doc_id for doc in docs),
        )
        return None
    if not isinstance(answer, ExtractionResult):
        # The LLMClient contract says an instance of some OTHER model is a violation, not
        # a response. Losing the batch is the honest outcome; inventing facts is not.
        tally.llm_failures += 1
        log.warning("extraction call returned %s, not ExtractionResult", type(answer).__name__)
        return None
    return answer


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def _accepted_docs(resolution: Resolution, docs: list[RawDoc]) -> list[RawDoc]:
    """The accepted documents, in the resolver's order, deduplicated.

    Only these are ever prompted, which is what makes "every surviving fact cites an
    accepted document" true by construction rather than by a filter afterwards.
    """
    by_id: dict[str, RawDoc] = {}
    for doc in docs:
        by_id.setdefault(doc.doc_id, doc)
    accepted: list[RawDoc] = []
    seen: set[str] = set()
    for doc_id in resolution.accepted_doc_ids:
        doc = by_id.get(doc_id)
        if doc is None or doc_id in seen:
            continue
        seen.add(doc_id)
        accepted.append(doc)
    return accepted


def _source_doc(
    candidate: CandidateFact, quote: str, batch: tuple[RawDoc, ...], by_id: dict[str, RawDoc]
) -> RawDoc | None:
    """The document that actually contains this quote, or None.

    The id the model claimed is tried FIRST and only accepted if the quote really is in
    that document. The fallback — any other document in the same batch that does contain
    the span — repairs a mixed-up id without ever inventing a citation, because a document
    holding the verbatim span is a true source of it whatever the model wrote down.
    """
    declared = by_id.get(candidate.doc_id.strip())
    if declared is not None and is_cited(quote, declared):
        return declared
    for doc in batch:
        if is_cited(quote, doc):
            return doc
    return None


def _category_for(candidate: CandidateFact, doc: RawDoc, tally: ExtractionStats) -> FactCategory:
    """T-3 acceptance 5: `non_obvious` needs BOTH the model's flag and an eligible source.

    Neither half alone: labelling by source kind makes every wayback fact non-obvious, and
    trusting the flag alone lets the subject's own about page — literally the first page —
    fill the "not on the first page" slot.
    """
    if candidate.category != "non_obvious":
        return candidate.category
    if doc.source_kind in NON_OBVIOUS_ELIGIBLE_KINDS:
        return "non_obvious"
    tally.downgraded_non_obvious += 1
    natural = candidate.natural_category
    return natural if natural != "non_obvious" else FALLBACK_CATEGORY


def _build_fact(
    candidate: CandidateFact,
    doc: RawDoc,
    fact_id: str,
    tally: ExtractionStats,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        text=_normalise_sentence(candidate.text),
        category=_category_for(candidate, doc, tally),
        provenance=Provenance(
            doc_id=doc.doc_id,
            url=doc.url,
            source_kind=doc.source_kind,
            quote=_normalise_sentence(candidate.quote),
            published_at=doc.published_at,
            retrieved_at=doc.fetched_at,
            confidence=min(1.0, max(0.0, float(candidate.confidence))),
        ),
    )


def _collect_facts(
    result: ExtractionResult,
    batch: tuple[RawDoc, ...],
    counters: dict[str, int],
    tally: ExtractionStats,
) -> tuple[list[Fact], dict[str, str], dict[str, RawDoc], dict[str, list[str]]]:
    """Check, build and identify every candidate fact from one answer.

    Returns the surviving facts, the map from the id the model used to the id we assigned
    (so a hub's `evidence_fact_ids` can be translated and dangling references dropped), the
    source document of each surviving fact, and the surviving fact ids per document.
    """
    by_id = {doc.doc_id: doc for doc in batch}
    facts: list[Fact] = []
    id_map: dict[str, str] = {}
    sources: dict[str, RawDoc] = {}
    kept_by_doc: dict[str, list[str]] = {}

    for candidate in result.facts:
        tally.facts_proposed += 1
        text = _normalise_sentence(candidate.text)
        quote = _normalise_sentence(candidate.quote)
        if not text or not quote:
            tally.dropped_empty += 1
            continue
        if len(text) > MAX_FACT_CHARS:
            # Dropped rather than truncated: a sentence cut at 200 characters can lose the
            # clause that carried its meaning, and a fact shown to staff has to be true.
            tally.dropped_over_length += 1
            log.info("dropping a %d-character fact (cap is %d)", len(text), MAX_FACT_CHARS)
            continue
        doc = _source_doc(candidate, quote, batch, by_id)
        if doc is None:
            tally.dropped_uncited += 1
            log.info("dropping an uncited fact; quote was not verbatim in any prompted document")
            continue

        index = counters.get(doc.doc_id, 0)
        counters[doc.doc_id] = index + 1
        fact_id = f"{doc.doc_id}-f{index}"

        fact = _build_fact(candidate, doc, fact_id, tally)
        facts.append(fact)
        tally.facts_kept += 1
        sources[fact_id] = doc
        kept_by_doc.setdefault(doc.doc_id, []).append(fact_id)
        claimed = candidate.fact_id.strip()
        if claimed:
            id_map.setdefault(claimed, fact_id)
        id_map.setdefault(fact_id, fact_id)
    return facts, id_map, sources, kept_by_doc


def _collect_hubs(
    result: ExtractionResult,
    batch: tuple[RawDoc, ...],
    id_map: dict[str, str],
    sources: dict[str, RawDoc],
    kept_by_doc: dict[str, list[str]],
    groups: dict[tuple[str, str], _HubGroup],
    tally: ExtractionStats,
) -> None:
    """Canonicalise, filter and merge every candidate hub from one answer, into `groups`."""
    by_id = {doc.doc_id: doc for doc in batch}
    for candidate in result.hubs:
        tally.hubs_proposed += 1
        label = _normalise_sentence(candidate.label)
        key = slug(label)
        if not label or not key:
            continue
        if label.casefold() in STOP_HUB_LABELS:
            # LABELS, not types and not id prefixes. See STOP_HUB_LABELS.
            tally.dropped_stop_hubs += 1
            continue

        evidence = [id_map[raw] for raw in candidate.evidence_fact_ids if raw in id_map]
        declared = by_id.get(candidate.doc_id.strip())
        if not evidence and declared is not None:
            evidence = list(kept_by_doc.get(declared.doc_id, []))
        if not evidence:
            # A hub whose every evidence fact failed the citation check is exactly as
            # unsupported as those facts were.
            tally.dropped_unsupported_hubs += 1
            continue

        evidence_docs = [sources[fact_id] for fact_id in evidence]
        qid = _valid_qid(candidate.qid)
        if qid is not None and not any(_states_qid(doc, qid) for doc in evidence_docs):
            qid = None
        recency = max(recency_for(doc.published_at) for doc in evidence_docs)

        group = groups.get((candidate.type, key))
        if group is None:
            group = _HubGroup(label=label, type=candidate.type, key=key)
            groups[(candidate.type, key)] = group
        group.add_evidence(evidence)
        group.recency = max(group.recency, recency)
        if group.qid is None:
            group.qid = qid


def _merge_groups(groups: dict[tuple[str, str], _HubGroup], tally: ExtractionStats) -> list[Hub]:
    """One `Hub` per canonical id, evidence merged, sorted for a stable dossier on disk."""
    merged: dict[str, _HubGroup] = {}
    for group in groups.values():
        hub_id = canonical_hub_id(group.type, group.label, group.qid)
        existing = merged.get(hub_id)
        if existing is None:
            merged[hub_id] = group
            continue
        existing.add_evidence(group.evidence)
        existing.recency = max(existing.recency, group.recency)

    hubs = [
        Hub(
            hub_id=hub_id,
            label=group.label,
            type=group.type,
            recency=group.recency,
            evidence_fact_ids=list(group.evidence),
        )
        for hub_id, group in merged.items()
    ]
    hubs.sort(key=lambda hub: hub.hub_id)
    tally.hubs_kept += len(hubs)
    return hubs


async def extract(
    person: PersonRef,
    resolution: Resolution,
    docs: list[RawDoc],
    llm: LLMClient,
    *,
    stats: ExtractionStats | None = None,
) -> tuple[list[Fact], list[Hub]]:
    """Turn the resolver's accepted documents into cited facts and canonical hubs.

    Only `resolution.accepted_doc_ids` are read, in the resolver's own order, at most
    `MAX_DOCS_PER_CALL` per structured call. Every returned `Fact` carries a quote that is
    verbatim in the document it names, is at most `MAX_FACT_CHARS` long, and leaves here
    un-excluded — T-4 makes the taste decisions.

    Pass `stats` to receive the counts, including how many facts the citation check threw
    away; they are logged either way. Every counter ACCUMULATES, so one `ExtractionStats`
    may be shared across a whole roster to get the build-wide numbers.
    """
    tally = stats if stats is not None else ExtractionStats()
    accepted = _accepted_docs(resolution, docs)
    tally.documents_prompted += len(accepted)
    if not accepted:
        log.info("nothing to extract for %s: the resolver accepted no documents", person.name)
        return [], []

    facts: list[Fact] = []
    groups: dict[tuple[str, str], _HubGroup] = {}
    counters: dict[str, int] = {}

    for batch in batched(accepted, MAX_DOCS_PER_CALL):
        result = await _ask(llm, person, batch, tally)
        if result is None:
            continue
        batch_facts, id_map, sources, kept_by_doc = _collect_facts(result, batch, counters, tally)
        facts.extend(batch_facts)
        _collect_hubs(result, batch, id_map, sources, kept_by_doc, groups, tally)

    hubs = _merge_groups(groups, tally)
    log.info(
        "extracted %d fact(s) and %d hub(s) for %s from %d document(s); dropped %d uncited, "
        "%d over-length, %d stop-hub(s), %d unsupported hub(s)",
        len(facts),
        len(hubs),
        person.name,
        tally.documents_prompted,
        tally.dropped_uncited,
        tally.dropped_over_length,
        tally.dropped_stop_hubs,
        tally.dropped_unsupported_hubs,
    )
    return facts, hubs
