"""Serve `tests/fixtures/http/{kind}_*.json` at httpx's transport boundary.

WHY A RECORDED RESPONSE AND NOT A RECORDED `RawDoc`.  A fixture that stores the finished
`RawDoc` grades nothing: it asserts that a JSON file can be read back.  The whole question
TASKS T-1 acceptance 2 asks — "does this connector turn its source's real response shape
into a citation?" — lives in the parsing, so what is recorded here is the *response*, in
the envelope the real API uses, and the connector has to do the work.

WHY THIS SEAM.  `httpx.AsyncHTTPTransport.handle_async_request` is where the project's own
offline block (SPEC C7, `tests/harness.py`) sits, and it is below every layer the client
builds — headers, redirects, `AsyncClient` construction — so a connector cannot
accidentally route around it.  Patching `AsyncClient.send` instead would stop grading the
header and cache behaviour that `fetch_record` adds.

MATCHING IS BY (method, host, path, the query parameters the fixture names, and — for a
POST — the request-body fields it names).  Not by exact URL: several connectors size a
query parameter off `budget` (`srlimit`, `hits`, `hitsPerPage`, `per_page`), so an
exact-string match would make every fixture silently stop matching at a different budget
and read as "the connector is broken".

WHY THE QUESTION IS RECORDED AND NOT ONLY THE PATH (T-042).  This module has compared the
fixture's query parameters since it was written — and until this change **not one** of the
`github`, `hn`, `self_page` or `edgar` recordings named a single parameter, so `all()` ran
over an empty mapping and the comparison was vacuously true.  The oracle graded (method,
host, path) and nothing else, which means a connector could send an arbitrarily wrong `q`,
`forms` or `tags` and its recording still answered.  Measured before the repair: replacing
`edgar.FORMS` with `"10-K"`, `hn`'s `tags` with `"poll"`, `github`'s `q` with a nonsense
string and `wayback`'s `filter` with a nonsense string each left
`test_t1_connector_fixtures.py` at 44 passed.  That is how four capabilities shipped with
acceptance criteria naming endpoints and parameters no code asked for: the corpus could
not tell a connector that asked the right question from one that asked nothing.

THE MATCHING POLICY, AND WHY IT IS NOT EXACT EQUALITY.  **Every parameter a recording
names must be present in the request with exactly that value; parameters the recording
does not name are ignored.**  Order is irrelevant (both sides are parsed into a mapping)
and a repeated parameter compares on its last value.

* Exact query equality was rejected because it grades things that are not the question.
  `budget` sizes `srlimit`/`hits`/`hitsPerPage`/`per_page`/`per-page`/`limit`, and
  `test_connector_respects_its_budget` drives every connector at budget 0, 1 and 5 — an
  exact match would answer at one of those and 404 at the other two.  Settings size
  others: OpenAlex sends `mailto=<contact_email>`, so an exact match would bind the corpus
  to one test's `Settings`.
* "Ignore unknown parameters" leaves one hole open: a connector that *adds* a wrong
  parameter beside the right ones still matches.  That is accepted deliberately, because
  the failure it hides ("asked the right question and also something else") is strictly
  weaker than the two this closes — a wrong VALUE and a DROPPED parameter both fail, since
  `asked.get(key)` returns the wrong value or `None` and neither equals what was recorded.
* So what a recording names is the QUESTION and never the SIZE OF THE ANSWER.  The
  sizing and environment parameters above are deliberately absent from every fixture;
  `SIZING_PARAMS` names them, and `test_t1_recorded_query_oracle.py` holds the corpus to
  the rule by requiring every other parameter a connector actually sends to be pinned.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from arrival.config import Settings
from arrival.contracts import PersonRef
from arrival.http.ratelimit import limiter

__all__ = [
    "ALLOWED_HOSTS",
    "FIXTURE_DIR",
    "KINDS",
    "Recording",
    "RESERVED_DOMAINS",
    "RESERVED_SUFFIXES",
    "RESERVED_TLDS",
    "SIZING_PARAMS",
    "fixture_path",
    "install_transport",
    "is_reserved_host",
    "load",
    "matches",
    "no_real_sleep",
    "query_of",
    "required_body",
    "required_query",
    "settings_for",
]

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "http"

#: Every kind TASKS T-1 acceptance 2 names, in `all_connectors()`'s display-priority order.
KINDS: tuple[str, ...] = (
    "self_page",
    "wikipedia",
    "wikidata",
    "github",
    "openalex",
    "edgar",
    "propublica",
    "hn",
    "wayback",
    "search",
)

#: Hosts a recorded response may legitimately name: the public APIs these connectors talk
#: to, plus the reserved-for-documentation domains of RFC 2606. Anything else in a fixture
#: would mean the recording came off a real page about a real person.
ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "api.openalex.org",
        "openalex.org",
        "api.tavily.com",
        "doi.org",
        "efts.sec.gov",
        "en.wikipedia.org",
        "hn.algolia.com",
        "html.duckduckgo.com",
        "news.ycombinator.com",
        "orcid.org",
        "projects.propublica.org",
        "web.archive.org",
        "www.sec.gov",
        "www.wikidata.org",
    }
)

#: RFC 2606 §3 reserves these three second-level domains, and RFC 2606 §2 / RFC 6761
#: reserve these TLDs. `example.org` and `www.example.org` are BOTH reserved, so the check
#: has to accept the bare name as well as any subdomain of it.
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
RESERVED_TLDS = ("example", "invalid", "test", "localhost")

#: Kept as the suffix forms too, for messages that show a reader what is acceptable.
RESERVED_SUFFIXES = tuple(f".{name}" for name in (*RESERVED_DOMAINS, *RESERVED_TLDS))


def is_reserved_host(host: str) -> bool:
    """True when `host` can never resolve to somebody's real site."""
    host = host.lower().rstrip(".")
    if host in RESERVED_DOMAINS or host in RESERVED_TLDS:
        return True
    return host.endswith(RESERVED_SUFFIXES)


def fixture_path(kind: str) -> Path:
    """The single recorded corpus for `kind`. Raises if there is not exactly one."""
    matches = sorted(FIXTURE_DIR.glob(f"{kind}_*.json"))
    matches = [p for p in matches if not p.name.startswith("fixture_dossier_docs")]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one tests/fixtures/http/{kind}_*.json (TASKS T-1 acceptance "
            f"5: 'a recorded, redacted response per connector'); found {[p.name for p in matches]}"
        )
    return matches[0]


@dataclass(frozen=True)
class Recording:
    """One connector's recorded corpus."""

    kind: str
    subject: dict[str, Any]
    provenance: str
    note: str
    responses: list[dict[str, Any]]
    path: Path

    @property
    def person(self) -> PersonRef:
        return PersonRef(**self.subject)

    def body_of(self, entry: dict[str, Any]) -> str:
        if "json" in entry:
            return json.dumps(entry["json"])
        return str(entry.get("body") or "")

    def urls(self) -> list[str]:
        """Every url named anywhere in this recording: the request urls and, for JSON
        payloads, any `http(s)` string inside them. Used by the no-real-people check."""
        found: list[str] = []
        for entry in self.responses:
            found.append(str(entry["url"]))
            found.extend(_urls_in_json(entry.get("json")))
            found.extend(_urls_in_markup(str(entry.get("body") or "")))
        return found


def _urls_in_json(payload: Any) -> list[str]:
    out: list[str] = []
    if isinstance(payload, str):
        if payload.startswith(("http://", "https://")):
            out.append(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            out.extend(_urls_in_json(value))
    elif isinstance(payload, list):
        for value in payload:
            out.extend(_urls_in_json(value))
    return out


def _urls_in_markup(markup: str) -> list[str]:
    import re

    return [m.rstrip('".,;)') for m in re.findall(r"https?://[^\s\"'<>]+", markup)]


def load(kind: str) -> Recording:
    """Read the recorded corpus for `kind`."""
    path = fixture_path(kind)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Recording(
        kind=payload["kind"],
        subject=payload["subject"],
        provenance=payload["provenance"],
        note=payload.get("note", ""),
        responses=payload["responses"],
        path=path,
    )


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings pointing the HTTP cache inside `tmp_path`.

    Built explicitly rather than read from the environment so a test never writes into the
    repository's own `.cache/http` and never depends on the developer's `.env`.
    """
    values: dict[str, Any] = {
        "contact_email": "t1-fixture-suite@example.org",
        "cache_dir": tmp_path / "cache" / "http",
        "tavily_api_key": "tvly-t1-fixture-suite",
        "github_token": "ghp-t1-fixture-suite",
    }
    values.update(overrides)
    return Settings(**values)


#: Parameters a recording must NEVER name, because they say how BIG the answer should be
#: or which environment asked, not what was asked. `budget` sizes the first group and
#: `Settings` the second, and both legitimately differ between two runs of the same test.
SIZING_PARAMS = frozenset(
    {
        "hits",
        "hitsPerPage",
        "limit",
        "mailto",
        "max_results",
        "per-page",
        "per_page",
        "srlimit",
    }
)


def query_of(url: str) -> dict[str, str]:
    """The query parameters of `url`, last value winning for a repeated key."""
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def required_query(entry: dict[str, Any]) -> dict[str, str]:
    """Every query parameter a request must carry, with the value it must carry.

    Two spellings, because both are readable and the older fixtures already use the first:
    parameters written into the recorded `url`, and a `query` mapping beside it. A `query`
    mapping is preferred for anything long or punctuated (`q`, `forms`, `filter`) — a
    percent-encoded url is not something a reviewer can check by eye.
    """
    required = query_of(str(entry["url"]))
    named = entry.get("query")
    if isinstance(named, dict):
        required.update({str(key): str(value) for key, value in named.items()})
    return required


def required_body(entry: dict[str, Any]) -> dict[str, Any]:
    """Fields the request's JSON body must carry. Empty for every GET recording.

    The `search` connector puts its question in a POST body rather than a query string, so
    without this its recording would be exactly as vacuous as the four this repair is
    named for: `POST /search` with any body at all would match.
    """
    named = entry.get("request_json")
    return dict(named) if isinstance(named, dict) else {}


def _body_json(body: bytes | str | None) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def matches(
    entry: dict[str, Any], method: str, url: str, body: bytes | str | None = None
) -> bool:
    """`entry` answers this request iff method, host and path agree, every query parameter
    the FIXTURE names is present in the request with the same value, and every request-body
    field it names is present in the request's JSON body with the same value.

    Parameters and fields the fixture does not name are ignored — see the module docstring
    for why that middle policy was chosen over exact equality.
    """
    if str(entry.get("method", "GET")).upper() != method.upper():
        return False
    want, got = urlsplit(str(entry["url"])), urlsplit(url)
    if (want.hostname or "").lower() != (got.hostname or "").lower():
        return False
    if want.path != got.path:
        return False

    asked = query_of(url)
    if any(asked.get(key) != value for key, value in required_query(entry).items()):
        return False

    wanted_body = required_body(entry)
    if not wanted_body:
        return True
    sent = _body_json(body)
    if not isinstance(sent, dict):
        return False
    return all(sent.get(key) == value for key, value in wanted_body.items())


def install_transport(
    monkeypatch: Any,
    recording: Recording | None = None,
    *,
    fail: str | None = None,
) -> list[str]:
    """Serve `recording` at the transport boundary. Returns the live list of urls asked for.

    `fail` replaces the corpus with one failure mode, which is how TASKS T-1 acceptance 3
    ("returns [] when its fixture is absent or the transport errors") is exercised:

    * ``"connect"``  - a DNS/connection failure
    * ``"timeout"``  - a read timeout
    * ``"500"``      - the server is up and broken
    * ``"garbage"``  - a 200 whose body is not the advertised JSON
    * ``"empty"``    - a 200 with no body at all
    * ``"absent"``   - every url 404s, i.e. nothing was recorded for this connector
    """
    requested: list[str] = []
    entries = list(recording.responses) if recording is not None else []
    body_of = recording.body_of if recording is not None else (lambda entry: "")

    async def handle(self: Any, request: httpx.Request, **_: Any) -> httpx.Response:
        url = str(request.url)
        requested.append(url)

        if fail == "connect":
            raise httpx.ConnectError("recorded failure: name resolution", request=request)
        if fail == "timeout":
            raise httpx.ReadTimeout("recorded failure: read timeout", request=request)
        if fail == "500":
            return httpx.Response(500, content=b"upstream is unwell", request=request)
        if fail == "garbage":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"<html>not the json you were promised</html>",
                request=request,
            )
        if fail == "empty":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"", request=request
            )
        if fail == "absent":
            return httpx.Response(404, content=b"no recording", request=request)

        for entry in entries:
            if matches(entry, request.method, url, request.content):
                return httpx.Response(
                    int(entry.get("status", 200)),
                    headers={"content-type": str(entry.get("content_type", "text/html"))},
                    content=body_of(entry).encode("utf-8"),
                    request=request,
                )
        # Nothing recorded for this url. A 404 is the honest answer and is also what the
        # real source returns for a person it has never heard of.
        return httpx.Response(404, content=b"not in this recording", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    limiter.reset()
    return requested


def no_real_sleep(monkeypatch: Any) -> list[float]:
    """Make the rate limiter's waits free, and record what it asked to wait.

    The politeness delay is real seconds; a test that actually served them would spend
    minutes proving something `test_t1_http_client.py` proves against a virtual clock.
    """
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        slept.append(delay)
        return await real_sleep(0, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept
