"""T-082: a fetch that fails has to say what happened.

MEASURED IN THE LIVE BUILD LOG, 2026-09-04.  `client.py` degrades rather than raises
(DESIGN Decision 8), so the WARNING it writes is the entire account of why a source came
back empty. It read:

    fetch failed for https://web.archive.org/cdx/search/cdx?url=…&limit=50:

with nothing after the colon. `str(httpx.ReadTimeout())` is `""` — every `httpx` timeout
class carries an empty message — and the log line interpolated only `exc`. So the single
most common failure in a degraded build was the one an operator could learn least from,
and it was indistinguishable from a bug in the logging.

`ConnectError` and friends DO carry a message, so the type is printed beside the message
rather than instead of it, and this module pins both directions.

ANSWER KEYS.  Nothing is compared against a module this lane owns. Every expectation is a
property of `httpx`'s own exception classes — which this lane cannot write — plus the
requirement that the log name the failure at all.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_text

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://thornfieldloom.example.com/notes"


def _raise(monkeypatch, exc: Exception) -> None:
    async def handle(self, request, **_):
        raise exc

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()


def test_httpx_timeouts_really_do_stringify_to_nothing() -> None:
    """The premise, checked rather than assumed — it is a property of the dependency.

    If a future `httpx` gives its timeouts a message, this module's subject is gone and the
    fix below becomes redundant rather than wrong. Better to be told.
    """
    for exc in (httpx.ReadTimeout(""), httpx.ConnectTimeout(""), httpx.PoolTimeout("")):
        assert str(exc) == "", f"{type(exc).__name__} now carries a message: {str(exc)!r}"


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), httpx.PoolTimeout("")],
    ids=lambda exc: type(exc).__name__,
)
def test_a_timeout_names_itself_in_the_log(monkeypatch, tmp_path, caplog, exc) -> None:
    """The reported defect: a warning whose reason was the empty string."""
    _raise(monkeypatch, exc)
    with caplog.at_level(logging.WARNING, logger="arrival.http.client"):
        assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "fetch failed" in record.getMessage()
    ]
    assert warnings, f"a failed fetch logged no 'fetch failed' warning at all: {caplog.text!r}"
    for message in warnings:
        assert type(exc).__name__ in message, (
            f"the log says {message!r}. `str({type(exc).__name__}())` is the empty string, "
            "so an operator reading this line is told a url and nothing else"
        )
        assert not message.rstrip().endswith(":"), (
            f"the message still ends at the colon with no reason after it: {message!r}"
        )


def test_an_exception_that_does_carry_a_message_keeps_it(monkeypatch, tmp_path, caplog) -> None:
    """The type is printed BESIDE the message, never instead of it."""
    _raise(monkeypatch, httpx.ConnectError("nodename nor servname provided"))
    with caplog.at_level(logging.WARNING, logger="arrival.http.client"):
        assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "fetch failed" in record.getMessage()
    ]
    assert warnings, f"a failed fetch logged no warning: {caplog.text!r}"
    assert any("ConnectError" in message for message in warnings)
    assert any("nodename nor servname provided" in message for message in warnings), (
        "naming the type must not cost the message that was already there"
    )


def test_the_url_is_still_named(monkeypatch, tmp_path, caplog) -> None:
    """The half that already worked, held down so the repair cannot trade it away."""
    _raise(monkeypatch, httpx.ReadTimeout(""))
    with caplog.at_level(logging.WARNING, logger="arrival.http.client"):
        asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert any(_URL in record.getMessage() for record in caplog.records), (
        f"the failing url is no longer in the log: {caplog.text!r}"
    )
