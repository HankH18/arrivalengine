"""T-066: `write_record` broke its own contract on a body it could not encode.

THE DEFECT, measured before the fix.  `write_record`'s docstring says in as many words
that "a cache that cannot be written is not an error", and its handler was `except OSError`
alone.  But the write is

    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

and `write_text(encoding="utf-8")` is STRICT, so a lone surrogate anywhere in the payload
raises `UnicodeEncodeError` -- a `ValueError`, which is not an `OSError`.  It escaped the
handler, escaped `fetch_record`, and escaped `fetch_text`, whose module docstring promises
"one cached, rate-limited, NEVER-RAISING door to the network" (DESIGN Decision 8):

    File ".../arrival/http/cache.py", line 223, in write_record
      temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud800' in position 118:
      surrogates not allowed

WHERE THE SURROGATE COMES FROM, and why this is not an exotic input.  Every byte on the
wire is ASCII.  A remote JSON body containing the six-character escape `\\ud800` is legal
JSON; `json.loads` decodes it into a REAL lone surrogate; `extract.json_to_text`
(extract.py:210) re-serialises with `ensure_ascii=False` and so emits it raw; that string
is what `client.fetch_record` hands to `write_record` as `text`.  The whole chain is
exercised below over a mocked transport rather than asserted about.

WHAT THE FIX IS NOT.  The body is not sanitised to get past the codec.  `read_record`'s
own reasoning applies -- an entry that answers differently warm than cold is a worse defect
than the one being fixed -- so an unencodable record is simply not cached, at the price of
one re-fetch.

Also pinned here: an HTTP ERROR STATUS is not cached at all (see the last section).

Graded against the CPython exception hierarchy, the stdlib `json` module and literals.
Nothing in `arrival.http` is consulted for an expected value.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import ratelimit as ratelimit_module
from arrival.http.cache import HttpRecord, read_record, write_record
from arrival.http.client import fetch_record, fetch_text

pytestmark = pytest.mark.ticket("T-066")

#: A lone high surrogate: a code point that is a legal `str` character and has no UTF-8
#: encoding at all. This is the whole class in one character.
LONE_SURROGATE = "\ud800"

URL = "https://api.example.com/profile"


def _record(url: str = URL, body: str = "{}") -> HttpRecord:
    return HttpRecord(
        url=url,
        status=200,
        content_type="application/json",
        body=body,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _serve(monkeypatch, content: bytes, content_type: str = "application/json", status: int = 200):
    """Answer every request with these exact bytes. Returns the list of requests seen."""
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        return httpx.Response(
            status, headers={"content-type": content_type}, content=content, request=request
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


# --- 1. the answer key: which exception, and which handler could ever catch it ---------


def test_a_unicode_encode_error_is_a_value_error_and_not_an_os_error():
    """Straight out of CPython. `except OSError` provably cannot catch this; the one-word
    widening to `except (OSError, ValueError)` provably can. If this fails, the language
    changed -- nothing in this repo can affect it."""
    assert issubclass(UnicodeEncodeError, UnicodeError)
    assert issubclass(UnicodeError, ValueError)
    assert not issubclass(UnicodeEncodeError, OSError)


def test_write_text_really_does_refuse_a_lone_surrogate(tmp_path):
    """The premise the ticket rests on, executed rather than assumed: the strict codec
    under `write_text` is what raises, so the failure is real before any of `arrival`
    is involved."""
    with pytest.raises(UnicodeEncodeError):
        (tmp_path / "x.json").write_text(LONE_SURROGATE, encoding="utf-8")


def test_a_lone_surrogate_survives_json_loads_and_comes_back_out_of_dumps():
    """The source of the character, in the stdlib alone: pure-ASCII bytes on the wire
    become a real surrogate in memory, and `ensure_ascii=False` re-emits it raw."""
    wire = '{"bio": "x \\ud800 y"}'
    assert wire.isascii(), "the specimen has to be ASCII on the wire or it proves nothing"
    parsed = json.loads(wire)
    assert LONE_SURROGATE in parsed["bio"]
    assert LONE_SURROGATE in json.dumps(parsed, ensure_ascii=False)


# --- 2. write_record keeps its documented promise -------------------------------------


def test_write_record_does_not_raise_on_an_unencodable_text(tmp_path):
    """The headline. "A cache that cannot be written is not an error" -- all of it, not
    just the `OSError` half."""
    write_record(tmp_path, URL, _record(), text=LONE_SURROGATE, title="")


def test_write_record_does_not_raise_on_an_unencodable_title(tmp_path):
    """`title` reaches the same `json.dumps`; the defect is a property of the payload,
    not of one field."""
    write_record(tmp_path, URL, _record(), text="fine", title=LONE_SURROGATE)


def test_write_record_does_not_raise_on_an_unencodable_body(tmp_path):
    """And so does the stored response body."""
    write_record(tmp_path, URL, _record(body=LONE_SURROGATE), text="fine", title="")


def test_an_unwritable_record_is_a_miss_rather_than_a_mangled_entry(tmp_path):
    """Not cached is the right answer; cached-but-rewritten is not. A body altered to get
    past the codec would answer differently warm than cold, which `read_record`'s own
    comments call the worse defect."""
    write_record(tmp_path, URL, _record(), text=LONE_SURROGATE, title="")

    assert read_record(tmp_path, URL) is None


def test_a_failed_write_leaves_no_partial_temp_file_behind(tmp_path):
    """`write_text` opens with "w", so the strict codec fails PART WAY THROUGH and leaves
    a truncated `.json.tmp`. Nothing ever reads one, but one per failure accumulates
    forever."""
    write_record(tmp_path, URL, _record(), text=LONE_SURROGATE, title="")

    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert leftovers == [], f"a failed cache write left files behind: {leftovers}"


def test_an_ordinary_record_is_still_written_and_read_back(tmp_path):
    """POSITIVE CONTROL. Every assertion above is satisfied by a `write_record` that does
    nothing at all; this is the one that fails if the fix were "return early"."""
    write_record(tmp_path, URL, _record(body='{"ok": 1}'), text="hello world", title="Hi")

    stored = read_record(tmp_path, URL)
    assert stored is not None, "the cache stopped storing anything"
    assert stored.body == '{"ok": 1}'
    assert stored.from_cache is True
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]


# --- 3. the whole chain, from the wire ------------------------------------------------


#: Legal JSON, entirely ASCII, whose decoded form holds a character UTF-8 cannot encode.
SURROGATE_BODY = b'{"bio": "Chief of staff \\ud800 at Northgate Labs"}'


def test_fetch_record_survives_a_json_body_carrying_an_escaped_surrogate(
    monkeypatch, tmp_path
):
    """The measured chain end to end: wire -> json.loads -> json_to_text -> write_record.

    `fetch_record` is DESIGN Decision 8's never-raising door. Before the fix this call
    raised `UnicodeEncodeError` out of the cache write, from a response that was pure
    ASCII on the wire and perfectly valid JSON.
    """
    assert SURROGATE_BODY.isascii()
    seen = _serve(monkeypatch, SURROGATE_BODY)

    record = asyncio.run(fetch_record(URL, settings=settings_for(tmp_path)))

    assert len(seen) == 1, "the transport was not exercised, so this measured nothing"
    assert record is not None, (
        "a valid JSON response came back as no record at all; degrading is for failures, "
        "and this response did not fail"
    )
    assert record.body == SURROGATE_BODY.decode()


def test_fetch_text_survives_the_same_body(monkeypatch, tmp_path):
    """The caller connectors actually use. The exception escaped this far too."""
    _serve(monkeypatch, SURROGATE_BODY)

    doc = asyncio.run(fetch_text(URL, settings=settings_for(tmp_path)))

    assert doc is not None
    assert "Northgate Labs" in doc.text


def test_the_unencodable_response_is_simply_not_cached(monkeypatch, tmp_path):
    """It is a miss, and a miss costs exactly one re-fetch per call. That is the whole
    price of the fix, and it is worth stating out loud."""
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, SURROGATE_BODY)

    asyncio.run(fetch_record(URL, settings=settings))
    asyncio.run(fetch_record(URL, settings=settings))

    assert len(seen) == 2, "an entry that could not be written was somehow served warm"
    cache_root = Path(settings.cache_dir)
    written = sorted(p.name for p in cache_root.iterdir()) if cache_root.is_dir() else []
    assert written == [], f"a record that cannot be encoded was written anyway: {written}"


def test_an_ordinary_response_is_cached_and_the_second_call_costs_nothing(
    monkeypatch, tmp_path
):
    """POSITIVE CONTROL for the two tests above: with an encodable body the cache does
    fill and the transport is hit once. Without this, "nothing was cached" would be
    consistent with a cache that never works."""
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, b'{"bio": "Chief of staff at Northgate Labs"}')

    first = asyncio.run(fetch_record(URL, settings=settings))
    second = asyncio.run(fetch_record(URL, settings=settings))

    assert first is not None and second is not None
    assert len(seen) == 1, "the cache did not serve the second call"
    assert second.from_cache is True
    assert len(list(Path(settings.cache_dir).iterdir())) == 1


# --- 4. an HTTP error status is not cached at all -------------------------------------
#
# A live build logged `fetch got HTTP 429 for https://nabeelqu.co`, and with negative
# entries living 900 seconds the worry was that a rate-limited host would be frozen out
# for fifteen minutes. It is not: `fetch_record` returns at `status >= 400` BEFORE the
# body is read and before any `write_record` call, so no entry of any kind is written.
# That is the right answer for a 429 -- "come back later" is not "there is nothing here"
# -- and these pin it so a later change to the negative-caching policy cannot quietly
# start persisting it.


@pytest.mark.parametrize("status", [429, 500, 503, 404])
def test_an_error_status_is_never_written_to_the_cache(monkeypatch, tmp_path, status):
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, b"<html>slow down</html>", "text/html", status=status)

    first = asyncio.run(fetch_record(URL, settings=settings))
    second = asyncio.run(fetch_record(URL, settings=settings))

    assert first is None and second is None
    cache_root = Path(settings.cache_dir)
    written = sorted(p.name for p in cache_root.iterdir()) if cache_root.is_dir() else []
    assert written == [], f"HTTP {status} was persisted as a cache entry: {written}"
    assert len(seen) == 2, (
        f"the second request to a host that answered {status} was served from the cache. "
        "A 429 means 'come back later'; caching it as a negative would keep a "
        "rate-limited host dead for the whole negative TTL"
    )
