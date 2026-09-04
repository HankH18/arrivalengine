"""TASKS T-1 acceptance 1: the shared HTTP door.

`fetch_text(url)` must return a `RawDoc` with non-empty extracted text, write and then
read `.cache/http/{doc_id}.json`, advertise `User-Agent: ArrivalEngine/0.1 (+{email})`,
enforce a per-host token bucket, and have no failure mode that reaches its caller as an
exception.  The three tests the ticket names by name are `test_client_cache_hit`,
`test_client_rate_limit` and `test_client_never_raises`.

The clock is virtual, not slow.  A rate limiter tested against the wall clock either takes
the real delay (minutes, here) or asserts nothing; the only honest way to grade ">= this
much spacing" is to replace `time.monotonic` and `asyncio.sleep` with a clock the test
advances itself, and then assert on how far it moved.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import cache as cache_module
from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_json, fetch_record, fetch_text
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("T-1")

_HTML = (
    "<html><head><title>Thornfield Loom — release notes</title></head><body>"
    "<nav><a href='/about'>About</a></nav>"
    "<p>Thornfield Loom publishes a monthly maintenance almanac.</p>"
    "<script>var tracking = 1;</script>"
    "</body></html>"
)
_SENTENCE = "Thornfield Loom publishes a monthly maintenance almanac."
_URL = "https://thornfieldloom.example.com/notes"


def _serve(monkeypatch, respond):
    """Install `respond(request) -> httpx.Response` and return the recorded requests."""
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        return respond(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


def _html_response(request):
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=_HTML.encode(),
        request=request,
    )


# --- extraction and the cache ------------------------------------------------------


def test_fetch_text_extracts_prose_and_stamps_the_doc_id_the_contract_names(
    monkeypatch, tmp_path
):
    _serve(monkeypatch, _html_response)
    settings = settings_for(tmp_path)

    doc = asyncio.run(fetch_text(_URL, source_kind="self_page", settings=settings))

    assert doc is not None, "a 200 with prose in it must produce a RawDoc, not None"
    assert doc.doc_id == doc_id(_URL), "DESIGN §Interfaces: RawDoc.doc_id is sha1(url)[:16]"
    assert doc.url == _URL
    assert doc.source_kind == "self_page"
    assert doc.title == "Thornfield Loom — release notes"
    assert _SENTENCE in doc.text, "the light extractor must keep the page's actual prose"
    assert "var tracking" not in doc.text, (
        "inline JavaScript is machinery, not prose; a host must never be able to quote it"
    )
    assert "<p>" not in doc.text and "</html>" not in doc.text, (
        "markup left in RawDoc.text becomes a quote full of angle brackets in T-3"
    )
    assert 0 < len(doc.text) <= 20_000


def test_fetch_text_passes_json_through_as_readable_text(monkeypatch, tmp_path):
    payload = {"name": "Thornfield Loom", "note": "a < b", "tags": ["looms", "scheduling"]}

    def respond(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
            request=request,
        )

    _serve(monkeypatch, respond)
    doc = asyncio.run(fetch_text(_URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert "Thornfield Loom" in doc.text
    assert "a < b" in doc.text, (
        "a JSON body must not be run through the HTML extractor: it silently eats "
        "angle brackets inside string VALUES"
    )
    assert json.loads(doc.text) == payload, (
        "TASKS T-1 acceptance 1 says JSON passes through; it must still parse as the "
        "same document afterwards"
    )


def test_client_cache_hit(monkeypatch, tmp_path):
    """A second call for the same url is served from disk without touching the transport."""
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, _html_response)

    first = asyncio.run(fetch_text(_URL, settings=settings))
    assert first is not None
    assert len(seen) == 1

    cache_file = settings.cache_dir / f"{doc_id(_URL)}.json"
    assert cache_file.exists(), (
        f"TASKS T-1 acceptance 1 pins the cache file at .cache/http/{{doc_id}}.json; "
        f"nothing was written to {cache_file}"
    )

    second = asyncio.run(fetch_text(_URL, settings=settings))

    assert len(seen) == 1, (
        f"the second fetch made {len(seen) - 1} extra request(s); a cache that still "
        "talks to the origin is not a cache, and re-running a build after a prompt "
        "change would re-hammer ten APIs"
    )
    assert second is not None
    assert second.doc_id == first.doc_id
    assert second.text == first.text
    assert second.title == first.title


def test_the_cache_survives_a_new_process_reading_only_the_file(monkeypatch, tmp_path):
    """The cache is on disk, not in memory: a fresh limiter and a dead transport still hit."""
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html_response)
    warm = asyncio.run(fetch_text(_URL, settings=settings))
    assert warm is not None

    def refuse(request):
        raise AssertionError(f"the cache should have answered {request.url}")

    _serve(monkeypatch, refuse)
    cold = asyncio.run(fetch_text(_URL, settings=settings))

    assert cold is not None and cold.text == warm.text


def test_fetch_text_sends_the_user_agent_spec_c5_requires(monkeypatch, tmp_path):
    seen = _serve(monkeypatch, _html_response)
    settings = settings_for(tmp_path, contact_email="host@thornfieldloom.example.com")

    asyncio.run(fetch_text(_URL, settings=settings))

    assert seen, "no request was made"
    agent = seen[0].headers.get("user-agent", "")
    assert agent == "ArrivalEngine/0.1 (+host@thornfieldloom.example.com)", (
        f"SPEC C5: every outbound request identifies itself and how to be reached; got "
        f"{agent!r}. SEC EDGAR blocks a client that does not."
    )


# --- the per-host token bucket ------------------------------------------------------


def _virtual_clock(monkeypatch, start: float = 10_000.0):
    """Replace monotonic time and sleeping with a clock the test advances itself."""
    now = [start]

    def monotonic() -> float:
        return now[0]

    async def sleep(delay: float, *args, **kwargs):
        if delay and delay > 0:
            now[0] += delay
        return None

    monkeypatch.setattr(ratelimit_module.time, "monotonic", monotonic)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return now


def _drain(urls, settings):
    async def run():
        for url in urls:
            await fetch_record(url, settings=settings, use_cache=False)

    asyncio.run(run())


def test_client_rate_limit(monkeypatch, tmp_path):
    """Repeated requests to ONE host are spaced at that host's documented rate."""
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html_response)
    clock = _virtual_clock(monkeypatch)

    requests = 12
    start = clock[0]
    _drain([f"https://slowhost.example.com/{n}" for n in range(requests)], settings)
    elapsed = clock[0] - start

    # Default is 2 requests/second with a two-second burst allowance, so at most a
    # handful are free and every one after that costs half a second.
    floor = (requests - 6) * (1.0 / ratelimit_module.DEFAULT_RATE_PER_SEC)
    assert elapsed >= floor, (
        f"{requests} requests to one host advanced the clock only {elapsed:.2f}s; at "
        f"{ratelimit_module.DEFAULT_RATE_PER_SEC}/s with a burst allowance that is at "
        f"least {floor:.2f}s. A limiter that never sleeps is not a limiter."
    )


def test_the_rate_limit_is_per_host_so_a_wide_fan_out_is_not_serialised(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html_response)
    clock = _virtual_clock(monkeypatch)

    start = clock[0]
    _drain([f"https://host{n}.example.com/page" for n in range(12)], settings)
    spread = clock[0] - start

    assert spread == 0.0, (
        f"12 requests across 12 different hosts waited {spread:.2f}s. The reason to slow "
        "down is that one SERVER is being asked for too much; a global limiter would make "
        "T-6's ten-connector fan-out pointlessly serial."
    )


def test_a_host_with_a_published_allowance_is_throttled_less_than_the_default(
    monkeypatch, tmp_path
):
    """SEC publishes 10/s; the default is 2/s. The bucket has to tell them apart."""
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html_response)

    clock = _virtual_clock(monkeypatch)
    start = clock[0]
    _drain([f"https://www.sec.gov/Archives/{n}" for n in range(12)], settings)
    sec = clock[0] - start

    clock = _virtual_clock(monkeypatch)
    start = clock[0]
    _drain([f"https://other.example.com/{n}" for n in range(12)], settings)
    default = clock[0] - start

    assert ratelimit_module.rate_for_host("efts.sec.gov") == 10.0, (
        "the rate is matched on domain SUFFIX, so efts.sec.gov and www.sec.gov must "
        "share sec.gov's published allowance"
    )
    assert sec < default, (
        f"sec.gov ({sec:.2f}s) was not throttled less than an unknown host "
        f"({default:.2f}s); the documented per-host rates are not being applied"
    )


# --- degradation: no failure reaches the caller as an exception ---------------------


def test_client_never_raises(monkeypatch, tmp_path):
    """A 500, a timeout, a dead connection and an empty body are all `None`."""
    settings = settings_for(tmp_path)

    def five_hundred(request):
        return httpx.Response(500, content=b"upstream is unwell", request=request)

    _serve(monkeypatch, five_hundred)
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None, "a 500 must be None"

    def timeout(request):
        raise httpx.ReadTimeout("too slow", request=request)

    _serve(monkeypatch, timeout)
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None, "a timeout must be None"

    def refused(request):
        raise httpx.ConnectError("name or service not known", request=request)

    _serve(monkeypatch, refused)
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None, "a dead host must be None"

    def blank(request):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"", request=request
        )

    _serve(monkeypatch, blank)
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None, (
        "DESIGN §Interfaces: RawDoc.text is never empty, so a blank page is None rather "
        "than a citation to nothing"
    )

    def not_json(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"<html>not the json you were promised</html>",
            request=request,
        )

    _serve(monkeypatch, not_json)
    assert asyncio.run(fetch_json(_URL, settings=settings)) is None, (
        "an unparseable body is a miss, not a crash"
    )


def test_fetch_text_returns_none_for_a_url_it_cannot_even_parse(monkeypatch, tmp_path):
    """A connector building a url out of scraped page text can hand in a broken one.

    `urlsplit` raises `ValueError: Invalid IPv6 URL` on this, and the rate limiter reads
    the host before the request is attempted — so this is the one input that reaches the
    limiter without ever reaching httpx.
    """
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html_response)

    assert asyncio.run(fetch_text("http://[::1/broken", settings=settings)) is None
    assert asyncio.run(fetch_text("", settings=settings)) is None


def test_a_corrupt_cache_file_is_a_miss_and_never_an_exception(monkeypatch, tmp_path):
    """Half a written JSON file after a killed build must not fail a research run."""
    settings = settings_for(tmp_path)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    path = settings.cache_dir / f"{doc_id(_URL)}.json"

    for corruption in (
        '{"http": {"status": "ok", "body": "<p>hi</p>"}}',  # status is not an int
        '{"http": {"status": {"a": 1}, "body": "hi"}}',  # status is not even scalar
        '{"http": {"status": 200, "body": 17}}',  # body is not a string
        '{"url": "x", "text": ',  # truncated mid-write
        "[]",  # valid JSON, wrong shape
        "",  # zero bytes
    ):
        path.write_text(corruption, encoding="utf-8")
        assert cache_module.read_record(settings.cache_dir, _URL) is None, (
            f"read_record({corruption!r}) should be a cache MISS; its own docstring says "
            "'a corrupt cache file is a cache miss, never an exception'"
        )

        path.write_text(corruption, encoding="utf-8")
        seen = _serve(monkeypatch, _html_response)
        doc = asyncio.run(fetch_text(_URL, settings=settings))
        assert doc is not None and _SENTENCE in doc.text, (
            f"a corrupt cache entry ({corruption!r}) must be re-fetched, not fatal"
        )
        assert len(seen) == 1


def test_a_hand_written_rawdoc_fixture_is_readable_as_a_cache_entry(tmp_path):
    """DESIGN §Verification: "connectors are tested by pointing the cache dir at fixtures".

    A file in plain `RawDoc` shape, with no `http` envelope, has to read back as a
    document whose text is the body.
    """
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id(_URL),
                "source_kind": "self_page",
                "url": _URL,
                "title": "Recorded",
                "text": _SENTENCE,
                "published_at": None,
                "fetched_at": "2024-05-02T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)

    assert record is not None, "a hand-written RawDoc fixture must be a cache HIT"
    assert record.body == _SENTENCE
    assert record.from_cache is True
