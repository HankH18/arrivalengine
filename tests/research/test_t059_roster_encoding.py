"""T-059: a roster that is not UTF-8 must be a `RosterError`, not a decode traceback.

`load_roster` read the file under `except OSError`. `UnicodeDecodeError` subclasses
`ValueError`, not `OSError`, so a roster saved as latin-1 escaped `RosterError` and fell
through to `build_command`'s catch-all at `research.py`'s CLI tail -- which logs a full
traceback and returns **1**, the "something unexpected broke" code, rather than the **2**
that means "your input is wrong". Measured on this branch before the fix::

    $ python -m arrival build --roster <latin-1 roster> --out /tmp/out
    ... Traceback (most recent call last) ...
    arrival: build failed: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xfc ...
    exit 1

and after::

    arrival: cannot read roster <path>: 'utf-8' codec can't decode byte 0xfc ...
    exit 2

A roster is hand-written by a human, so the wrong codec is an ordinary operator mistake and
is the input class this exception type exists for.

**What these grade against, none of which this ticket may write:**

* the CPython exception hierarchy;
* the CLI's documented exit codes -- `arrival/__main__.py:main`'s own docstring says 2 is
  the code for input the CLI will not act on, and `test_t6_cli.py` already grades 0 the same
  way -- checked here as an integer, not as a string from `research.py`;
* `arrival.util.slug` and `arrival.contracts.PersonRef`, for the positive path;
* byte-string literals written in this module.

No assertion here compares against message text authored in `research.py`.
"""

from __future__ import annotations

import pytest

from arrival.__main__ import main
from arrival.research import RosterError, load_roster
from arrival.util import slug
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

#: Two people whose names are pure Latin-1 territory, so every codec below can encode them.
ROSTER_TEXT = (
    "people:\n"
    '  - name: "Jürgen Müller"\n'
    "    details: [founder]\n"
    '  - name: "Åsa Lindqvist"\n'
    "    details: [investor]\n"
)


def test_the_stdlib_puts_unicodedecodeerror_under_valueerror_not_oserror():
    """The fact `except OSError` got wrong, pinned so a revert cannot look reasonable."""
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, OSError)


@pytest.mark.parametrize(
    ("codec", "why"),
    [
        ("latin-1", "a hand-edited YAML file saved from a legacy editor"),
        ("cp1252", "what 'ANSI' means in a Windows save dialog"),
        ("utf-16", "a BOM, so it fails at position 0 rather than mid-string"),
    ],
)
def test_a_roster_in_the_wrong_codec_is_a_rostererror_naming_the_file(tmp_path, codec, why):
    path = tmp_path / "roster.yaml"
    path.write_bytes(ROSTER_TEXT.encode(codec))

    with pytest.raises(RosterError) as excinfo:
        load_roster(path)

    exc = excinfo.value
    assert str(path) in str(exc), f"{codec} ({why}) did not name the roster: {exc}"
    assert not isinstance(exc, UnicodeDecodeError), "the raw decode error escaped unwrapped"
    assert isinstance(exc.__cause__, UnicodeDecodeError), (
        f"the cause chain lost the codec detail; got {type(exc.__cause__).__name__}"
    )


def test_a_valid_utf8_roster_with_non_ascii_names_still_loads(tmp_path):
    """The fix widens a handler; it must not change what a readable roster produces.

    Graded against `arrival.util.slug`, which owns the id convention (SPEC Q1), rather than
    against ids written out in this module.
    """
    path = tmp_path / "roster.yaml"
    path.write_text(ROSTER_TEXT, encoding="utf-8")

    people = load_roster(path)

    assert [p.name for p in people] == ["Jürgen Müller", "Åsa Lindqvist"]
    for person in people:
        assert person.person_id == slug(person.name)


def test_a_missing_roster_is_still_a_rostererror(tmp_path):
    """The `OSError` arm the old handler did cover must survive widening it to `ValueError`."""
    with pytest.raises(RosterError) as excinfo:
        load_roster(tmp_path / "nothing-here.yaml")
    assert isinstance(excinfo.value.__cause__, OSError)


def test_the_cli_rejects_a_mis_encoded_roster_with_the_input_error_code(tmp_path, capsys):
    """Exit 2 ("your input is wrong"), not 1 ("something unexpected broke").

    Driven through the in-process `main(argv, *, connectors, llm)` seam, so no subprocess and
    no network (SPEC C7). `build_all` calls `load_roster` before it touches a connector or
    the model, so the injected doubles are never used -- they are here to make it impossible
    for a regression in that ordering to reach the network instead of failing this test.
    """
    path = tmp_path / "roster.yaml"
    path.write_bytes(ROSTER_TEXT.encode("latin-1"))

    rc = main(
        ["build", "--roster", str(path), "--out", str(tmp_path / "dossiers")],
        connectors=[ConnectorDouble(kind="self_page")],
        llm=LLMDouble(),
    )

    assert rc == 2, (
        "an unreadable roster is a usage error, not an internal failure; "
        f"got exit {rc}, which is what the catch-all returns"
    )
    err = capsys.readouterr().err
    assert str(path) in err, f"the operator was not told which file to fix:\n{err}"
    assert "Traceback" not in err, f"a CLI reports; it does not traceback at an operator:\n{err}"


def test_the_cli_reports_a_mis_encoded_env_file_instead_of_a_dotenv_traceback(
    tmp_path, capsys, monkeypatch
):
    """The same bug class one layer out, found by sweeping rather than by the ticket.

    `Settings` is configured with `env_file=".env"`, which python-dotenv opens in text mode
    under a strict utf-8 codec, so a latin-1 `.env` raises `UnicodeDecodeError` -- again a
    `ValueError`, not an `OSError`. `build_command`'s `get_settings()` call sat between the
    argparse `try` and the budget `try`, in no handler at all. Measured before the fix::

        File ".../dotenv/parser.py", line 73, in __init__
        UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 25 ...
        exit 1

    `env_file=".env"` is relative, so the file is resolved against the process CWD -- which
    is why this test `chdir`s rather than writing into the repo. The roster is valid UTF-8
    so that an exit of 2 can only have come from the configuration read.
    """
    roster = tmp_path / "roster.yaml"
    roster.write_text(ROSTER_TEXT, encoding="utf-8")
    (tmp_path / ".env").write_bytes(b'ANTHROPIC_API_KEY="sk-caf\xe9"\n')
    monkeypatch.chdir(tmp_path)

    rc = main(
        ["build", "--roster", str(roster), "--out", str(tmp_path / "dossiers")],
        connectors=[ConnectorDouble(kind="self_page")],
        llm=LLMDouble(),
    )

    assert rc == 2, f"an unreadable .env is bad input, not an internal failure; got exit {rc}"
    err = capsys.readouterr().err
    assert ".env" in err, f"the operator was not told which file to fix:\n{err}"
    assert "Traceback" not in err, f"a CLI reports; it does not traceback at an operator:\n{err}"
