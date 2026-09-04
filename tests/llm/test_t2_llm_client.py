"""T-2 acceptance 1: the production `LLMClient`.

Nothing here touches the network. The SDK is replaced by a stub that records the request
and returns a scripted response, which is the only way to assert the three things the
ticket actually asks for — JSON-schema output, a cached system prefix, and one retry on
invalid JSON then `LLMError` — without a key or a socket.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from arrival.config import get_settings
from arrival.contracts import LLMClient, LLMError
from arrival.llm.client import (
    TEMPERATURE,
    AnthropicClient,
    accepts_temperature,
    strict_json_schema,
)
from doubles import LLMDouble, assert_conforms

pytestmark = pytest.mark.ticket("T-2")

DUMMY_KEY = "test-key-never-used"


class Answer(BaseModel):
    verdict: str
    confidence: float = 0.0


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    """Stands in for `AsyncAnthropic().messages`: records requests, replays a script."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def create(self, **request):
        self.requests.append(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(response, BaseException):
            raise response
        return response


class _SDK:
    def __init__(self, *responses) -> None:
        self.messages = _Messages(responses)


def client(*responses, **kwargs) -> tuple[AnthropicClient, _SDK]:
    sdk = _SDK(*responses)
    return AnthropicClient(api_key=DUMMY_KEY, client=sdk, **kwargs), sdk


# ------------------------------------------------------------------ conformance


def test_client_conforms():
    """Both the real client and the double satisfy the Protocol — signature included."""
    assert_conforms(AnthropicClient(api_key=DUMMY_KEY), LLMClient)
    assert_conforms(AnthropicClient, LLMClient)
    assert_conforms(LLMDouble(), LLMClient)
    assert isinstance(AnthropicClient(api_key=DUMMY_KEY), LLMClient)


def test_construction_is_offline_and_needs_no_key():
    """A missing key disables a capability; it must never crash at construction."""
    AnthropicClient()
    AnthropicClient(api_key=DUMMY_KEY)
    AnthropicClient(get_settings())


async def test_a_missing_api_key_is_an_llm_error_not_a_crash():
    settings = get_settings().model_copy(update={"anthropic_api_key": None})
    bare = AnthropicClient(settings)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        await bare.structured(system="s", user="u", schema=Answer)


# ----------------------------------------------------------------- the request


async def test_client_parses_a_stubbed_sdk_response_into_the_schema():
    llm, sdk = client(_Response('{"verdict": "yes", "confidence": 0.8}'))
    answer = await llm.structured(system="rules", user="doc", schema=Answer, max_tokens=512)
    assert isinstance(answer, Answer)
    assert (answer.verdict, answer.confidence) == ("yes", 0.8)
    assert len(sdk.messages.requests) == 1


async def test_the_request_asks_for_the_schema_as_json_and_caches_the_system_prefix():
    llm, sdk = client(_Response('{"verdict": "yes"}'))
    await llm.structured(system="rules", user="doc", schema=Answer, max_tokens=512)
    request = sdk.messages.requests[0]

    assert request["max_tokens"] == 512
    assert request["messages"] == [{"role": "user", "content": "doc"}]
    assert request["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
    ], "the system prefix is one cacheable block; volatile text belongs in `user`"

    fmt = request["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"].keys() == {"verdict", "confidence"}
    assert fmt["schema"]["additionalProperties"] is False
    assert sorted(fmt["schema"]["required"]) == ["confidence", "verdict"], (
        "a field with a default is still required of the model, or the answer can arrive "
        "missing the only part the caller wanted"
    )


async def test_cache_prefix_false_sends_a_plain_system_prompt():
    llm, sdk = client(_Response('{"verdict": "yes"}'))
    await llm.structured(system="rules", user="doc", schema=Answer, cache_prefix=False)
    assert sdk.messages.requests[0]["system"] == "rules"


def test_temperature_zero_is_sent_only_where_the_api_still_accepts_it():
    """DESIGN wants deterministic sampling; the current models reject the parameter.

    `anthropic==1.3.0`'s `messages.create` has no `temperature` parameter at all, and Opus
    5 / Sonnet 5 / Opus 4.6-4.8 reject sampling parameters with a 400. Sending it anyway
    would turn every resolution call into a hard error in the name of determinism, so it
    goes only to models that still take one.
    """
    assert TEMPERATURE == 0
    assert accepts_temperature("claude-haiku-4-5-20251001") is True
    assert accepts_temperature("claude-sonnet-5") is False
    assert accepts_temperature("claude-opus-5") is False

    haiku = AnthropicClient(api_key=DUMMY_KEY, model="claude-haiku-4-5-20251001")
    request = haiku.build_request(system="s", user="u", schema=Answer)
    assert request["extra_body"] == {"temperature": 0}

    sonnet = AnthropicClient(api_key=DUMMY_KEY, model="claude-sonnet-5")
    assert "extra_body" not in sonnet.build_request(system="s", user="u", schema=Answer)


def test_strict_json_schema_closes_nested_objects_too():
    class Outer(BaseModel):
        inner: Answer
        label: str = ""

    schema = strict_json_schema(Outer)
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["inner", "label"]
    nested = schema["$defs"]["Answer"]
    assert nested["additionalProperties"] is False
    assert sorted(nested["required"]) == ["confidence", "verdict"]


# ------------------------------------------------------------- model selection


def test_the_model_id_comes_from_settings_at_call_time():
    """DESIGN Decision 9. Read at call time: the frozen harness never clears the cache."""
    settings = get_settings()
    assert AnthropicClient().model_id() == settings.anthropic_model_smart
    assert AnthropicClient.fast().model_id() == settings.anthropic_model_fast
    assert AnthropicClient.smart().model_id() == settings.anthropic_model_smart
    assert AnthropicClient(model="claude-explicit").model_id() == "claude-explicit"

    changed = settings.model_copy(update={"anthropic_model_smart": "claude-configured"})
    assert AnthropicClient(changed).model_id() == "claude-configured"


def test_an_unknown_tier_is_refused_at_construction():
    with pytest.raises(ValueError, match="tier"):
        AnthropicClient(tier="medium")


# ---------------------------------------------------------------- the one retry


async def test_invalid_json_is_retried_exactly_once_and_then_raises():
    llm, sdk = client(_Response("this is not json"))
    with pytest.raises(LLMError, match="after one retry"):
        await llm.structured(system="s", user="u", schema=Answer)
    assert len(sdk.messages.requests) == 2, "one call, one retry, then stop"


async def test_json_the_schema_rejects_is_retried_then_raises():
    llm, sdk = client(_Response(json.dumps({"confidence": "not a number"})))
    with pytest.raises(LLMError):
        await llm.structured(system="s", user="u", schema=Answer)
    assert len(sdk.messages.requests) == 2


async def test_the_retry_can_succeed():
    llm, sdk = client(_Response("{oops"), _Response('{"verdict": "no"}'))
    answer = await llm.structured(system="s", user="u", schema=Answer)
    assert answer.verdict == "no"
    assert len(sdk.messages.requests) == 2


async def test_a_fenced_json_block_is_still_valid_json():
    llm, _sdk = client(_Response('```json\n{"verdict": "yes"}\n```'))
    assert (await llm.structured(system="s", user="u", schema=Answer)).verdict == "yes"


async def test_a_refusal_is_an_llm_error():
    llm, _sdk = client(_Response("", stop_reason="refusal"))
    with pytest.raises(LLMError):
        await llm.structured(system="s", user="u", schema=Answer)


async def test_a_transport_failure_becomes_an_llm_error_and_is_not_retried():
    llm, sdk = client(RuntimeError("connection reset"))
    with pytest.raises(LLMError, match="failed"):
        await llm.structured(system="s", user="u", schema=Answer)
    assert len(sdk.messages.requests) == 1, (
        "the SDK already retries 429/5xx; retrying here would multiply every outage"
    )


async def test_the_returned_object_is_an_instance_of_the_requested_schema():
    class Other(BaseModel):
        verdict: str

    llm, _sdk = client(_Response('{"verdict": "yes", "confidence": 0.5}'))
    answer = await llm.structured(system="s", user="u", schema=Answer)
    assert type(answer) is Answer and not isinstance(answer, Other)
