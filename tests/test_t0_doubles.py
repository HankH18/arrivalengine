"""T-0 acceptance 5: the doubles conform to the frozen Protocols and are loud when unscripted."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

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
