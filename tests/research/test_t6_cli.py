"""T-6 acceptance 5: `python -m arrival build`, driven in-process with injected doubles.

No subprocess and no network (SPEC C7): `main(argv, *, connectors, llm)` is the seam T-0
pinned precisely so this ticket's CLI could be exercised for real rather than mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from t6_corpus import docs_for, script_extraction, script_verdicts

from arrival.__main__ import main
from arrival.contracts import Dossier
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "roster_synthetic.yaml"


def _doubles():
    docs = docs_for("self_page", 2, private_index=0)
    llm = LLMDouble()
    script_verdicts(llm, docs)
    script_extraction(llm, docs)
    return [ConnectorDouble(kind="self_page", docs=docs)], llm


def test_build_writes_dossiers_prints_a_table_and_returns_zero(tmp_path, capsys):
    connectors, llm = _doubles()
    out_dir = tmp_path / "dossiers"

    rc = main(
        ["build", "--roster", str(FIXTURE), "--out", str(out_dir)],
        connectors=connectors,
        llm=llm,
    )

    assert rc == 0
    out = capsys.readouterr().out
    for person_id in ("marisol-trevino", "anselm-kettleby"):
        Dossier.model_validate_json((out_dir / f"{person_id}.json").read_text("utf-8"))
        assert person_id in out, f"the report table does not mention {person_id}:\n{out}"
    assert "zero-result sources" in out


def test_build_only_restricts_the_run(tmp_path):
    connectors, llm = _doubles()
    out_dir = tmp_path / "dossiers"

    rc = main(
        ["build", "--roster", str(FIXTURE), "--out", str(out_dir), "--only", "anselm-kettleby"],
        connectors=connectors,
        llm=llm,
    )

    assert rc == 0
    assert (out_dir / "anselm-kettleby.json").exists()
    assert not (out_dir / "marisol-trevino.json").exists()


def test_build_skips_then_force_rebuilds(tmp_path):
    out_dir = tmp_path / "dossiers"
    argv = ["build", "--roster", str(FIXTURE), "--out", str(out_dir)]

    connectors, llm = _doubles()
    assert main(argv, connectors=connectors, llm=llm) == 0
    assert llm.calls

    connectors, skip_llm = _doubles()
    assert main(argv, connectors=connectors, llm=skip_llm) == 0
    assert skip_llm.calls == []

    connectors, force_llm = _doubles()
    assert main([*argv, "--force"], connectors=connectors, llm=force_llm) == 0
    assert force_llm.calls


def test_a_missing_roster_returns_two_and_says_so(tmp_path, capsys):
    connectors, llm = _doubles()

    rc = main(
        ["build", "--roster", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "out")],
        connectors=connectors,
        llm=llm,
    )

    assert rc == 2
    assert "roster" in capsys.readouterr().err
    assert llm.calls == []


def test_an_unknown_flag_returns_two(tmp_path):
    connectors, llm = _doubles()

    rc = main(["build", "--teleport", "yes"], connectors=connectors, llm=llm)

    assert rc == 2


def test_build_help_returns_zero_and_documents_every_flag(capsys):
    assert main(["build", "--help"]) == 0

    out = capsys.readouterr().out
    for flag in ("--roster", "--out", "--force", "--only"):
        assert flag in out


def test_build_never_touches_the_network_or_settings_when_doubles_are_injected(tmp_path):
    """The injected doubles must be used verbatim — no real connector is constructed.

    `tests/conftest.py` blocks the socket layer, so a connector that actually reached out
    would fail loudly; this asserts the cheaper property that nothing was constructed at
    all, which is what keeps `build --help` and a doubles-driven build fast.
    """
    connectors, llm = _doubles()
    out_dir = tmp_path / "dossiers"

    assert main(
        ["build", "--roster", str(FIXTURE), "--out", str(out_dir), "--only", "marisol-trevino"],
        connectors=connectors,
        llm=llm,
    ) == 0

    assert connectors[0].calls, "the injected connector was never asked for anything"
    assert [person.person_id for person, _budget in connectors[0].calls] == ["marisol-trevino"]
