"""T-0 acceptance 3 + 4: the --ticket selector really discriminates, and the network is off.

DESIGN Decision 10 is tagged "[reasoned — NOT executed]". This module executes it: it
plants a `ticket("T-999")` sentinel in a `ticket("T-0")` module and shells out to a real
`pytest --ticket T-0 --collect-only -q` to prove the sentinel is deselected while the T-0
tests are selected. A `pytester`-based version is not available here because
`pytest_plugins` may only be declared in the rootdir conftest, and this suite's conftest
lives in `tests/`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]

# The sentinel's node id, spelled once so the assertions cannot drift from the test below.
SENTINEL_NODE = "tests/test_t0_harness.py::test_planted_t999_sentinel"
SELECTED_NODE = "tests/test_t0_util.py::test_slug_pinned_example"
UNMARKED_NODE = "tests/test_t0_unmarked_sentinel.py::test_t0_unmarked_sentinel"


# --------------------------------------------------------------------------
# the planted sentinel
# --------------------------------------------------------------------------


@pytest.mark.ticket("T-999")
def test_planted_t999_sentinel():
    """Deliberately attributed to a ticket that does not exist.

    It must be DESELECTED by `pytest --ticket T-0` even though this module's
    `pytestmark` says T-0 — the function-level marker is the closest one and wins. It
    passes when the whole suite runs, so it never turns the suite red.
    """
    assert True


# --------------------------------------------------------------------------
# acceptance 3 — selection discriminates
# --------------------------------------------------------------------------


def _collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@pytest.fixture(scope="module")
def collected_for_t0() -> subprocess.CompletedProcess[str]:
    return _collect("--ticket", "T-0")


def test_harness_runs(collected_for_t0):
    """`pytest --ticket T-0 --collect-only -q` selects T-0 and deselects the T-999 sentinel."""
    proc = collected_for_t0
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert SELECTED_NODE in proc.stdout, proc.stdout
    assert SENTINEL_NODE not in proc.stdout, proc.stdout
    assert "deselected" in proc.stdout, proc.stdout


def test_unmarked_tests_are_deselected_too(collected_for_t0):
    """Decision 10: selection is opt-in. An unmarked test is not "everyone's" test."""
    assert UNMARKED_NODE not in collected_for_t0.stdout, collected_for_t0.stdout


def test_the_sentinels_are_collectable_without_the_option():
    """Sanity: both sentinels exist and are only missing above because they were deselected."""
    proc = _collect()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert SENTINEL_NODE in proc.stdout, proc.stdout
    assert UNMARKED_NODE in proc.stdout, proc.stdout
    assert SELECTED_NODE in proc.stdout, proc.stdout


def test_selecting_another_ticket_deselects_all_of_t0():
    """`--ticket T-999` keeps only the sentinel — the selector is not hard-coded to T-0."""
    proc = _collect("--ticket", "T-999")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert SENTINEL_NODE in proc.stdout, proc.stdout
    assert SELECTED_NODE not in proc.stdout, proc.stdout


def test_ticket_option_is_registered(request: pytest.FixtureRequest):
    assert request.config.getoption("--ticket") in (None, "T-0", "T-999")


# --------------------------------------------------------------------------
# acceptance 4 — the network is disabled
# --------------------------------------------------------------------------


def test_network_disabled():
    """SPEC C7: a real sync request raises rather than reaching a socket."""
    with httpx.Client() as client:
        with pytest.raises(RuntimeError, match="network disabled in tests"):
            client.get("https://arrival-engine-should-never-resolve.invalid/ping")


async def test_network_disabled_async():
    """The same for AsyncClient — the codebase is async-heavy, so both paths must be shut."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="network disabled in tests"):
            await client.get("https://arrival-engine-should-never-resolve.invalid/ping")


def test_network_block_survives_an_explicit_default_transport():
    """Constructing the default transport by hand does not route around the block."""
    with httpx.Client(transport=httpx.HTTPTransport()) as client:
        with pytest.raises(RuntimeError, match="network disabled in tests"):
            client.get("https://arrival-engine-should-never-resolve.invalid/ping")


def test_mock_transport_still_works():
    """The block is at the network boundary, so a supplied MockTransport is untouched.

    T-1 needs this to prove `fetch_text` returns None on a 500 without touching a socket.
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="nope"))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://recorded.example/x").status_code == 500


@pytest.mark.network
def test_network_marker_opts_out_of_the_block():
    """The `network` escape hatch exists and is honoured.

    This test makes NO request — it only asserts the guard was not installed, so the
    acceptance suite still never touches the network.
    """
    assert httpx.HTTPTransport.handle_request.__name__ != "_blocked_handle_request"
    assert (
        httpx.AsyncHTTPTransport.handle_async_request.__name__
        != "_blocked_handle_async_request"
    )


def test_the_block_is_installed_for_unmarked_tests():
    """The mirror image of the test above, proving that assertion is not vacuous."""
    assert httpx.HTTPTransport.handle_request.__name__ == "_blocked_handle_request"
    assert (
        httpx.AsyncHTTPTransport.handle_async_request.__name__
        == "_blocked_handle_async_request"
    )
