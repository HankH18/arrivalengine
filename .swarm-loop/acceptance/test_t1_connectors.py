"""FROZEN acceptance: ticket T-1 — HTTP core and connectors.

Grades SPEC R1, C1, C2, C5, C7, DESIGN Decision 8 and the five TASKS.md T-1 acceptance
criteria against the public surface DESIGN's function table names:

    arrival.http.client.fetch_text(url) -> RawDoc | None
    arrival.connectors.all_connectors(settings) -> list[Connector]

Nothing here is scored green at baseline: `arrival.http` and `arrival.connectors` do not
exist until T-1 lands, so every test fails with ModuleNotFoundError, which is exactly
what an unbuilt feature should read as.

HOW THE NETWORK IS STUBBED, and why this way.  DESIGN's function table gives
`fetch_text(url)` a single parameter and names no injection point for a transport, a
clock or a cache directory.  Rather than invent three keyword arguments the ticket has
no reason to grow, these tests intercept at httpx's own transport boundary
(`AsyncHTTPTransport.handle_async_request`), which is the same seam DESIGN §Verification
already uses for the C7 offline rule, and which works regardless of how the client
constructs its `AsyncClient`.  The cache is redirected by chdir into `tmp_path` plus the
plausible env names, so nothing is written into the repo (see NEEDS in the authoring
report: the injection point is a real gap in the contract).

Every product import is INSIDE a test body: a module-scope import of an unbuilt module
is a collection error, which erases this file from the pass-rate denominator instead of
failing loudly.  `httpx` is imported lazily for the same reason — it is a project
dependency, not a stdlib module, and at cycle 0 there is no environment holding it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect

import pytest

# Two markers, deliberately. `t1` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t1`), and every scored metric
# selects on it. `ticket("T-1")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t1, pytest.mark.ticket("T-1")]


# --------------------------------------------------------------------------------------
# Stub plumbing.  No network, no real sleeps, no writes into the repo.
# --------------------------------------------------------------------------------------

_ENV = {
    "CONTACT_EMAIL": "frozen-harness@example.org",
    "ANTHROPIC_API_KEY": "sk-frozen-harness",
    "TAVILY_API_KEY": "tvly-frozen-harness",
    "GITHUB_TOKEN": "ghp-frozen-harness",
    "DEBUG_VIEWS": "0",
}

_HTML = (
    "<html><head><title>Pelmyre Works release notes</title></head><body>"
    "<p>Pelmyre Works publishes its release notes as plain text every Thursday.</p>"
    "</body></html>"
)
_HTML_SENTENCE = "Pelmyre Works publishes its release notes as plain text every Thursday."


def _isolate(monkeypatch, tmp_path):
    """Point configuration and the on-disk cache at a scratch directory.

    Rule: a frozen test never writes into the repo.  `.cache/http/` is documented as a
    relative path (DESIGN §Data models), so chdir covers the default; the env names
    cover a Settings-driven cache dir.  Called before the product import so a module
    that resolves its cache location at import time also lands in tmp_path.
    """
    cache = tmp_path / ".cache" / "http"
    monkeypatch.chdir(tmp_path)
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("CACHE_DIR", "ARRIVAL_CACHE_DIR", "HTTP_CACHE_DIR"):
        monkeypatch.setenv(key, str(cache))
    return cache


def _stub_transport(monkeypatch, routes, default=None):
    """Serve canned responses at httpx's real transport boundary; record every request.

    `routes` maps an exact URL string to one of
        {"status": int, "body": str, "content_type": str}   -> a response
        {"raise": "timeout" | "connect"}                    -> a transport failure
    `default` is used for any URL not in `routes`; None means "fail like an unreachable
    host", so a connector reaching for an unstubbed endpoint is a failure it must absorb
    rather than a silent success.
    """
    import httpx

    seen = []

    def _respond(request):
        seen.append(str(request.url))
        spec = routes.get(str(request.url), default)
        if spec is None:
            spec = {"raise": "connect"}
        failure = spec.get("raise")
        if failure == "timeout":
            raise httpx.ReadTimeout("frozen-harness timeout", request=request)
        if failure == "connect":
            raise httpx.ConnectError("frozen-harness connect error", request=request)
        return httpx.Response(
            spec.get("status", 200),
            headers={"content-type": spec.get("content_type", "text/html; charset=utf-8")},
            content=spec.get("body", _HTML).encode("utf-8"),
            request=request,
        )

    async def _handle_async(self, request, **kwargs):
        return _respond(request)

    def _handle_sync(self, request, **kwargs):
        return _respond(request)

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _handle_async, raising=False
    )
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _handle_sync, raising=False)
    return seen


def _no_real_sleep(monkeypatch, cap=2000):
    """Make every sleep instantaneous and record what was asked for.

    Returns the recorder.  `cap` converts a spin-loop rate limiter into a fast, named
    failure instead of a hung suite.
    """
    import time

    state = {"requested": []}

    async def _async_sleep(delay, result=None):
        state["requested"].append(float(delay or 0.0))
        if len(state["requested"]) > cap:
            raise AssertionError(f"more than {cap} sleeps requested - spin loop?")
        return result

    def _sync_sleep(delay):
        state["requested"].append(float(delay or 0.0))
        if len(state["requested"]) > cap:
            raise AssertionError(f"more than {cap} sleeps requested - spin loop?")

    monkeypatch.setattr(asyncio, "sleep", _async_sleep)
    monkeypatch.setattr(time, "sleep", _sync_sleep)
    return state


def _virtual_clock(monkeypatch, cap=2000):
    """An INJECTED clock: sleeping advances it, and it is the only clock the code sees.

    `time.monotonic` is what `asyncio`'s own `loop.time()` reads, so patching it here
    covers both a `time.monotonic()`-based token bucket and a `loop.time()`-based one.
    No wall-clock time passes, so this test cannot be slow and cannot flake on a loaded
    machine.
    """
    import time

    state = {"now": 10_000.0, "requested": []}
    epoch = 1_770_000_000.0

    def _advance(delay):
        d = float(delay or 0.0)
        state["requested"].append(d)
        if len(state["requested"]) > cap:
            raise AssertionError(f"more than {cap} sleeps requested - spin loop?")
        state["now"] += d

    async def _async_sleep(delay, result=None):
        _advance(delay)
        return result

    def _sync_sleep(delay):
        _advance(delay)

    monkeypatch.setattr(asyncio, "sleep", _async_sleep)
    monkeypatch.setattr(time, "sleep", _sync_sleep)
    monkeypatch.setattr(time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(time, "perf_counter", lambda: state["now"])
    monkeypatch.setattr(time, "time", lambda: epoch + state["now"] - 10_000.0)
    return state


async def _resolve(value):
    """Await `value` if it is awaitable. DESIGN pins `async fetch_text`; be tolerant."""
    if inspect.isawaitable(value):
        return await value
    return value


def _fetch_kwargs(fetch_text, cache_dir):
    """Pass a cache directory only if the implementation offers one."""
    try:
        params = inspect.signature(fetch_text).parameters
    except (TypeError, ValueError):
        return {}
    for name in ("cache_dir", "cache_directory", "cache_path"):
        if name in params:
            return {name: str(cache_dir)}
    return {}


def _settings(monkeypatch):
    from arrival.config import Settings

    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _person():
    from arrival.contracts import PersonRef

    return PersonRef(
        person_id="pell-marrowby",
        name="Pell Marrowby",
        details=["co-founder, Pelmyre Works", "Austin"],
    )


# --------------------------------------------------------------------------------------
# http/client.py
# --------------------------------------------------------------------------------------


def test_fetch_text_returns_a_rawdoc_whose_doc_id_is_sha1_of_the_url(monkeypatch, tmp_path):
    """T-1 acceptance 1 / DESIGN §Interfaces RawDoc: doc_id == sha1(url)[:16], text extracted."""
    cache = _isolate(monkeypatch, tmp_path)
    url = "https://frozen-a.example.org/release-notes"
    _stub_transport(monkeypatch, {url: {"body": _HTML}})

    from arrival.contracts import RawDoc
    from arrival.http.client import fetch_text

    async def _inner():
        return await _resolve(fetch_text(url, **_fetch_kwargs(fetch_text, cache)))

    doc = asyncio.run(_inner())

    assert doc is not None, "fetch_text returned None for a 200 response"
    assert isinstance(doc, RawDoc)
    assert doc.url == url
    assert doc.doc_id == hashlib.sha1(url.encode()).hexdigest()[:16]
    assert doc.text.strip(), "RawDoc.text must never be empty (DESIGN §Interfaces)"
    # Positive control for the negative assertion that follows: the body survives.
    assert _HTML_SENTENCE in " ".join(doc.text.split())
    # And the markup does not — "extracted plain text", not raw HTML.
    assert "<p>" not in doc.text and "<html>" not in doc.text


def test_fetch_text_serves_a_repeat_url_from_disk_without_touching_the_transport(
    monkeypatch, tmp_path
):
    """T-1 acceptance 1 (`test_client_cache_hit`): the second call reads the disk cache."""
    cache = _isolate(monkeypatch, tmp_path)
    url = "https://frozen-b.example.org/cached-once"
    seen = _stub_transport(monkeypatch, {url: {"body": _HTML}})

    from arrival.http.client import fetch_text

    async def _inner():
        kwargs = _fetch_kwargs(fetch_text, cache)
        first = await _resolve(fetch_text(url, **kwargs))
        after_first = list(seen)
        second = await _resolve(fetch_text(url, **kwargs))
        return first, second, after_first

    first, second, after_first = asyncio.run(_inner())

    # Control: without a real first fetch there is nothing for a cache to hit, so an
    # implementation that returns None twice must not pass this test.
    assert first is not None and second is not None
    assert after_first == [url], f"the first call should hit the transport once, saw {after_first}"
    assert seen == after_first, (
        f"the second call for {url} hit the transport again ({seen}); the disk cache "
        "documented in DESIGN §Data models is not being read"
    )
    assert second.doc_id == first.doc_id
    assert second.text == first.text


def test_fetch_text_sends_a_user_agent_naming_arrivalengine_and_the_contact_email(
    monkeypatch, tmp_path
):
    """SPEC C5 / T-1 acceptance 1: `User-Agent: ArrivalEngine/… (+{CONTACT_EMAIL})`."""
    cache = _isolate(monkeypatch, tmp_path)
    url = "https://frozen-c.example.org/ua-probe"

    import httpx

    captured = {}

    async def _handle_async(self, request, **kwargs):
        captured["user_agent"] = request.headers.get("user-agent", "")
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=_HTML.encode("utf-8"),
            request=request,
        )

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _handle_async, raising=False
    )

    from arrival.http.client import fetch_text

    async def _inner():
        return await _resolve(fetch_text(url, **_fetch_kwargs(fetch_text, cache)))

    doc = asyncio.run(_inner())

    assert doc is not None, "fetch_text returned None, so no request was ever sent"
    ua = captured.get("user_agent", "")
    assert "ArrivalEngine" in ua, f"C5: User-Agent must name the client, got {ua!r}"
    assert _ENV["CONTACT_EMAIL"] in ua, (
        f"C5: User-Agent must carry the configured contact email, got {ua!r}. "
        "It is read from CONTACT_EMAIL via arrival.config.Settings, not hard-coded."
    )


def test_fetch_text_spaces_repeated_calls_to_one_host_but_not_across_hosts(
    monkeypatch, tmp_path
):
    """SPEC C5 / T-1 acceptance 1: a PER-HOST token bucket, measured on an injected clock.

    Six distinct URLs (distinct so the disk cache cannot answer any of them) on one host
    must cost real waiting; the same six spread over six hosts must not.  The default
    budget in TASKS T-1 is 2 requests/second, so six serial requests to one host owe at
    least 1.0 s of waiting even if the bucket starts with as many as four tokens in it.
    """
    cache = _isolate(monkeypatch, tmp_path)
    same_host = [f"https://frozen-same.example.org/page-{i}" for i in range(6)]
    spread = [f"https://frozen-h{i}.example.org/page" for i in range(6)]
    _stub_transport(monkeypatch, {}, default={"body": _HTML})
    clock = _virtual_clock(monkeypatch)

    from arrival.http.client import fetch_text

    async def _fetch_all(urls):
        kwargs = _fetch_kwargs(fetch_text, cache)
        docs = []
        for url in urls:
            docs.append(await _resolve(fetch_text(url, **kwargs)))
        return docs

    async def _inner():
        start = clock["now"]
        same_docs = await _fetch_all(same_host)
        one_host_elapsed = clock["now"] - start

        start = clock["now"]
        spread_docs = await _fetch_all(spread)
        many_hosts_elapsed = clock["now"] - start
        return same_docs, spread_docs, one_host_elapsed, many_hosts_elapsed

    same_docs, spread_docs, one_host_elapsed, many_hosts_elapsed = asyncio.run(_inner())

    # Control: the limiter only means anything if the fetches actually happened.
    assert all(d is not None for d in same_docs), "same-host fetches did not succeed"
    assert all(d is not None for d in spread_docs), "cross-host fetches did not succeed"

    assert one_host_elapsed >= 1.0, (
        f"six serial requests to one host advanced the clock by {one_host_elapsed:.3f}s; "
        "C5 requires a per-host rate limit and TASKS T-1 pins the default at 2/s"
    )
    assert many_hosts_elapsed < one_host_elapsed, (
        f"six requests to six different hosts waited {many_hosts_elapsed:.3f}s, as long "
        f"as six to a single host ({one_host_elapsed:.3f}s); the limit is PER HOST, not "
        "a blanket sleep on every request"
    )


def test_fetch_text_returns_none_instead_of_raising_on_a_500_and_on_a_timeout(
    monkeypatch, tmp_path
):
    """DESIGN Decision 8 / T-1 acceptance 1 (`test_client_never_raises`): degrade, never raise."""
    cache = _isolate(monkeypatch, tmp_path)
    ok_url = "https://frozen-d.example.org/ok"
    error_url = "https://frozen-d.example.org/server-error"
    timeout_url = "https://frozen-d.example.org/slow"
    _stub_transport(
        monkeypatch,
        {
            ok_url: {"body": _HTML},
            error_url: {"status": 500, "body": "<html><body>upstream is unwell</body></html>"},
            timeout_url: {"raise": "timeout"},
        },
    )
    _no_real_sleep(monkeypatch)

    from arrival.http.client import fetch_text

    async def _inner():
        kwargs = _fetch_kwargs(fetch_text, cache)
        return (
            await _resolve(fetch_text(ok_url, **kwargs)),
            await _resolve(fetch_text(error_url, **kwargs)),
            await _resolve(fetch_text(timeout_url, **kwargs)),
        )

    ok, failed, timed_out = asyncio.run(_inner())

    # Control: a fetch_text that returns None for everything is not "never raises",
    # it is "never works", and the two must not grade the same.
    assert ok is not None and ok.text.strip(), "the healthy URL must still yield a RawDoc"
    assert failed is None, "a 500 must yield None, not a RawDoc and not an exception"
    assert timed_out is None, "a transport timeout must yield None (DESIGN Decision 8)"


# --------------------------------------------------------------------------------------
# connectors/__init__.py
# --------------------------------------------------------------------------------------


def test_all_connectors_returns_only_objects_conforming_to_the_connector_protocol(
    monkeypatch,
):
    """T-1 "Conforms to": every returned object satisfies `contracts.Connector`."""
    from arrival.connectors import all_connectors
    from arrival.contracts import Connector

    connectors = all_connectors(_settings(monkeypatch))

    assert isinstance(connectors, list)
    assert len(connectors) >= 5, (
        f"all_connectors returned {len(connectors)} connectors; TASKS T-1 acceptance 2 "
        "names nine, and a near-empty list would make every other assertion vacuous"
    )
    nonconforming = [c for c in connectors if not isinstance(c, Connector)]
    assert nonconforming == [], (
        f"these do not satisfy contracts.Connector (kind + async search): {nonconforming}"
    )


def test_all_connectors_omits_fec_and_courtlistener_while_keeping_the_display_sources(
    monkeypatch,
):
    """SPEC Q4 / R11 / C1 + T-1 acceptance 4: the withheld sources are not even built."""
    from arrival.connectors import all_connectors

    kinds = [c.kind for c in all_connectors(_settings(monkeypatch))]

    # Positive control first: without it, an empty list would satisfy the exclusions.
    required = {"search", "wikidata", "github", "edgar", "wayback"}
    assert required.issubset(set(kinds)), (
        f"all_connectors is missing display sources {sorted(required - set(kinds))}; "
        f"got {kinds}"
    )
    # Negative space: SPEC Q4's default is that FEC and CourtListener are not built, and
    # R11 forbids ever displaying them, so they must not be in the fan-out list.
    assert "fec" not in kinds, f"SPEC Q4/R11: fec connector must not be returned; got {kinds}"
    assert "courtlistener" not in kinds, (
        f"SPEC Q4/R11: courtlistener connector must not be returned; got {kinds}"
    )
    assert len(kinds) == len(set(kinds)), f"duplicate connector kinds in the list: {kinds}"


def test_every_connector_returns_an_empty_list_when_its_transport_fails(
    monkeypatch, tmp_path
):
    """T-1 acceptance 3 / DESIGN Decision 8: a dead source is `[]` and a log, never a raise."""
    _isolate(monkeypatch, tmp_path)
    # Every route fails: `default=None` makes any URL raise a connect error.
    _stub_transport(monkeypatch, {}, default=None)
    _no_real_sleep(monkeypatch)

    from arrival.connectors import all_connectors

    connectors = all_connectors(_settings(monkeypatch))
    # Control: "they all returned []" is only meaningful over a non-trivial list.
    assert len(connectors) >= 5, f"only {len(connectors)} connectors to exercise"

    person = _person()

    async def _inner():
        results = {}
        for connector in connectors:
            try:
                results[connector.kind] = await _resolve(connector.search(person, 2))
            except Exception as exc:  # noqa: BLE001 - the raise IS the failure being graded
                results[connector.kind] = exc
        return results

    results = asyncio.run(_inner())

    raised = {k: repr(v) for k, v in results.items() if isinstance(v, BaseException)}
    assert raised == {}, (
        "these connectors raised instead of degrading to [] on a transport failure: "
        f"{raised}. The build must finish even if half the internet is down."
    )
    non_empty = {k: v for k, v in results.items() if v != []}
    assert non_empty == {}, (
        f"these connectors invented documents with every transport failing: {non_empty}"
    )
