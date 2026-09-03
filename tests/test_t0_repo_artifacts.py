"""T-0 acceptance 7: the repo furniture exists and says what it is required to say.

Acceptance 7 names five artifacts — `pyproject.toml`, `.gitignore`, `.env.example`, a
README skeleton and `HOURS.md` — and only `.env.example` was graded (by
`test_t0_config.py`). Measured: deleting `README.md` and `HOURS.md`, and stripping the two
`.gitignore` entries acceptance 7 spells out by name, all left the gate at
"100 passed, exit 0". Those two entries are what stops a later ticket committing `.env`
secrets or the `.cache/` tree.

The `--strict-markers` assertion here is the same class of defect one level up: without it a
misspelled `pytest.mark.tickets("T-3")` is silently deselected by `--ticket T-3` and the
ticket gate reports a green run for tests that never executed.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_bytes().decode())


# --------------------------------------------------------------------------
# .gitignore
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", [".cache/", ".env"])
def test_gitignore_contains_the_entries_acceptance_7_names(entry: str):
    """Named verbatim in the acceptance list; both keep secrets and cache out of git."""
    lines = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert entry in lines


def test_no_env_file_is_tracked():
    """The rule the .gitignore entry exists to enforce, checked against git itself."""
    proc = subprocess.run(
        ["git", "ls-files", "--", ".env", "**/.env", ".cache", ".cache/**"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.stdout.strip() == "", f"tracked secrets/cache: {proc.stdout}"


# --------------------------------------------------------------------------
# pyproject
# --------------------------------------------------------------------------


def test_pyproject_pins_the_c3_stack_exactly(pyproject):
    """SPEC C3 pins the stack; every worktree must provision the same tree."""
    required = {
        "anthropic", "fastapi", "httpx", "jinja2", "networkx", "pydantic", "uvicorn",
    }
    dependencies = pyproject["project"]["dependencies"]
    named = {dep.split("[")[0].split("==")[0]: dep for dep in dependencies}
    assert required <= set(named), f"missing from C3 stack: {required - set(named)}"
    for dep in dependencies:
        assert "==" in dep, f"{dep!r} is not pinned to an exact version"

    dev = pyproject["dependency-groups"]["dev"]
    assert {d.split("==")[0] for d in dev} >= {"pytest", "ruff"}
    for dep in dev:
        assert "==" in dep, f"{dep!r} is not pinned to an exact version"


def test_pyproject_configures_ruff_and_pytest(pyproject):
    ruff = pyproject["tool"]["ruff"]
    assert ruff["line-length"] > 0
    assert set(ruff["lint"]["select"]) >= {"E", "F", "I"}

    pytest_ini = pyproject["tool"]["pytest"]["ini_options"]
    assert pytest_ini["testpaths"] == ["tests"]
    assert "src" in pytest_ini["pythonpath"]
    assert pytest_ini["asyncio_mode"] == "auto"
    assert any(m.startswith("ticket(") for m in pytest_ini["markers"])
    assert any(m.startswith("network") for m in pytest_ini["markers"])


def test_unknown_markers_are_an_error_not_a_warning(pyproject):
    assert "--strict-markers" in pyproject["tool"]["pytest"]["ini_options"]["addopts"]


def test_strict_markers_really_rejects_a_misspelled_ticket_marker(tmp_path: Path):
    """Executed, not assumed: `pytest.mark.tickets("T-0")` must fail the run.

    Run in a throwaway rootdir so the repo's own suite is untouched.
    """
    module = tmp_path / "test_typo_marker.py"
    module.write_text(
        "import pytest\n"
        'pytestmark = pytest.mark.tickets("T-0")   # typo: "tickets"\n'
        "def test_should_have_run():\n"
        "    assert True\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--strict-markers", "-q", "-p", "no:cacheprovider",
         str(module)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "tickets" in proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# the documents
# --------------------------------------------------------------------------


def test_env_example_exists_and_documents_every_acceptance_7_key():
    """Key-by-key coverage lives in test_t0_config.py; this pins the names acceptance 7 lists."""
    text = (REPO_ROOT / ".env.example").read_text()
    for key in (
        "ANTHROPIC_API_KEY",
        "TAVILY_API_KEY",
        "GITHUB_TOKEN",
        "CONTACT_EMAIL",
        "DEBUG_VIEWS",
        "ANTHROPIC_MODEL_FAST",
        "ANTHROPIC_MODEL_SMART",
    ):
        assert key in text, f"{key} is not documented in .env.example"


def test_readme_skeleton_exists_and_tells_someone_how_to_run_it():
    text = (REPO_ROOT / "README.md").read_text()
    assert text.startswith("# ")
    assert "uv sync" in text
    assert "python -m arrival build" in text
    assert "pytest" in text


def test_hours_log_exists_and_has_a_row_per_closed_ticket():
    """EXECUTION §8: one appended line per ticket as it closes; the client scores the total.

    A header-only table at T-9 is a silent zero, and nothing else in the suite looks at
    this file.
    """
    lines = [
        line.strip()
        for line in (REPO_ROOT / "HOURS.md").read_text().splitlines()
        if line.strip().startswith("|")
    ]
    assert len(lines) >= 2, "HOURS.md has no table"
    header, separator, *rows = lines
    assert [c.strip().lower() for c in header.strip("|").split("|")] == [
        "ticket",
        "what",
        "hours",
    ]
    assert set(separator) <= set("| -:")
    assert rows, "HOURS.md has no ticket rows; T-0 is closed and must be logged"

    tickets = [row.strip("|").split("|")[0].strip() for row in rows]
    assert "T-0" in tickets, f"T-0 is closed but not logged; rows: {tickets}"
    for row in rows:
        hours = row.strip("|").split("|")[2].strip()
        assert float(hours) > 0, f"{row!r} has no hour count"
