"""Serve HAND-WRITTEN AMBIGUOUS corpora at httpx's transport boundary.

WHY THIS EXISTS BESIDE `t1_recorded`.  The recorded corpus in `tests/fixtures/http/` is a
*happy path*: one Wikidata candidate, one OpenAlex author, one plausible page per title.
A connector that treats "the name matched" as "this is the person" is INVISIBLE against a
corpus that only ever offers one candidate — there is nothing for it to get wrong.  The
false-attribution defects (T-017/T-018/T-019/T-020) all live in the branch that has to
choose, so grading them needs a corpus that presents the choice: two candidates, a
same-name stranger, an organisation the member is not an officer of.

Those corpora are written inline in the test modules rather than added to
`tests/fixtures/http/`, because `t1_recorded.fixture_path` requires EXACTLY ONE
`{kind}_*.json` per connector and raises if a second appears.

The seam is the same one `t1_recorded.install_transport` and the frozen suite use —
`httpx.AsyncHTTPTransport.handle_async_request` — so nothing here routes around the
project's offline block (SPEC C7).  The difference is only that routing is a Python
function rather than a table of recorded urls, which is what lets a test answer the same
endpoint differently depending on the parameters the connector chose.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from t1_recorded import install_transport, load, no_real_sleep, settings_for

from arrival.connectors import all_connectors
from arrival.contracts import PersonRef, RawDoc
from arrival.http.ratelimit import limiter

__all__ = ["MEMBER", "install_router", "parts", "search", "search_recorded"]

#: The same synthetic subject the recorded corpus uses, so a reader comparing a recorded
#: test with an ambiguous one is looking at one person throughout.
MEMBER = PersonRef(
    person_id="marisol-quennebeck",
    name="Marisol Quennebeck",
    details=[
        "co-founder, Thornfield Loom",
        "Providence, Rhode Island",
        "https://thornfieldloom.example.com/",
    ],
)

Router = Callable[[httpx.Request], Any]


def parts(request: httpx.Request) -> tuple[str, dict[str, str]]:
    """`(path, query)` of a request, with the path left PERCENT-ENCODED.

    `httpx.URL.path` percent-DECODES, which would silently turn a `%2F` in a page title
    back into a path separator and make the "a slash in a title must be encoded" test
    unable to tell a correct connector from a broken one.
    """
    split = urlsplit(str(request.url))
    return split.path, dict(parse_qsl(split.query, keep_blank_values=True))


def install_router(monkeypatch: Any, router: Router) -> list[str]:
    """Answer every request with `router(request)`. Returns the live list of urls asked for.

    A `dict`/`list` return is served as JSON, a `str` as HTML, and `None` as a 404 — the
    honest answer for a url the corpus never recorded, and the one the real APIs give for
    a page that does not exist.
    """
    requested: list[str] = []

    async def handle(self: Any, request: httpx.Request, **_: Any) -> httpx.Response:
        requested.append(str(request.url))
        payload = router(request)
        if payload is None:
            return httpx.Response(404, content=b"no such resource", request=request)
        if isinstance(payload, (dict, list)):
            return httpx.Response(
                200,
                headers={"content-type": "application/json; charset=utf-8"},
                content=json.dumps(payload).encode("utf-8"),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=str(payload).encode("utf-8"),
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    limiter.reset()
    return requested


def search(
    kind: str,
    router: Router,
    monkeypatch: Any,
    tmp_path: Path,
    budget: int = 5,
    person: PersonRef | None = None,
) -> tuple[list[RawDoc], list[str]]:
    """Drive one connector against `router`. Returns (docs, urls asked for)."""
    requested = install_router(monkeypatch, router)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path)
    found = [c for c in all_connectors(settings) if getattr(c, "kind", None) == kind]
    assert found, f"all_connectors() returned no connector with kind {kind!r}"
    docs = asyncio.run(found[0].search(person or MEMBER, budget))
    return docs, requested


def search_recorded(
    kind: str,
    monkeypatch: Any,
    tmp_path: Path,
    budget: int = 5,
) -> tuple[list[RawDoc], list[str]]:
    """Drive one connector against its own `tests/fixtures/http/{kind}_*.json` recording.

    Two of the four false-attribution defects are already present in the RECORDED corpus
    (a Wikipedia page about the company, a nonprofit whose board the member is not on),
    because the lane that recorded them wrote the wrong-entity response down on purpose.
    Those need no hand-written ambiguity — only an assertion nobody had made yet.
    """
    recording = load(kind)
    requested = install_transport(monkeypatch, recording)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path)
    found = [c for c in all_connectors(settings) if getattr(c, "kind", None) == kind]
    assert found, f"all_connectors() returned no connector with kind {kind!r}"
    docs = asyncio.run(found[0].search(recording.person, budget))
    return docs, requested
