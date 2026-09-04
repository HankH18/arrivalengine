"""T-0b regression for D2: the harness guarantees must not depend on which paths are named.

SPEC C7 is "no test may hit the network", not "no test under `tests/`". The block, and the
`--ticket` selector with it, used to live only in `tests/conftest.py`, which pytest loads
only when a named path (or `testpaths`) leads into `tests/`. Naming any path outside it —
`pytest src/`, or a probe module at the repo root — skipped that conftest, and with it
`pytest_addoption` and all three layers of the offline block, silently.

Measured before the repair:

    $ pytest --ticket T-0 src/
    ERROR: usage: pytest [options] [file_or_dir] ...
    pytest: error: unrecognized arguments: --ticket           # exit 4

    $ pytest <probe module at the repo root>                  # exit 1
    E  AssertionError: httpx.HTTPTransport.handle_request is 'handle_request', not the
       block — the offline guarantee did not load for this invocation

Nothing escaped on the day, because `src/` holds no tests. That is not a guarantee; it is
a coincidence of the current file layout, and C7 is graded on the guarantee.

The repair is a rootdir `conftest.py`, which pytest loads for EVERY invocation whose
rootdir is this repo, re-exporting the hooks and fixtures from `tests/harness.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]

#: A module OUTSIDE `tests/`, written for the duration of one test and then removed. It has
#: to be inside the repo: pytest only walks conftest files from the rootdir down to the
#: named path, so a probe in /tmp would prove nothing about this repo's guarantees.
PROBE = REPO_ROOT / "_t0b_offline_probe_test.py"

PROBE_SOURCE = '''\
"""Written by tests/test_t0b_harness_scope.py. Deleted again by it. Do not commit."""

import socket

import httpx
import httpx2


def test_the_offline_block_is_installed_outside_the_tests_directory():
    for owner, attribute, expected in (
        (httpx.HTTPTransport, "handle_request", "_blocked_handle_request"),
        (httpx.AsyncHTTPTransport, "handle_async_request", "_blocked_handle_async_request"),
        (httpx2.HTTPTransport, "handle_request", "_blocked_handle_request"),
        (httpx2.AsyncHTTPTransport, "handle_async_request", "_blocked_handle_async_request"),
        (socket.socket, "connect", "_blocked_socket_connect"),
        (socket.socket, "connect_ex", "_blocked_socket_connect_ex"),
    ):
        got = getattr(owner, attribute).__name__
        assert got == expected, (
            f"{owner.__module__}.{owner.__name__}.{attribute} is {got!r}, not the block "
            "- the offline guarantee did not load for this invocation"
        )
'''


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_the_ticket_option_is_registered_for_a_path_outside_tests():
    """`--ticket` came from `tests/conftest.py`, so naming only `src/` was a usage error.

    Exit 5 ("no tests ran") is the expected, healthy answer here: `src/` holds no tests.
    Exit 4 is the defect — pytest refusing the flag because the conftest never loaded.
    """
    proc = _pytest("--ticket", "T-0", "--collect-only", "-q", "src/")
    output = proc.stdout + proc.stderr
    assert "unrecognized arguments: --ticket" not in output, output
    assert proc.returncode != 4, output


def test_the_ticket_selector_still_deselects_when_paths_are_mixed():
    """`pytest src/ tests/...` must select the same way a bare run does."""
    proc = _pytest(
        "--ticket", "T-999", "--collect-only", "-q", "src/", "tests/test_t0_harness.py"
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "tests/test_t0_harness.py::test_planted_t999_sentinel" in proc.stdout, output
    assert "test_harness_runs" not in proc.stdout, output


def test_a_test_outside_the_tests_directory_still_runs_behind_the_offline_block():
    """The guarantee itself, executed: all three layers, for an invocation naming no test dir."""
    assert not PROBE.exists(), f"{PROBE} already exists; refusing to overwrite it"
    PROBE.write_text(PROBE_SOURCE)
    try:
        proc = _pytest("-q", PROBE.name)
    finally:
        PROBE.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_blank_ticket_usage_error_also_survives_outside_tests():
    """The "never silently select everything" rule is part of the same guarantee."""
    proc = _pytest("--ticket", "", "--collect-only", "-q", "src/")
    output = proc.stdout + proc.stderr
    assert proc.returncode == 4, output
    assert "--ticket needs a ticket id" in output, output
