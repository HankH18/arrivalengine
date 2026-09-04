"""Shared plumbing for T-4's own taste tests: read the corpus, build Facts, script a double.

Not a test module (no ``test_`` prefix), so pytest does not collect it. ``tests/taste/`` is
inserted on ``sys.path`` by pytest's ``prepend`` import mode when it imports the first test
module in this directory, which is how ``from t4_corpus import ...`` resolves.

WHY A CATCH-ALL RULE RATHER THAN ONE RULE PER FACT. :class:`doubles.LLMDouble` matches a
rule on ``(schema name, substring of the user prompt)`` and the first match wins, returning
ONE response for the whole call. ``apply_taste`` batches the unsure facts, so a per-fact
rule would answer a twenty-fact prompt with a single fact's ruling and every other fact in
that batch would fail closed — a green-looking corpus test that actually measured the batch
size. The rule registered here is keyed on the empty substring and carries a ruling for
EVERY case, so the corpus test is independent of how many calls the implementation makes.
Staging is then measured separately, on the recorded prompts, in ``test_t4_cases.py``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from arrival.contracts import Fact, Provenance
from arrival.taste import TasteRuling, TasteRulings
from doubles import LLMDouble

__all__ = [
    "CASES",
    "FIXTURE_PATH",
    "SIX_CATEGORIES",
    "expected_reason",
    "expected_verdict",
    "fact_for",
    "facts_for",
    "load_cases",
    "scripted_double",
]

#: T-4's own, owner-facing corpus. The GRADED corpus is the orchestrator's frozen copy;
#: this one is the human-approval artifact and the input to ``test_taste_cases``.
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "taste_cases.yaml"

#: The six R11 categories, spelled as ``contracts.ExclusionReason`` literals.
SIX_CATEGORIES: tuple[str, ...] = (
    "home_or_property",
    "family",
    "health",
    "legal",
    "wealth",
    "political",
)

#: A literal timestamp. SPEC C7 and determinism both forbid a wall clock in a fixture.
_RETRIEVED_AT = datetime.fromisoformat("2026-03-14T09:15:00+00:00")


def load_cases() -> list[dict[str, Any]]:
    """Every case in T-4's corpus, in file order."""
    doc = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = doc["cases"]
    # Not a graded assertion — a guard. An empty corpus would make every test below
    # vacuously green, which is the exact failure mode these tests exist to prevent.
    assert isinstance(cases, list) and cases, "tests/fixtures/taste_cases.yaml has no cases"
    return cases


#: Read once. The corpus is static input, not state.
CASES: list[dict[str, Any]] = load_cases()


def expected_reason(case: dict[str, Any]) -> str | None:
    """The ``ExclusionReason`` this case must end up carrying, or ``None`` on a keep."""
    if case["expect"] != "exclude":
        return None
    return "low_confidence" if case.get("fail_closed") else case["reason"]


def expected_verdict(case: dict[str, Any]) -> str:
    """The taste verdict the corpus says this sentence deserves."""
    return "keep" if case["expect"] == "keep" else case["reason"]


def fact_for(case: dict[str, Any], *, source_kind: str = "self_page", confidence: float = 0.9):
    """One ``Fact`` carrying the case's sentence. Category is display metadata, not taste."""
    return Fact(
        fact_id=case["id"],
        text=case["text"],
        category="hook",
        provenance=Provenance(
            doc_id=f"doc-{case['id']}",
            url=f"https://example.invalid/{case['id']}",
            source_kind=source_kind,
            quote=case["text"],
            published_at=None,
            retrieved_at=_RETRIEVED_AT,
            confidence=confidence,
        ),
    )


def facts_for(cases: list[dict[str, Any]]) -> list[Fact]:
    return [fact_for(case) for case in cases]


def script_verdict(case: dict[str, Any]) -> str:
    """What the double answers for this case.

    ``rule_layer: llm`` cases are scripted with the corpus' own ``llm_returns`` — that is
    the field's whole purpose. ``deterministic`` cases are scripted with their EXPECTED
    verdict, so that an implementation which wrongly routes a deterministic case to the
    classifier fails the STAGING test rather than corrupting the OUTCOME test. The two
    properties stay orthogonal and each failure names its own defect.
    """
    if case["rule_layer"] == "llm":
        return case["llm_returns"]
    return expected_verdict(case)


def scripted_double(
    cases: list[dict[str, Any]], *, override: dict[str, str] | None = None
) -> LLMDouble:
    """An ``LLMDouble`` that answers any classifier call with a ruling for every case.

    ``override`` replaces the scripted verdict for the named fact ids — that is how the
    fail-closed and hostile-classifier tests script an answer the corpus does not contain.
    """
    verdicts = {case["id"]: script_verdict(case) for case in cases}
    verdicts.update(override or {})
    rulings = TasteRulings(
        rulings=[TasteRuling(fact_id=fid, verdict=v) for fid, v in verdicts.items()]
    )
    llm = LLMDouble()
    llm.when(TasteRulings.__name__, "", rulings)
    return llm
