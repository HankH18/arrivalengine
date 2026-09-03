"""Test doubles shipped by T-0 and imported by T-2, T-3, T-4, T-6 and T-7.

``tests/`` is not a package, so import these as top-level names::

    from doubles import ConnectorDouble, LLMCall, LLMDouble

Design rule both doubles obey: **an unscripted call is loud.** `LLMDouble` raises
`contracts.LLMError` rather than returning a plausible default, because a double that
quietly satisfies an unexpected prompt turns a broken pipeline into a green test.

**Conformance: use `assert_conforms`, not `isinstance`.** `Connector` and `LLMClient` are
`runtime_checkable`, and `isinstance` against a runtime-checkable Protocol checks only that
attributes with the right NAMES exist. It cannot see a signature, an argument order, an
async/sync mismatch, a return type, or whether `Connector.kind` is a real `SourceKind` —
`isinstance(BogusLLM(), LLMClient)` is `True` for a class whose only method is
`def structured(self): return "not a BaseModel"`. So the `conforms_to` test TASKS.md asks
T-1 and T-2 for is written as::

    from doubles import assert_conforms
    assert_conforms(AnthropicClient(settings), LLMClient)   # raises TypeError, listing
    assert_conforms(GithubConnector(...), Connector)        # every mismatch it found

`issubclass` is not an option for `Connector`: it carries the non-method member `kind`, so
`issubclass(x, Connector)` raises `TypeError("Protocols with non-method members don\'t
support issubclass()")` rather than answering. `assert_conforms` accepts a class or an
instance and works for both Protocols.
"""

from __future__ import annotations

import asyncio
import inspect
import typing
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from arrival.contracts import Connector, LLMClient, LLMError, PersonRef, RawDoc, SourceKind

__all__ = [
    "ConnectorDouble",
    "LLMCall",
    "LLMDouble",
    "assert_conforms",
    "conforms",
    "protocol_members",
    "protocol_mismatches",
]

SOURCE_KINDS: frozenset[str] = frozenset(typing.get_args(SourceKind))


# --------------------------------------------------------------------------
# Protocol conformance — the mechanism T-1 and T-2 are told to use
# --------------------------------------------------------------------------


def protocol_members(protocol: type) -> frozenset[str]:
    """The names a Protocol declares. ``typing.get_protocol_members`` is 3.13+; this is 3.12."""
    getter = getattr(typing, "get_protocol_members", None)
    if getter is not None:  # pragma: no cover - 3.12 takes the fallback
        return frozenset(getter(protocol))
    return frozenset(getattr(protocol, "__protocol_attrs__", ()))


def _resolved_signature(func: Any) -> inspect.Signature | str:
    """``inspect.Signature`` with annotations evaluated, or the raw string form."""
    try:
        return inspect.signature(func, eval_str=True)
    except Exception:  # unresolvable forward ref — compare the strings instead
        try:
            return str(inspect.signature(func))
        except (TypeError, ValueError):  # pragma: no cover - not a callable
            return "<no signature>"


def _declares(candidate: Any, name: str) -> bool:
    """True when ``candidate`` has the attribute OR annotates it anywhere in its MRO."""
    if hasattr(candidate, name):
        return True
    owner = candidate if isinstance(candidate, type) else type(candidate)
    return any(name in getattr(klass, "__annotations__", {}) for klass in owner.__mro__)


def protocol_mismatches(candidate: Any, protocol: type) -> list[str]:
    """Every way ``candidate`` fails to implement ``protocol``. Empty list means it does.

    Checks what ``isinstance`` against a ``runtime_checkable`` Protocol cannot:

    * each method EXISTS and is callable;
    * async-ness matches (a sync ``structured`` is not an ``LLMClient``);
    * the full ``inspect.signature`` matches — parameter names, kinds, defaults,
      annotations AND the return annotation, so a renamed or retyped parameter, a changed
      default, and positional-vs-keyword-only are all caught. (``Signature`` equality
      treats KEYWORD-ONLY parameters as unordered, correctly: reordering ``system`` and
      ``user`` in the declaration is invisible to every caller. What no static check can
      see is a BODY that swaps the two — that is what ``LLMCall``'s recorded
      ``system``/``user`` fields are for.)
    * a non-method data member (``Connector.kind``) is present, and when the Protocol
      types it as a ``Literal`` the VALUE is one of that Literal's members.

    ``candidate`` may be a class or an instance.
    """
    owner = candidate if isinstance(candidate, type) else type(candidate)
    hints = typing.get_type_hints(protocol)
    members = protocol_members(protocol)

    problems: list[str] = []
    for name in sorted(members):
        expected = getattr(protocol, name, None)

        if expected is None or not callable(expected):
            # a data member, e.g. `kind: SourceKind`
            if not _declares(candidate, name):
                problems.append(f"{name}: missing (protocol declares it)")
                continue
            if isinstance(candidate, type):
                # A class may only ANNOTATE the member (a dataclass field with no default
                # has no class attribute), so there is no value to range-check yet.
                continue
            hint = hints.get(name)
            allowed = typing.get_args(hint) if typing.get_origin(hint) is typing.Literal else ()
            value = getattr(candidate, name)
            if allowed and value not in allowed:
                problems.append(
                    f"{name}: value {value!r} is not one of {list(allowed)!r}"
                )
            continue

        actual = getattr(owner, name, None)
        if actual is None:
            problems.append(f"{name}(): missing (protocol declares it)")
            continue
        if not callable(actual):
            problems.append(f"{name}: {type(actual).__name__} is not callable")
            continue

        want_async = inspect.iscoroutinefunction(expected)
        got_async = inspect.iscoroutinefunction(actual)
        if want_async != got_async:
            problems.append(
                f"{name}(): protocol declares it "
                f"{'async' if want_async else 'sync'} but the implementation is "
                f"{'async' if got_async else 'sync'}"
            )

        want_sig, got_sig = _resolved_signature(expected), _resolved_signature(actual)
        if want_sig != got_sig:
            problems.append(f"{name}{got_sig} does not match the protocol's {name}{want_sig}")

    return problems


def conforms(candidate: Any, protocol: type) -> bool:
    """``True`` when ``candidate`` really implements ``protocol``. See ``assert_conforms``."""
    return not protocol_mismatches(candidate, protocol)


def assert_conforms(candidate: Any, protocol: type) -> None:
    """Raise ``TypeError`` listing every mismatch, or return None.

    This is the ``conforms_to`` check TASKS.md hands T-1 (``Connector``) and T-2
    (``LLMClient``). Prefer it over ``isinstance``, which sees only attribute names, and
    over ``issubclass``, which raises for ``Connector`` because of its ``kind`` member.
    """
    problems = protocol_mismatches(candidate, protocol)
    if problems:
        name = candidate.__name__ if isinstance(candidate, type) else type(candidate).__name__
        raise TypeError(
            f"{name} does not conform to {protocol.__name__}:\n  - "
            + "\n  - ".join(problems)
        )


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
        # The Protocol says structured "returns an instance of `schema`", so a model of
        # some OTHER type is a mis-scripted test, not a response. Dicts and JSON strings
        # are validated into `schema` below and would already raise; the model-instance
        # path is the one all six downstream tickets use, so it must be just as loud.
        if not isinstance(response, schema):
            raise LLMError(
                f"LLMDouble was scripted with a {type(response).__name__} but the call "
                f"asked for schema {schema.__name__}. The real client can only return an "
                f"instance of the requested schema, so this test would pass on behaviour "
                f"production cannot produce. Check the order of your .queue() calls or "
                f"the schema_name on your .when() rule."
            )
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

    ``delay`` awaits before returning, mirroring :class:`LLMDouble`. T-6's fan-out is
    required to be concurrent, and a double that answers instantly makes
    ``asyncio.gather`` and a serial ``for`` loop indistinguishable on the wall clock.

    Args:
        kind: the ``SourceKind`` this connector claims to be. Validated: a typo'd kind
            would otherwise flow into T-6's ``BuildReport.zero_result_sources``, which is
            a ``list[dict]`` and validates nothing.
        docs: the documents it will return, in order.
        raises: optional exception (instance or class) to raise from ``search``.
        delay: seconds to await before responding.
    """

    kind: SourceKind
    docs: list[RawDoc] = field(default_factory=list)
    raises: BaseException | type[BaseException] | None = None
    delay: float = 0.0
    calls: list[tuple[PersonRef, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValueError(
                f"{self.kind!r} is not a SourceKind. Valid kinds: {sorted(SOURCE_KINDS)}"
            )

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        self.calls.append((person, budget))
        if self.raises is not None:
            raise self.raises
        if self.delay:
            await asyncio.sleep(self.delay)
        # max(0, ...) because docs[:-1] is len(docs)-1 documents, not zero: an underflowed
        # per-connector budget in T-6 would otherwise hand back nearly the FULL corpus and
        # a real over-fetch bug would read as green.
        return list(self.docs[: max(0, budget)])


# Fail at import time rather than at some downstream ticket's assertion if a double ever
# drifts out of conformance with the frozen Protocols. `raise`, not `assert`: `python -O`
# strips assert statements, and a guard that vanishes under an optimised CI lane is not a
# guard. `assert_conforms`, not `isinstance`: see the module docstring.
assert_conforms(LLMDouble(), LLMClient)
assert_conforms(ConnectorDouble(kind="search", docs=[]), Connector)
