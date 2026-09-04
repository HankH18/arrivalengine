"""The shared HTTP core: one cached, rate-limited, never-raising door to the network.

`from arrival.http.client import fetch_text` is the surface DESIGN's function table names;
these re-exports exist so a caller does not have to know which submodule a helper lives in.
"""

from __future__ import annotations

from arrival.http.client import (
    DEFAULT_TIMEOUT_SECONDS,
    build_url,
    fetch_all_text,
    fetch_json,
    fetch_record,
    fetch_text,
)
from arrival.http.extract import MAX_TEXT_CHARS, clip, html_title, html_to_text
from arrival.http.ratelimit import DEFAULT_RATE_PER_SEC, HOST_RATE_PER_SEC, limiter

__all__ = [
    "DEFAULT_RATE_PER_SEC",
    "DEFAULT_TIMEOUT_SECONDS",
    "HOST_RATE_PER_SEC",
    "MAX_TEXT_CHARS",
    "build_url",
    "clip",
    "fetch_all_text",
    "fetch_json",
    "fetch_record",
    "fetch_text",
    "html_title",
    "html_to_text",
    "limiter",
]
