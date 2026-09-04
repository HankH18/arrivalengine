"""The one HTTP door out of this process (DESIGN fn-table `http/client.py`).

    async fetch_text(url) -> RawDoc | None

Everything a connector fetches goes through here, so four policies are enforced in one
place instead of ten:

* **Identity (SPEC C5).**  `User-Agent: ArrivalEngine/0.1 (+{CONTACT_EMAIL})`, read from
  `Settings` at call time.  Several of the free sources this project leans on — SEC EDGAR
  most explicitly — will block a client that does not say who it is and how to reach them.
* **Politeness (SPEC C5).**  A per-host token bucket; see `ratelimit`.
* **Cache (DESIGN §Data models).**  `.cache/http/{doc_id}.json`.  A rebuild of one
  person's dossier after a prompt change must not re-hammer ten APIs.
* **Degradation (DESIGN Decision 8).**  A 500, a timeout, a DNS failure, a body that is
  not text — every one of them is `None`.  This function has no failure mode that reaches
  its caller as an exception, because the build has to finish even when half the internet
  is down.

`fetch_text` takes ONE positional parameter, as DESIGN's function table writes it.  The
keyword-only extras exist for the two things a caller genuinely knows and this module
cannot: which `SourceKind` the citation should carry, and (for tests and for a connector
holding injected `Settings`) where the cache lives.  All of them default, so `fetch_text(url)`
is the whole contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from arrival.config import Settings, get_settings
from arrival.contracts import RawDoc, SourceKind
from arrival.http.cache import HttpRecord, read_record, write_record
from arrival.http.extract import clip, html_title, html_to_text, json_to_text, looks_like
from arrival.http.ratelimit import limiter
from arrival.util import doc_id

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "build_url", "fetch_json", "fetch_record", "fetch_text"]

log = logging.getLogger(__name__)

#: Generous enough for a slow public API, short enough that one dead host cannot hold a
#: whole fan-out open. A timeout is a `None`, not a retry: see DESIGN Decision 8.
DEFAULT_TIMEOUT_SECONDS = 15.0

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


def _request_key(url: str, method: str, json_body: Any | None) -> str:
    """The cache identity of a request.

    For a GET this is exactly the URL, so the cache file is `.cache/http/{doc_id}.json`
    with the same `doc_id` the resulting `RawDoc` carries — that is the identity DESIGN
    names.  A POST (Tavily's search API is POST-only) is not addressed by its URL alone,
    so its body joins the key; the file name is then a hash of the request, which is the
    only thing that can be correct for it.
    """
    if method.upper() == "GET" or json_body is None:
        return url
    return f"{method.upper()} {url} {json.dumps(json_body, sort_keys=True)}"


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
    full_url = build_url(url, params)
    key = _request_key(full_url, method, json_body)
    root = _cache_root(resolved, cache_dir)

    if use_cache:
        cached = read_record(root, key)
        if cached is not None:
            # A cache hit costs the remote host nothing, so it does not spend a token.
            return cached

    await limiter.acquire(full_url)

    request_headers = {
        "User-Agent": resolved.user_agent,  # SPEC C5
        "Accept": _ACCEPT,
        "Accept-Language": "en",
    }
    if headers:
        request_headers.update(headers)

    try:
        # A client per request rather than a shared one: `httpx.AsyncClient` binds to the
        # event loop it is used on, and this module is called from short-lived loops
        # (`asyncio.run` in the CLI and in tests) as well as from a long-lived server.
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=request_headers
        ) as client:
            response = await client.request(method.upper(), full_url, json=json_body)
    except Exception as exc:  # noqa: BLE001 - DESIGN Decision 8: degrade, never raise
        log.warning("fetch failed for %s: %s", full_url, exc)
        return None

    if response.status_code >= 400:
        log.warning("fetch got HTTP %s for %s", response.status_code, full_url)
        return None

    try:
        body = response.text
    except Exception as exc:  # noqa: BLE001 - an undecodable body is a miss, not a crash
        log.warning("undecodable body for %s: %s", full_url, exc)
        return None

    record = HttpRecord(
        url=str(response.url) or full_url,
        status=response.status_code,
        content_type=response.headers.get("content-type", "") or "text/html",
        body=body,
        fetched_at=datetime.now(UTC),
    )
    if use_cache:
        text, title = _extract(record)
        write_record(root, key, record, text=text, title=title)
    return record


def _extract(record: HttpRecord) -> tuple[str, str]:
    """`(text, title)` for a record: HTML -> extracted text, JSON -> passthrough."""
    content_type = record.content_type
    if looks_like(content_type, "json"):
        return clip(json_to_text(record.body)), ""
    if looks_like(content_type, "html") or looks_like(content_type, "xml"):
        return clip(html_to_text(record.body)), html_title(record.body)
    return clip(record.body.strip()), ""


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
