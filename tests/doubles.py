"""Test doubles shipped by T-0 and imported by T-2, T-3, T-4, T-6 and T-7.

``tests/`` is not a package, so import these as top-level names::

    from doubles import ConnectorDouble, LLMCall, LLMDouble

Design rule both doubles obey: **an unscripted call is loud.** `LLMDouble` raises
`contracts.LLMError` rather than returning a plausible default, because a double that
quietly satisfies an unexpected prompt turns a broken pipeline into a green test.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from arrival.contracts import Connector, LLMClient, LLMError, PersonRef, RawDoc, SourceKind

__all__ = ["ConnectorDouble", "LLMCall", "LLMDouble"]


@dataclass(frozen=True)
class LLMCall:
    """One recorded call to :meth:`LLMDouble.structured`.

    FIELD ORDER IS PART OF THE CONTRACT and downstream tickets construct these
    positionally, so it is ``schema_name`` first and ``user`` second — the two fields a
    test actually asserts on. ``system``, ``max_tokens`` and ``cache_prefix`` follow and
    all carry defaults::

        LLMCall("ExtractionResult", "…prompt text…")
        LLMCall("Verdict", "…", system="you are…", max_tokens=512, cache_prefix=True)

    Note this is NOT the argument order of ``LLMClient.structured``, which is
    keyword-only.
    """

    schema_name: str
    user: str
    system: str = ""
    max_tokens: int = 2000
    cache_prefix: bool = True


@dataclass
class _Rule:
    schema_name: str
    substring: str
    response: Any
    delay: float | None = None

    def matches(self, schema_name: str, user: str) -> bool:
        return self.schema_name == schema_name and self.substring in user


@dataclass
class _Queued:
    response: Any
    delay: float | None = None


@dataclass
class LLMDouble:
    """A scripted, recording stand-in for :class:`arrival.contracts.LLMClient`.

    Scripting is keyed by ``(schema.__name__, substring-of-the-user-prompt)``::

        llm = LLMDouble()
        llm.when("Verdict", "Nabeel", Verdict(...))          # a keyed rule
        llm.queue(Verdict(...))                              # next call, any key

    Rules are matched in registration order and the first match wins. Queued responses are
    consumed before rules are consulted, so ``queue`` is the way to script a *sequence*.
    An unmatched call raises :class:`arrival.contracts.LLMError`.

    Responses may be a ``BaseModel`` instance (returned as-is), a mapping, or a JSON
    string; the latter two are validated into the requested ``schema``. A response that is
    an ``Exception`` instance or class is raised instead of returned, which is how a test
    scripts a failing call.

    ``delay`` (constructor, per-rule or per-queued-item) awaits before responding, so T-7
    can drive its 2.5 s say-out-loud timeout with ``LLMDouble(delay=3.0)``.

    Args:
        script: optional initial rules, either ``{(schema_name, substring): response}`` or
            an iterable of ``(schema_name, substring, response)`` triples.
        delay: default seconds to await before every response.
    """

    script: Any = None
    delay: float = 0.0
    calls: list[LLMCall] = field(default_factory=list)
    _rules: list[_Rule] = field(default_factory=list, repr=False)
    _queue: deque[_Queued] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if self.script is None:
            return
        if isinstance(self.script, Mapping):
            for key, response in self.script.items():
                schema_name, substring = key
                self.when(schema_name, substring, response)
        elif isinstance(self.script, Iterable):
            for schema_name, substring, response in self.script:
                self.when(schema_name, substring, response)
        else:  # pragma: no cover - programmer error
            raise TypeError(f"unsupported script type: {type(self.script)!r}")

    # -- scripting ---------------------------------------------------------

    def when(
        self, schema_name: str, substring: str, response: Any, *, delay: float | None = None
    ) -> LLMDouble:
        """Add a rule. Returns self, so calls chain."""
        self._rules.append(_Rule(schema_name, substring, response, delay))
        return self

    def queue(self, response: Any, *, delay: float | None = None) -> LLMDouble:
        """Answer the NEXT call with ``response`` regardless of schema or prompt."""
        self._queue.append(_Queued(response, delay))
        return self

    # -- the Protocol ------------------------------------------------------

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        schema_name = schema.__name__
        self.calls.append(
            LLMCall(
                schema_name=schema_name,
                user=user,
                system=system,
                max_tokens=max_tokens,
                cache_prefix=cache_prefix,
            )
        )

        if self._queue:
            item = self._queue.popleft()
            response, delay = item.response, item.delay
        else:
            rule = next((r for r in self._rules if r.matches(schema_name, user)), None)
            if rule is None:
                raise LLMError(
                    f"LLMDouble has no scripted response for schema {schema_name!r}. "
                    f"Add one with .when({schema_name!r}, '<prompt substring>', response) "
                    f"or .queue(response). Prompt was: {user[:200]!r}"
                )
            response, delay = rule.response, rule.delay

        wait = self.delay if delay is None else delay
        if wait:
            await asyncio.sleep(wait)

        return _coerce(response, schema)

    # -- convenience for assertions ---------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, schema_name: str) -> list[LLMCall]:
        return [c for c in self.calls if c.schema_name == schema_name]


def _coerce(response: Any, schema: type[BaseModel]) -> BaseModel:
    if isinstance(response, BaseException) or (
        isinstance(response, type) and issubclass(response, BaseException)
    ):
        raise response
    if isinstance(response, BaseModel):
        return response
    if isinstance(response, str):
        return schema.model_validate_json(response)
    return schema.model_validate(response)


@dataclass
class ConnectorDouble:
    """A canned stand-in for :class:`arrival.contracts.Connector`.

    ``search`` returns ``docs[:budget]`` and records every call. Set ``raises`` to an
    exception instance or class to make it blow up, which is how T-6 proves the pipeline
    degrades instead of aborting when one source dies.

    Args:
        kind: the ``SourceKind`` this connector claims to be.
        docs: the documents it will return, in order.
        raises: optional exception (instance or class) to raise from ``search``.
    """

    kind: SourceKind
    docs: list[RawDoc] = field(default_factory=list)
    raises: BaseException | type[BaseException] | None = None
    calls: list[tuple[PersonRef, int]] = field(default_factory=list)

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        self.calls.append((person, budget))
        if self.raises is not None:
            raise self.raises
        return list(self.docs[:budget])


# Fail at import time rather than at some downstream ticket's assertion if a double ever
# drifts out of conformance with the frozen Protocols.
assert isinstance(LLMDouble(), LLMClient)
assert isinstance(ConnectorDouble(kind="search", docs=[]), Connector)
