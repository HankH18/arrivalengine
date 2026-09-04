"""`AnthropicClient` — the production `arrival.contracts.LLMClient`.

One method, `structured`, which is deliberately narrow: a system prompt, a user prompt and
a Pydantic schema in; an instance of that schema out; `LLMError` when the model cannot be
made to produce one. Every caller in this project (T-2's resolver, T-3's extractor, T-4's
taste pass, T-6's pipeline, T-7's digest, T-8's web app) goes through that one door, which
is what makes `tests.doubles.LLMDouble` a faithful stand-in for the whole LLM layer.

Design points, each of which is load-bearing:

* **JSON-schema output.** The request carries `output_config.format` with the schema
  Pydantic derives from the model class, tightened to `additionalProperties: false` with
  every property required. The response is then parsed and validated back into that same
  class, so `structured` can only ever return an instance of the schema it was handed —
  an instance of some other model is a contract violation, not a response.
* **Cached system prefix.** With `cache_prefix=True` (the default) the system prompt is
  sent as a single text block carrying `cache_control: {"type": "ephemeral"}`. Callers are
  expected to keep the system prompt CONSTANT and put the per-document text in `user`;
  prompt caching is a prefix match, so one varying byte in the system prompt throws the
  cache away silently.
* **One retry, then `LLMError`.** A response that is not valid JSON, or that is valid JSON
  the schema rejects, is retried EXACTLY once and then raised as `LLMError`. A REFUSAL is
  not retried — it is a decision about the request, and the identical request earns the
  identical refusal at twice the price. Transport and
  API failures are not retried here (the SDK already retries 429/5xx) and are re-raised as
  `LLMError` so that a pipeline written against the double behaves the same way against
  the real client.
* **Model ids come from `Settings` at CALL time.** DESIGN Decision 9: model ids are
  settings, never constants at a call site. `get_settings()` is read inside `structured`,
  not at import and not in `__init__`, because the frozen acceptance harness does not
  reset the settings cache around tests.

Temperature: see `accepts_temperature` below — the pinned SDK (anthropic 1.3.0) has no
`temperature` parameter at all, and the current model families reject sampling parameters
outright, so `TEMPERATURE = 0` is applied only where the API still accepts it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from arrival.config import get_settings
from arrival.contracts import LLMError

__all__ = [
    "TEMPERATURE",
    "AnthropicClient",
    "accepts_temperature",
    "strict_json_schema",
]

#: DESIGN: resolution verdicts and extraction must be reproducible, so sampling is off.
#: Whether it can be *expressed* depends on the model — see `accepts_temperature`.
TEMPERATURE = 0

#: Model ids that still accept sampling parameters (`temperature`/`top_p`/`top_k`).
#: Deny by default: on the current families (Opus 5 / Sonnet 5 / Opus 4.6-4.8 / Fable /
#: Mythos) sampling parameters were REMOVED and a request carrying one is rejected with a
#: 400, while a request that omits it is served normally. A wrong "yes" here is a hard
#: failure; a wrong "no" only means the model uses its own default sampling.
_TEMPERATURE_MODELS = re.compile(
    r"claude-(?:haiku-4-5|haiku-3|opus-4-5|sonnet-4-5|3-5-|3-)",
    re.IGNORECASE,
)

#: Where `structured` looks up its model id on `Settings`, by tier (DESIGN Decision 9).
_TIER_FIELDS = {"smart": "anthropic_model_smart", "fast": "anthropic_model_fast"}

_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


def accepts_temperature(model_id: str) -> bool:
    """True when `model_id` still takes a `temperature` parameter.

    Measured on the pinned SDK: `anthropic==1.3.0`'s `messages.create` does not declare a
    `temperature` parameter at all (sampling parameters were removed from the surface), so
    the only way to send one is `extra_body`. On Claude Opus 5, Sonnet 5, Opus 4.6/4.7/4.8
    and the Fable/Mythos family the API rejects sampling parameters with a 400, and the
    default `Settings.anthropic_model_smart` is one of those. Sending it anyway would turn
    every production resolution call into a hard error in the name of determinism.
    """
    return bool(_TEMPERATURE_MODELS.search(model_id or ""))


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema for `schema`, tightened for structured output.

    Structured output wants closed objects: every property required and no extras. Pydantic
    omits fields that carry defaults from `required`, which would let the model return an
    object missing the very field the caller cares about (a verdict with no `evidence`
    still validates). Closing the schema is what makes "the model answered" and "the caller
    got an answer" the same event.
    """
    return _tighten(schema.model_json_schema())


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _tighten(value) for key, value in node.items()}
    properties = out.get("properties")
    if isinstance(properties, dict):
        out["additionalProperties"] = False
        out["required"] = list(properties)
    return out


class AnthropicClient:
    """`arrival.contracts.LLMClient`, backed by the Anthropic Messages API.

    Construction never touches the network and never needs a key: the SDK client is built
    lazily on the first real call, so `AnthropicClient(api_key="…")`,
    `AnthropicClient(settings)` and a bare `AnthropicClient()` are all safe to construct in
    a test, a CLI `--help` path, or an import-time module scope.

    Args:
        settings: a `Settings`. Omitted, `get_settings()` is read at call time.
        api_key: overrides `Settings.anthropic_api_key`.
        model: an explicit model id, overriding the tier lookup entirely.
        tier: `"smart"` (resolution verdicts, the say-out-loud line) or `"fast"`
            (extraction, taste classification). DESIGN Decision 9.
        client: an already-built async SDK client, injected by tests.
    """

    def __init__(
        self,
        settings: Any = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        tier: str = "smart",
        client: Any = None,
    ) -> None:
        if tier not in _TIER_FIELDS:
            raise ValueError(f"unknown model tier {tier!r}; expected one of {sorted(_TIER_FIELDS)}")
        self._settings = settings
        self._api_key = api_key
        self._model = model
        self._tier = tier
        self._client = client

    # -- construction helpers ------------------------------------------------

    @classmethod
    def fast(cls, settings: Any = None, **kwargs: Any) -> AnthropicClient:
        """The cheap-model client: extraction and taste classification."""
        return cls(settings, tier="fast", **kwargs)

    @classmethod
    def smart(cls, settings: Any = None, **kwargs: Any) -> AnthropicClient:
        """The smart-model client: resolution verdicts and the say-out-loud line."""
        return cls(settings, tier="smart", **kwargs)

    # -- the Protocol --------------------------------------------------------

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        """Ask the model for one `schema` instance. Raise `LLMError` if it cannot give one."""
        request = self.build_request(
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            cache_prefix=cache_prefix,
        )
        messages = self._sdk().messages

        problem: Exception | None = None
        for _attempt in range(2):  # the first call, then EXACTLY one retry
            try:
                response = await messages.create(**request)
            except LLMError:
                raise
            except Exception as exc:  # SDK / transport / API failure
                raise LLMError(
                    f"the Anthropic API call for {schema.__name__} failed: {exc}"
                ) from exc
            try:
                return _parse(response, schema)
            except LLMError:
                # A refusal is a DECISION, not a malformed answer. The retry exists to
                # give the model a second chance at valid JSON; re-sending a request the
                # model has already declined buys a second identical refusal and bills
                # for it. Raise it on the first response.
                raise
            except (ValueError, ValidationError) as exc:
                problem = exc
        raise LLMError(
            f"the model did not return valid {schema.__name__} JSON after one retry: {problem}"
        ) from problem

    # -- request construction (public so it can be asserted on directly) -----

    def build_request(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> dict[str, Any]:
        """The keyword arguments `messages.create` is called with."""
        model = self.model_id()
        system_block: Any = system
        if cache_prefix:
            # One block, marked cacheable: the caller keeps this prefix constant and puts
            # everything volatile in `user`, so the cache actually hits.
            system_block = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_block,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "format": {"type": "json_schema", "schema": strict_json_schema(schema)}
            },
        }
        if accepts_temperature(model):
            # anthropic 1.3.0 has no `temperature` parameter; `extra_body` is the only
            # channel, and only models that still accept sampling parameters get it.
            request["extra_body"] = {"temperature": TEMPERATURE}
        return request

    def model_id(self) -> str:
        """The model id for this client, read from `Settings` at CALL time."""
        if self._model:
            return self._model
        settings = self._settings if self._settings is not None else get_settings()
        return str(getattr(settings, _TIER_FIELDS[self._tier]))

    # -- internals -----------------------------------------------------------

    def _sdk(self) -> Any:
        """The async SDK client, built on first use so construction stays offline."""
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - the dependency is pinned
                raise LLMError(f"the anthropic SDK is not installed: {exc}") from exc
            settings = self._settings if self._settings is not None else get_settings()
            api_key = self._api_key or getattr(settings, "anthropic_api_key", None)
            if not api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set, so no LLM call can be made. A missing "
                    "key disables the capability; it must not crash at import time."
                )
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._client


def _parse(response: Any, schema: type[BaseModel]) -> BaseModel:
    """Validate one Messages response into `schema`, or raise."""
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise LLMError(f"the model refused the {schema.__name__} request")
    text = _response_text(response)
    if not text:
        raise ValueError(f"the {schema.__name__} response carried no text block")
    payload = json.loads(_unfence(text))
    if not isinstance(payload, dict):
        raise ValueError(
            f"expected a JSON object for {schema.__name__}, got {type(payload).__name__}"
        )
    return schema.model_validate(payload)


def _response_text(response: Any) -> str:
    """Concatenate the text blocks of a Messages response, dict- or object-shaped."""
    blocks = getattr(response, "content", None)
    if blocks is None and isinstance(response, dict):
        blocks = response.get("content")
    parts: list[str] = []
    for block in blocks or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts).strip()


def _unfence(text: str) -> str:
    """Strip a ```json fence, which a model occasionally adds around valid JSON."""
    match = _FENCE.match(text)
    return match.group("body") if match else text
