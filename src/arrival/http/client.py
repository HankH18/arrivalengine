"""The one HTTP door out of this process (DESIGN fn-table `http/client.py`).

    async fetch_text(url) -> RawDoc | None

Everything a connector fetches goes through here, so four policies are enforced in one
place instead of ten:

* **Identity (SPEC C5).**  `User-Agent: ArrivalEngine/0.1 (+{CONTACT_EMAIL})`, read from
  `Settings` at call time.  Several of the free sources this project leans on — SEC EDGAR
  most explicitly — will block a client that does not say who it is and how to reach them.
* **Politeness (SPEC C5).**  A per-host token bucket; see `ratelimit`.
* **Cache (DESIGN §Data models).**  `.cache/http/{doc_id}.json`.  A rebuild of one
  person's dossier after a prompt change must not re-hammer ten APIs.  Keyed by
  everything that can change the answer — url, method, body, and the caller's headers —
  and split into two lifetimes: an answer that yielded usable text is durable, one that
  yielded nothing expires quickly so a transient failure heals instead of being frozen
  into the corpus.  See `POSITIVE_TTL_SECONDS` / `NEGATIVE_TTL_SECONDS` below.
* **Size (T-043).**  The response is STREAMED and refused past `MAX_RESPONSE_BYTES`, so a
  hostile or merely broken endpoint cannot make this process buffer an unbounded body.  A
  cap that runs after `client.request()` — which is what every caller-side guard, including
  `connectors/feed.py`'s `MAX_FEED_BYTES`, actually was — bounds parser cost and not memory.
* **Degradation (DESIGN Decision 8).**  A 500, a timeout, a DNS failure, an oversized
  response, a body that is not text — every one of them is `None`.  This function has no
  failure mode that reaches its caller as an exception, because the build has to finish
  even when half the internet is down.  "Not text" is decided on the decoded body as well
  as the declared type (`_is_not_text`), and the DECODE is done against the document's own
  declared codec (`extract.decode_body`): httpx reads only the header's `charset`, so a
  page in a legacy encoding used to arrive as U+FFFD and be thrown away as if it were a
  PNG (T-044).  Deciding "not text" from a decode known to have used the wrong codec is
  how a real document gets discarded; deciding it from a correct decode is the promise.

`fetch_text` takes ONE positional parameter, as DESIGN's function table writes it.  The
keyword-only extras exist for the two things a caller genuinely knows and this module
cannot: which `SourceKind` the citation should carry, and (for tests and for a connector
holding injected `Settings`) where the cache lives.  All of them default, so `fetch_text(url)`
is the whole contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from arrival.config import Settings, get_settings
from arrival.contracts import RawDoc, SourceKind
from arrival.http.cache import HttpRecord, read_record, write_record
from arrival.http.extract import (
    clip,
    decode_body,
    detect_encoding,
    html_title,
    html_to_text,
    is_binary_type,
    json_to_text,
    looks_binary,
    looks_like,
    sniff_content_type,
)
from arrival.http.ratelimit import limiter
from arrival.util import doc_id

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "NEGATIVE_TTL_SECONDS",
    "POSITIVE_TTL_SECONDS",
    "build_url",
    "fetch_json",
    "fetch_record",
    "fetch_text",
]

log = logging.getLogger(__name__)

#: Generous enough for a slow public API, short enough that one dead host cannot hold a
#: whole fan-out open. A timeout is a `None`, not a retry: see DESIGN Decision 8.
DEFAULT_TIMEOUT_SECONDS = 15.0

# --- how long a cache entry is served for (T-025) ------------------------------------
#
# THE DEFECT.  There was no expiry of any kind. `fetched_at` was written and never
# compared against anything, so an empty or unextractable 200 -- a single-page app caught
# before its bundle ran, a page behind a momentary interstitial -- was cached PERMANENTLY.
# Measured: one JS-shell response made that URL return `None` with zero further requests
# for the life of the cache directory, and the research build is designed around
# re-running against a warm cache, so the damage compounds with use.
#
# THE POLICY, and why it is asymmetric.  The two classes of answer have opposite costs:
#
# * A response that YIELDED USABLE TEXT is durable. Expiring successes is the one change
#   that would undo the reason the cache exists -- a rebuild after a prompt change must
#   not re-hammer ten APIs for every person on the roster -- and it would also rot
#   DESIGN §Verification's offline path, where connectors are tested by pointing the cache
#   directory at recorded fixtures carrying whatever `fetched_at` they were recorded with.
#   A URL's content drifting is not a risk this product carries: a dossier is built once
#   and reviewed fact by fact against its source URLs.
# * A response that yielded NOTHING is kept only briefly. Long enough that a second
#   connector reaching the same dead URL inside one run is still free, short enough that a
#   transient failure heals by itself rather than being frozen into the corpus.
#
#: Successes never expire. Set to a number of seconds to age them out too -- the mechanism
#: is the same one the negative class uses; only this default declines to spend it.
POSITIVE_TTL_SECONDS: float | None = None

#: Fifteen minutes: longer than a build run's fan-out, shorter than a working session.
NEGATIVE_TTL_SECONDS: float | None = 900.0

# --- how much of a response this process will hold (T-043) ---------------------------
#
# THE DEFECT.  There was no cap of any kind. `client.request()` buffers the WHOLE body
# before returning, so every caller-side guard ran after the allocation it was meant to
# prevent -- `connectors/feed.py`'s `MAX_FEED_BYTES` refuses an oversized feed only once it
# is already in memory, which bounds parser cost and not memory. A hostile endpoint, or
# merely a broken one streaming a log file, made this process buffer without limit, and a
# fan-out runs several of these at once.
#
# THE FIX is to read the response as a stream and stop, rather than to measure it after the
# fact. `Content-Length` is checked first when the origin offers one (a refusal that costs
# no download at all), and the running total is checked as the chunks arrive, because
# `Content-Length` is absent on every chunked response and is a claim in any case.
#
# The cap counts DECODED bytes -- `aiter_bytes` un-gzips as it goes -- so a small
# compressed body that expands past the cap is refused while it expands, which is the
# shape a decompression bomb actually has.
#
#: Ten megabytes: far above any page or API response these ten connectors read (the
#: largest in the recorded corpus is under 200kB), far below a size worth buffering. An
#: oversized response is a `None` like every other failure, per DESIGN Decision 8.
MAX_RESPONSE_BYTES = 10_000_000

_ACCEPT = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"


def _settings_for(settings: Settings | None) -> Settings:
    """Settings resolved AT CALL TIME.

    Never at import time: the frozen acceptance suite does not reset `get_settings`'s
    cache, and a module that snapshots a `Settings` while `CONTACT_EMAIL` is still unset
    advertises the wrong contact address for the life of the process.
    """
    return settings if settings is not None else get_settings()


def _cache_root(settings: Settings, cache_dir: str | Path | None) -> Path:
    return Path(cache_dir) if cache_dir is not None else Path(settings.cache_dir)


def build_url(url: str, params: dict[str, Any] | None = None) -> str:
    """`url` with `params` appended. None-valued params are dropped, not sent as "None"."""
    if not params:
        return url
    pairs = [(key, str(value)) for key, value in params.items() if value is not None]
    if not pairs:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(pairs)}"


def _headers_digest(headers: dict[str, str] | None) -> str:
    """A short, stable fingerprint of the headers the CALLER supplied, or "" for none.

    Only the caller's headers, never the three this module adds itself: `User-Agent`,
    `Accept` and `Accept-Language` are a function of `Settings` and are identical on every
    request, so folding them in would move every cache file for no gain.

    Hashed rather than embedded because the header block routinely holds a credential, and
    the key reaches log lines and exception messages. Names are lower-cased and sorted:
    HTTP header names are case-insensitive, so `Authorization` and `authorization` are one
    identity and must not cost two fetches.
    """
    if not headers:
        return ""
    normalised = sorted((str(name).lower(), str(value)) for name, value in headers.items())
    blob = json.dumps(normalised, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _request_key(
    url: str,
    method: str,
    json_body: Any | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """The cache identity of a request: everything that can change the answer.

    For a plain header-free GET this is exactly the URL, so the cache file is
    `.cache/http/{doc_id}.json` with the same `doc_id` the resulting `RawDoc` carries —
    that is the identity DESIGN names, and it is preserved deliberately.

    WHAT USED TO BE MISSING, both measured (T-025):

    * **The method**, whenever there was no body — `json_body is None` short-circuited it
      out — so GET, HEAD, bodyless POST and DELETE of one URL shared a file. A HEAD
      followed by a GET made ONE request and the GET was served the HEAD's empty body.
    * **The headers**, entirely. An authenticated and an anonymous fetch of the same URL
      shared a file, and because the authenticated response is the richer one, the
      collision handed an anonymous caller a payload the origin never gave it.

    A key change orphans every file written under the old scheme. That is a MISS — the
    reader looks at a path that is not there — so it costs exactly one re-fetch and can
    never surface as an error.
    """
    verb = (method or "GET").upper()
    digest = _headers_digest(headers)
    if verb == "GET" and json_body is None and not digest:
        return url
    key = f"{verb} {url}"
    if json_body is not None:
        # A POST (Tavily's search API is POST-only) is not addressed by its URL alone.
        key = f"{key} {json.dumps(json_body, sort_keys=True)}"
    if digest:
        key = f"{key} h={digest}"
    return key


def _expiry_for(*, durable: bool) -> datetime | None:
    """When an entry of this class stops being served, or None to keep it indefinitely.

    Read off the module globals at CALL time so the policy is one place a test — or an
    operator editing this file — can move, rather than a number baked into a write.
    """
    ttl = POSITIVE_TTL_SECONDS if durable else NEGATIVE_TTL_SECONDS
    if ttl is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=max(0.0, float(ttl)))


async def fetch_record(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    settings: Settings | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
) -> HttpRecord | None:
    """One rate-limited, cached, never-raising HTTP round trip. `None` on any failure.

    The raw body is what comes back, deliberately: `fetch_text` derives a `RawDoc` from it
    and `fetch_json` parses it, and neither can be reconstructed from the other's output.
    """
    resolved = _settings_for(settings)
    try:
        full_url = build_url(url, params)
        key = _request_key(full_url, method, json_body, headers)
    except Exception as exc:  # noqa: BLE001 - an unaddressable request is a miss
        # `urlencode` on an exotic param and `json.dumps` on a non-serialisable POST body
        # both raise here, upstream of every other guard.
        log.warning("cannot address %r: %s", url, exc)
        return None
    root = _cache_root(resolved, cache_dir)

    if use_cache:
        cached = read_record(root, key)
        if cached is not None:
            # The same rejection the fresh path applies, applied again here. The warm
            # answer and the cold answer for one URL have to be the SAME answer: a body
            # refused as non-text on the way in must not come back as a document on the
            # second call just because it went through the disk.
            if _is_not_text(cached.content_type, cached.body):
                return None
            # A cache hit costs the remote host nothing, so it does not spend a token.
            return cached

    request_headers = {
        "User-Agent": resolved.user_agent,  # SPEC C5
        "Accept": _ACCEPT,
        "Accept-Language": "en",
    }
    if headers:
        request_headers.update(headers)

    try:
        # Inside the guard, not before it: the limiter reads the hostname out of the url,
        # and `urlsplit` raises `ValueError: Invalid IPv6 URL` on a malformed one. A
        # connector that builds a url out of scraped page text can hand that in, and it
        # used to escape `fetch_text` as an exception instead of degrading to None.
        await limiter.acquire(full_url)
        # A client per request rather than a shared one: `httpx.AsyncClient` binds to the
        # event loop it is used on, and this module is called from short-lived loops
        # (`asyncio.run` in the CLI and in tests) as well as from a long-lived server.
        #
        # `default_encoding` is a callable (T-044): httpx otherwise decodes a response that
        # named no charset as UTF-8 with `errors="replace"`, never reading the document's
        # own `<meta charset>`, which turned an ordinary legacy-encoded page into a wall of
        # U+FFFD that `looks_binary` then discarded. The body below is decoded explicitly
        # by `decode_body`; this keeps ANY other read of `.text` on the same policy.
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=request_headers,
            default_encoding=detect_encoding,
        ) as client:
            # `send(..., stream=True)` rather than `request(...)`: the latter buffers the
            # whole body before it returns, so a size cap applied to its result would be
            # measuring an allocation it was supposed to prevent (T-043).
            request = client.build_request(method.upper(), full_url, json=json_body)
            response = await client.send(request, stream=True)
            try:
                status = response.status_code
                declared_type = response.headers.get("content-type", "")
                charset = response.charset_encoding
                resolved_url = str(response.url) or full_url
                if status >= 400:
                    # Refused before the body is read at all: a 500's body is never used.
                    log.warning("fetch got HTTP %s for %s", status, full_url)
                    return None
                content = await _read_capped(response, full_url)
            finally:
                # Releases the connection whether or not the body was read to completion.
                await response.aclose()
    except Exception as exc:  # noqa: BLE001 - DESIGN Decision 8: degrade, never raise
        log.warning("fetch failed for %s: %s", full_url, exc)
        return None

    if content is None:  # over the size cap; `_read_capped` has logged why
        return None

    try:
        body = decode_body(content, charset)
    except Exception as exc:  # noqa: BLE001 - an undecodable body is a miss, not a crash
        log.warning("undecodable body for %s: %s", full_url, exc)
        return None

    # T-026: what the body IS, rather than what the response felt like claiming.
    #
    # The old line here was `response.headers.get("content-type", "") or "text/html"` --
    # an unlabelled response was assumed to be HTML, so an unlabelled JSON payload went
    # through the HTML extractor. Measured: `{"expr": "a<b and c>d"}` came back as
    # `{"expr": "ad"}`, nine characters deleted from inside a string VALUE, and the
    # document no longer parsed as itself.
    content_type = sniff_content_type(body, declared_type)

    if _is_not_text(content_type, body):
        # The module docstring's promise, restored: "a body that is not text -- every one
        # of them is None". `httpx` decodes anything with `errors="replace"`, so this used
        # to return a RawDoc of mojibake that T-3 could quote and T-7 could display.
        log.warning("non-text body (%s) at %s", content_type or "unlabelled", resolved_url)
        if use_cache:
            _remember_non_text(root, key, resolved_url)
        return None

    record = HttpRecord(
        url=resolved_url,
        status=response.status_code,
        content_type=content_type,
        body=body,
        fetched_at=datetime.now(UTC),
    )
    if use_cache:
        text, title = _extract(record)
        # The classification T-025 turns on: a response that produced usable text is
        # durable, one that produced nothing gets the short lifetime. The BODY is kept
        # either way -- `fetch_record`'s direct callers (search, self_page) parse it
        # themselves, and an entry that answers differently warm than cold is a worse
        # defect than the one being fixed. Only the lifetime differs.
        write_record(
            root,
            key,
            record,
            text=text,
            title=title,
            expires_at=_expiry_for(durable=bool(text.strip())),
        )
    return record


async def _read_capped(response: httpx.Response, url: str) -> bytes | None:
    """The response body, or None when it is larger than `MAX_RESPONSE_BYTES` (T-043).

    Read at CALL time off the module global, so an operator -- or a test -- can move the
    cap in one place, the same way `_expiry_for` reads the TTL policy.
    """
    limit = MAX_RESPONSE_BYTES
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            promised = int(declared)
        except ValueError:
            promised = -1  # a header we cannot read is no promise; the running total wins
        if promised > limit:
            log.warning(
                "refusing %s: Content-Length %s is over the %s byte cap", url, promised, limit
            )
            return None

    # A chunked response carries no Content-Length, and one that does may be lying, so the
    # running total is the guard that actually holds. `aiter_bytes` yields CONTENT-DECODED
    # bytes, which is what the process must hold, so a gzip bomb is refused as it inflates.
    buffer = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=65_536):
        buffer += chunk
        if len(buffer) > limit:
            log.warning("refusing %s: body exceeded the %s byte cap", url, limit)
            return None
    return bytes(buffer)


def _is_not_text(content_type: str, body: str) -> bool:
    """True when this response body is not a text document (T-026).

    Two independent checks because the label and the bytes fail in different directions:
    a declared `image/png` is decisive on its own, and so is a body full of replacement
    characters that the origin labelled `text/html` -- which is the common case, a CDN
    answering `text/html` for everything it does not recognise.
    """
    return is_binary_type(content_type) or looks_binary(body)


def _remember_non_text(root: Path, key: str, url: str) -> None:
    """Record that this URL answered with something that is not a document.

    The BODY is deliberately dropped and the type rewritten to `application/octet-stream`:
    there is no reason to keep megabytes of a PDF that nothing may cite, and storing the
    resolved type is what lets the read path re-derive the same rejection without needing
    the bytes. Short-lived like every other negative -- a URL that serves a PDF today may
    serve a page tomorrow.
    """
    write_record(
        root,
        key,
        HttpRecord(
            url=url,
            status=200,
            content_type="application/octet-stream",
            body="",
            fetched_at=datetime.now(UTC),
        ),
        text="",
        title="",
        expires_at=_expiry_for(durable=False),
    )


def _extract(record: HttpRecord) -> tuple[str, str]:
    """`(text, title)` for a record: HTML -> extracted text, JSON -> passthrough."""
    content_type = record.content_type
    if looks_like(content_type, "json"):
        return clip(json_to_text(record.body)), ""
    if looks_like(content_type, "html") or looks_like(content_type, "xml"):
        return clip(html_to_text(record.body)), html_title(record.body)
    return clip(record.body.strip()), ""


def _drop_surrogates(text: str) -> str:
    """Remove lone surrogates, which are decode debris and cannot survive anywhere.

    A lone surrogate has no UTF-8 form, so a string carrying one cannot be JSON-encoded,
    cannot be written to the cache (`write_record` refuses it and the miss is tested), and
    cannot be rendered — starlette encodes strict. `RawDoc.text` is a constrained string as
    of T-054, so pydantic refuses it outright and `fetch_text` raised a ValidationError at
    the caller connectors actually use.

    Dropping the whole document was the alternative and it is worse: the surrogate is one
    character of decode debris in an otherwise good page, and T-044's encoding work is
    built on exactly this judgement — a text document read imperfectly is still a text
    document. So the debris goes and the page survives, lossily and visibly.

    `errors="surrogatepass"` is deliberately NOT used here, unlike `util.doc_id`: there the
    goal is a stable key for a url we must not lose, here the goal is a string that every
    downstream consumer can actually encode.
    """
    if not text:
        return text
    return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


async def fetch_text(
    url: str,
    *,
    source_kind: SourceKind = "self_page",
    title: str | None = None,
    published_at: date | None = None,
    headers: dict[str, str] | None = None,
    settings: Settings | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RawDoc | None:
    """Fetch `url` and return it as a `RawDoc`, or `None` if anything at all went wrong.

    HTML becomes plain text through the light extractor; JSON passes through.  An empty
    document is `None` rather than a `RawDoc` with empty text: DESIGN §Interfaces says
    `text` is never empty, and a citation to a blank page is worse than no citation.
    """
    record = await fetch_record(
        url,
        headers=headers,
        settings=settings,
        cache_dir=cache_dir,
        timeout=timeout,
    )
    if record is None:
        return None

    text, extracted_title = _extract(record)
    text, extracted_title = _drop_surrogates(text), _drop_surrogates(extracted_title)
    if not text.strip():
        log.warning("no extractable text at %s", record.url)
        return None

    return RawDoc(
        doc_id=doc_id(record.url),
        source_kind=source_kind,
        url=record.url,
        title=(title if title is not None else extracted_title) or "",
        text=text,
        published_at=published_at,
        fetched_at=record.fetched_at,
    )


async def fetch_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    settings: Settings | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any | None:
    """The same round trip, parsed. `None` on a failed fetch OR an unparseable body.

    Connectors talk to JSON APIs and need the whole payload, not the 20k-clipped
    projection `fetch_text` produces, so they come through here.
    """
    record = await fetch_record(
        url,
        method=method,
        params=params,
        headers=headers,
        json_body=json_body,
        settings=settings,
        cache_dir=cache_dir,
        timeout=timeout,
    )
    if record is None:
        return None
    try:
        return json.loads(record.body)
    except ValueError as exc:
        log.warning("non-JSON body from %s: %s", url, exc)
        return None


async def fetch_all_text(
    urls: list[str],
    *,
    source_kind: SourceKind = "self_page",
    settings: Settings | None = None,
    cache_dir: str | Path | None = None,
) -> list[RawDoc]:
    """Fetch several URLs concurrently, dropping the ones that failed.

    The per-host bucket is what keeps this polite: hitting ten different hosts at once is
    fine and is the point, hitting one host ten times at once is what `reserve` spaces out.
    """
    results = await asyncio.gather(
        *(
            fetch_text(url, source_kind=source_kind, settings=settings, cache_dir=cache_dir)
            for url in urls
        )
    )
    return [doc for doc in results if doc is not None]
