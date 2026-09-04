"""T-043: `fetch_record` buffered the whole response before any guard could run.

THE DEFECT.  `httpx.AsyncClient.request()` reads the entire body into memory and only then
returns, so every size guard in this codebase ran AFTER the allocation it was meant to
prevent.  `connectors/feed.py` is the clearest case: `MAX_FEED_BYTES` refuses an oversized
feed, but the refusal happens once the bytes are already on the heap, which bounds expat's
cost and not the process's memory.  A hostile endpoint -- or merely a broken one streaming
a log file with no `Content-Length` -- could make this process buffer without limit, and a
dossier build runs several of these concurrently.

THE FIX is to stream and stop, not to measure afterwards.  Three guards, in the order they
cost anything:

1. a `Content-Length` over the cap is refused before a single body byte is read;
2. the running total is checked as chunks arrive, because a chunked response carries no
   `Content-Length` and one that does may be lying;
3. the total counts CONTENT-DECODED bytes, so a small gzip body that inflates past the cap
   is refused while it inflates.

The tests below assert the memory guarantee directly where they can: the streaming double
counts how many chunks it was actually asked for, so "it stopped early" is observed rather
than inferred from a `None`.
"""

from __future__ import annotations

import asyncio
import gzip

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import client as client_module
from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_json, fetch_record, fetch_text

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://bulk.example.com/payload"
_SENTENCE = "Thornfield Loom publishes a monthly maintenance almanac."
_PAGE = f"<html><body><p>{_SENTENCE}</p></body></html>"


class _CountingStream(httpx.AsyncByteStream):
    """A body that reports how much of itself was actually asked for.

    This is the whole point of the ticket: a cap that returns `None` after reading
    everything is indistinguishable, from the caller's side, from one that stops. Only the
    origin can tell the difference, so the test stands where the origin stands.
    """

    def __init__(self, chunk: bytes, count: int) -> None:
        self.chunk = chunk
        self.count = count
        self.yielded = 0

    async def __aiter__(self):
        for _ in range(self.count):
            self.yielded += 1
            yield self.chunk

    async def aclose(self) -> None:
        return None


def _serve(monkeypatch, respond):
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        response = respond(request)
        response.request = request
        return response

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


def _body(content: bytes, **headers: str):
    def respond(request):
        return httpx.Response(
            200, headers={"content-type": "text/html", **headers}, content=content
        )

    return respond


# --- 1. the running total, observed at the origin ------------------------------------


def test_a_streaming_body_is_abandoned_once_it_passes_the_cap(monkeypatch, tmp_path):
    """The memory guarantee, stated as a fact about the ORIGIN rather than the caller.

    Without a cap this stream is read to the end and 500kB is buffered. With one, the read
    stops within a chunk or two of the limit and the rest is never requested.
    """
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 50_000)
    stream = _CountingStream(b"x" * 10_000, 50)  # 500kB offered, 50kB allowed

    def respond(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    _serve(monkeypatch, respond)

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None, (
        "a body ten times the cap is a failed fetch, like every other failure "
        "(DESIGN Decision 8)"
    )
    assert stream.yielded <= 7, (
        f"the whole body was read before it was refused: the origin was asked for "
        f"{stream.yielded} of {stream.count} chunks ({stream.yielded * 10_000} bytes) "
        "against a 50,000 byte cap. A cap that runs after the allocation is not a cap"
    )
    assert stream.yielded >= 5, "sanity: the cap must not fire before the limit is reached"


def test_a_body_under_the_cap_streams_to_completion(monkeypatch, tmp_path):
    """The control. A cap that refuses everything is not a cap either."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 50_000)
    _serve(monkeypatch, _body(_PAGE.encode()))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None and _SENTENCE in doc.text


def test_a_body_exactly_at_the_cap_is_still_served(monkeypatch, tmp_path):
    """The boundary is `>`, not `>=`: a document the size of the limit is within it."""
    padding = " ".join(["almanac"] * 200)
    page = f"<html><body><p>{_SENTENCE}</p><p>{padding}</p></body></html>"
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", len(page.encode()))
    _serve(monkeypatch, _body(page.encode()))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None and _SENTENCE in doc.text


# --- 2. Content-Length, refused before the body is read at all -----------------------


def test_a_content_length_over_the_cap_is_refused_without_reading_the_body(
    monkeypatch, tmp_path
):
    """The cheapest refusal there is. The body here is TINY and well under the cap, so the
    only thing that can produce a `None` is the header having been read and believed."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 50_000)
    _serve(monkeypatch, _body(_PAGE.encode(), **{"content-length": "999999999"}))

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None, (
        "an origin promising a gigabyte is refused on the promise; waiting to find out "
        "whether it meant it is the cost this guard exists to avoid"
    )


def test_an_unreadable_content_length_falls_back_to_the_running_total(
    monkeypatch, tmp_path
):
    """A header we cannot parse is not a promise, and must not become a free pass."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 5_000)
    _serve(monkeypatch, _body(b"<html><body><p>" + b"x" * 40_000 + b"</p></body></html>",
                              **{"content-length": "several"}))

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None


def test_a_lying_content_length_does_not_get_the_body_past_the_cap(monkeypatch, tmp_path):
    """`Content-Length: 12` on a 40kB body. The header is a claim; the total is the fact."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 5_000)
    _serve(monkeypatch, _body(b"<html><body><p>" + b"y" * 40_000 + b"</p></body></html>",
                              **{"content-length": "12"}))

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None


# --- 3. the cap counts what the process holds, not what the wire carried -------------


def test_a_small_body_that_inflates_past_the_cap_is_refused_as_it_inflates(
    monkeypatch, tmp_path
):
    """A decompression bomb is small on the wire and large in memory, and memory is what
    the cap is protecting. `aiter_bytes` un-gzips as it goes, so the running total sees the
    inflated size -- which is the only size that matters here."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 50_000)
    payload = gzip.compress(b"<html><body><p>" + b"z" * 2_000_000 + b"</p></body></html>")
    assert len(payload) < 50_000, "sanity: the compressed body is under the cap"

    def respond(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=_CountingStream(payload, 1),
        )

    _serve(monkeypatch, respond)

    assert asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path))) is None, (
        "a 2MB body arrived in under 50kB of gzip and was accepted: the cap is measuring "
        "the wire and not the heap"
    )


# --- 4. the cap belongs to the one door, not to `fetch_text` -------------------------


def test_every_entry_point_refuses_an_oversized_response(monkeypatch, tmp_path):
    """`fetch_record`'s direct callers (search, self_page) parse the body themselves, so a
    guard that only `fetch_text` applies is a guard two connectors route around."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 5_000)
    settings = settings_for(tmp_path)
    huge = b'{"bio": "' + b"w" * 40_000 + b'"}'
    _serve(monkeypatch, _body(huge, **{"content-type": "application/json"}))

    assert asyncio.run(fetch_record(_URL, settings=settings)) is None
    assert asyncio.run(fetch_json(_URL, settings=settings)) is None
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None


def test_an_oversized_response_is_not_cached_as_a_document(monkeypatch, tmp_path):
    """A refusal must not be written to disk as an answer. The second call has to reach
    the origin again -- a cap is a refusal to READ, not a verdict about the URL."""
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 5_000)
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, _body(b"<html><body><p>" + b"q" * 40_000 + b"</p></body></html>"))

    assert asyncio.run(fetch_text(_URL, settings=settings)) is None
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None
    assert len(seen) == 2, (
        "the oversized response was cached and the second call never left the process; "
        f"the origin saw {len(seen)} request(s)"
    )


def test_the_default_cap_is_generous_enough_for_a_real_document(monkeypatch, tmp_path):
    """The default is not monkeypatched here on purpose: a cap set too low is a silent
    outage across ten connectors, and no test that moves it would ever notice."""
    assert client_module.MAX_RESPONSE_BYTES >= 1_000_000, (
        "a cap under a megabyte would refuse ordinary API responses; the largest body in "
        "the recorded corpus is under 200kB and headroom is the point"
    )
    _serve(monkeypatch, _body((_PAGE + "<p>" + "x" * 500_000 + "</p>").encode()))

    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None and _SENTENCE in doc.text
