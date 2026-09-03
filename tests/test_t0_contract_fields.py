"""T-0 acceptance 1, graded: EVERY field of the frozen contract, verbatim from DESIGN.

Why this module exists, when ``test_t0_contracts.py`` already round-trips a fully
populated ``Dossier``: **pydantic v2 defaults to ``extra="ignore"``.** Constructing a model
with a keyword for a field the model no longer has silently drops it, and
``model_validate_json`` on a dump that lacks a field succeeds too. So a suite built out of
constructor calls and a round trip is blind to almost every transcription slip in the one
module nine tickets import and are forbidden to edit. Measured before this module existed:
dropping ``Verdict.disambiguator``, ``Match.why``, ``Digest.non_obvious``,
``Digest.sources``, ``BuildReport.started_at``, ``PersonRef.name`` …; retyping
``Match.score`` from ``float`` to ``int``; loosening ``Provenance.quote`` to
``str | None``; loosening ``Resolution.status`` from a ``Literal`` to ``str`` — 19 of 20
such mutations left the gate green.

``CONTRACT`` below is transcribed from DESIGN §Interfaces and is the *specification*, not a
mirror of ``contracts.py``. If a test here fails, the contract drifted: fix
``contracts.py``, or escalate (EXECUTION §6) if DESIGN itself is wrong. Never edit the
table to match the code.

Inline ``Literal``s are written out in full here on purpose — the named aliases are pinned
by ``test_literal_members_are_verbatim``, but ``Verdict.match`` and ``Resolution.status``
have no alias, and ``Verdict.match`` losing ``"unsure"`` would break DESIGN Decision 6
("anything unsure after both stages is excluded, fail closed") at runtime rather than at
the gate.
"""

from __future__ import annotations

import inspect
import typing
from datetime import date, datetime
from typing import Any, Literal

import pytest
from pydantic import BaseModel

import arrival.contracts as contracts
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
    Match,
    PersonRef,
    Provenance,
    RawDoc,
    Resolution,
    SourceKind,
    Verdict,
)
from doubles import protocol_members

pytestmark = pytest.mark.ticket("T-0")


class _Required:
    """Sentinel: DESIGN gives this field NO default, so the field is required."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<required>"


REQUIRED = _Required()

# (field name, annotation, default) in DESIGN's declaration order.
CONTRACT: dict[type[BaseModel], list[tuple[str, Any, Any]]] = {
    PersonRef: [
        ("person_id", str, REQUIRED),
        ("name", str, REQUIRED),
        ("details", list[str], []),
    ],
    RawDoc: [
        ("doc_id", str, REQUIRED),
        ("source_kind", SourceKind, REQUIRED),
        ("url", str, REQUIRED),
        ("title", str, ""),
        ("text", str, REQUIRED),
        ("published_at", date | None, None),
        ("fetched_at", datetime, REQUIRED),
    ],
    Verdict: [
        ("doc_id", str, REQUIRED),
        ("match", Literal["yes", "no", "unsure"], REQUIRED),
        ("confidence", float, REQUIRED),
        ("evidence", str, REQUIRED),
        ("disambiguator", str, REQUIRED),
    ],
    Resolution: [
        ("person_id", str, REQUIRED),
        ("status", Literal["resolved", "unresolved"], REQUIRED),
        ("strong_keys", dict[str, str], {}),
        ("accepted_doc_ids", list[str], REQUIRED),
        ("rejected", list[Verdict], REQUIRED),
        ("confidence", float, REQUIRED),
    ],
    Provenance: [
        ("doc_id", str, REQUIRED),
        ("url", str, REQUIRED),
        ("source_kind", SourceKind, REQUIRED),
        ("quote", str, REQUIRED),
        ("published_at", date | None, None),
        ("retrieved_at", datetime, REQUIRED),
        ("confidence", float, REQUIRED),
    ],
    Fact: [
        ("fact_id", str, REQUIRED),
        ("text", str, REQUIRED),
        ("category", FactCategory, REQUIRED),
        ("provenance", Provenance, REQUIRED),
        ("excluded", bool, False),
        ("exclusion_reason", ExclusionReason | None, None),
    ],
    Hub: [
        ("hub_id", str, REQUIRED),
        ("label", str, REQUIRED),
        ("type", HubType, REQUIRED),
        ("recency", float, 1.0),
        ("evidence_fact_ids", list[str], []),
    ],
    Dossier: [
        ("person", PersonRef, REQUIRED),
        ("resolution", Resolution, REQUIRED),
        ("facts", list[Fact], REQUIRED),
        ("hubs", list[Hub], REQUIRED),
        ("built_at", datetime, REQUIRED),
        ("schema_version", int, 1),
    ],
    HubContribution: [
        ("hub", Hub, REQUIRED),
        ("idf_weight", float, REQUIRED),
        ("recency", float, REQUIRED),
        ("type_boost", float, REQUIRED),
        ("contribution", float, REQUIRED),
    ],
    Match: [
        ("other", PersonRef, REQUIRED),
        ("score", float, REQUIRED),
        ("contributions", list[HubContribution], REQUIRED),
        ("path", list[str], REQUIRED),
        ("why", str, REQUIRED),
    ],
    Digest: [
        ("digest_id", str, REQUIRED),
        ("person", PersonRef, REQUIRED),
        ("who_line", str, REQUIRED),
        ("meet", list[Match], REQUIRED),
        ("lately", list[Fact], REQUIRED),
        # NO default: DESIGN writes `non_obvious: Fact | None`, so T-7 must decide it
        # explicitly. `= None` would make R7's "exactly 1 when available" silently
        # optional.
        ("non_obvious", Fact | None, REQUIRED),
        ("say_out_loud", str, REQUIRED),
        ("sources", list[Provenance], REQUIRED),
        ("exclusion_policy", str, REQUIRED),
        ("created_at", datetime, REQUIRED),
    ],
    Budget: [
        ("docs_per_connector", int, 8),
        ("max_docs_total", int, 40),
        ("max_llm_calls", int, 80),
    ],
    BuildReport: [
        ("people", list[dict], REQUIRED),
        ("started_at", datetime, REQUIRED),
        ("finished_at", datetime, REQUIRED),
    ],
}

MODEL_IDS = [model.__name__ for model in CONTRACT]


def _default(model: type[BaseModel], name: str) -> Any:
    info = model.model_fields[name]
    if info.is_required():
        return REQUIRED
    return info.get_default(call_default_factory=True)


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT, ids=MODEL_IDS)
def test_field_names_and_order_are_verbatim(model: type[BaseModel]):
    """Exactly these fields, in this order — no field added, renamed or dropped.

    Order matters as well as membership: DESIGN's block is the declaration order, and
    ``model_fields`` preserves it.
    """
    assert list(model.model_fields) == [name for name, _, _ in CONTRACT[model]]


@pytest.mark.parametrize("model", CONTRACT, ids=MODEL_IDS)
def test_field_annotations_are_verbatim(model: type[BaseModel]):
    """Every field's TYPE, including the two inline Literals that have no named alias."""
    for name, annotation, _ in CONTRACT[model]:
        assert model.model_fields[name].annotation == annotation, (
            f"{model.__name__}.{name}: contract says {annotation!r}, "
            f"code says {model.model_fields[name].annotation!r}"
        )


@pytest.mark.parametrize("model", CONTRACT, ids=MODEL_IDS)
def test_field_requiredness_and_defaults_are_verbatim(model: type[BaseModel]):
    """A required field must stay required, and every default is pinned to its value.

    Both directions are load-bearing. ``Fact.excluded`` defaulting to ``True`` would make
    every fact T-3 emits non-displayable (R12/R13) and produce empty digests rather than an
    error; ``Digest.non_obvious`` gaining ``= None`` would let T-7 skip R7's slot silently.
    """
    for name, _, expected in CONTRACT[model]:
        actual = _default(model, name)
        if expected is REQUIRED:
            assert actual is REQUIRED, f"{model.__name__}.{name} must have NO default"
        else:
            assert actual is not REQUIRED, (
                f"{model.__name__}.{name} must default to {expected!r}, but is required"
            )
            assert actual == expected and type(actual) is type(expected), (
                f"{model.__name__}.{name}: contract default {expected!r}, code {actual!r}"
            )


def test_the_table_covers_every_exported_model():
    """The table itself cannot go stale: a model added to or removed from contracts fails."""
    exported = {
        getattr(contracts, name)
        for name in contracts.__all__
        if isinstance(getattr(contracts, name), type)
        and issubclass(getattr(contracts, name), BaseModel)
    }
    assert exported == set(CONTRACT), (
        "contracts.__all__ and this table disagree; symmetric difference: "
        f"{ {m.__name__ for m in exported ^ set(CONTRACT)} }"
    )


@pytest.mark.parametrize("model", CONTRACT, ids=MODEL_IDS)
def test_literal_defaults_are_emitted_into_the_json_schema(model: type[BaseModel]):
    """DESIGN Decision 9 ships ``schema`` inside the CACHED Anthropic prefix.

    ``Field(default_factory=list)`` and ``= []`` behave identically in Python but differ in
    ``model_json_schema()``: the factory form suppresses the ``"default"`` key. DESIGN
    spells these three fields with literal defaults, so the schema the LLM sees must carry
    them.
    """
    schema = model.model_json_schema()
    for name, _, expected in CONTRACT[model]:
        if expected is REQUIRED:
            continue
        prop = schema["properties"][name]
        assert "default" in prop, f"{model.__name__}.{name} lost its JSON-schema default"
        assert prop["default"] == expected


# --------------------------------------------------------------------------
# the Protocols
# --------------------------------------------------------------------------


def test_connector_protocol_is_verbatim():
    """``kind: SourceKind`` plus one async ``search``; nothing else, nothing retyped.

    Deleting ``kind`` was one of the mutations the T-0 gate did not catch, and it is the
    field T-6's ``BuildReport.zero_result_sources`` is built from.
    """
    assert protocol_members(Connector) == {"kind", "search"}
    assert typing.get_type_hints(Connector)["kind"] == SourceKind

    signature = inspect.signature(Connector.search, eval_str=True)
    assert list(signature.parameters) == ["self", "person", "budget"]
    assert signature.parameters["person"].annotation is PersonRef
    assert signature.parameters["budget"].annotation is int
    assert signature.return_annotation == list[RawDoc]
    assert inspect.iscoroutinefunction(Connector.search)


def test_llm_client_protocol_is_verbatim():
    """One async ``structured``; the keyword-only shape is pinned in test_t0_contracts.py."""
    assert protocol_members(LLMClient) == {"structured"}
    assert inspect.iscoroutinefunction(LLMClient.structured)
    signature = inspect.signature(LLMClient.structured, eval_str=True)
    assert signature.return_annotation is BaseModel
