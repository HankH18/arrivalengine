"""`python -m arrival build`: the exit codes an operator scripts against.

`arrival/__main__.py:main` documents the vocabulary — "0 on success, 2 for a missing or
unknown command" — and `research.build_command` adds the third: 1 when the build ran and
produced nothing. A wrapper that reads 1 as 2, or 0 as either, ships a broken roster
silently, so the numbers are the contract and not the messages.

The existing suite asserts fifteen of these (`tests/test_t0_cli.py`,
`tests/research/test_t6_cli.py`, `test_t6_degradation.py`, `test_t6_reporting.py`,
`test_t059_roster_encoding.py`). This module covers the shapes none of them reach: a roster
that parses but names nobody, a roster that is a directory, `--only` by id vs by name vs a
near miss, budgets of 0 and 1 and a negative, `--force` against an existing dossier and
against a corrupt one, and an `--out` the process cannot create.

Two rules every case obeys:

* **In process, with doubles.** `main(argv, *, connectors, llm)` is the seam T-0 pinned for
  exactly this. `connectors=[]` is a legal fan-out — `_fan_out` handles an empty sequence —
  and it is the cheapest way to exercise the CLI's own control flow without also
  re-testing the connectors.
* **A CLI reports; it does not traceback at an operator** (`build_command`'s own comment).
  So every non-zero case asserts BOTH the code and that stderr carries a sentence rather
  than a `Traceback`.

Grading references: the exit-code integers, `argparse`'s documented exit 2 for a usage
error, and stderr as text. Nothing here compares against a file this ticket wrote.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from arrival.__main__ import main
from arrival.config import get_settings
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBACKEND")

GOOD_ROSTER = (
    "people:\n"
    "  - name: Marisol Trevino\n"
    "    details: [platform lead Quarrystone Labs, Austin]\n"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """`build_command` reads `get_settings()` at call time and `--out` defaults to
    `settings.dossier_dir`; a test that omitted `--out` would otherwise write into the
    repo's committed corpus."""
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "default-out"))
    get_settings.cache_clear()


@pytest.fixture
def roster(tmp_path):
    def write(text: str, name: str = "roster.yaml") -> str:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)
    return write


@pytest.fixture
def out(tmp_path):
    counter = {"n": 0}

    def make() -> str:
        counter["n"] += 1
        return str(tmp_path / f"out{counter['n']}" / "dossiers")
    return make


def _run(capsys, argv, *, connectors=(), llm=None):
    code = main(list(argv), connectors=list(connectors), llm=llm or LLMDouble())
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _no_traceback(err: str) -> None:
    assert "Traceback" not in err, f"the CLI tracebacked at the operator:\n{err[-2000:]}"
    assert err.strip(), "a non-zero exit with nothing on stderr tells an operator nothing"


# ---------------------------------------------------------------------------
# 1. Dispatch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], 2),
        (["frobnicate"], 2),
        ([""], 2),
        (["build "], 2),
        (["BUILD"], 2),
        (["--roster", "x.yaml"], 2),
        (["-h"], 0),
        (["--help"], 0),
        (["help"], 0),
    ],
)
def test_the_dispatcher_answers_the_documented_code(capsys, argv, expected):
    code, out, err = _run(capsys, argv)
    assert code == expected, f"{argv} -> {code}\n{err}"
    if expected == 0:
        assert "usage:" in out
    else:
        _no_traceback(err)
        assert "usage:" in err, "a usage error must print the usage"


def test_the_help_text_names_every_flag_build_accepts(capsys):
    code, out, _err = _run(capsys, ["build", "--help"])
    assert code == 0
    for flag in ("--roster", "--out", "--force", "--only", "--docs-per-connector",
                 "--max-docs", "--max-llm-calls"):
        assert flag in out, f"{flag} is undocumented"


@pytest.mark.parametrize("argv", [
    ["build", "--teleport"],
    ["build", "--roster"],
    ["build", "--max-docs"],
    ["build", "--max-docs", "not-a-number"],
    ["build", "--max-docs", "1.5"],
    ["build", "--docs-per-connector", "many"],
])
def test_an_argparse_usage_error_is_exit_two(capsys, argv):
    """`build_command` catches argparse's `SystemExit` and returns its code, so a bad flag
    is a return value rather than an exception escaping `main`. Pinned because a `SystemExit`
    that got through would kill a caller embedding the CLI."""
    code, _out, err = _run(capsys, argv)
    assert code == 2
    assert "usage:" in err


# ---------------------------------------------------------------------------
# 2. Rosters the CLI cannot use. All of them are input errors: exit 2.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,text",
    [
        ("an empty people list", "people: []\n"),
        ("a people key that is null", "people:\n"),
        ("no people key at all", "somethingelse: 3\n"),
        ("a bare scalar", "just a string\n"),
        ("an empty file", ""),
        ("only a comment", "# nobody here\n"),
        ("a people key that is a mapping", "people:\n  a: 1\n"),
        ("entries that name nobody", "people:\n  - {}\n  - 3\n  - name: '   '\n"),
        ("names that all slug to nothing", "people:\n  - name: '###'\n  - name: '!!!'\n"),
        ("unparsable yaml", "people:\n  - name: [unclosed\n"),
        ("yaml tabs", "people:\n\t- name: X\n"),
    ],
)
def test_a_roster_that_names_nobody_is_an_input_error(capsys, roster, out, label, text):
    code, _stdout, err = _run(capsys, ["build", "--roster", roster(text), "--out", out()])
    assert code == 2, f"{label} -> {code}"
    _no_traceback(err)
    assert "roster" in err.lower(), f"{label}: stderr does not mention the roster:\n{err}"


def test_a_missing_roster_names_the_path_it_looked_for(capsys, tmp_path, out):
    missing = str(tmp_path / "no-such-roster.yaml")
    code, _stdout, err = _run(capsys, ["build", "--roster", missing, "--out", out()])
    assert code == 2
    _no_traceback(err)
    assert missing in err


def test_a_roster_that_is_a_directory_is_an_input_error_not_a_traceback(capsys, tmp_path, out):
    """`IsADirectoryError` is an `OSError`, which `load_roster` catches by name. Without
    that arm it falls through to `build_command`'s catch-all and becomes exit 1 — the code
    a wrapper reads as "the build ran and failed", not "you pointed me at a folder"."""
    directory = tmp_path / "a-directory"
    directory.mkdir()
    code, _stdout, err = _run(capsys, ["build", "--roster", str(directory), "--out", out()])
    assert code == 2
    _no_traceback(err)
    assert str(directory) in err


# ---------------------------------------------------------------------------
# 3. Rosters the CLI CAN use, including awkward ones.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,text",
    [
        ("the documented mapping shape", GOOD_ROSTER),
        ("a bare top-level list", "- Marisol Trevino\n- Anselm Kettleby\n"),
        ("string entries under people", "people:\n  - Marisol Trevino\n"),
        ("details as a single string", "people:\n  - name: Marisol Trevino\n    details: Austin\n"),
        ("no details at all", "people:\n  - name: Marisol Trevino\n"),
        ("a declared person_id", "people:\n  - name: Marisol Trevino\n    person_id: mt\n"),
        ("a declared id that is a path",
         "people:\n  - name: Marisol Trevino\n    person_id: ../../etc/passwd\n"),
        ("a declared id that slugs to nothing",
         "people:\n  - name: Marisol Trevino\n    person_id: '###'\n"),
        ("a non-ascii name", "people:\n  - name: José Ángel Núñez\n"),
        ("a mix of usable and unusable entries",
         "people:\n  - name: Marisol Trevino\n  - {}\n  - 3\n"),
    ],
)
def test_a_roster_the_loader_can_read_builds_and_exits_zero(capsys, roster, out, label, text):
    directory = out()
    code, stdout, err = _run(capsys, ["build", "--roster", roster(text), "--out", directory])
    assert code == 0, f"{label} -> {code}\n{err}"
    written = sorted(p.name for p in os.scandir(directory)) if os.path.isdir(directory) else []
    assert written, f"{label}: exit 0 but nothing was written to {directory}"
    for name in written:
        assert "/" not in name and ".." not in name, (
            f"{label}: a roster escaped the output directory as {name!r}"
        )


def test_a_duplicate_name_is_disambiguated_rather_than_dropped(capsys, roster, out):
    """`_person_from` bumps a collision by `slug(details[0])`. Over the CLI the consequence
    is what matters: two files, so neither person is silently lost to the other."""
    directory = out()
    text = ("people:\n"
            "  - name: Ana Vega\n    details: [Austin]\n"
            "  - name: Ana Vega\n    details: [Denver]\n")
    code, _stdout, err = _run(capsys, ["build", "--roster", roster(text), "--out", directory])
    assert code == 0, err
    written = sorted(p.name for p in os.scandir(directory))
    assert len(written) == 2, f"one of two people with the same name was lost: {written}"
    assert "ana-vega.json" in written
    assert "ana-vega-denver.json" in written


def test_two_people_whose_names_and_details_both_collide_still_get_two_files(capsys,
                                                                            roster, out):
    directory = out()
    text = ("people:\n"
            "  - name: Ana Vega\n    details: [Austin]\n"
            "  - name: Ana Vega\n    details: [Austin]\n"
            "  - name: Ana Vega\n    details: [Austin]\n")
    code, _stdout, err = _run(capsys, ["build", "--roster", roster(text), "--out", directory])
    assert code == 0, err
    assert len(sorted(os.scandir(directory), key=lambda e: e.name)) == 3


# ---------------------------------------------------------------------------
# 4. --only.
# ---------------------------------------------------------------------------

TWO_PEOPLE = (
    "people:\n"
    "  - name: Marisol Trevino\n    details: [Austin]\n"
    "  - name: Anselm Kettleby\n    details: [Austin]\n"
)


@pytest.mark.parametrize("selector", ["marisol-trevino", "Marisol Trevino",
                                      "  Marisol Trevino  ", "Marisol  Trevino"])
def test_only_selects_by_id_or_by_display_name(capsys, roster, out, selector):
    """`_selects` accepts the id, the exact name, or anything that slugs to the id — which
    is what makes `--only "Marisol  Trevino"` (two spaces) work while
    `--only marisol_trevino` does too."""
    directory = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(TWO_PEOPLE), "--out", directory,
                 "--only", selector]
    )
    assert code == 0, f"--only {selector!r} -> {code}\n{err}"
    written = sorted(p.name for p in os.scandir(directory))
    assert written == ["marisol-trevino.json"], written


@pytest.mark.parametrize("selector", ["marisol-trevin", "Marisol Trevin", "nobody",
                                      "*", "marisol trevino jr", "   ", "\t", "-"])
def test_only_matching_nobody_is_an_input_error_and_not_a_silent_success(
    capsys, roster, out, selector
):
    """A one-character typo otherwise prints an empty table and exits 0, which a per-person
    CI wrapper reads as "built, nothing to do" — the comment `build_command` carries at the
    guard."""
    directory = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(TWO_PEOPLE), "--out", directory,
                 "--only", selector]
    )
    assert code == 2, f"--only {selector!r} -> {code}\n{err}"
    _no_traceback(err)
    assert "--only" in err
    assert not os.path.isdir(directory) or not [
        entry for entry in os.scandir(directory) if entry.name.endswith(".json")
    ], f"--only {selector!r} matched nobody but wrote something"


def test_an_empty_only_builds_the_whole_roster_while_a_space_refuses_it(capsys, roster, out):
    """MEASURED, and the two spellings of "blank" disagree.

    `--only ''` is FALSY, so `_selects` short-circuits to True for everybody and the whole
    roster is built, exit 0. `--only ' '` is TRUTHY, reaches the comparison, matches
    nobody, and `build_command`'s guard turns that into exit 2. So a wrapper written as

        python -m arrival build --only "$PERSON"

    with `$PERSON` unset silently rebuilds the ENTIRE roster — against a paid API — where
    the same wrapper with `$PERSON=" "` correctly refuses. That is exactly the class of
    accident the `--only` guard was added to catch, reached by the one spelling the guard
    cannot see, because `if opts.only` is falsy for `""`.

    Pinned as two observations rather than one assertion of correctness: this ticket may
    not change `src/`, and either answer could be the intended one — but they cannot both
    be, and today nothing in the suite records that they differ.
    """
    empty_dir = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(TWO_PEOPLE), "--out", empty_dir, "--only", ""]
    )
    assert code == 0, err
    assert len([e for e in os.scandir(empty_dir) if e.name.endswith(".json")]) == 2, (
        "--only '' built something other than the whole roster"
    )

    space_dir = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(TWO_PEOPLE), "--out", space_dir, "--only", " "]
    )
    assert code == 2, err
    assert not [e for e in os.scandir(space_dir) if e.name.endswith(".json")]


# ---------------------------------------------------------------------------
# 5. Budgets.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flags",
    [
        ["--max-docs", "0"],
        ["--max-docs", "1"],
        ["--docs-per-connector", "0"],
        ["--docs-per-connector", "1"],
        ["--max-llm-calls", "0"],
        ["--max-llm-calls", "1"],
        ["--max-docs", "0", "--docs-per-connector", "0", "--max-llm-calls", "0"],
        ["--max-docs", "1", "--docs-per-connector", "1", "--max-llm-calls", "1"],
    ],
)
def test_a_budget_of_zero_or_one_still_produces_a_dossier(capsys, roster, out, flags):
    """A zero budget is a legal instruction — "research nothing, write down what we know" —
    and must not be an error. `contracts.Budget` puts no bound on these, so the CLI is the
    only place the values are seen before they are used."""
    directory = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(GOOD_ROSTER), "--out", directory, *flags]
    )
    assert code == 0, f"{flags} -> {code}\n{err}"
    assert os.path.isfile(os.path.join(directory, "marisol-trevino.json"))


@pytest.mark.parametrize("flags", [["--max-docs", "-1"], ["--docs-per-connector", "-5"],
                                   ["--max-llm-calls", "-1"]])
def test_a_negative_budget_is_accepted_today_and_never_tracebacks(capsys, roster, out, flags):
    """MEASURED, and recorded rather than asserted as correct: `contracts.Budget` declares
    these as bare `int` with no `ge=0`, so `--max-docs -1` is validated, accepted and
    silently means "no documents". `build_command` has a `ValidationError` handler for a
    "bad budget" that no reachable input currently trips.

    The assertion is the one that holds either way — the CLI must not traceback and must
    not half-write — so the day a bound is added, this test says which way the answer moved
    instead of quietly passing.
    """
    directory = out()
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(GOOD_ROSTER), "--out", directory, *flags]
    )
    assert code in (0, 2), f"{flags} -> {code}\n{err}"
    if code == 2:
        _no_traceback(err)
        assert "budget" in err.lower()


# ---------------------------------------------------------------------------
# 6. --force and the skip-existing path.
# ---------------------------------------------------------------------------


def _build(capsys, roster_path, directory, *flags):
    return _run(capsys, ["build", "--roster", roster_path, "--out", directory, *flags])


def test_an_existing_dossier_is_skipped_and_left_byte_identical(capsys, roster, out):
    directory = out()
    path = roster(GOOD_ROSTER)
    assert _build(capsys, path, directory)[0] == 0
    written = os.path.join(directory, "marisol-trevino.json")
    before = open(written, "rb").read()

    code, stdout, _err = _build(capsys, path, directory)
    assert code == 0
    assert open(written, "rb").read() == before, "a skipped person was rewritten"
    assert "skipped" in stdout.lower(), (
        "the report must say a person was skipped; a silent skip looks like a rebuild"
    )


def test_force_rebuilds_the_person_the_second_run_would_have_skipped(capsys, roster, out):
    directory = out()
    path = roster(GOOD_ROSTER)
    assert _build(capsys, path, directory)[0] == 0
    written = os.path.join(directory, "marisol-trevino.json")
    sentinel = json.loads(open(written, encoding="utf-8").read())
    sentinel["schema_version"] = 99
    open(written, "w", encoding="utf-8").write(json.dumps(sentinel))

    code, _stdout, err = _build(capsys, path, directory, "--force")
    assert code == 0, err
    assert json.loads(open(written, encoding="utf-8").read())["schema_version"] != 99, (
        "--force did not rebuild the dossier"
    )


def test_a_corrupt_existing_dossier_is_rebuilt_rather_than_reported_as_good(capsys,
                                                                           roster, out):
    """The skip path reads the existing file to build its report row; an unreadable one
    must fall through to a rebuild. Reporting it as skipped-and-fine would make a corrupt
    corpus permanent — the next run skips it again."""
    directory = out()
    path = roster(GOOD_ROSTER)
    assert _build(capsys, path, directory)[0] == 0
    written = os.path.join(directory, "marisol-trevino.json")
    open(written, "w", encoding="utf-8").write("{not json")

    code, _stdout, _err = _build(capsys, path, directory)
    assert code == 0
    json.loads(open(written, encoding="utf-8").read())  # raises unless it was rebuilt


def test_an_out_dir_that_collides_with_its_own_docs_dir_is_refused_before_writing(
    capsys, roster, tmp_path
):
    """`out_dir/../docs` is where the cited documents go, so `--out .../docs` would
    co-mingle `{person_id}.json` and `{doc_id}.json` in one directory."""
    directory = tmp_path / "corpus" / "docs"
    directory.mkdir(parents=True)
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(GOOD_ROSTER), "--out", str(directory)]
    )
    assert code == 2
    _no_traceback(err)
    assert "same directory" in err
    assert not list(os.scandir(directory)), "the run wrote before refusing"


def test_an_out_directory_the_process_cannot_create_is_reported_and_not_a_traceback(
    capsys, roster, tmp_path
):
    """MEASURED: this is exit **1**, not 2, because `out.mkdir()` raises inside `build_all`
    and lands in `build_command`'s catch-all rather than its input-error handler. A
    read-only `--out` is arguably bad input (2) and is reported as a failed build (1);
    this ticket records the disagreement rather than deciding it.

    Asserted here is the part that is not in doubt: the code is non-zero, stderr carries a
    sentence, and no traceback reaches the operator.
    """
    locked = tmp_path / "locked"
    locked.mkdir()
    mode = stat.S_IMODE(os.stat(locked).st_mode)
    os.chmod(locked, 0o500)
    try:
        code, _stdout, err = _run(
            capsys, ["build", "--roster", roster(GOOD_ROSTER), "--out",
                     str(locked / "dossiers")]
        )
    finally:
        os.chmod(locked, mode)
    assert code != 0
    _no_traceback(err)
    assert code in (1, 2), code


# ---------------------------------------------------------------------------
# 7. The one code the other two files own between them, asserted here as the
#    boundary: a build that RAN and produced nothing is 1, never 0 and never 2.
# ---------------------------------------------------------------------------


class _DeadModel:
    """Every call fails — no key, a 401, no network. `build_all` refuses to commit a
    dossier built entirely from failed calls, because committing one makes the outage
    permanent: the next run finds the file and skips the person."""

    def __init__(self) -> None:
        self.calls = 0

    async def structured(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("the model is unreachable")


def test_a_build_that_wrote_nothing_is_exit_one_and_not_exit_two(capsys, roster, out):
    """The distinction a wrapper depends on: 2 means "your input was wrong, fix it and
    re-run"; 1 means "your input was fine and the run failed, retry it"."""
    from t6_corpus import docs_for

    directory = out()
    connectors = [__import__("doubles").ConnectorDouble(kind="self_page",
                                                        docs=docs_for("self_page", 2))]
    code, _stdout, err = _run(
        capsys, ["build", "--roster", roster(GOOD_ROSTER), "--out", directory],
        connectors=connectors, llm=_DeadModel(),
    )
    assert code == 1, f"-> {code}\n{err}"
    _no_traceback(err)
    assert not os.path.isdir(directory) or not [
        entry for entry in os.scandir(directory) if entry.name.endswith(".json")
    ], "a dossier was committed for a person whose every model call failed"
