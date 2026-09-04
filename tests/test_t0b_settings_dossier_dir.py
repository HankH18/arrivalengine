"""T-0b regression for D8: `Settings` had no `dossier_dir`, so `DOSSIER_DIR` was undocumented.

The frozen acceptance harness for T-8 constructs the web app by setting a `DOSSIER_DIR`
environment variable and then importing `arrival.web.app`; 12 of T-8's 14 graded criteria
go through that seam. `Settings` declared no such field, `.env.example` had no such line,
and no line of SPEC, DESIGN or TASKS mentions it — so T-8 would have shipped a
configuration key that no document describes and no operator could discover.

Two properties matter beyond "the field exists":

* **CWD-independence.** `cache_dir` defaults to a relative `Path(".cache/http")`, so every
  consumer resolves it against the process working directory and a CLI run from a
  subdirectory silently gets a different root. `dossier_dir` must not repeat that shape:
  its default is anchored on the repo, not on wherever the process happens to be.
* **The D3 interaction.** `get_settings()` is `lru_cache`'d, so a test that sets
  `DOSSIER_DIR` and then reads it is only trustworthy because the shared harness clears
  that cache around every test. Without the D3 repair, the assertion below reads a
  `Settings` built before the env var existed — exactly how the T-8 seam would fail. The
  first test here poisons the cache on purpose so the second one is a real measurement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arrival.config import Settings, get_settings

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_settings_declares_a_dossier_dir_field():
    """The name is fixed by the frozen T-8 seam: field `dossier_dir`, env `DOSSIER_DIR`."""
    assert "dossier_dir" in Settings.model_fields, sorted(Settings.model_fields)


def test_the_dossier_dir_default_points_at_the_committed_corpus():
    """DESIGN §Data models: `data/dossiers/{person_id}.json` is what T-6 writes and T-9 ships."""
    default = Settings(_env_file=None).dossier_dir
    assert default.parts[-2:] == ("data", "dossiers"), default
    assert default == REPO_ROOT / "data" / "dossiers", default


def test_the_dossier_dir_default_is_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The defect `cache_dir` already has, and the one thing D8 must not reproduce."""
    default = Settings(_env_file=None).dossier_dir
    assert default.is_absolute(), f"{default} is relative, so it moves with the process CWD"

    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).dossier_dir == default, (
        "the default dossier directory changed when the process changed directory"
    )


def test_an_earlier_test_populates_the_settings_cache():
    """Deliberately ordinary, and deliberately before the env-var test below (see D3)."""
    assert get_settings() is not None


def test_dossier_dir_honours_the_env_var_through_get_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The T-8 seam, executed: set `DOSSIER_DIR`, then read it back through `get_settings`.

    This is the assertion the frozen harness's 12 `/arrive`, `/digest` and `/debug`
    criteria depend on, and it is only meaningful because D3 clears the `lru_cache`
    between tests — the preceding test has already filled it.
    """
    corpus = tmp_path / "orchestrator-owned" / "dossiers"
    corpus.mkdir(parents=True)
    monkeypatch.setenv("DOSSIER_DIR", str(corpus))
    assert get_settings().dossier_dir == corpus
    assert Settings(_env_file=None).dossier_dir == corpus


def test_env_example_documents_dossier_dir():
    """An undocumented env var is one an operator cannot find. It is documented as
    OPTIONAL (commented out, like CACHE_DIR): an active `DOSSIER_DIR=data/dossiers` line
    would hand every operator who copies the file the relative, CWD-dependent path the
    default exists to avoid.
    """
    text = ENV_EXAMPLE.read_text()
    assert "DOSSIER_DIR" in text, ".env.example does not mention DOSSIER_DIR"
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if "DOSSIER_DIR" in line)
    assert lines[index].lstrip().startswith("#"), "DOSSIER_DIR must be documented, not set"
    assert any(
        line.lstrip().startswith("#") and line.strip() != "#"
        for line in lines[max(0, index - 3) : index]
    ), "the DOSSIER_DIR line has no comment saying what it is for"
