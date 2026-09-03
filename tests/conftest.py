"""Suite-wide harness: per-ticket selection and a hard offline block.

Two jobs, both shipped by T-0 and relied on by every later ticket.

1. **Ticket selection (DESIGN Decision 10).** pytest's ``-m`` matches marker *names*, not
   marker *arguments*, so ``-m 'ticket("T-0")'`` cannot work. Instead every test module
   carries ``pytestmark = pytest.mark.ticket("T-N")`` and ``--ticket T-N`` deselects
   everything whose closest ``ticket`` marker says otherwise — unmarked tests included.
   Ticket ids are ``T-0`` .. ``T-9``: single digit, no zero padding.

2. **Offline rule (SPEC C7).** An autouse fixture replaces httpx's *default* transports so
   any real request raises ``RuntimeError("network disabled in tests")``. It patches
   ``HTTPTransport.handle_request`` / ``AsyncHTTPTransport.handle_async_request`` rather
   than ``Client.send`` on purpose: the network boundary is the transport, so a test that
   supplies its own ``httpx.MockTransport`` still works (T-1 needs that to prove a 500
   yields ``None``) while nothing can reach a socket. Opt out with ``@pytest.mark.network``
   — no test in the acceptance suite makes a real request.

``tests/`` is deliberately NOT a package, so this directory is on ``sys.path`` and helpers
import as top-level modules: ``from doubles import LLMDouble``.
"""

from __future__ import annotations

import httpx
import pytest

TICKET_MARKER = "ticket"
NETWORK_MARKER = "network"
NETWORK_DISABLED_MESSAGE = "network disabled in tests"


# --------------------------------------------------------------------------
# 1. ticket marker + --ticket selection
# --------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ticket",
        action="store",
        default=None,
        metavar="TICKET_ID",
        help='Run only tests marked ticket("<id>"), e.g. --ticket T-0. '
        "Everything else is deselected, unmarked tests included.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        'ticket(id): attribute a test to a ticket id, e.g. ticket("T-0"); '
        "select with --ticket T-0",
    )
    config.addinivalue_line(
        "markers",
        "network: opt out of the offline-transport block installed by tests/conftest.py",
    )


def _ticket_ids(item: pytest.Item) -> set[str]:
    """Ticket ids on the CLOSEST ticket marker, or the empty set when unmarked.

    Closest wins so a function-level marker overrides the module's ``pytestmark`` — that
    is how a ``ticket("T-999")`` sentinel can live inside a ``ticket("T-0")`` module.
    """
    marker = item.get_closest_marker(TICKET_MARKER)
    if marker is None:
        return set()
    return {str(arg) for arg in marker.args}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    wanted = config.getoption("--ticket")
    if not wanted:
        return

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        (selected if wanted in _ticket_ids(item) else deselected).append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


# --------------------------------------------------------------------------
# 2. offline block
# --------------------------------------------------------------------------


def _blocked_handle_request(self, request: httpx.Request) -> httpx.Response:
    raise RuntimeError(NETWORK_DISABLED_MESSAGE)


async def _blocked_handle_async_request(self, request: httpx.Request) -> httpx.Response:
    raise RuntimeError(NETWORK_DISABLED_MESSAGE)


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Make every real httpx request raise. Autouse; opt out with ``@pytest.mark.network``."""
    if request.node.get_closest_marker(NETWORK_MARKER) is not None:
        yield False
        return

    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", _blocked_handle_request, raising=True
    )
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        _blocked_handle_async_request,
        raising=True,
    )
    yield True
