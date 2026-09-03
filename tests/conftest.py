"""Suite-wide harness: per-ticket selection and a hard offline block.

Two jobs, both shipped by T-0 and relied on by every later ticket.

1. **Ticket selection (DESIGN Decision 10).** pytest's ``-m`` matches marker *names*, not
   marker *arguments*, so ``-m 'ticket("T-0")'`` cannot work. Instead every test module
   carries ``pytestmark = pytest.mark.ticket("T-N")`` and ``--ticket T-N`` deselects
   everything whose closest ``ticket`` marker says otherwise — unmarked tests included.
   Ticket ids are ``T-0`` .. ``T-9``: single digit, no zero padding. A *blank* ``--ticket``
   is a ``UsageError``, never "select everything": a selector that silently selects the
   whole repo turns a ticket gate into a lie.

2. **Offline rule (SPEC C7).** The block is installed in ``pytest_configure`` — i.e.
   BEFORE collection, so a module that does HTTP at import time and a session- or
   module-scoped fixture that does HTTP are both covered. (A function-scoped autouse
   fixture is instantiated *after* every higher-scoped one, so it cannot guard them.)

   Three layers, because the repo now runs two independent HTTP stacks:

   * ``httpx`` (0.28) — what T-1's connectors and ``http/client.py`` use.
   * ``httpx2`` (2.x) — a SEPARATE distribution with SEPARATE transport classes
     (``httpx.HTTPTransport is httpx2.HTTPTransport`` is ``False``). ``anthropic`` and
     ``starlette.testclient`` both run on it, so patching only ``httpx`` leaves the one
     billable, PII-carrying client wide open.
   * ``socket`` — the floor under everything else (``urllib``, ``requests``, any vendored
     SDK). SPEC C7 says *no test may hit the network*, not "no httpx test".

   Transports are patched at ``HTTPTransport.handle_request`` /
   ``AsyncHTTPTransport.handle_async_request`` rather than ``Client.send`` on purpose: the
   network boundary is the transport, so a test that supplies its own ``MockTransport``
   still works (T-1 needs that to prove a 500 yields ``None``). The socket guard only
   refuses ``AF_INET``/``AF_INET6``; ``AF_UNIX`` is local IPC, not the network.

   Opt out with ``@pytest.mark.network`` — no test in the acceptance suite makes a real
   request.

``tests/`` is deliberately NOT a package, so this directory is on ``sys.path`` and helpers
import as top-level modules: ``from doubles import LLMDouble``.
"""

from __future__ import annotations

import socket

import httpx
import pytest

try:  # anthropic + starlette.testclient run on this; httpx does NOT provide it
    import httpx2
except ImportError:  # pragma: no cover - httpx2 is a hard dependency of `anthropic`
    httpx2 = None

TICKET_MARKER = "ticket"
NETWORK_MARKER = "network"
NETWORK_DISABLED_MESSAGE = "network disabled in tests"

_MONKEYPATCH_ATTR = "_arrival_network_monkeypatch"


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

    # Installed here, not in an autouse fixture: fixtures of a wider scope than `function`
    # (and module import during collection) both run before any function-scoped fixture,
    # so a function-scoped guard has holes a session-scoped "warm the cache" fixture in
    # T-1/T-6 would walk straight through.
    monkeypatch = pytest.MonkeyPatch()
    _install_block(monkeypatch)
    setattr(config, _MONKEYPATCH_ATTR, monkeypatch)


def pytest_unconfigure(config: pytest.Config) -> None:
    monkeypatch = getattr(config, _MONKEYPATCH_ATTR, None)
    if monkeypatch is not None:
        monkeypatch.undo()
        delattr(config, _MONKEYPATCH_ATTR)


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
    if wanted is None:  # `is None`, NOT falsiness: "" must not mean "run everything"
        return
    if not wanted.strip():
        raise pytest.UsageError(
            "--ticket needs a ticket id such as T-0; got an empty value. "
            "Omit the flag entirely to run the whole suite."
        )

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


def _blocked_handle_request(self, request):
    raise RuntimeError(NETWORK_DISABLED_MESSAGE)


async def _blocked_handle_async_request(self, request):
    raise RuntimeError(NETWORK_DISABLED_MESSAGE)


_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)


def _blocked_socket_connect(self, address):
    if self.family in _INET_FAMILIES:
        raise RuntimeError(f"{NETWORK_DISABLED_MESSAGE} (socket.connect to {address!r})")
    return _ORIGINAL_SOCKET_CONNECT(self, address)


def _blocked_socket_connect_ex(self, address):
    if self.family in _INET_FAMILIES:
        raise RuntimeError(f"{NETWORK_DISABLED_MESSAGE} (socket.connect_ex to {address!r})")
    return _ORIGINAL_SOCKET_CONNECT_EX(self, address)


_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex


def network_block_targets() -> list[tuple[type, str, object]]:
    """``(owner, attribute, replacement)`` for every patch point in the offline block.

    Public so the acceptance suite can assert the list covers BOTH http stacks rather than
    re-deriving it — a second stack arriving unguarded is exactly the T-0 defect that let
    the Anthropic SDK reach the internet from inside the suite.
    """
    targets: list[tuple[type, str, object]] = []
    for module in (httpx, httpx2):
        if module is None:  # pragma: no cover - httpx2 is always installed here
            continue
        targets.append((module.HTTPTransport, "handle_request", _blocked_handle_request))
        targets.append(
            (module.AsyncHTTPTransport, "handle_async_request", _blocked_handle_async_request)
        )
    targets.append((socket.socket, "connect", _blocked_socket_connect))
    targets.append((socket.socket, "connect_ex", _blocked_socket_connect_ex))
    return targets


_ORIGINALS: dict[tuple[type, str], object] = {}


def _install_block(monkeypatch: pytest.MonkeyPatch) -> None:
    for owner, attribute, replacement in network_block_targets():
        _ORIGINALS.setdefault((owner, attribute), getattr(owner, attribute))
        monkeypatch.setattr(owner, attribute, replacement, raising=True)


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest):
    """Report whether the offline block is live, and lift it for ``@pytest.mark.network``.

    The block itself is installed in ``pytest_configure``; this fixture only carves the
    documented escape hatch out of it for the duration of one test.
    """
    if request.node.get_closest_marker(NETWORK_MARKER) is None:
        yield True
        return

    restore = pytest.MonkeyPatch()
    try:
        for owner, attribute, _ in network_block_targets():
            restore.setattr(owner, attribute, _ORIGINALS[(owner, attribute)], raising=True)
        yield False
    finally:
        restore.undo()
