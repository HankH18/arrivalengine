"""T-0 acceptance 5: the doubles conform to the frozen Protocols and are loud when unscripted."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from arrival.contracts import Connector, LLMClient, LLMError, PersonRef, RawDoc, Verdict
from doubles import ConnectorDouble, LLMCall, LLMDouble

pytestmark = pytest.mark.ticket("T-0")

NOW = datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC)
PERSON = PersonRef(person_id="alpha", name="Teodoro Vance", details=["CTO", "Austin"])


class Shape(BaseModel):
    """A throwaway schema, standing in for the internal schemas T-2/T-3 will define."""

    value: str


def _doc(n: int) -> RawDoc:
    return RawDoc(
        doc_id=f"doc{n}",
        source_kind="search",
        url=f"https://recorded.example/{n}",
        text=f"document number {n}",
        fetched_at=NOW,
    )


# --------------------------------------------------------------------------
# conformance
# --------------------------------------------------------------------------


def test_doubles_conform():
    """Both doubles satisfy their frozen Protocol via runtime_checkable isinstance."""
    assert isinstance(LLMDouble(), LLMClient)
    assert isinstance(ConnectorDouble(kind="search", docs=[]), Connector)


# --------------------------------------------------------------------------
# LLMDouble
# --------------------------------------------------------------------------


async def test_llm_double_matches_on_schema_name_and_prompt_substring():
    llm = LLMDouble().when("Shape", "resolve this", Shape(value="matched"))
    out = await llm.structured(system="s", user="please resolve this document", schema=Shape)
    assert out == Shape(value="matched")


async def test_llm_double_script_mapping_form():
    llm = LLMDouble(
        script={("Shape", "alpha"): Shape(value="a"), ("Shape", "bravo"): {"value": "b"}}
    )
    assert (await llm.structured(system="", user="about alpha", schema=Shape)).value == "a"
    assert (await llm.structured(system="", user="about bravo", schema=Shape)).value == "b"


async def test_llm_double_script_triple_form():
    llm = LLMDouble(script=[("Shape", "alpha", Shape(value="a"))])
    assert (await llm.structured(system="", user="alpha", schema=Shape)).value == "a"


async def test_llm_double_wrong_schema_name_does_not_match():
    """Keying on schema.__name__ means a rule for one schema never answers another."""
    llm = LLMDouble().when("Verdict", "", Verdict(
        doc_id="d", match="yes", confidence=1.0, evidence="e", disambiguator="d"
    ))
    with pytest.raises(LLMError):
        await llm.structured(system="", user="anything", schema=Shape)


async def test_llm_double_raises_when_unscripted():
    """An unscripted call must be LOUD — never a silent default (the whole point)."""
    llm = LLMDouble()
    with pytest.raises(LLMError, match="no scripted response"):
        await llm.structured(system="s", user="u", schema=Shape)
    assert llm.call_count == 1, "the failed call is still recorded, so tests can assert on it"


async def test_llm_double_queue_beats_rules_and_is_consumed_in_order():
    llm = LLMDouble().when("Shape", "", Shape(value="rule"))
    llm.queue(Shape(value="first")).queue({"value": "second"})
    assert (await llm.structured(system="", user="x", schema=Shape)).value == "first"
    assert (await llm.structured(system="", user="x", schema=Shape)).value == "second"
    assert (await llm.structured(system="", user="x", schema=Shape)).value == "rule"


async def test_llm_double_first_matching_rule_wins():
    llm = LLMDouble().when("Shape", "a", Shape(value="one")).when("Shape", "a", Shape(value="two"))
    assert (await llm.structured(system="", user="a", schema=Shape)).value == "one"


async def test_llm_double_coerces_dicts_and_json_strings():
    llm = LLMDouble().queue({"value": "from-dict"}).queue('{"value": "from-json"}')
    assert (await llm.structured(system="", user="x", schema=Shape)).value == "from-dict"
    assert (await llm.structured(system="", user="x", schema=Shape)).value == "from-json"


async def test_llm_double_can_script_a_failure():
    llm = LLMDouble().queue(LLMError("upstream exploded"))
    with pytest.raises(LLMError, match="upstream exploded"):
        await llm.structured(system="", user="x", schema=Shape)


async def test_llm_double_records_calls_with_the_documented_field_order():
    llm = LLMDouble().when("Shape", "", Shape(value="v"))
    await llm.structured(system="sys", user="usr", schema=Shape, max_tokens=512, cache_prefix=False)
    assert llm.calls == [LLMCall("Shape", "usr", "sys", 512, False)]
    call = llm.calls[0]
    assert (call.schema_name, call.user) == ("Shape", "usr")
    assert (call.system, call.max_tokens, call.cache_prefix) == ("sys", 512, False)
    assert llm.calls_for("Shape") == llm.calls
    assert llm.calls_for("Verdict") == []


def test_llm_call_defaults_match_the_protocol_defaults():
    """A positionally-constructed LLMCall must match one recorded from a default call."""
    assert LLMCall("Shape", "usr") == LLMCall("Shape", "usr", "", 2000, True)


async def test_llm_double_delay_lets_t7_simulate_a_timeout():
    llm = LLMDouble(delay=0.25).when("Shape", "", Shape(value="slow"))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            llm.structured(system="", user="x", schema=Shape), timeout=0.05
        )

    fast = LLMDouble().when("Shape", "", Shape(value="quick"))
    started = time.monotonic()
    await fast.structured(system="", user="x", schema=Shape)
    assert time.monotonic() - started < 0.25


async def test_llm_double_per_rule_delay_overrides_the_default():
    llm = LLMDouble(delay=0.0).when("Shape", "slow", Shape(value="s"), delay=0.2)
    llm.when("Shape", "fast", Shape(value="f"))
    started = time.monotonic()
    await llm.structured(system="", user="fast", schema=Shape)
    assert time.monotonic() - started < 0.2


# --------------------------------------------------------------------------
# ConnectorDouble
# --------------------------------------------------------------------------


async def test_connector_double_respects_budget_and_records_calls():
    docs = [_doc(i) for i in range(5)]
    connector = ConnectorDouble("github", docs)
    assert connector.kind == "github"
    assert await connector.search(PERSON, 2) == docs[:2]
    assert await connector.search(PERSON, 99) == docs
    assert connector.calls == [(PERSON, 2), (PERSON, 99)]


async def test_connector_double_returns_a_copy():
    """A caller that mutates the result must not corrupt the double for the next test."""
    docs = [_doc(0)]
    connector = ConnectorDouble("search", docs)
    got = await connector.search(PERSON, 1)
    got.clear()
    assert await connector.search(PERSON, 1) == docs


async def test_connector_double_can_be_made_to_raise():
    """T-6 proves the pipeline degrades rather than aborting when a source blows up."""
    connector = ConnectorDouble("edgar", [_doc(0)], raises=RuntimeError("EDGAR is down"))
    with pytest.raises(RuntimeError, match="EDGAR is down"):
        await connector.search(PERSON, 1)
    assert connector.calls == [(PERSON, 1)], "the attempt is recorded even though it raised"


async def test_connector_double_empty_by_default():
    assert await ConnectorDouble("hn").search(PERSON, 5) == []


# --------------------------------------------------------------------------
# a mis-scripted double must be loud in EVERY response form
# --------------------------------------------------------------------------


class OtherShape(BaseModel):
    """A second throwaway schema that happens to overlap Shape on nothing."""

    wrong: int


async def test_llm_double_rejects_a_model_of_the_wrong_schema():
    """The Protocol says structured "returns an instance of `schema`" — so enforce it.

    The dict and JSON-string paths were already validated into `schema` and raise; the
    model-instance path (the primary form, and the one all six downstream tickets use) used
    to return whatever it was handed. A test that queued its responses out of order, or
    wrote an over-broad rule like `.when("Verdict", "", ...)`, then fed the code under test
    a model the real client could never return — and wherever the two models overlap on the
    fields the code touches, the test goes green on behaviour production cannot produce.
    """
    llm = LLMDouble().when("Shape", "", OtherShape(wrong=1))
    with pytest.raises(LLMError, match="asked for schema 'Shape'|asked for schema Shape"):
        await llm.structured(system="", user="x", schema=Shape)


async def test_llm_double_rejects_a_wrong_schema_model_from_the_queue_too():
    llm = LLMDouble().queue(OtherShape(wrong=1))
    with pytest.raises(LLMError):
        await llm.structured(system="", user="x", schema=Shape)


async def test_llm_double_still_accepts_a_subclass_of_the_requested_schema():
    """`isinstance`, not `type is` — a narrower model is still an instance of `schema`."""

    class NarrowerShape(Shape):
        extra: int = 0

    llm = LLMDouble().queue(NarrowerShape(value="v", extra=3))
    out = await llm.structured(system="", user="x", schema=Shape)
    assert isinstance(out, Shape) and out.value == "v"


# --------------------------------------------------------------------------
# ConnectorDouble: budget arithmetic, latency, and a real SourceKind
# --------------------------------------------------------------------------


async def test_connector_double_treats_a_negative_budget_as_zero():
    """`docs[:-1]` is len(docs)-1 documents, not none — the dangerous direction.

    T-6 computes a per-connector remaining budget against `max_docs_total`; the moment that
    arithmetic underflows past zero, a double that slices with a raw negative hands back
    nearly the FULL corpus and a genuine over-fetch bug reads as a green
    `test_build_dossier_happy`.
    """
    docs = [_doc(i) for i in range(5)]
    connector = ConnectorDouble("search", docs)
    assert await connector.search(PERSON, 0) == []
    assert await connector.search(PERSON, -1) == []
    assert await connector.search(PERSON, -99) == []
    assert connector.calls == [(PERSON, 0), (PERSON, -1), (PERSON, -99)]


async def test_connector_double_delay_makes_concurrency_observable():
    """T-6's fan-out must be concurrent; without latency, gather and a for-loop look alike."""
    connectors = [ConnectorDouble("search", [_doc(0)], delay=0.1) for _ in range(3)]
    started = time.monotonic()
    await asyncio.gather(*(c.search(PERSON, 1) for c in connectors))
    concurrent = time.monotonic() - started
    assert concurrent < 0.25, "three 0.1s searches gathered should not cost 0.3s"

    started = time.monotonic()
    for connector in connectors:
        await connector.search(PERSON, 1)
    assert time.monotonic() - started > concurrent, "serial must be measurably slower"


def test_connector_double_rejects_a_kind_that_is_not_a_source_kind():
    """A typo'd kind would flow into T-6's BuildReport, which validates nothing."""
    with pytest.raises(ValueError, match="is not a SourceKind"):
        ConnectorDouble(kind="githbu")


# --------------------------------------------------------------------------
# the import-time guard
# --------------------------------------------------------------------------


def test_the_import_time_conformance_guard_survives_python_dash_O():
    """`assert` is stripped by `python -O`; a guard that vanishes in CI is not a guard."""
    source = (Path(__file__).resolve().parent / "doubles.py").read_text()
    assert "assert_conforms(LLMDouble(), LLMClient)" in source
    assert "assert_conforms(ConnectorDouble(kind=\"search\", docs=[]), Connector)" in source
    assert "\nassert isinstance(" not in source, "a bare assert here is stripped by -O"

    proc = subprocess.run(
        [sys.executable, "-O", "-c", "import sys; sys.path.insert(0, 'tests'); import doubles"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
