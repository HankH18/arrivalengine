"""T-099: what the four spellings of `--only` mean, and why `''` is not "everybody".

`build_command`'s `--only` guard exists to catch one accident: a per-person CI wrapper
written as

    python -m arrival build --only "$PERSON"

whose `$PERSON` is unset or empty.  Without the guard the run prints an empty table and
exits 0, which the wrapper reads as "built, nothing to do".  The guard was written as
`if opts.only and not report.people`, and `_selects` as `if not only: return True` — and
BOTH of those are falsy for the empty string, which is exactly the value an unset shell
variable expands to.  So the one spelling the guard was added for was the one spelling it
could not see: `--only ''` short-circuited `_selects` to True for every person and built
the ENTIRE roster, exit 0, while `--only ' '` — a single space, the same accident with a
stray character — correctly refused with exit 2.

The distinction that makes this decidable is ABSENT versus BLANK, not falsy versus truthy:

* `--only` OMITTED is `None` (argparse's `default=None`, pinned below against argparse
  itself rather than against a docstring).  That means "everybody" and always did.
* `--only` PRESENT with a blank value — `''`, `'   '`, `'\t'` — is an operator naming a
  person, badly.  It matches nobody, and matching nobody is exit 2.

These tests grade against the documented exit-code mapping (0 success / 2 usage-or-input /
1 ran-and-produced-nothing) as literals, and against `argparse`'s own default handling.
Nothing here compares to a file this ticket may write.

Offline by construction (SPEC C7): every run injects `connectors=[ConnectorDouble(...)]`
and `llm=LLMDouble()` through the `main(argv, *, connectors, llm)` seam.  The connectors
carry no documents, so no model call is ever needed and `llm.calls` is asserted empty —
which is also what makes "the whole roster was built" a statement about *research work
that was started*, not merely about files on disk.
"""

from __future__ import annotations

import argparse
import os

import pytest

from arrival.__main__ import main
from arrival.config import get_settings
from arrival.contracts import PersonRef
from arrival.research import _selects
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("CLIFIX")


TEN_PEOPLE = "people:\n" + "".join(
    f"  - name: Person {n:02d}\n    details: [Austin]\n" for n in range(1, 11)
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """No run here may fall back to the committed dossier directory."""
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "default-out"))
    get_settings.cache_clear()


@pytest.fixture()
def roster(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(TEN_PEOPLE, encoding="utf-8")
    return str(path)


def _run(tmp_path, roster_path, *flags, name="out"):
    """One CLI build, offline. Returns (exit code, dossier filenames, connector, llm)."""
    directory = tmp_path / name / "dossiers"
    connector = ConnectorDouble(kind="search", docs=[])
    llm = LLMDouble()
    code = main(
        ["build", "--roster", roster_path, "--out", str(directory), *flags],
        connectors=[connector],
        llm=llm,
    )
    written = (
        sorted(e.name for e in os.scandir(directory) if e.name.endswith(".json"))
        if directory.is_dir()
        else []
    )
    return code, written, connector, llm


# ---------------------------------------------------------------------------
# The premise: what argparse actually hands `build_command` for each spelling.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),
        (["--only", ""], ""),
        (["--only="], ""),
        (["--only", " "], " "),
        (["--only", "someone"], "someone"),
    ],
)
def test_argparse_distinguishes_an_omitted_only_from_an_empty_one(argv, expected):
    """The whole fix rests on this: an omitted `--only` is `None` and an empty one is `''`.

    Graded against argparse itself, with the same `default=None` `build_command` declares,
    so the distinction is a property of the parser and not of a comment in `research.py`.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None)
    assert parser.parse_args(argv).only == expected


# ---------------------------------------------------------------------------
# `_selects`, the predicate the CLI's blast radius is decided by.
# ---------------------------------------------------------------------------


PERSON = PersonRef(person_id="marisol-trevino", name="Marisol Trevino", details=[])


def test_selects_admits_everybody_only_when_only_is_absent():
    assert _selects(PERSON, None) is True


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n"])
def test_a_blank_only_selects_nobody_rather_than_everybody(blank):
    """`''` is the value `"$PERSON"` expands to when `PERSON` is unset. It names nobody.

    If this ever returns True again, `--only ''` is once more a full-roster rebuild
    against a paid API, which is the defect T-099 exists to close.
    """
    assert _selects(PERSON, blank) is False


# ---------------------------------------------------------------------------
# The CLI contract, end to end, over a ten-person roster.
# ---------------------------------------------------------------------------


def test_an_empty_only_refuses_instead_of_rebuilding_the_whole_roster(tmp_path, roster, capsys):
    """THE REPRODUCTION. `--only ''` used to exit 0 with all ten people researched."""
    code, written, connector, llm = _run(tmp_path, roster, "--only", "")
    err = capsys.readouterr().err

    assert code == 2, f"--only '' exited {code}, not the usage-error code\n{err}"
    assert written == [], f"--only '' wrote dossiers for {written}"
    assert connector.calls == [], (
        "--only '' asked a connector to research "
        f"{[p.person_id for p, _ in connector.calls]} — on a live run that is paid work"
    )
    assert llm.calls == []
    assert "--only" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t"])
def test_every_blank_spelling_of_only_agrees_on_exit_two(tmp_path, roster, blank):
    """The bug was that two spellings of the same accident disagreed. They agree now."""
    code, written, connector, _llm = _run(tmp_path, roster, "--only", blank)
    assert code == 2, f"--only {blank!r} exited {code}"
    assert written == []
    assert connector.calls == []


def test_an_omitted_only_still_means_everybody(tmp_path, roster):
    """The other half, and the one a too-eager fix breaks: no `--only` builds all ten."""
    code, written, connector, _llm = _run(tmp_path, roster)
    assert code == 0
    assert len(written) == 10, written
    assert len(connector.calls) == 10


def test_a_named_person_still_builds_exactly_that_person(tmp_path, roster):
    code, written, connector, _llm = _run(tmp_path, roster, "--only", "person-03")
    assert code == 0
    assert written == ["person-03.json"], written
    assert [person.person_id for person, _budget in connector.calls] == ["person-03"]


def test_a_typo_in_only_is_still_exit_two(tmp_path, roster):
    """The guard's original purpose, unchanged by the fix."""
    code, written, _connector, _llm = _run(tmp_path, roster, "--only", "person-99")
    assert code == 2
    assert written == []
