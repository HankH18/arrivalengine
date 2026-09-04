"""T-6 acceptance 1 and 4: the three budgets, and the concurrency the fan-out promises."""

from __future__ import annotations

import asyncio
import time

import pytest
from t6_corpus import PERSON, docs_for, script_extraction, script_verdicts

from arrival.contracts import Budget, Dossier, LLMClient, LLMError
from arrival.research import BuildTrace, _BudgetedClient, _interleave, build_dossier
from doubles import ConnectorDouble, LLMDouble, assert_conforms

pytestmark = pytest.mark.ticket("T-6")


class _RudeConnector:
    """A `Connector` that ignores the budget it is handed, the way a real API can.

    `ConnectorDouble` politely returns `docs[:budget]`, so on its own it cannot tell a
    pipeline that enforces the per-source budget apart from one that merely asks nicely.
    """

    def __init__(self, kind, docs):
        self.kind = kind
        self.docs = list(docs)
        self.budgets = []

    async def search(self, person, budget):
        self.budgets.append(budget)
        return list(self.docs)


async def test_each_connector_is_asked_for_docs_per_connector_and_trimmed_anyway():
    """The budget is ours to enforce: a connector handing back more is cut here."""
    docs = docs_for("search", 6)
    polite = ConnectorDouble(kind="search", docs=docs)
    rude = _RudeConnector("github", docs_for("github", 6))
    trace = BuildTrace()
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)

    await build_dossier(
        PERSON,
        [polite, rude],
        llm,
        Budget(docs_per_connector=2, max_docs_total=40),
        trace=trace,
    )

    assert [budget for _person, budget in polite.calls] == [2]
    assert rude.budgets == [2]
    assert len(trace.documents) == 4, "a source that ignored its budget was not trimmed"
    assert trace.docs_by_source == {"search": 2, "github": 2}


async def test_max_docs_total_caps_the_person_and_keeps_the_fan_out_wide():
    """Round-robin, not connector order: a cap must not silently mute the later sources."""
    kinds = ("self_page", "search", "github")
    batches = {kind: docs_for(kind, 5) for kind in kinds}
    connectors = [ConnectorDouble(kind=kind, docs=batches[kind]) for kind in kinds]
    trace = BuildTrace()
    llm = LLMDouble()
    for kind in kinds:
        script_verdicts(llm, batches[kind])
        script_extraction(llm, batches[kind])

    await build_dossier(
        PERSON, connectors, llm, Budget(docs_per_connector=5, max_docs_total=3), trace=trace
    )

    assert len(trace.documents) == 3
    assert sorted({doc.source_kind for doc in trace.documents}) == ["github", "search", "self_page"]


def test_interleave_deduplicates_and_never_exceeds_the_cap():
    shared = docs_for("search", 2)
    other = docs_for("github", 2)

    assert [d.doc_id for d in _interleave([shared, shared], 10)] == [d.doc_id for d in shared]
    assert len(_interleave([shared, other], 3)) == 3
    assert _interleave([shared, other], 0) == []


async def test_max_llm_calls_stops_the_build_and_keeps_what_it_has():
    """Acceptance 4: the cap binds across stages and yields a Dossier, never a raise."""
    docs = docs_for("self_page", 4, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)

    uncapped = await build_dossier(
        PERSON,
        [ConnectorDouble(kind="self_page", docs=docs)],
        llm,
        Budget(docs_per_connector=8, max_docs_total=40, max_llm_calls=80),
    )
    spent = len(llm.calls)
    assert spent > 2, f"the uncapped run only spent {spent} calls; a cap of 2 measures nothing"
    assert uncapped.facts, "the uncapped control produced nothing to compare against"

    capped_llm = LLMDouble()
    script_verdicts(capped_llm, docs)
    script_extraction(capped_llm, docs)
    trace = BuildTrace()
    capped = await build_dossier(
        PERSON,
        [ConnectorDouble(kind="self_page", docs=docs)],
        capped_llm,
        Budget(docs_per_connector=8, max_docs_total=40, max_llm_calls=2),
        trace=trace,
    )

    assert isinstance(capped, Dossier)
    assert len(capped_llm.calls) <= 2, [c.schema_name for c in capped_llm.calls]
    assert trace.llm_calls <= 2
    assert trace.llm_refused > 0, "the cap was never actually reached"


async def test_a_zero_call_budget_still_produces_a_dossier():
    docs = docs_for("search", 2)
    llm = LLMDouble()
    script_verdicts(llm, docs)

    dossier = await build_dossier(
        PERSON, [ConnectorDouble(kind="search", docs=docs)], llm, Budget(max_llm_calls=0)
    )

    assert llm.calls == []
    assert dossier.resolution.status == "unresolved"
    assert dossier.facts == []


class _TimedConnector:
    """Records when its `search` was entered and left, so overlap can be PROVEN.

    A wall-clock threshold was the obvious way to write this and it is the wrong one: the
    first `asyncio.sleep` on a fresh event loop under pytest costs ~180 ms of one-off
    warm-up here, which is the same order as the delays being measured, so a correct
    concurrent fan-out reads as serial on its first run and passes on its second. Interval
    overlap is the property actually being claimed, and it is machine-speed independent.
    """

    def __init__(self, kind, delay):
        self.kind = kind
        self.delay = delay
        self.started = 0.0
        self.finished = 0.0

    async def search(self, person, budget):
        self.started = time.perf_counter()
        await asyncio.sleep(self.delay)
        self.finished = time.perf_counter()
        return []


async def test_the_fan_out_is_concurrent_not_a_for_loop():
    """Every source must be in flight at the same moment, not one after another."""
    connectors = [
        _TimedConnector(kind, 0.05) for kind in ("self_page", "search", "github", "hn")
    ]

    await build_dossier(PERSON, connectors, LLMDouble(), Budget())

    last_start = max(c.started for c in connectors)
    first_finish = min(c.finished for c in connectors)
    assert last_start < first_finish, (
        "the last source started only after the first had already finished, which is a "
        "serial loop wearing an async signature"
    )


class _Counting:
    """A minimal LLMClient that answers nothing; only its call count is interesting."""

    def __init__(self) -> None:
        self.calls = 0

    async def structured(
        self, *, system, user, schema, max_tokens=2000, cache_prefix=True
    ):  # pragma: no cover - exercised below
        self.calls += 1
        raise LLMError("not scripted")


async def test_budgeted_client_conforms_refuses_past_the_cap_and_never_calls_through():
    inner = _Counting()
    metered = _BudgetedClient(inner, 2)
    assert_conforms(metered, LLMClient)

    for _ in range(2):
        with pytest.raises(LLMError):
            await metered.structured(system="s", user="u", schema=Budget)
    assert inner.calls == 2
    assert metered.remaining == 0

    with pytest.raises(LLMError):
        await metered.structured(system="s", user="u", schema=Budget)
    assert inner.calls == 2, "a refused call must not reach the real client"
    assert metered.used == 2
    assert metered.refused == 1


def test_interleave_with_no_sources_at_all_is_empty_not_an_error():
    assert _interleave([], 40) == []
    assert _interleave([[]], 40) == []
