"""What the build does when the world is broken — the failures the doubles hide.

Every other module here injects a working `LLMDouble`, which is exactly the condition
under which a keyless production build looks identical to a successful one: `resolve`
turns a failed call into `unsure`, `unsure` turns into `unresolved`, and `unresolved`
turns into a schema-valid empty dossier. These tests drive the failure instead.
"""

from __future__ import annotations

import pytest
from t6_corpus import docs_for, script_extraction, script_verdicts

from arrival.__main__ import main
from arrival.contracts import Budget, Dossier, LLMError
from arrival.research import build_all
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

ROSTER = """\
people:
  - name: Marisol Trevino
    details: [platform lead Quarrystone Labs, Austin]
  - name: Anselm Kettleby
    details: [co-founder Quarrystone Labs, Austin]
"""


class _DeadClient:
    """Every call fails, the way an unset key, a 401 or a dead network really fails.

    `AnthropicClient.structured` wraps every SDK, transport and API error in `LLMError`,
    so this one type is the whole failure surface of the production client.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def structured(self, *, system, user, schema, max_tokens=2000, cache_prefix=True):
        self.calls += 1
        raise LLMError("ANTHROPIC_API_KEY is not set")


def _roster(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(ROSTER, encoding="utf-8")
    return path


async def test_a_build_whose_every_model_call_fails_writes_nothing_and_says_so(tmp_path):
    """An unreachable model and a person with nothing to say produce the SAME empty
    dossier. Committing one makes the outage permanent — the next run skips the file."""
    docs = docs_for("self_page", 2)
    llm = _DeadClient()
    out_dir = tmp_path / "data" / "dossiers"

    report = await build_all(
        _roster(tmp_path),
        out_dir,
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm,
        budget=Budget(),
    )

    assert llm.calls, "the control is vacuous: no model call was even attempted"
    assert list(out_dir.iterdir()) == [], (
        f"an empty dossier was committed for an unreachable model: "
        f"{[p.name for p in out_dir.iterdir()]}"
    )
    assert len(report.people) == 2
    for row in report.people:
        assert row["error"], f"a failed build is reported as an ordinary result: {row}"
        assert row["status"] == "unresolved"
        assert row["facts_kept"] == 0


def test_the_cli_returns_non_zero_when_nobody_could_be_built(tmp_path, capsys):
    rc = main(
        ["build", "--roster", str(_roster(tmp_path)), "--out", str(tmp_path / "out")],
        connectors=[ConnectorDouble(kind="self_page", docs=docs_for("self_page", 2))],
        llm=_DeadClient(),
    )

    assert rc == 1, "a build that wrote nothing at all reported success"
    assert "model call" in capsys.readouterr().err


def test_the_cli_returns_two_when_only_matches_nobody(tmp_path, capsys):
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)

    rc = main(
        [
            "build",
            "--roster", str(_roster(tmp_path)),
            "--out", str(tmp_path / "out"),
            "--only", "marisol-trevin",  # one character short
        ],
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm,
    )

    assert rc == 2, "a typo'd --only reported success having built nobody"
    assert "--only" in capsys.readouterr().err


async def test_one_person_failing_does_not_cost_the_rest_of_the_roster(tmp_path):
    """DESIGN Decision 8 at the person level, not just the connector level."""
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    out_dir = tmp_path / "data" / "dossiers"
    # Make the FIRST person's dossier path un-writable by putting a directory there.
    out_dir.mkdir(parents=True)
    (out_dir / "marisol-trevino.json").mkdir()

    report = await build_all(
        _roster(tmp_path),
        out_dir,
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm,
        budget=Budget(),
    )

    rows = {row["person_id"]: row for row in report.people}
    assert rows["marisol-trevino"]["error"], "the unwritable person was reported as built"
    assert not rows["anselm-kettleby"].get("error"), (
        "one person's disk error aborted the roster: "
        f"{rows['anselm-kettleby']}"
    )
    Dossier.model_validate_json((out_dir / "anselm-kettleby.json").read_text("utf-8"))


async def test_a_dossier_is_rebuilt_when_the_documents_it_cites_are_gone(tmp_path):
    """T-9 validates every displayed quote against `docs/{doc_id}.json`. A dossier whose
    documents were deleted looks complete and cites nothing, and skipping is permanent."""
    docs = docs_for("self_page", 2, private_index=0)
    out_dir = tmp_path / "data" / "dossiers"
    roster = _roster(tmp_path)

    # One person: the double hands every person the SAME documents, so a second person
    # would rebuild the files this test just deleted and mask the thing under test.
    first = LLMDouble()
    script_verdicts(first, docs)
    script_extraction(first, docs)
    await build_all(
        roster, out_dir,
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=first, budget=Budget(), only="marisol-trevino",
    )
    docs_dir = out_dir.parent / "docs"
    committed = sorted(docs_dir.iterdir())
    assert committed, "nothing was committed, so 'the documents are gone' cannot be staged"
    for path in committed:
        path.unlink()

    second = LLMDouble()
    script_verdicts(second, docs)
    script_extraction(second, docs)
    report = await build_all(
        roster, out_dir,
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=second, budget=Budget(), only="marisol-trevino",
    )

    assert second.calls, "a dossier citing deleted documents was skipped as if complete"
    assert all(row["skipped"] is False for row in report.people)
    assert sorted(p.name for p in docs_dir.iterdir()) == sorted(p.name for p in committed)


async def test_the_documents_land_before_the_dossier_that_cites_them(tmp_path):
    """A dossier on disk is what makes a person 'already built', so it must be last."""
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    out_dir = tmp_path / "data" / "dossiers"

    # One person, because the double gives every person the same documents and the second
    # person's run would rewrite the first person's files after their dossier had landed.
    await build_all(
        _roster(tmp_path), out_dir,
        connectors=[ConnectorDouble(kind="self_page", docs=docs)],
        llm=llm, budget=Budget(), only="marisol-trevino",
    )

    docs_dir = out_dir.parent / "docs"
    dossier = Dossier.model_validate_json(
        (out_dir / "marisol-trevino.json").read_text("utf-8")
    )
    assert dossier.resolution.accepted_doc_ids
    for doc_id in dossier.resolution.accepted_doc_ids:
        committed = docs_dir / f"{doc_id}.json"
        assert committed.exists()
        assert committed.stat().st_mtime_ns <= (out_dir / "marisol-trevino.json").stat().st_mtime_ns

    # And nothing temporary was left behind by the atomic write.
    for directory in (out_dir, docs_dir):
        assert [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == []
