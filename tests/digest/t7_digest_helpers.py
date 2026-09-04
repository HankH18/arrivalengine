"""Shared construction helpers for T-7's own tests.

Not a test module: ``tests/`` is not a package, so this imports as a top-level module and
its basename must be unique across the whole suite (see the T-0 hazard note on duplicate
basenames). Hence the ``t7_`` prefix here as well as on every ``test_t7_*.py``.

Two things live here, and nothing else:

* loaders for the T-0 unit fixture dossiers, so no test hard-codes a path;
* builders for :class:`~arrival.contracts.Match` and for *variants* of a fixture fact.

The variant builder matters more than it looks. The committed fixtures carry excluded
facts but no low-confidence fact and no ``fec`` fact, so a test that only ever loads them
grades one third of R12. :func:`variant` produces the other two by copying a real fact and
changing exactly the field under test, which keeps the sentence, the quote and the
provenance chain intact — a synthetic fact with a made-up quote would be a fixture the
citation rules cannot be checked against.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from arrival.contracts import Dossier, Fact, Hub, HubContribution, Match

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "dossiers"


def load(person_id: str) -> Dossier:
    """One of the T-0 unit fixture dossiers (``alpha``..``delta``)."""
    path = FIXTURE_DIR / f"{person_id}.json"
    return Dossier.model_validate_json(path.read_text(encoding="utf-8"))


def fact_of(dossier: Dossier, fact_id: str) -> Fact:
    for fact in dossier.facts:
        if fact.fact_id == fact_id:
            return fact
    raise AssertionError(f"fixture changed: {fact_id} missing from {dossier.person.person_id}")


def hub_of(dossier: Dossier, hub_id: str) -> Hub:
    for hub in dossier.hubs:
        if hub.hub_id == hub_id:
            return hub
    raise AssertionError(f"fixture changed: hub {hub_id} missing from {dossier.person.person_id}")


def variant(fact: Fact, **changes: Any) -> Fact:
    """A copy of ``fact`` with provenance fields changed by name.

    ``category``, ``excluded`` and ``exclusion_reason`` land on the fact; ``published_at``,
    ``confidence``, ``source_kind``, ``doc_id`` and ``url`` land on its provenance.
    """
    provenance_fields = {"published_at", "confidence", "source_kind", "doc_id", "url"}
    on_provenance = {k: v for k, v in changes.items() if k in provenance_fields}
    on_fact = {k: v for k, v in changes.items() if k not in provenance_fields}
    if on_provenance:
        on_fact["provenance"] = fact.provenance.model_copy(update=on_provenance)
    return fact.model_copy(update=on_fact)


def with_facts(dossier: Dossier, facts: list[Fact]) -> Dossier:
    return dossier.model_copy(update={"facts": facts})


def replacing(dossier: Dossier, replacements: dict[str, Fact]) -> Dossier:
    """``dossier`` with the named facts swapped for the given ones, order preserved."""
    return with_facts(dossier, [replacements.get(f.fact_id, f) for f in dossier.facts])


def make_match(
    arriving: Dossier,
    other: Dossier,
    *,
    score: float,
    why: str,
    hub_id: str | None = None,
    type_boost: float = 1.0,
    idf_weight: float = 0.5108256237659907,
) -> Match:
    """A ``Match`` shaped exactly as DESIGN says T-5 emits one.

    ``HubContribution.hub`` is the ARRIVING person's Hub object, which is what makes its
    ``evidence_fact_ids`` resolvable in the arriving dossier — and therefore what makes the
    digest's citation rule for Meet rows meaningful.
    """
    contributions: list[HubContribution] = []
    path = [f"person:{arriving.person.person_id}", f"person:{other.person.person_id}"]
    if hub_id is not None:
        contributions = [
            HubContribution(
                hub=hub_of(arriving, hub_id),
                idf_weight=idf_weight,
                recency=1.0,
                type_boost=type_boost,
                contribution=idf_weight * type_boost,
            )
        ]
        path = [
            f"person:{arriving.person.person_id}",
            f"hub:{hub_id}",
            f"person:{other.person.person_id}",
        ]
    return Match(other=other.person, score=score, contributions=contributions, path=path, why=why)


def promoted_to_freshest(dossier: Dossier, fact_ids: set[str]) -> Dossier:
    """Make the named facts the freshest ``recent_activity`` in the dossier.

    Ordering must never be what hides a withheld fact. In the fixtures as committed the
    excluded facts are older than the displayable ones, so a builder that filtered nothing
    at all would still look clean; this puts them at the front of the queue while touching
    nothing that makes them withheld.
    """
    facts = []
    for index, fact in enumerate(dossier.facts):
        if fact.fact_id in fact_ids:
            fact = variant(
                fact,
                category="recent_activity",
                published_at=dt.date(2026, 12, 1) + dt.timedelta(days=index),
            )
        facts.append(fact)
    return with_facts(dossier, facts)
