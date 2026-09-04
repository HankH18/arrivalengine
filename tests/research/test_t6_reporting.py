"""The report and the CLI's operator-facing surface.

Everything here exists because `BuildReport.people` is `list[dict]` that pydantic does not
validate, so the report is the one artefact in this pipeline with no schema behind it.
"""

from __future__ import annotations

import pytest
from t6_corpus import PERSON, docs_for, script_extraction, script_verdicts

from arrival.__main__ import main
from arrival.contracts import Budget, BuildReport
from arrival.research import (
    REPORT_COLUMNS,
    BuildError,
    BuildTrace,
    build_all,
    build_dossier,
    format_report,
    report_row,
)
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

ROSTER = "people:\n  - name: Marisol Trevino\n    details: [Austin]\n"


def _roster(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(ROSTER, encoding="utf-8")
    return path


def _working():
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    return docs, llm


async def test_a_source_that_exploded_is_told_apart_from_one_that_was_merely_empty():
    """DESIGN Decision 8's whole point. `connector_errors` was computed and read by nobody."""
    docs, llm = _working()
    trace = BuildTrace()

    dossier = await build_dossier(
        PERSON,
        [
            ConnectorDouble(kind="self_page", docs=docs),
            ConnectorDouble(kind="github", raises=RuntimeError("502")),
            ConnectorDouble(kind="hn", docs=[]),
        ],
        llm,
        Budget(),
        trace=trace,
    )
    row = report_row(dossier, trace)

    assert sorted(row["zero_result_sources"]) == ["github", "hn"]
    assert row["failed_sources"] == ["github"], (
        "a source that raised is indistinguishable from one that had nothing: "
        f"{row['failed_sources']}"
    )
    table = format_report(
        BuildReport(
            people=[row],
            started_at="2026-02-20T14:00:00Z",
            finished_at="2026-02-20T14:00:01Z",
        )
    )
    assert "failed sources" in table
    assert "github" in table


async def test_an_out_of_range_confidence_is_reported_not_quietly_clamped(caplog):
    """Clamping alone hides the resolver bug the coercion exists to catch."""
    docs, llm = _working()
    dossier = await build_dossier(
        PERSON, [ConnectorDouble(kind="self_page", docs=docs)], llm, Budget()
    )
    broken = dossier.model_copy(
        update={"resolution": dossier.resolution.model_copy(update={"confidence": 1.7})}
    )

    with caplog.at_level("WARNING"):
        row = report_row(broken)

    assert row["confidence"] == 1.0
    assert any("out-of-range" in record.message for record in caplog.records), (
        "the report clamped 1.7 to 1.00 and said nothing"
    )


def test_the_table_columns_are_derived_from_the_documented_row_keys():
    """One source of truth: a new contract key must not need a second edit to be printed."""
    table = format_report(
        BuildReport(
            people=[], started_at="2026-02-20T14:00:00Z", finished_at="2026-02-20T14:00:00Z"
        )
    )
    header = table.splitlines()[0]

    assert "person" in header and "zero-result sources" in header
    assert len(REPORT_COLUMNS) == 7


def test_a_row_carrying_none_where_a_number_belongs_does_not_kill_the_report():
    """`.get(key, default)` guards a MISSING key, not a key present with `None`, and
    `BuildReport.people` is a `list[dict]` pydantic never looks inside."""
    report = BuildReport(
        people=[{"person_id": "marisol-trevino", "status": None, "confidence": None,
                 "facts_kept": None, "facts_excluded": None, "hubs": None,
                 "zero_result_sources": None}],
        started_at="2026-02-20T14:00:00Z",
        finished_at="2026-02-20T14:00:01Z",
    )

    table = format_report(report)

    assert "marisol-trevino" in table


async def test_an_out_dir_that_collides_with_its_own_docs_dir_is_refused(tmp_path):
    """DESIGN pins the docs dir at `out_dir/../docs`, so `--out docs` makes them ONE
    directory and `{person_id}.json` and `{doc_id}.json` co-mingle in it."""
    docs, llm = _working()

    with pytest.raises(BuildError):
        await build_all(
            _roster(tmp_path),
            tmp_path / "docs",
            connectors=[ConnectorDouble(kind="self_page", docs=docs)],
            llm=llm,
            budget=Budget(),
        )


def test_the_cli_refuses_a_colliding_out_dir_with_exit_two(tmp_path, capsys):
    docs, llm = _working()

    rc = main(
        ["build", "--roster", str(_roster(tmp_path)), "--out", str(tmp_path / "docs")],
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm,
    )

    assert rc == 2
    assert "same directory" in capsys.readouterr().err


def test_the_cli_can_set_every_budget_number(tmp_path):
    """Without a lever the only way to change what a build costs is to edit the source."""
    docs = docs_for("self_page", 6)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    connector = ConnectorDouble(kind="self_page", docs=docs)

    rc = main(
        [
            "build",
            "--roster", str(_roster(tmp_path)),
            "--out", str(tmp_path / "out"),
            "--docs-per-connector", "2",
            "--max-docs", "2",
            "--max-llm-calls", "1",
        ],
        connectors=[connector],
        llm=llm,
    )

    assert rc == 0
    assert [budget for _person, budget in connector.calls] == [2]
    assert len(llm.calls) == 1, [c.schema_name for c in llm.calls]


def test_a_nonsense_budget_is_a_usage_error_not_a_traceback(tmp_path):
    docs, llm = _working()

    rc = main(
        [
            "build",
            "--roster", str(_roster(tmp_path)),
            "--out", str(tmp_path / "out"),
            "--max-docs", "not-a-number",
        ],
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm,
    )

    assert rc == 2


async def test_a_connector_returning_junk_alongside_documents_says_so(caplog):
    """Invisible partial loss otherwise: a regressed parser looks like a quiet source."""

    class _JunkConnector:
        kind = "search"

        async def search(self, person, budget):
            return [*docs_for("search", 1), {"not": "a RawDoc"}, None]

    trace = BuildTrace()
    with caplog.at_level("WARNING"):
        await build_dossier(PERSON, [_JunkConnector()], LLMDouble(), Budget(), trace=trace)

    assert len(trace.documents) == 1
    assert any("not RawDocs" in record.message for record in caplog.records)
