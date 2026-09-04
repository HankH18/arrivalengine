"""T-101: two places where `python -m arrival build` broke its own exit-code contract.

The documented mapping, which an operator's wrapper scripts against:

    0  the build succeeded
    1  the build RAN and produced nothing — retry me
    2  usage or input error — fix your arguments and re-run

Both gaps below are the same shape: bad *input* being reported as something other than 2,
so a wrapper retries an argument that will never work.

A. **A `--out` the process cannot create was exit 1.**  `out.mkdir()` raised inside
   `build_all` and landed in `build_command`'s catch-all.  An unreadable roster and an
   unreadable `.env` are both already 2; a directory the operator named and the process
   cannot make is the same kind of fact about the same kind of argument.

B. **A negative budget was accepted silently, exit 0.**  `contracts.Budget` declared its
   three fields as bare `int`, so `Budget(max_docs_total=-1)` constructed happily and
   `build_command`'s `ValidationError` handler — which prints "bad budget" and returns 2 —
   was unreachable by any input a user could type.  Worse than a crash: `_fan_out` clamps
   with `max(0, ...)`, so a negative budget silently *became zero* and the run reported a
   successful build of empty dossiers.

Zero remains legal (`--max-docs 0` is a real, if useless, request, and
`tests/research/test_t6_budgets.py` constructs `Budget(max_llm_calls=0)`); only negatives
are refused.

Grading references: the exit codes are literals from the documented mapping, and the
budget half is graded through `contracts.Budget`'s own pydantic behaviour — never against
the text of a module this ticket may edit.

Offline (SPEC C7) through the `main(argv, *, connectors, llm)` seam with an empty connector
list and an `LLMDouble`, the idiom `tests/research/test_testbackend_cli_exit_codes.py`
established.  Nothing here shells out.
"""

from __future__ import annotations

import os
import stat

import pytest
from pydantic import ValidationError

from arrival.__main__ import main
from arrival.config import get_settings
from arrival.contracts import Budget
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("CLIFIX")


ROSTER = "people:\n  - name: Marisol Trevino\n    details: [Austin]\n"

BUDGET_FLAGS = {
    "--docs-per-connector": "docs_per_connector",
    "--max-docs": "max_docs_total",
    "--max-llm-calls": "max_llm_calls",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "default-out"))
    get_settings.cache_clear()


@pytest.fixture()
def roster(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(ROSTER, encoding="utf-8")
    return str(path)


def _run(capsys, argv):
    code = main(list(argv), connectors=[], llm=LLMDouble())
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------
# A. A `--out` the process cannot create is bad input, not a failed run.
# ---------------------------------------------------------------------------


needs_unprivileged = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions, so a read-only --out is still writable",
)


@needs_unprivileged
def test_an_out_dir_under_a_read_only_parent_is_exit_two(capsys, roster, tmp_path):
    """THE REPRODUCTION for A. This was exit 1 — "retry me" — for an argument that can
    never work without a chmod."""
    locked = tmp_path / "locked"
    locked.mkdir()
    mode = stat.S_IMODE(os.stat(locked).st_mode)
    os.chmod(locked, 0o500)
    try:
        code, _out, err = _run(
            capsys, ["build", "--roster", roster, "--out", str(locked / "dossiers")]
        )
    finally:
        os.chmod(locked, mode)

    assert code == 2, f"a --out that cannot be created exited {code}\n{err}"
    assert "Traceback" not in err
    assert err.strip(), "a non-zero exit with nothing on stderr tells an operator nothing"
    assert str(locked / "dossiers") in err, (
        f"stderr does not name the directory the operator has to fix:\n{err}"
    )


@needs_unprivileged
def test_a_read_only_out_dir_that_already_exists_is_also_exit_two(capsys, roster, tmp_path):
    """The neighbouring shape: `--out` EXISTS, so `out.mkdir(exist_ok=True)` succeeds and
    it is the sibling `docs` directory that cannot be made. Same bad argument, same code.
    """
    locked = tmp_path / "locked"
    dossiers = locked / "dossiers"
    dossiers.mkdir(parents=True)
    mode = stat.S_IMODE(os.stat(locked).st_mode)
    os.chmod(locked, 0o500)
    try:
        code, _out, err = _run(capsys, ["build", "--roster", roster, "--out", str(dossiers)])
    finally:
        os.chmod(locked, mode)

    assert code == 2, f"a read-only --out exited {code}\n{err}"
    assert "Traceback" not in err
    assert not [e for e in os.scandir(dossiers) if e.name.endswith(".json")]


def test_a_writable_out_dir_is_still_a_successful_build(capsys, roster, tmp_path):
    """The control. Refusing a directory it cannot create must not refuse one it can."""
    code, _out, err = _run(
        capsys, ["build", "--roster", roster, "--out", str(tmp_path / "fine" / "dossiers")]
    )
    assert code == 0, err
    assert (tmp_path / "fine" / "dossiers" / "marisol-trevino.json").is_file()


# ---------------------------------------------------------------------------
# B. A negative budget is bad input.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(BUDGET_FLAGS.values()))
@pytest.mark.parametrize("value", [-1, -5, -1000])
def test_the_budget_model_refuses_a_negative_count(field, value):
    """Graded against pydantic itself: constructing the model is what must fail, because
    that is what makes `build_command`'s "bad budget" handler reachable at all."""
    with pytest.raises(ValidationError):
        Budget(**{field: value})


@pytest.mark.parametrize("field", sorted(BUDGET_FLAGS.values()))
def test_the_budget_model_still_accepts_zero_and_the_documented_defaults(field):
    """Zero is a legal request — `test_t6_budgets.py` builds `Budget(max_llm_calls=0)` —
    so the constraint is `>= 0`, never `>= 1`."""
    assert getattr(Budget(**{field: 0}), field) == 0
    assert getattr(Budget(), field) > 0


@pytest.mark.parametrize("flag", sorted(BUDGET_FLAGS))
def test_a_negative_budget_flag_is_exit_two_and_names_the_budget(capsys, roster, flag, tmp_path):
    """THE REPRODUCTION for B. `--max-docs -1` used to exit 0 having built empty dossiers.

    Note the argv shape: argparse's negative-number matcher hands `-1` to `type=int`
    because this parser declares no numeric-looking options, so the value really does
    reach `Budget`.
    """
    directory = tmp_path / "out" / "dossiers"
    code, _out, err = _run(
        capsys, ["build", "--roster", roster, "--out", str(directory), flag, "-1"]
    )

    assert code == 2, f"{flag} -1 exited {code}\n{err}"
    assert "budget" in err.lower(), err
    assert "Traceback" not in err
    assert not directory.is_dir() or not [
        e for e in os.scandir(directory) if e.name.endswith(".json")
    ], f"{flag} -1 refused the budget but wrote dossiers anyway"


@pytest.mark.parametrize("flag", sorted(BUDGET_FLAGS))
def test_a_zero_budget_flag_is_still_a_successful_build(capsys, roster, flag, tmp_path):
    """The control that keeps the constraint from being a `>= 1` in disguise."""
    directory = tmp_path / "out" / "dossiers"
    code, _out, err = _run(
        capsys, ["build", "--roster", roster, "--out", str(directory), flag, "0"]
    )
    assert code == 0, err
    assert (directory / "marisol-trevino.json").is_file()
