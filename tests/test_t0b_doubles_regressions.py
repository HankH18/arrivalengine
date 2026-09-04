"""T-0b regressions for three defects in `tests/doubles.py` (D1, D4, D5).

Every test in this module was written BEFORE its repair and observed failing against the
shipped T-0 scaffold. They are the evidence the repairs work, and the tripwire that stops
the same defect coming back the next time somebody "tidies up" the doubles.

D1 — `assert_conforms` rejected a CORRECT implementation whenever the candidate's
     annotations could not be evaluated at runtime. `_resolved_signature` returned an
     `inspect.Signature` when `eval_str=True` succeeded and a plain `str` when it did not,
     and the two were then compared with `!=`. The protocol side always resolves, so the
     comparison was `Signature != str` — always true. The pattern that triggers it,
     `from __future__ import annotations` plus `if TYPE_CHECKING: from arrival.contracts
     import ...`, is the ordinary way T-1 and T-2 will write their modules.

D4 — `ConnectorDouble` checked `raises` BEFORE awaiting `delay`, so a dying connector
     always died instantly and T-6 could not tell "we handled a timeout" from "we never
     waited".

D5 — `LLMCall`'s 2nd and 3rd fields were `user: str` then `system: str`, both `str` and
     both positional, so the swapped construction stored them reversed in silence.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from arrival.contracts import Connector, LLMClient
from doubles import ConnectorDouble, LLMCall, LLMDouble, assert_conforms, conforms

if TYPE_CHECKING:  # the idiom under test: these names exist only for a type checker
    from pydantic import BaseModel

    from arrival.contracts import PersonRef, RawDoc, SourceKind

pytestmark = pytest.mark.ticket("T-0")


# --------------------------------------------------------------------------
# D1 — annotations that only resolve under TYPE_CHECKING
# --------------------------------------------------------------------------
#
# NOTE: this module must NOT import PersonRef / RawDoc / SourceKind / BaseModel at
# runtime. Their absence from the module globals is the whole point: it is what makes
# `inspect.signature(..., eval_str=True)` fail on the classes below, exactly as it fails
# on a T-1 connector written the ordinary way.


class ForwardRefConnector:
    """A CORRECT `Connector`, written the way T-1 will write one.

    Same signature as `contracts.Connector.search` in every observable respect; the only
    difference is that its annotations are unevaluated strings at runtime.
    """

    kind: SourceKind = "github"

    async def search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        return []


class ForwardRefDriftedConnector:
    """The same idiom, but `budget` has been renamed — real drift, must still be caught."""

    kind: SourceKind = "github"

    async def search(self, person: PersonRef, limit: int) -> list[RawDoc]:
        return []


class ForwardRefLLM:
    """A CORRECT `LLMClient` whose annotations also only resolve under TYPE_CHECKING."""

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ) -> BaseModel:
        return None  # type: ignore[return-value]


class ForwardRefDriftedLLM:
    """The same idiom with `user` renamed to `prompt` — the drift T-0 built the check for."""

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


def test_the_probe_classes_really_do_have_unresolvable_annotations():
    """Guards the guard: if these ever resolve, the four tests below prove nothing."""
    import inspect

    for func in (ForwardRefConnector.search, ForwardRefLLM.structured):
        with pytest.raises(NameError):
            inspect.signature(func, eval_str=True)


def test_assert_conforms_accepts_a_connector_whose_annotations_need_type_checking():
    """D1: a correct connector written with `if TYPE_CHECKING:` imports must conform."""
    assert conforms(ForwardRefConnector(), Connector), (
        "a correct Connector was rejected because its annotations are strings at runtime"
    )
    assert_conforms(ForwardRefConnector(), Connector)
    assert_conforms(ForwardRefConnector, Connector)  # a class, the way T-1 may call it


def test_assert_conforms_accepts_an_llm_client_whose_annotations_need_type_checking():
    assert conforms(ForwardRefLLM(), LLMClient)
    assert_conforms(ForwardRefLLM(), LLMClient)


def test_real_drift_is_still_rejected_under_the_same_idiom():
    """The other half of D1: the fix must not turn the check into a rubber stamp."""
    assert not conforms(ForwardRefDriftedConnector(), Connector)
    with pytest.raises(TypeError, match="does not match"):
        assert_conforms(ForwardRefDriftedConnector(), Connector)

    assert not conforms(ForwardRefDriftedLLM(), LLMClient)
    with pytest.raises(TypeError, match="does not match"):
        assert_conforms(ForwardRefDriftedLLM(), LLMClient)


def test_the_mismatch_message_shows_both_sides_in_the_same_form():
    """The D1 message was `Signature` vs `str`, so the two halves looked identical.

    A mismatch report that a reader cannot act on costs more time than no report at all:
    the observed message named `person: 'PersonRef'` on one side and
    `person: arrival.contracts.PersonRef` on the other for a connector that was correct.
    """
    from doubles import protocol_mismatches

    problems = protocol_mismatches(ForwardRefDriftedConnector(), Connector)
    assert len(problems) == 1, problems
    assert "limit" in problems[0] and "budget" in problems[0], problems[0]
    # Both halves must be quoted the same way, so the difference is the only difference.
    assert problems[0].count("'PersonRef'") in (0, 2), problems[0]


# --------------------------------------------------------------------------
# D4 — a connector that dies SLOWLY
# --------------------------------------------------------------------------


async def test_connector_double_waits_before_it_raises():
    """T-6's realistic source death is a timeout: it hangs, and then it fails.

    With `raises` checked before `delay` was awaited, a dying connector returned in
    ~0.0003 s and T-6 could not distinguish "we handled the failure" from "we never
    waited" — which is the whole content of its degradation requirement.
    """
    connector = ConnectorDouble(kind="search", raises=TimeoutError("source hung"), delay=0.2)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="source hung"):
        await connector.search(_PERSON, 5)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.2, f"the dying connector raised after {elapsed:.4f}s, without waiting"


async def test_a_connector_that_raises_without_a_delay_is_still_instant():
    """The mirror image, so the fix cannot be "always sleep"."""
    connector = ConnectorDouble(kind="search", raises=RuntimeError("boom"))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        await connector.search(_PERSON, 5)
    assert time.monotonic() - started < 0.1


# --------------------------------------------------------------------------
# D5 — the silent system/user swap
# --------------------------------------------------------------------------


def test_llm_call_keeps_schema_name_and_user_positional():
    """The documented contract: `schema_name` first, `user` second, both positional."""
    call = LLMCall("ExtractionResult", "…prompt text…")
    assert (call.schema_name, call.user) == ("ExtractionResult", "…prompt text…")
    assert (call.system, call.max_tokens, call.cache_prefix) == ("", 2000, True)

    keyed = LLMCall("Verdict", "u", system="you are…", max_tokens=512, cache_prefix=False)
    assert (keyed.system, keyed.max_tokens, keyed.cache_prefix) == ("you are…", 512, False)


def test_llm_call_refuses_the_swapped_system_user_construction():
    """D5: `LLMCall("Verdict", system_text, user_text)` used to store them REVERSED.

    Both fields are `str`, the dataclass is frozen and positional, and `calls_for()`
    output still looked plausible — so a downstream assertion compared the wrong two
    strings and passed. `system` is keyword-only now, so the third positional argument
    lands on `max_tokens`, where the type guard catches it.
    """
    with pytest.raises(TypeError):
        LLMCall("Verdict", "you are a careful analyst", "Is this the same person?")


def test_llm_call_type_guard_names_the_field_it_rejected():
    with pytest.raises(TypeError, match="max_tokens"):
        LLMCall("Verdict", "user prompt", "system prompt")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="cache_prefix"):
        LLMCall("Verdict", "user prompt", 512, "yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="user"):
        LLMCall("Verdict", 3)  # type: ignore[arg-type]


async def test_a_recorded_call_still_equals_the_documented_construction():
    """The double records by keyword; the recorded value must still be constructible."""
    from pydantic import BaseModel as _BaseModel

    class Shape(_BaseModel):
        value: str

    llm = LLMDouble().when("Shape", "", Shape(value="v"))
    await llm.structured(system="sys", user="usr", schema=Shape, max_tokens=512, cache_prefix=False)
    assert llm.calls == [LLMCall("Shape", "usr", system="sys", max_tokens=512, cache_prefix=False)]


# Built lazily so the module keeps its runtime globals free of PersonRef (see D1 above).
def _person():
    from arrival.contracts import PersonRef as _PersonRef

    return _PersonRef(person_id="teodoro-vance", name="Teodoro Vance", details=["CTO", "Austin"])


_PERSON = _person()
