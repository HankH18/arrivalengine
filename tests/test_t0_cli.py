"""T-0 acceptance 7: the CLI skeleton T-6 will fill.

`main` is called in-process with injected doubles — never through a subprocess and never
over the network (SPEC C7). These tests pin the signature so T-6 cannot quietly change it.
"""

from __future__ import annotations

import inspect

import pytest

from arrival.__main__ import USAGE, main

pytestmark = pytest.mark.ticket("T-0")


def test_missing_command_prints_usage_and_returns_2(capsys):
    assert main([]) == 2
    assert "usage: python -m arrival" in capsys.readouterr().err


def test_unknown_command_returns_2(capsys):
    assert main(["teleport"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'teleport'" in err
    assert "usage: python -m arrival" in err


def test_help_returns_0(capsys):
    for flag in ("-h", "--help", "help"):
        assert main([flag]) == 0
        assert "usage: python -m arrival" in capsys.readouterr().out


# justify-test-edit -- the assertion below was RETIRED, not weakened, and the reasoning
# is recorded because an unjustified test edit is indistinguishable from reward hacking
# after the fact.
#
# WAS: test_build_is_reserved_for_t6, asserting `"not implemented yet" in stderr`.
# REQUIREMENT IT ENCODED: `build` must fail loudly rather than pretend to work.
# WOULD IT STILL FAIL IF MY CHANGE WERE REVERTED? Yes -- and that is exactly why it goes.
#     The "change" here is T-6 landing, and the test's OWN docstring scoped it "Until T-6
#     lands". Its precondition has ended by its own terms; it is obsolete, not wrong, and
#     keeping it would require the CLI to keep lying about being unimplemented.
# THE REQUIREMENT IS PRESERVED, not dropped: `build` must still fail loudly on a bad
#     roster, and the exit-code half of the original assertion is kept verbatim.
def test_build_reports_a_missing_roster_rather_than_pretending_to_work(capsys):
    """T-6 has landed, so `build` runs. It must still fail loudly on a bad roster."""
    assert main(["build", "--roster", "x.yaml"]) == 2
    assert "roster" in capsys.readouterr().err


def test_main_accepts_injected_dependencies():
    """T-6's test_cli_build calls main([...], connectors=[...], llm=LLMDouble())."""
    signature = inspect.signature(main)
    assert list(signature.parameters) == ["argv", "connectors", "llm"]
    assert signature.parameters["argv"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("connectors", "llm"):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None
    assert signature.return_annotation in (int, "int")  # PEP 563 makes it a string
    # and it really accepts them
    assert main([], connectors=[], llm=object()) == 2


def test_usage_names_the_build_command():
    assert "build" in USAGE
    assert "--roster" in USAGE
