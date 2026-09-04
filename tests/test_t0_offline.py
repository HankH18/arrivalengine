"""T-0 acceptance 4 / SPEC C7, graded across BOTH http stacks and the socket floor.

``test_t0_harness.py`` proves the block works for ``httpx``. That was never the whole
guarantee, because this repo runs two independent HTTP stacks:

* ``httpx`` 0.28 — T-1's connectors and ``http/client.py``.
* ``httpx2`` 2.x — a SEPARATE distribution with SEPARATE transport classes
  (``httpx.HTTPTransport is httpx2.HTTPTransport`` is ``False``). ``anthropic==1.3.0``
  depends on ``httpx2``, not ``httpx``, and so does ``starlette.testclient``.

Measured before this module existed: with only ``httpx`` patched, an
``anthropic.Anthropic(...).messages.create(...)`` inside the test suite completed a round
trip to api.anthropic.com and came back with a 401. That is the single most expensive
escape in the project — the only billable, rate-limited, PII-carrying client, built by T-2
and exercised by T-2/T-6/T-7 — and it was invisible.

Every test here aims at ``.invalid`` or a closed local port, so a REGRESSION fails loudly
without the suite ever making a real outbound request.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import anthropic
import httpx
import httpx2
import pytest

from conftest import NETWORK_DISABLED_MESSAGE, network_block_targets

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]
UNRESOLVABLE = "https://arrival-engine-should-never-resolve.invalid/ping"


def _causes(exc: BaseException) -> list[BaseException]:
    chain, cursor = [], exc
    while cursor is not None:
        chain.append(cursor)
        cursor = cursor.__cause__ or cursor.__context__
    return chain


def _is_blocked(exc: BaseException) -> bool:
    return any(
        isinstance(e, RuntimeError) and NETWORK_DISABLED_MESSAGE in str(e) for e in _causes(exc)
    )


# --------------------------------------------------------------------------
# the block covers both stacks by construction
# --------------------------------------------------------------------------


def test_the_block_covers_both_http_stacks_and_the_socket():
    """Enumerated rather than assumed: a third stack arriving unguarded shows up here."""
    owners = {(owner.__module__, owner.__name__, attribute)
              for owner, attribute, _ in network_block_targets()}
    assert ("httpx", "HTTPTransport", "handle_request") in owners
    assert ("httpx", "AsyncHTTPTransport", "handle_async_request") in owners
    assert ("httpx2", "HTTPTransport", "handle_request") in owners
    assert ("httpx2", "AsyncHTTPTransport", "handle_async_request") in owners
    assert ("socket", "socket", "connect") in owners


def test_the_two_http_stacks_are_genuinely_different_classes():
    """The premise of this module. If this ever fails, one patch would have sufficed."""
    assert httpx.HTTPTransport is not httpx2.HTTPTransport
    assert httpx.AsyncHTTPTransport is not httpx2.AsyncHTTPTransport


# --------------------------------------------------------------------------
# httpx2 — the stack anthropic and starlette.testclient run on
# --------------------------------------------------------------------------


def test_httpx2_sync_is_blocked():
    with httpx2.Client() as client:
        with pytest.raises(RuntimeError, match=NETWORK_DISABLED_MESSAGE):
            client.get(UNRESOLVABLE)


async def test_httpx2_async_is_blocked():
    async with httpx2.AsyncClient() as client:
        with pytest.raises(RuntimeError, match=NETWORK_DISABLED_MESSAGE):
            await client.get(UNRESOLVABLE)


def test_httpx2_mock_transport_still_works():
    """T-8's starlette TestClient is an httpx2.Client; the block must not break it."""
    transport = httpx2.MockTransport(lambda request: httpx2.Response(500, text="nope"))
    with httpx2.Client(transport=transport) as client:
        assert client.get("https://recorded.example/x").status_code == 500


# --------------------------------------------------------------------------
# the Anthropic SDK — the reason any of this matters
# --------------------------------------------------------------------------


def test_anthropic_sdk_is_blocked():
    """T-2 ships the real client on this SDK. It must not be able to spend the key.

    The SDK wraps transport failures in ``APIConnectionError``, so the assertion is on the
    CAUSE: our RuntimeError means the request died at the transport, while a bare
    ``ConnectError`` would mean a DNS lookup actually happened.
    """
    client = anthropic.Anthropic(
        api_key="sk-ant-not-a-real-key",
        max_retries=0,
        base_url="https://arrival-engine-should-never-resolve.invalid",
    )
    with pytest.raises(anthropic.APIConnectionError) as excinfo:
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": "hi"}],
        )
    assert _is_blocked(excinfo.value), _causes(excinfo.value)


async def test_async_anthropic_sdk_is_blocked():
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-not-a-real-key",
        max_retries=0,
        base_url="https://arrival-engine-should-never-resolve.invalid",
    )
    with pytest.raises(anthropic.APIConnectionError) as excinfo:
        await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": "hi"}],
        )
    assert _is_blocked(excinfo.value), _causes(excinfo.value)


# --------------------------------------------------------------------------
# the socket floor — SPEC C7 says "no test may hit the network", not "no httpx test"
# --------------------------------------------------------------------------


def test_raw_socket_is_blocked():
    with pytest.raises(RuntimeError, match=NETWORK_DISABLED_MESSAGE):
        socket.create_connection(("127.0.0.1", 1), timeout=0.25)


def test_urllib_is_blocked():
    """``urllib`` wraps ``OSError`` into ``URLError``; a RuntimeError propagates bare.

    Either way it must not resolve. The block is deliberately a ``RuntimeError`` and not an
    ``OSError`` so it can never be swallowed by a ``except OSError: return None`` retry
    path — T-1's connectors are required to do exactly that on real network errors.
    """
    with pytest.raises((RuntimeError, urllib.error.URLError)) as excinfo:
        urllib.request.urlopen("http://127.0.0.1:1/", timeout=0.25)
    assert _is_blocked(excinfo.value), _causes(excinfo.value)


def test_unix_sockets_are_not_the_network():
    """AF_UNIX is local IPC. Blocking it would break tooling without serving C7."""
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"


# --------------------------------------------------------------------------
# scope — the block must predate every fixture, not just function-scoped ones
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _session_scoped_request() -> str:
    """A session fixture that tries HTTP, exactly like a "warm the cache" fixture would.

    pytest instantiates higher-scoped fixtures BEFORE function-scoped ones, so when the
    block lived in a function-scoped autouse fixture this reached a real DNS lookup.
    """
    try:
        with httpx.Client() as client:
            client.get(UNRESOLVABLE)
    except RuntimeError as exc:
        return f"BLOCKED: {exc}"
    except Exception as exc:  # noqa: BLE001 - the point is to report what escaped
        return f"ESCAPED: {type(exc).__name__}: {exc}"
    return "ESCAPED: the request succeeded"


def test_session_scoped_fixtures_are_blocked(_session_scoped_request):
    assert _session_scoped_request.startswith("BLOCKED"), _session_scoped_request


@pytest.fixture(scope="module")
def _module_scoped_request() -> str:
    try:
        with httpx2.Client() as client:
            client.get(UNRESOLVABLE)
    except RuntimeError as exc:
        return f"BLOCKED: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"ESCAPED: {type(exc).__name__}: {exc}"
    return "ESCAPED: the request succeeded"


def test_module_scoped_fixtures_are_blocked(_module_scoped_request):
    assert _module_scoped_request.startswith("BLOCKED"), _module_scoped_request


# --------------------------------------------------------------------------
# the escape hatch still works, for every layer
# --------------------------------------------------------------------------


@pytest.mark.network
def test_network_marker_lifts_every_layer():
    """Makes NO request — it only asserts the guards were lifted, all of them."""
    assert httpx.HTTPTransport.handle_request.__name__ != "_blocked_handle_request"
    assert httpx2.HTTPTransport.handle_request.__name__ != "_blocked_handle_request"
    assert socket.socket.connect.__name__ != "_blocked_socket_connect"


def test_the_block_is_installed_for_unmarked_tests():
    """The mirror image, proving the assertions above are not vacuous."""
    assert httpx.HTTPTransport.handle_request.__name__ == "_blocked_handle_request"
    assert httpx2.HTTPTransport.handle_request.__name__ == "_blocked_handle_request"
    assert socket.socket.connect.__name__ == "_blocked_socket_connect"


# --------------------------------------------------------------------------
# the selector must never silently select everything
# --------------------------------------------------------------------------


def test_blank_ticket_is_a_usage_error_not_a_full_run():
    """`pytest --ticket "$TICKET"` with an unset variable must NOT grade every ticket.

    Falsiness (`if not wanted`) turned an empty value into "run the whole suite" and
    reported it as the ticket's own green gate.

    ``--collect-only`` on purpose. The ``UsageError`` is raised during collection, so it
    still fires — and if a regression ever restores the old falsiness, the subprocess
    merely COLLECTS the suite instead of RUNNING it, which is what keeps this test from
    spawning itself recursively until the run times out.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--ticket", "", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "--ticket needs a ticket id" in proc.stdout + proc.stderr
