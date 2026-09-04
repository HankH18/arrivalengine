"""The offline research pipeline: connectors -> resolver -> extractor -> taste -> Dossier.

This is the only production caller of `arrival.connectors.all_connectors`,
`arrival.resolve.resolve`, `arrival.extract.extract` and `arrival.taste.apply_taste`. Every
one of those modules is a stage that decides one thing well; nothing here re-decides any of
them. What this module owns is the *composition*: what gets fetched, in what order, how much
of it, what is allowed to fail, and what the operator is told afterwards.

Four composition rules, each of which is a design decision rather than plumbing:

1. **Fan out concurrently, cap deliberately** (DESIGN Decision 2). Ten sources are queried
   at once because the wall clock of a roster build is the sum of its slowest sources, not
   of all of them. `budget.docs_per_connector` is what each source is *asked* for and
   `budget.max_docs_total` is what the person is allowed to cost overall; the second is
   applied by taking documents round-robin across sources rather than in connector order,
   so a chatty source cannot crowd out a quiet one. A connector that returns more than it
   was asked for is trimmed here — the `Connector` Protocol asks for politeness, and this
   module does not rely on it.

2. **A dying source is a report line, never a build failure** (DESIGN Decision 8). The
   Protocol says a connector must never raise; some will anyway, and a roster build that
   dies because ProPublica 500'd is worse than one that says so. Every source that returned
   nothing — empty or exploded — is named in `zero_result_sources` for that person, which
   is the difference between "we looked and there was nothing" and "we never looked".

3. **An unresolved person is not researched** (SPEC R2). `resolve` returning `unresolved`
   ends the pipeline for that person: no extraction call is made, no facts are stored, no
   hubs are stored. "We kept nothing" and "we never went looking" are different
   guarantees, and only the second one is honest about a person we could not identify.

4. **The LLM budget is enforced at the seam, not asked for politely.** `_BudgetedClient`
   wraps the injected client and refuses past `budget.max_llm_calls`, so the cap holds no
   matter how the stages downstream choose to batch their calls. Every stage already
   degrades on `LLMError` — a refused verdict is `unsure`, a refused extraction batch costs
   its own documents, a refused taste ruling fails closed — so hitting the cap keeps what
   the build already has instead of raising.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from arrival.config import get_settings
from arrival.contracts import (
    Budget,
    BuildReport,
    Dossier,
    Fact,
    Hub,
    LLMError,
    PersonRef,
    RawDoc,
    SourceKind,
)
from arrival.extract import ExtractionStats, extract
from arrival.resolve import DocVerdict, resolve
from arrival.taste import apply_taste
from arrival.util import slug

if TYPE_CHECKING:  # pragma: no cover - typing only
    from arrival.contracts import Connector, LLMClient

__all__ = [
    "REPORT_COLUMNS",
    "BuildError",
    "BuildTrace",
    "RosterError",
    "build_all",
    "build_dossier",
    "format_report",
    "load_roster",
    "report_row",
]

log = logging.getLogger(__name__)

#: Every `SourceKind` spelling that may appear in a report row. `BuildReport.people` is
#: `list[dict]` and pydantic validates NOTHING inside it, so a typo'd kind would travel all
#: the way to T-9's committed report unnoticed. Validated on the way in instead.
_SOURCE_KINDS: frozenset[str] = frozenset(get_args(SourceKind))

#: The report row's keys, in table order. The first seven are the contract
#: (`contracts.BuildReport`); the rest are diagnostics an operator asked for during a
#: build and nothing downstream is allowed to require.
REPORT_COLUMNS: tuple[str, ...] = (
    "person_id",
    "status",
    "confidence",
    "facts_kept",
    "facts_excluded",
    "hubs",
    "zero_result_sources",
)

#: The CLI table, derived from `REPORT_COLUMNS` plus the one diagnostic worth a column, so
#: the documented row keys and the printed table cannot drift into two sources of truth.
_TABLE_COLUMNS: tuple[str, ...] = (*REPORT_COLUMNS, "failed_sources")

_COLUMN_LABELS: dict[str, str] = {
    "person_id": "person",
    "status": "status",
    "confidence": "conf",
    "facts_kept": "kept",
    "facts_excluded": "excl",
    "hubs": "hubs",
    "zero_result_sources": "zero-result sources",
    "failed_sources": "failed sources",
}


class RosterError(Exception):
    """The roster file is missing, unreadable, or does not describe any people."""


class BuildError(Exception):
    """The build cannot be run as asked — a bad output layout, not a bad roster."""


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# the budget seam
# --------------------------------------------------------------------------


class _BudgetedClient:
    """An `LLMClient` that refuses calls past a cap instead of trusting its callers.

    The cap has to live here rather than in the stages: `resolve` spends one call per
    document, `extract` spends one per batch of `MAX_DOCS_PER_CALL`, and `apply_taste`
    spends one per twenty unsure facts, so no stage can know what the others have already
    used. Refusing with `LLMError` — the failure every stage is already written to survive
    — turns "out of budget" into the degradation each stage already implements, rather than
    a new error path none of them has.
    """

    def __init__(self, inner: LLMClient, max_calls: int) -> None:
        self._inner = inner
        self._max_calls = max(0, int(max_calls))
        self.used = 0
        self.refused = 0
        self.errors = 0

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self.used)

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        if self.used >= self._max_calls:
            self.refused += 1
            raise LLMError(
                f"research budget exhausted: max_llm_calls={self._max_calls} already spent, "
                f"refusing a {schema.__name__} call"
            )
        # Counted BEFORE the await: the call has been made whether or not it succeeds, and
        # counting after would let a failing stage retry its way past the cap.
        self.used += 1
        try:
            return await self._inner.structured(
                system=system,
                user=user,
                schema=schema,
                max_tokens=max_tokens,
                cache_prefix=cache_prefix,
            )
        except LLMError:
            # Counted and RE-RAISED, never handled: each stage's own degradation is the
            # right local answer. What no stage can see is that EVERY call failed, which
            # is an unreachable model rather than a person with nothing written about
            # them — and those two produce identical empty dossiers. `build_all` reads
            # this counter to tell them apart.
            self.errors += 1
            raise


class _TieredClient:
    """Routes resolution to the smart model and everything else to the fast one.

    DESIGN Decision 9 splits the work by tier, but `build_dossier` takes ONE client because
    that is the seam the tests inject at. This adapter is how the split survives that: it
    is built only on the production path (`build_all(llm=None)`), and an injected client is
    used verbatim for every stage.
    """

    #: Response schemas that get the smart model. `DocVerdict` is `arrival.resolve`'s
    #: internal judgement schema — the identity decision every later stage inherits, and
    #: the one place in the pipeline where being cheap is expensive. Every other schema in
    #: the pipeline (`ExtractionResult`, `TasteRulings`) is fast-model work by DESIGN
    #: Decision 9.
    #:
    #: Taken from the CLASS rather than written as the string "DocVerdict": that schema is
    #: internal to `arrival.resolve` and pinned by nothing, so a rename there would
    #: otherwise route resolution to the cheap model silently and forever. Read this way a
    #: rename follows automatically and a deletion is an ImportError.
    SMART_SCHEMAS = frozenset({DocVerdict.__name__})

    def __init__(self, smart: LLMClient, fast: LLMClient) -> None:
        self._smart = smart
        self._fast = fast

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        client = self._smart if schema.__name__ in self.SMART_SCHEMAS else self._fast
        return await client.structured(
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            cache_prefix=cache_prefix,
        )


def _default_llm() -> LLMClient:
    """The production client. Imported lazily so `--help` never touches the SDK."""
    from arrival.llm.client import AnthropicClient

    settings = get_settings()  # read at CALL time; never snapshot settings at import
    return _TieredClient(AnthropicClient(settings, tier="smart"), AnthropicClient.fast(settings))


# --------------------------------------------------------------------------
# what one person's build did, beyond the dossier itself
# --------------------------------------------------------------------------


@dataclass
class BuildTrace:
    """Everything `build_all` needs that does not belong in a `Dossier`.

    `Dossier` is the product artefact and carries doc *ids*; the build needs the documents
    themselves (to commit them beside the dossier so citations replay offline) and the
    per-source outcome (to report it). Both are out-parameters rather than a wider return
    type, mirroring `extract`'s own `stats=` convention, so that `build_dossier` keeps the
    signature DESIGN pins and stays the real production entry point rather than a wrapper
    around a richer private one.
    """

    zero_result_sources: list[str] = field(default_factory=list)
    documents: list[RawDoc] = field(default_factory=list)
    docs_by_source: dict[str, int] = field(default_factory=dict)
    connector_errors: dict[str, str] = field(default_factory=dict)
    llm_calls: int = 0
    llm_refused: int = 0
    llm_errors: int = 0
    hubs_dropped_unsupported: list[str] = field(default_factory=list)
    extraction: ExtractionStats = field(default_factory=ExtractionStats)

    def accepted_documents(self, doc_ids: Iterable[str]) -> list[RawDoc]:
        """The fetched documents whose ids are in `doc_ids`, in resolver order."""
        by_id = {doc.doc_id: doc for doc in self.documents}
        out: list[RawDoc] = []
        seen: set[str] = set()
        for doc_id in doc_ids:
            doc = by_id.get(doc_id)
            if doc is not None and doc_id not in seen:
                seen.add(doc_id)
                out.append(doc)
        return out


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------


async def _search_one(
    connector: Connector, person: PersonRef, ask: int, trace: BuildTrace
) -> list[RawDoc]:
    """One connector's documents, or `[]` if it fails. Never raises (DESIGN Decision 8)."""
    kind = str(getattr(connector, "kind", "") or "")
    try:
        found = await connector.search(person, ask)
    except Exception as exc:  # a Connector must not raise; this one did, and the build lives
        trace.connector_errors[kind] = f"{type(exc).__name__}: {exc}"
        log.warning("connector %s failed for %s: %s", kind or connector, person.name, exc)
        return []
    returned = list(found or [])
    docs = [doc for doc in returned if isinstance(doc, RawDoc)]
    if len(docs) != len(returned):
        # Silent partial loss otherwise: a connector whose parser regresses hands back
        # dicts, contributes fewer documents, and looks exactly like a quiet source.
        log.warning(
            "connector %s returned %d item(s) that are not RawDocs for %s; dropping them",
            kind,
            len(returned) - len(docs),
            person.name,
        )
    if len(docs) > ask:
        # The double, and a real connector under a generous API, can both hand back more
        # than they were asked for. The budget is ours to enforce, not theirs to honour.
        log.debug("connector %s returned %d docs for a budget of %d", kind, len(docs), ask)
        docs = docs[:ask]
    return docs


def _display_rank(kind: str) -> int:
    """Where `kind` sits in `connectors.DISPLAY_PRIORITY`; unranked kinds sort last.

    Imported lazily and not cached here: `arrival.connectors` pulls in httpx and all ten
    source modules, and `import arrival.research` is deliberately free of both (see
    `_default_connectors`). After the first call it is a `sys.modules` lookup.

    A `SourceKind` with no connector — `uspto`, `podcast`, or the two SPEC Q4 withholds —
    is a legal value of the field and must not become a `ValueError` from `.index()`. It
    simply has no claim to display priority, so it loses to every kind that does.
    """
    from arrival.connectors import DISPLAY_PRIORITY

    try:
        return DISPLAY_PRIORITY.index(kind)  # type: ignore[arg-type]
    except ValueError:
        return len(DISPLAY_PRIORITY)


def _by_display_priority(batches: Sequence[list[RawDoc]]) -> dict[str, RawDoc]:
    """For each `doc_id`, the copy from the highest-priority source that returned it.

    `doc_id` is `sha1(url)[:16]`, so two connectors that surface the same page hand this
    module two `RawDoc`s with ONE identity, and exactly one of them can survive. Which one
    is not cosmetic: `source_kind` is read as a claim about provenance downstream, by
    `resolve._sec_cik` (`if doc.source_kind != "edgar": continue`, so the `sec_cik` strong
    key is only ever earned from an edgar-stamped document) and by
    `digest.NON_OBVIOUS_KINDS` (which excludes `search`, so a `search` stamp costs the
    document R7's "Not on the first page" slot). Deciding it first-wins would let two
    remote APIs' relative latency and ranking pick a durable identifier and a displayed
    section — the same input scoring differently on two runs.

    `CONNECTOR_CLASSES` already declares the order a reader should meet sources in, and
    `DISPLAY_PRIORITY` is that order as a tuple. This is where the declaration becomes
    true. Ties — including two documents of the same kind, the ordinary duplicate — keep
    the first arrival, so the merge stays deterministic for inputs priority cannot separate.

    The whole document survives, not just its kind: a search engine's snippet restamped
    `edgar` would pass every downstream kind check while still not containing the CIK that
    makes the check worth passing. The stamp and the evidence move together or neither does.
    """
    winners: dict[str, RawDoc] = {}
    ranks: dict[str, int] = {}
    for batch in batches:
        for doc in batch:
            rank = _display_rank(doc.source_kind)
            if doc.doc_id in winners and rank >= ranks[doc.doc_id]:
                continue
            if doc.doc_id in winners:
                log.debug(
                    "same url from two sources: %s outranks %s for %s",
                    doc.source_kind,
                    winners[doc.doc_id].source_kind,
                    doc.url,
                )
            winners[doc.doc_id] = doc
            ranks[doc.doc_id] = rank
    return winners


def _interleave(batches: Sequence[list[RawDoc]], max_total: int) -> list[RawDoc]:
    """Documents taken round-robin across sources, deduplicated, capped at `max_total`.

    Round-robin rather than source order: `max_docs_total` is usually smaller than what ten
    connectors return together, and taking the first N in connector order would spend the
    whole allowance on `self_page` and never read a word from `search`. Going wide is the
    entire retrieval strategy, so the cap has to be applied in a way that keeps it wide.

    Two decisions, kept separate on purpose. WHERE a document sits is the round-robin's
    call — the position of its FIRST appearance across the columns, so a duplicate costs
    one slot and never a source's place in the spread. WHAT is emitted there is
    `_by_display_priority`'s call, so a page several sources indexed arrives stamped with
    the source the project ranks highest rather than the one that answered first.
    """
    ordered: list[RawDoc] = []
    seen: set[str] = set()
    if max_total <= 0:
        return ordered
    winners = _by_display_priority(batches)
    for column in zip_longest(*batches):
        for doc in column:
            if doc is None or doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            ordered.append(winners[doc.doc_id])
            if len(ordered) >= max_total:
                return ordered
    return ordered


async def _fan_out(
    person: PersonRef,
    connectors: Sequence[Connector],
    budget: Budget,
    trace: BuildTrace,
) -> list[RawDoc]:
    """Query every connector at once and return the deduplicated, budgeted document set."""
    ask = max(0, min(int(budget.docs_per_connector), int(budget.max_docs_total)))
    batches = list(
        await asyncio.gather(*(_search_one(c, person, ask, trace) for c in connectors))
    )

    for connector, batch in zip(connectors, batches, strict=True):
        kind = str(getattr(connector, "kind", "") or "")
        trace.docs_by_source[kind] = trace.docs_by_source.get(kind, 0) + len(batch)

    zero: list[str] = []
    for kind, found in trace.docs_by_source.items():
        if found:
            continue
        if kind not in _SOURCE_KINDS:
            # Hazard: BuildReport.people is list[dict] and validates nothing inside it.
            log.warning("dropping %r from zero_result_sources: not a SourceKind", kind)
            continue
        zero.append(kind)

    trace.zero_result_sources = zero
    docs = _interleave(batches, max(0, int(budget.max_docs_total)))
    trace.documents = docs
    log.info(
        "fetched %d document(s) for %s from %d source(s); %d source(s) returned nothing: %s",
        len(docs),
        person.name,
        len(connectors),
        len(zero),
        ", ".join(zero) or "none",
    )
    return docs


# --------------------------------------------------------------------------
# one person
# --------------------------------------------------------------------------


def _supported_hubs(hubs: list[Hub], facts: list[Fact], trace: BuildTrace) -> list[Hub]:
    """Drop any hub whose every evidence fact was excluded by the taste filter.

    The stage order makes this necessary and it belongs to nobody else. `extract` already
    refuses a hub with no surviving evidence, but it runs BEFORE `apply_taste`, so a hub
    can leave extraction properly evidenced and then have its entire evidence excluded a
    moment later. Nothing downstream looks again: `Hub` carries a LABEL, T-5 joins on it
    and T-7 prints it in `Match.why`, so the surviving hub reintroduces exactly the
    sentence taste just removed.

    Reproduced before this guard existed: a fact excluded `home_or_property` left behind
    `city:pecan-street` — the member's street, as a joinable graph node and a candidate
    match reason. That is the wrong side of the "seen vs. dossiered" line (SPEC R11).

    A surviving hub also has its EXCLUDED evidence ids stripped, and that half matters
    more than the drop: it needs only ONE excluded fact rather than all of them.
    `contracts.HubContribution` says a hub's "evidence_fact_ids resolve in the arriving
    dossier", and the dossier keeps excluded facts by contract, so an id left in the list
    is a live pointer from a displayed match reason back to the withheld sentence.

    Deliberately narrow in the other direction: a hub keeps its place if ANY evidence fact
    survived, and an id that resolves to no fact at all is left alone rather than judged on
    silence — `extract` cannot currently emit that shape, and inventing a rule for it here
    would be guessing.
    """
    excluded = {fact.fact_id for fact in facts if fact.excluded}
    known = {fact.fact_id for fact in facts}
    kept: list[Hub] = []
    for hub in hubs:
        evidence = [fact_id for fact_id in hub.evidence_fact_ids if fact_id in known]
        if evidence and all(fact_id in excluded for fact_id in evidence):
            trace.hubs_dropped_unsupported.append(hub.hub_id)
            log.info(
                "dropping hub %s (%r): every fact evidencing it was excluded by taste",
                hub.hub_id,
                hub.label,
            )
            continue
        surviving = [fact_id for fact_id in hub.evidence_fact_ids if fact_id not in excluded]
        if surviving != hub.evidence_fact_ids:
            log.info(
                "hub %s keeps its place but loses %d excluded evidence fact(s)",
                hub.hub_id,
                len(hub.evidence_fact_ids) - len(surviving),
            )
            hub = hub.model_copy(update={"evidence_fact_ids": surviving})
        kept.append(hub)
    return kept


async def build_dossier(
    person: PersonRef,
    connectors: Sequence[Connector],
    llm: LLMClient,
    budget: Budget | None = None,
    *,
    trace: BuildTrace | None = None,
) -> Dossier:
    """Research one person end to end and assemble their `Dossier`.

    EVERY BUDGET IS PER PERSON, including `max_llm_calls`: a ten-person roster on the
    defaults is authorised for ten times these numbers. `_BudgetedClient` is constructed
    here, once per person, which is what makes that true — `contracts.Budget` does not say
    it and a roster-wide reading of the same numbers is the natural misreading.

    Fans out over `connectors` concurrently (each asked for at most
    `budget.docs_per_connector`, the person capped at `budget.max_docs_total`), resolves
    identity, extracts facts and hubs from the ACCEPTED documents only, applies the taste
    filter, and assembles the result. Never raises for a failing source or an exhausted
    budget: both degrade into a thinner dossier that says so.

    An `unresolved` resolution ends the pipeline here — `facts == []`, `hubs == []`, and no
    extraction call is made at all (SPEC R2).

    Pass `trace` to receive the per-source outcome, the documents that were fetched and the
    LLM call counts; `build_all` needs all three to write its report and commit the cited
    documents.
    """
    trace = trace if trace is not None else BuildTrace()
    budget = budget if budget is not None else Budget()

    docs = await _fan_out(person, list(connectors), budget, trace)
    metered = _BudgetedClient(llm, budget.max_llm_calls)

    resolution = await resolve(person, docs, metered)

    facts = []
    hubs = []
    if resolution.status != "resolved":
        log.info(
            "%s is unresolved (%d document(s) judged); storing no facts and not extracting",
            person.name,
            len(docs),
        )
    elif metered.remaining <= 0:
        # R2 is satisfied and there is simply nothing left to spend. Keeping the resolution
        # and no facts is the honest outcome; raising would throw away work already paid for.
        log.warning(
            "budget of %d LLM call(s) exhausted by resolution for %s; skipping extraction",
            budget.max_llm_calls,
            person.name,
        )
    else:
        candidates, hubs = await extract(
            person, resolution, docs, metered, stats=trace.extraction
        )
        facts = await apply_taste(candidates, metered)
        hubs = _supported_hubs(hubs, facts, trace)

    trace.llm_calls = metered.used
    trace.llm_refused = metered.refused
    trace.llm_errors = metered.errors
    return Dossier(
        person=person,
        resolution=resolution,
        facts=facts,
        hubs=hubs,
        built_at=_now(),
    )


# --------------------------------------------------------------------------
# the roster
# --------------------------------------------------------------------------


def _person_from(entry: Any, taken: set[str]) -> PersonRef | None:
    """One roster entry as a `PersonRef`, or `None` if it names nobody."""
    if isinstance(entry, str):
        name, details, declared = entry.strip(), [], ""
    elif isinstance(entry, dict):
        name = str(entry.get("name") or "").strip()
        details = entry.get("details") or []
        declared = str(entry.get("person_id") or "").strip()
    else:
        log.warning("skipping roster entry of type %s", type(entry).__name__)
        return None
    if not name:
        log.warning("skipping a roster entry with no name: %r", entry)
        return None
    if isinstance(details, str):
        details = [details]
    cleaned = [str(detail).strip() for detail in details if str(detail).strip()]

    # SPEC Q1 / contracts.PersonRef: person_id == slug(name), disambiguated by the first
    # detail on a collision. A roster may state an id explicitly; the product contract is
    # what applies when it does not.
    #
    # A DECLARED id is slugged too, and that is a security decision rather than a tidiness
    # one: the id becomes a filename in `build_all` (`out_dir/{person_id}.json`), a roster
    # is hand-written YAML, and `person_id: ../../../etc/whatever` would otherwise write
    # outside the output directory. `slug` maps every separator to "-", so a legitimate id
    # survives it unchanged and a path does not survive it at all.
    person_id = slug(declared) if declared else slug(name)
    if declared and person_id != declared:
        log.warning("roster person_id %r is not a slug; using %r", declared, person_id)
    if declared and not person_id:
        # A declared id of "###" or "n/a" slugs away to nothing. Dropping the person there
        # would lose someone whose NAME keys perfectly well, and say so in a message that
        # blames the name — so fall back rather than drop.
        person_id = slug(name)
        log.warning("roster person_id %r slugs to nothing; falling back to %r", declared, person_id)
    if not person_id:
        log.warning("skipping %r: the name slugs to nothing, so it cannot be keyed", name)
        return None
    if person_id in taken:
        suffix = slug(cleaned[0]) if cleaned else ""
        candidate = f"{person_id}-{suffix}" if suffix else person_id
        bump = 2
        while candidate in taken:
            candidate = f"{person_id}-{suffix}-{bump}" if suffix else f"{person_id}-{bump}"
            bump += 1
        log.warning("roster id collision on %r; using %r", person_id, candidate)
        person_id = candidate
    taken.add(person_id)
    return PersonRef(person_id=person_id, name=name, details=cleaned)


def load_roster(roster_path: str | Path) -> list[PersonRef]:
    """Read `people: [{name, details: [..]}]` (DESIGN §Data models) into `PersonRef`s.

    A bare top-level list and a bare string entry are both accepted, because a roster is
    hand-written and those are the two shapes people actually write.
    """
    path = Path(roster_path)
    try:
        raw = path.read_text(encoding="utf-8")
    # `ValueError` is here for `UnicodeDecodeError`, which subclasses it rather than
    # `OSError`. A roster is hand-written, so a file saved as latin-1 or cp1252 is an
    # ordinary operator mistake -- and with only `except OSError` it escaped `RosterError`
    # and fell through to `_main`'s catch-all, which logs a traceback and returns 1 instead
    # of the diagnosis and exit 2 a roster problem is supposed to get. Same shape as
    # `_existing_row` below, which already caught `ValueError` alongside the OS errors.
    except (OSError, ValueError) as exc:
        raise RosterError(f"cannot read roster {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RosterError(f"roster {path} is not valid YAML: {exc}") from exc

    if isinstance(data, dict):
        entries = data.get("people")
    elif isinstance(data, list):
        entries = data
    else:
        entries = None
    if not isinstance(entries, list) or not entries:
        raise RosterError(f"roster {path} lists no people under a 'people:' key")

    taken: set[str] = set()
    people = [person for person in (_person_from(e, taken) for e in entries) if person]
    if not people:
        raise RosterError(f"roster {path} lists no usable people")
    return people


def _selects(person: PersonRef, only: str | None) -> bool:
    if not only:
        return True
    wanted = only.strip()
    return wanted in (person.person_id, person.name) or slug(wanted) == person.person_id


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def report_row(
    dossier: Dossier,
    trace: BuildTrace | None = None,
    *,
    skipped: bool = False,
) -> dict[str, Any]:
    """One `BuildReport.people` row, with every value coerced to its documented type.

    `BuildReport.people` is `list[dict]`, so pydantic validates nothing inside it — a
    typo'd `SourceKind` or a `None` where a count belongs would travel silently all the way
    into T-9's committed report. This is the only place rows are built, and it is the only
    validation they get.
    """
    kept = sum(1 for fact in dossier.facts if not fact.excluded)
    excluded = len(dossier.facts) - kept
    sources = list(trace.zero_result_sources) if trace is not None else []
    zero = [kind for kind in sources if kind in _SOURCE_KINDS]
    if len(zero) != len(sources):
        log.warning("dropped non-SourceKind entries from zero_result_sources: %s", sources)

    confidence = float(dossier.resolution.confidence)
    if not 0.0 <= confidence <= 1.0:
        # Clamped AND reported. `Resolution.confidence` is an unconstrained float, so a
        # resolver bug yielding 1.7 would otherwise print 1.00 in the report while the
        # dossier on disk still held 1.7 — the report and the artefact disagreeing, with
        # the bug the coercion exists to catch concealed by the coercion.
        log.warning(
            "%s has an out-of-range resolution confidence %r; clamping it for the report",
            dossier.person.person_id,
            dossier.resolution.confidence,
        )
        confidence = min(1.0, max(0.0, confidence))

    row: dict[str, Any] = {
        "person_id": str(dossier.person.person_id),
        "status": str(dossier.resolution.status),
        "confidence": confidence,
        "facts_kept": int(kept),
        "facts_excluded": int(excluded),
        "hubs": int(len(dossier.hubs)),
        "zero_result_sources": zero,
        # A source that EXPLODED, as distinct from one that had nothing. DESIGN Decision
        # 8's whole point is that difference, and it was being computed into
        # `trace.connector_errors` and then read by nobody.
        "failed_sources": sorted(trace.connector_errors) if trace is not None else [],
        # Diagnostics. Additive, and nothing downstream may require them.
        "name": str(dossier.person.name),
        "documents": int(len(trace.documents)) if trace is not None else 0,
        "llm_calls": int(trace.llm_calls) if trace is not None else 0,
        "skipped": bool(skipped),
    }
    return row


def _cell(row: dict[str, Any], key: str) -> str:
    """One table cell. Every read is `.get`-with-a-default AND None-tolerant.

    `BuildReport.people` is `list[dict]` that pydantic does not validate, and `.get(k, d)`
    only covers a MISSING key — a key present with the value `None` still reaches the
    formatter, where `f"{float(None):.2f}"` is a TypeError that kills the whole report
    over one bad row.
    """
    value = row.get(key)
    if key == "person_id":
        marker = " (skipped)" if row.get("skipped") else ""
        return f"{value if value is not None else '?'}{marker}"
    if key == "confidence":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "?"
    if key in ("zero_result_sources", "failed_sources"):
        return ", ".join(str(item) for item in (value or [])) or "-"
    if key in ("facts_kept", "facts_excluded", "hubs"):
        return "?" if value is None else str(value)
    return "?" if value is None else str(value)


def format_report(report: BuildReport) -> str:
    """The report table the CLI prints. Also the shape a human reads at T-9."""
    headers = tuple(_COLUMN_LABELS[key] for key in _TABLE_COLUMNS)
    rows: list[tuple[str, ...]] = [
        tuple(_cell(row, key) for key in _TABLE_COLUMNS) for row in report.people
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    resolved = sum(1 for row in report.people if row.get("status") == "resolved")
    skipped = sum(1 for row in report.people if row.get("skipped"))
    elapsed = (report.finished_at - report.started_at).total_seconds()
    out = [line(headers), "  ".join("-" * width for width in widths)]
    out.extend(line(row) for row in rows)
    out.append("")
    out.append(
        f"{len(report.people)} person/people: {resolved} resolved, "
        f"{len(report.people) - resolved} unresolved, {skipped} skipped "
        f"({elapsed:.1f}s)"
    )
    return "\n".join(out)


# --------------------------------------------------------------------------
# the whole roster
# --------------------------------------------------------------------------


def _safe_segment(value: str) -> bool:
    """True when `value` is usable as ONE filename component.

    `RawDoc.doc_id` is contractually `sha1(url)[:16]` but the model carries no validator,
    so a connector — ours today, someone else's tomorrow — can hand back any string, and
    this one is about to become a path. A separator or a `..` here writes outside the
    output directory.
    """
    return bool(value) and value not in (".", "..") and not set(value) & {"/", "\\", "\0"}


def _write_json(path: Path, model: BaseModel) -> None:
    """Write `model` as JSON, atomically.

    Truncate-then-write leaves a HALF file behind on an interrupt, and a half-written
    `docs/{doc_id}.json` never heals: nothing validates a committed `RawDoc`, and the
    person it belongs to is skipped on every later run. Writing beside the target and
    renaming makes the file either the old one or the new one, never neither.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _existing_row(
    path: Path, person: PersonRef, docs_dir: Path
) -> dict[str, Any] | None:
    """The report row for a person whose dossier is already on disk, or None to rebuild.

    A dossier is only "already built" if the documents it cites are still beside it. T-9
    validates every displayed quote against `docs/{doc_id}.json`, so a dossier whose
    documents were deleted, gitignored, or written under a different working directory is
    an artefact that looks complete and cites nothing. Rebuilding is cheaper than shipping
    that, and skipping it would be permanent: nothing else in this loop ever looks again.
    """
    try:
        dossier = Dossier.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("existing dossier %s is unreadable (%s); rebuilding it", path, exc)
        return None
    missing = [
        doc_id
        for doc_id in dossier.resolution.accepted_doc_ids
        if not (docs_dir / f"{doc_id}.json").exists()
    ]
    if missing:
        log.warning(
            "%s cites %d document(s) missing from %s (%s…); rebuilding it",
            path,
            len(missing),
            docs_dir,
            missing[0],
        )
        return None
    log.info("skipping %s: %s already exists (use force=True to rebuild)", person.name, path)
    return report_row(dossier, None, skipped=True)


def _failed_row(person: PersonRef, trace: BuildTrace, reason: str) -> dict[str, Any]:
    """A report row for a person whose build did not produce a usable dossier.

    Carries every contract key so the report stays one shape, plus an `error` the CLI
    turns into a non-zero exit code. Nothing is written to disk for this person, which is
    the point: an empty dossier committed here would be skipped by every later run and the
    failure would become permanent.
    """
    return {
        "person_id": str(person.person_id),
        "status": "unresolved",
        "confidence": 0.0,
        "facts_kept": 0,
        "facts_excluded": 0,
        "hubs": 0,
        "zero_result_sources": [k for k in trace.zero_result_sources if k in _SOURCE_KINDS],
        "failed_sources": sorted(trace.connector_errors),
        "name": str(person.name),
        "documents": len(trace.documents),
        "llm_calls": int(trace.llm_calls),
        "skipped": False,
        "error": reason,
    }


async def build_all(
    roster_path: str | Path,
    out_dir: str | Path,
    *,
    connectors: Sequence[Connector] | None = None,
    llm: LLMClient | None = None,
    budget: Budget | None = None,
    force: bool = False,
    only: str | None = None,
) -> BuildReport:
    """Research every person on the roster and commit the results to `out_dir`.

    Writes `out_dir/{person_id}.json` per person and every ACCEPTED `RawDoc` to
    `out_dir/../docs/{doc_id}.json`, so a committed dossier's citations can be checked
    offline months later without re-fetching anything (T-9 depends on this).

    A person whose dossier already exists is skipped unless `force` — a build is expensive
    and mostly idempotent, so re-running it after adding one person must not re-research
    the other nine. `only` restricts the run to a single person by id (or by name).

    `connectors` and `llm` default to the production fan-out and the production client,
    both built from `get_settings()` at CALL time. Every test injects doubles instead.
    """
    started = _now()
    people = [p for p in load_roster(roster_path) if _selects(p, only)]
    if only and not people:
        log.warning("no roster person matched only=%r", only)

    out = Path(out_dir)
    docs_dir = out.parent / "docs"
    if out.resolve() == docs_dir.resolve():
        # DESIGN pins the docs directory as `out_dir/../docs`, so `--out docs` makes the
        # two ONE directory and `{person_id}.json` and `{doc_id}.json` co-mingle in it.
        # Refusing is the only honest option: the location is not ours to move.
        raise BuildError(
            f"--out {out} would put dossiers and their documents in the same directory "
            f"({docs_dir}); the documents go to out_dir/../docs, so pick another --out"
        )
    out.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    fan_out = list(connectors) if connectors is not None else _default_connectors()
    client = llm if llm is not None else _default_llm()
    limit = budget if budget is not None else Budget()

    rows: list[dict[str, Any]] = []
    for person in people:
        path = out / f"{person.person_id}.json"
        if path.exists() and not force:
            row = _existing_row(path, person, docs_dir)
            if row is not None:
                rows.append(row)
                continue

        trace = BuildTrace()
        try:
            dossier = await build_dossier(person, fan_out, client, limit, trace=trace)

            if trace.llm_calls and trace.llm_errors == trace.llm_calls:
                # EVERY model call failed. That is an unreachable model — no key, a 401,
                # no network — and not a person there is nothing to say about, but the two
                # produce byte-identical empty dossiers. Committing one would make the
                # outage permanent: the next run finds the file and skips the person.
                log.error(
                    "every one of %d model call(s) failed for %s; writing nothing",
                    trace.llm_calls,
                    person.name,
                )
                rows.append(
                    _failed_row(person, trace, f"all {trace.llm_calls} model call(s) failed")
                )
                continue

            # Documents FIRST: a dossier on disk is what makes a person "already built",
            # so it must not exist until everything it cites does.
            for doc in trace.accepted_documents(dossier.resolution.accepted_doc_ids):
                if not _safe_segment(doc.doc_id):
                    log.warning("refusing to commit %r: its doc_id is not a filename", doc.url)
                    continue
                _write_json(docs_dir / f"{doc.doc_id}.json", doc)
            _write_json(path, dossier)
            rows.append(report_row(dossier, trace))
        except Exception as exc:
            # DESIGN Decision 8 at the PERSON level. A disk error or a bug in one stage
            # must cost that person, not the nine after them and not the rows already
            # earned by the three before.
            log.exception("build failed for %s", person.name)
            rows.append(_failed_row(person, trace, f"{type(exc).__name__}: {exc}"))

    return BuildReport(people=rows, started_at=started, finished_at=_now())


def _default_connectors() -> list[Connector]:
    """The production fan-out: `arrival.connectors.all_connectors`, imported lazily.

    The connector package pulls in httpx and all ten source modules; a CLI that only needs
    `--help`, and a test that injects doubles, should pay for none of that. Settings are
    read HERE rather than at import, so a process that reconfigures the environment before
    building sees the configuration it set.
    """
    from arrival.connectors import all_connectors

    return all_connectors(get_settings())


# --------------------------------------------------------------------------
# the CLI verb (called by arrival.__main__)
# --------------------------------------------------------------------------


def build_command(
    args: Sequence[str],
    *,
    connectors: Sequence[Connector] | None = None,
    llm: LLMClient | None = None,
) -> int:
    """`python -m arrival build ...`. Returns the process exit code.

    Lives here rather than in `__main__` so it can be exercised without going through
    argv dispatch, and so `__main__` keeps the small shape T-0 pinned.
    """
    parser = argparse.ArgumentParser(
        prog="python -m arrival build",
        description="Research the roster and write dossier JSON.",
        add_help=True,
    )
    parser.add_argument("--roster", default=None, help="roster YAML (default: data/roster.yaml)")
    parser.add_argument("--out", default=None, help="dossier output dir (default: DOSSIER_DIR)")
    parser.add_argument("--force", action="store_true", help="rebuild dossiers that exist")
    # PER PERSON, all three — see `build_dossier`. Without a lever here the only way to
    # change what a build costs against a paid API is to edit the source.
    parser.add_argument(
        "--docs-per-connector", type=int, default=None,
        help=(
            "documents to ask each source for, per person "
            f"(default {Budget().docs_per_connector})"
        ),
    )
    parser.add_argument(
        "--max-docs", type=int, default=None,
        help=f"documents to research per person (default {Budget().max_docs_total})",
    )
    parser.add_argument(
        "--max-llm-calls", type=int, default=None,
        help=f"model calls per person (default {Budget().max_llm_calls})",
    )
    parser.add_argument(
        "--only", default=None, help="build just this person_id (a name is accepted too)"
    )
    try:
        opts = parser.parse_args(list(args))
    except SystemExit as exc:  # --help exits 0; a usage error exits 2
        return int(exc.code or 0)

    try:
        settings = get_settings()  # read at CALL time, never at import
    except ValueError as exc:
        # The same escape as the roster read above, one layer further out. `Settings` is
        # configured with `env_file=".env"` (config.py), which python-dotenv opens in text
        # mode with a STRICT utf-8 codec -- so a `.env` saved as latin-1 raises
        # `UnicodeDecodeError`, a `ValueError` and not an `OSError`. This line sits between
        # the argparse `try` and the budget `try`, so nothing caught it: `python -m arrival
        # build` printed a raw dotenv traceback and exited 1. A configuration the CLI cannot
        # read is bad input, which is exit 2 with a sentence an operator can act on.
        #
        # The path is worth printing because `env_file=".env"` is RELATIVE: unlike
        # `dossier_dir`, which config.py anchors on `__file__`, this resolves against the
        # process CWD, so the offending file is not necessarily the one in the repo.
        print(f"arrival: cannot read configuration from {Path.cwd() / '.env'}: {exc}",
              file=sys.stderr)
        return 2
    roster = Path(opts.roster) if opts.roster else Path("data/roster.yaml")
    out_dir = Path(opts.out) if opts.out else Path(settings.dossier_dir)

    overrides = {
        "docs_per_connector": opts.docs_per_connector,
        "max_docs_total": opts.max_docs,
        "max_llm_calls": opts.max_llm_calls,
    }
    try:
        budget = Budget(**{k: v for k, v in overrides.items() if v is not None})
    except ValidationError as exc:
        print(f"arrival: bad budget: {exc}", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(
            build_all(
                roster,
                out_dir,
                connectors=connectors,
                llm=llm,
                budget=budget,
                force=opts.force,
                only=opts.only,
            )
        )
    except (RosterError, BuildError) as exc:
        print(f"arrival: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # a CLI reports; it does not traceback at an operator
        log.exception("build failed")
        print(f"arrival: build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(format_report(report))

    if opts.only and not report.people:
        # A one-character typo in `--only` otherwise prints an empty table and exits 0,
        # which a per-person CI wrapper reads as "built, nothing to do".
        print(f"arrival: no roster person matched --only {opts.only!r}", file=sys.stderr)
        return 2

    failed = [row for row in report.people if row.get("error")]
    if failed:
        for row in failed:
            print(f"arrival: {row['person_id']}: {row['error']}", file=sys.stderr)
        print(
            f"arrival: {len(failed)} of {len(report.people)} people were not built; "
            "nothing was written for them",
            file=sys.stderr,
        )
        return 1
    return 0
