"""The PRODUCTION defaults: what `build_all` uses when nothing is injected.

Every other module here injects doubles, which proves the composition but says nothing
about the path T-9 will actually run. Constructing the real fan-out and the real client
costs no network — connectors build lazily and `AnthropicClient` builds its SDK client on
first use — so the wiring can be asserted offline, which is the only place it ever gets
asserted at all.
"""

from __future__ import annotations

import pytest

from arrival.connectors import DISPLAY_PRIORITY
from arrival.contracts import Connector, LLMClient
from arrival.research import _default_connectors, _default_llm, _TieredClient
from doubles import LLMDouble, assert_conforms

pytestmark = pytest.mark.ticket("T-6")


def test_the_default_fan_out_is_the_ten_connectors_in_display_priority_order():
    connectors = _default_connectors()

    assert [c.kind for c in connectors] == list(DISPLAY_PRIORITY)
    assert len(connectors) == 10
    for connector in connectors:
        assert_conforms(connector, Connector)


def test_the_default_client_conforms_and_needs_no_api_key():
    client = _default_llm()

    assert_conforms(client, LLMClient)


async def test_the_default_client_sends_resolution_to_the_smart_model_and_the_rest_to_fast():
    """DESIGN Decision 9. `build_dossier` takes ONE client, so the split lives here."""
    from arrival.extract import ExtractionResult
    from arrival.resolve import DocVerdict

    smart = LLMDouble().when("DocVerdict", "", DocVerdict(doc_id="d", match="yes"))
    fast = LLMDouble().when("ExtractionResult", "", ExtractionResult())
    tiered = _TieredClient(smart, fast)

    await tiered.structured(system="s", user="u", schema=DocVerdict)
    await tiered.structured(system="s", user="u", schema=ExtractionResult)

    assert [c.schema_name for c in smart.calls] == ["DocVerdict"]
    assert [c.schema_name for c in fast.calls] == ["ExtractionResult"]


def test_the_default_client_reads_settings_at_call_time_not_import_time(monkeypatch):
    """Hazard 8: a module that snapshots settings at import fails the frozen suite only."""
    from arrival.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ANTHROPIC_MODEL_FAST", "a-model-set-after-import")
    try:
        assert get_settings().anthropic_model_fast == "a-model-set-after-import"
        assert_conforms(_default_llm(), LLMClient)
    finally:
        get_settings.cache_clear()


def test_the_smart_schema_set_is_read_from_the_resolver_not_written_as_a_string():
    """A rename inside `arrival.resolve` must not silently demote resolution to the cheap
    model. Reading the name off the class is what makes that impossible."""
    from arrival.resolve import DocVerdict

    assert _TieredClient.SMART_SCHEMAS == frozenset({DocVerdict.__name__})
