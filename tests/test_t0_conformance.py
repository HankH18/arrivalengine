"""T-0 acceptance 5: the conformance MECHANISM T-0 hands to T-1 and T-2 is evidence.

TASKS.md tells T-1 "Conforms to: contracts.Connector (test: ``isinstance(c, Connector)``
for each)" and T-2 "test_client_conforms asserts both AnthropicClient and LLMDouble satisfy
the Protocol". As written, those tests are nearly evidence-free: ``isinstance`` against a
``runtime_checkable`` Protocol checks only that attributes with the right NAMES exist.
Measured::

    class BogusLLM:
        def structured(self): return "not a BaseModel"          # sync, no args
    class BogusConnector:
        kind = "not-a-source-kind"
        def search(self): raise RuntimeError("always raises")   # sync, no args

    isinstance(BogusLLM(), LLMClient)        -> True
    isinstance(BogusConnector(), Connector)  -> True

So a T-1 connector whose ``search`` is synchronous, or a T-2 ``AnthropicClient`` whose
``structured`` is not async and returns a ``str``, both pass the prescribed test. T-0 ships
``doubles.assert_conforms`` instead, and this module proves it discriminates — the half a
conformance helper is usually missing.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from arrival.contracts import Connector, LLMClient, PersonRef, RawDoc
from doubles import (
    ConnectorDouble,
    LLMDouble,
    assert_conforms,
    conforms,
    protocol_members,
    protocol_mismatches,
)

pytestmark = pytest.mark.ticket("T-0")


# --------------------------------------------------------------------------
# the shipped doubles really conform
# --------------------------------------------------------------------------


def test_the_shipped_doubles_conform_by_signature_not_just_by_name():
    """Stronger than ``test_doubles_conform``: this compares signatures, not attributes."""
    assert_conforms(LLMDouble(), LLMClient)
    assert_conforms(ConnectorDouble(kind="search"), Connector)
    assert_conforms(LLMDouble, LLMClient)  # a class works too, for T-2's client
    assert_conforms(ConnectorDouble, Connector)


def test_protocol_members_are_what_we_think_they_are():
    assert protocol_members(LLMClient) == {"structured"}
    assert protocol_members(Connector) == {"kind", "search"}


# --------------------------------------------------------------------------
# ... and the helper rejects every drift isinstance cannot see
# --------------------------------------------------------------------------


class SyncStructured:
    """T-2's client, but not a coroutine — the SDK has a sync surface, so this is easy."""

    def structured(self, *, system: str, user: str, schema, max_tokens=2000, cache_prefix=True):
        return None


class RenamedKeyword:
    """A "harmless tidy-up" that renames ``user`` to ``prompt``.

    Every one of the six downstream consumers calls ``structured(system=..., user=...)``,
    so this breaks all of them at runtime — and ``isinstance`` says it is an ``LLMClient``.
    """

    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        return None  # type: ignore[return-value]


class RetypedReturn:
    """T-2's client, but it hands back a string instead of an instance of ``schema``."""

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> str:
        return "{}"


class ReorderedKeywords:
    """NOT drift: reordering keyword-only parameters is invisible to every caller."""

    async def structured(
        self,
        *,
        user: str,
        system: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        return None  # type: ignore[return-value]


class DriftedDefault:
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 4000,  # DESIGN says 2000
        cache_prefix: bool = True,
    ) -> BaseModel:
        return None  # type: ignore[return-value]


class PositionalArgs:
    async def structured(
        self,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        return None  # type: ignore[return-value]


class SyncConnector:
    kind = "github"

    def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        return []


class BadKindConnector:
    kind = "githbu"  # typo

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        return []


class NoKindConnector:
    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        return []


class WrongArityConnector:
    kind = "github"

    async def search(self, person: PersonRef) -> list[RawDoc]:  # budget dropped
        return []


DRIFTS = [
    (SyncStructured, LLMClient, "async"),
    (RenamedKeyword, LLMClient, "does not match"),
    (RetypedReturn, LLMClient, "does not match"),
    (DriftedDefault, LLMClient, "does not match"),
    (PositionalArgs, LLMClient, "does not match"),
    (SyncConnector, Connector, "async"),
    (BadKindConnector, Connector, "is not one of"),
    (NoKindConnector, Connector, "missing"),
    (WrongArityConnector, Connector, "does not match"),
]


@pytest.mark.parametrize(
    ("candidate", "protocol", "expected"), DRIFTS, ids=[c.__name__ for c, _, _ in DRIFTS]
)
def test_assert_conforms_rejects_drift(candidate, protocol, expected):
    assert not conforms(candidate(), protocol)
    with pytest.raises(TypeError, match=expected):
        assert_conforms(candidate(), protocol)


@pytest.mark.parametrize(
    "candidate", [c for c, _, _ in DRIFTS], ids=[c.__name__ for c, _, _ in DRIFTS]
)
def test_isinstance_would_have_accepted_most_of_these(candidate):
    """Not a redundant test: it measures how little the prescribed check proves.

    Every one of these classes has the right attribute NAMES, which is all
    ``runtime_checkable`` ``isinstance`` inspects — so all but the one with a missing
    attribute sail through the conformance test TASKS.md currently prescribes.
    """
    protocol = LLMClient if hasattr(candidate, "structured") else Connector
    if candidate is NoKindConnector:
        assert not isinstance(candidate(), protocol), "the only drift isinstance catches"
    else:
        assert isinstance(candidate(), protocol)


def test_mismatch_messages_name_the_member_and_the_expectation():
    """A conformance failure must tell a T-1/T-2 agent what to change, not just 'False'."""
    problems = protocol_mismatches(RenamedKeyword(), LLMClient)
    assert len(problems) == 1
    assert "structured" in problems[0]
    assert "prompt: str" in problems[0] and "user: str" in problems[0], problems[0]


def test_reordering_keyword_only_parameters_is_not_drift():
    """Records the one shape the check deliberately allows, so nobody "fixes" it later.

    ``system`` and ``user`` are keyword-only, so their declaration order is unobservable to
    callers and ``inspect.Signature`` equality ignores it. The genuinely dangerous swap —
    a BODY that passes the system prompt as the user prompt — is invisible to any static
    check; ``LLMCall`` records both fields separately so a test can assert on them.
    """
    assert conforms(ReorderedKeywords(), LLMClient)
