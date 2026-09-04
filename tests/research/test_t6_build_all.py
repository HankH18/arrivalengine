"""T-6 acceptance 3: what a whole-roster build leaves on disk, and what it reports."""

from __future__ import annotations

import pytest
from t6_corpus import PERSON, docs_for, script_extraction, script_verdicts

from arrival.contracts import Budget, BuildReport, Dossier, RawDoc
from arrival.research import (
    REPORT_COLUMNS,
    BuildTrace,
    build_all,
    build_dossier,
    format_report,
    report_row,
)
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

ROSTER = """\
people:
  - name: Marisol Trevino
    details: [platform lead Quarrystone Labs, Austin]
  - name: Anselm Kettleby
    details: [co-founder Quarrystone Labs, Austin]
"""


def _roster(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(ROSTER, encoding="utf-8")
    return path


def _fixtures(*, raising_kind: str | None = None):
    docs = docs_for("self_page", 2, private_index=0)
    connectors = [ConnectorDouble(kind="self_page", docs=docs)]
    if raising_kind:
        connectors.append(
            ConnectorDouble(kind=raising_kind, raises=RuntimeError("source down"), delay=0.01)
        )
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    return docs, connectors, llm


async def test_writes_one_dossier_per_person_and_every_accepted_doc_beside_it(tmp_path):
    docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path), out_dir, connectors=connectors, llm=llm, budget=Budget()
    )

    assert isinstance(report, BuildReport)
    assert {row["person_id"] for row in report.people} == {"marisol-trevino", "anselm-kettleby"}
    accepted = set()
    for person_id in ("marisol-trevino", "anselm-kettleby"):
        path = out_dir / f"{person_id}.json"
        dossier = Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        assert dossier.person.person_id == person_id
        accepted.update(dossier.resolution.accepted_doc_ids)

    assert accepted, "nothing was accepted, so the document-commit assertion is vacuous"
    docs_dir = out_dir.parent / "docs"
    for doc_id in accepted:
        written = RawDoc.model_validate_json((docs_dir / f"{doc_id}.json").read_text("utf-8"))
        assert written.doc_id == doc_id
        assert written.text == next(d.text for d in docs if d.doc_id == doc_id)


async def test_a_rejected_document_is_not_committed(tmp_path):
    """Only CITED documents are worth committing; the rest are noise in the repo."""
    docs = docs_for("search", 3)
    rejected = docs[2]
    llm = LLMDouble()
    script_verdicts(llm, docs[:2])
    script_verdicts(llm, [rejected], match="no", confidence=0.4)
    script_extraction(llm, docs)
    out_dir = tmp_path / "data" / "dossiers"

    await build_all(
        _roster(tmp_path),
        out_dir,
        connectors=[ConnectorDouble(kind="search", docs=docs)],
        llm=llm,
        budget=Budget(),
    )

    docs_dir = out_dir.parent / "docs"
    assert (docs_dir / f"{docs[0].doc_id}.json").exists()
    assert not (docs_dir / f"{rejected.doc_id}.json").exists()


async def test_an_existing_dossier_is_skipped_until_forced(tmp_path):
    _docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"
    roster = _roster(tmp_path)

    first = await build_all(roster, out_dir, connectors=connectors, llm=llm, budget=Budget())
    assert llm.calls, "the first build did no work, so 'skipped' cannot be distinguished"
    on_disk = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    assert all(row["skipped"] is False for row in first.people)

    _docs, connectors, skip_llm = _fixtures()
    skipped = await build_all(
        roster, out_dir, connectors=connectors, llm=skip_llm, budget=Budget()
    )
    assert skip_llm.calls == [], "an existing dossier was rebuilt without force"
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == on_disk
    assert all(row["skipped"] is True for row in skipped.people)
    # A skipped row still has to describe the person, or the report lies by omission.
    for row in skipped.people:
        for key in REPORT_COLUMNS:
            assert key in row
        assert row["status"] in ("resolved", "unresolved")

    _docs, connectors, force_llm = _fixtures()
    await build_all(
        roster, out_dir, connectors=connectors, llm=force_llm, budget=Budget(), force=True
    )
    assert force_llm.calls, "force did not rebuild an existing dossier"


async def test_a_corrupt_dossier_is_rebuilt_rather_than_reported_as_good(tmp_path):
    _docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"
    out_dir.mkdir(parents=True)
    (out_dir / "marisol-trevino.json").write_text("{ this is not a dossier", encoding="utf-8")

    report = await build_all(
        _roster(tmp_path), out_dir, connectors=connectors, llm=llm, budget=Budget()
    )

    row = next(r for r in report.people if r["person_id"] == "marisol-trevino")
    assert row["skipped"] is False
    Dossier.model_validate_json((out_dir / "marisol-trevino.json").read_text("utf-8"))


async def test_only_restricts_the_build_to_one_person(tmp_path):
    _docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path),
        out_dir,
        connectors=connectors,
        llm=llm,
        budget=Budget(),
        only="anselm-kettleby",
    )

    assert [row["person_id"] for row in report.people] == ["anselm-kettleby"]
    assert (out_dir / "anselm-kettleby.json").exists()
    assert not (out_dir / "marisol-trevino.json").exists()


async def test_only_matching_nobody_builds_nothing_without_raising(tmp_path):
    _docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path),
        out_dir,
        connectors=connectors,
        llm=llm,
        budget=Budget(),
        only="nobody-at-all",
    )

    assert report.people == []
    assert list(out_dir.iterdir()) == []


async def test_a_zero_result_source_is_named_per_person(tmp_path):
    _docs, connectors, llm = _fixtures(raising_kind="propublica")
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path), out_dir, connectors=connectors, llm=llm, budget=Budget()
    )

    assert len(report.people) == 2
    for row in report.people:
        assert row["zero_result_sources"] == ["propublica"]


async def test_report_rows_are_typed_counts_that_describe_their_dossier(tmp_path):
    _docs, connectors, llm = _fixtures()
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path), out_dir, connectors=connectors, llm=llm, budget=Budget()
    )

    assert report.finished_at >= report.started_at
    for row in report.people:
        dossier = Dossier.model_validate_json(
            (out_dir / f"{row['person_id']}.json").read_text("utf-8")
        )
        assert row["facts_kept"] == sum(1 for f in dossier.facts if not f.excluded)
        assert row["facts_excluded"] == sum(1 for f in dossier.facts if f.excluded)
        assert row["facts_kept"] + row["facts_excluded"] == len(dossier.facts)
        assert row["hubs"] == len(dossier.hubs)
        assert 0.0 <= row["confidence"] <= 1.0
        for key in ("facts_kept", "facts_excluded", "hubs"):
            assert isinstance(row[key], int) and not isinstance(row[key], bool)


class _BogusKindConnector:
    """A connector whose `kind` is not a `SourceKind`. `ConnectorDouble` refuses to be one.

    `BuildReport.people` is `list[dict]`; pydantic validates nothing inside it, so this is
    the only thing standing between a typo and a report T-9 commits.
    """

    kind = "linkedn"  # a plausible typo, and not a SourceKind

    async def search(self, person, budget):
        return []


async def test_a_source_kind_typo_never_reaches_the_report():
    trace = BuildTrace()
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)

    dossier = await build_dossier(
        PERSON,
        [ConnectorDouble(kind="self_page", docs=docs), _BogusKindConnector()],
        llm,
        Budget(),
        trace=trace,
    )

    assert trace.zero_result_sources == []
    assert trace.docs_by_source["linkedn"] == 0
    assert report_row(dossier, trace)["zero_result_sources"] == []


def test_format_report_renders_every_row_and_a_total():
    report = BuildReport(
        people=[
            {
                "person_id": "marisol-trevino",
                "status": "resolved",
                "confidence": 0.91,
                "facts_kept": 3,
                "facts_excluded": 1,
                "hubs": 2,
                "zero_result_sources": ["propublica", "edgar"],
            },
            {
                "person_id": "anselm-kettleby",
                "status": "unresolved",
                "confidence": 0.0,
                "facts_kept": 0,
                "facts_excluded": 0,
                "hubs": 0,
                "zero_result_sources": [],
                "skipped": True,
            },
        ],
        started_at="2026-02-20T14:00:00Z",
        finished_at="2026-02-20T14:00:09Z",
    )

    table = format_report(report)

    assert "marisol-trevino" in table
    assert "anselm-kettleby (skipped)" in table
    assert "propublica, edgar" in table
    assert "0.91" in table
    assert "1 resolved, 1 unresolved, 1 skipped" in table


@pytest.mark.parametrize("missing", ["absent.yaml", "nested/absent.yaml"])
async def test_a_missing_roster_raises_rosererror_not_oserror(tmp_path, missing):
    from arrival.research import RosterError

    with pytest.raises(RosterError):
        await build_all(tmp_path / missing, tmp_path / "out", connectors=[], llm=LLMDouble())


def test_format_report_survives_a_report_with_nobody_in_it():
    """`--only` matching nobody still prints a table rather than dividing by zero."""
    empty = BuildReport(
        people=[], started_at="2026-02-20T14:00:00Z", finished_at="2026-02-20T14:00:00Z"
    )

    table = format_report(empty)

    assert "zero-result sources" in table
    assert "0 person/people: 0 resolved, 0 unresolved, 0 skipped" in table
