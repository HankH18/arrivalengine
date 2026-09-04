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

WHAT EACH CONNECTOR IS GRADED AGAINST.  TASKS T-1 acceptance 2 gives each of the ten
connectors "one test ... asserting >=1 RawDoc with correct `source_kind`, `url`,
non-empty `text`, and `budget` respected", against `tests/fixtures/http/`.  A frozen
metric cannot use that directory - it is the gradee's own writable scope, so grading
against it would let the measured thing write its own answer key.  The recording
therefore lives in this file (see THE RECORDED CORPUS below) and is served at the same
transport boundary.  Those ten per-kind tests are the positive half of the ticket; the
`degrade` test is the same assertion inverted.  A connector must pass both, and neither
an always-`[]` stub nor a connector that fabricates documents without fetching can.

Every product import is INSIDE a test body: a module-scope import of an unbuilt module
is a collection error, which erases this file from the pass-rate denominator instead of
failing loudly.  `httpx` is imported lazily for the same reason — it is a project
dependency, not a stdlib module, and at cycle 0 there is no environment holding it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from urllib.parse import urlsplit

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


def _install_transport(monkeypatch, resolve):
    """Serve canned responses at httpx's real transport boundary; record every request.

    `resolve(url)` returns one of
        {"status": int, "body": str, "content_type": str}   -> a response
        {"raise": "timeout" | "connect"}                    -> a transport failure
        None                                                -> an unreachable host
    Intercepting here rather than at a product seam means it works no matter how the
    client builds its `AsyncClient`, and it is the boundary DESIGN §Verification already
    names for the C7 offline rule.  Two resolvers are built on it: `_stub_transport`
    (an exact-URL table, for the http/client tests, which know every url they ask for)
    and `_recorded_transport` (the per-source corpus, for the connector tests, which do
    not).
    """
    import httpx

    seen = []

    def _respond(request):
        seen.append(str(request.url))
        spec = resolve(str(request.url))
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


def _stub_transport(monkeypatch, routes, default=None):
    """Exact-URL routing table over `_install_transport`.

    `default` is used for any URL not in `routes`; None means "fail like an unreachable
    host", so a connector reaching for an unstubbed endpoint is a failure it must absorb
    rather than a silent success.
    """
    return _install_transport(monkeypatch, lambda url: routes.get(url, default))


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
    """The synthetic subject of the recorded corpus. Fictional, per T-1 acceptance 5.

    `details` carries an employer, a city AND a homepage, because TASKS T-1 acceptance 2
    defines two connectors in terms of what is in there: `self_page` fetches "URLs found
    in details or Wikidata official-website", and `wayback` runs CDX over "the person's
    site/company site".  A PersonRef with no URL in `details` would starve both of their
    documented input and grade a correct implementation red for it.
    """
    from arrival.contracts import PersonRef

    return PersonRef(
        person_id="pell-marrowby",
        name=_SUBJECT,
        details=[f"co-founder, {_COMPANY}", "Austin, Texas", _SITE],
    )


# --------------------------------------------------------------------------------------
# THE RECORDED CORPUS the connectors are graded against.
#
# TASKS T-1 acceptance 2 names ten connectors and says each gets "one test against
# tests/fixtures/http/{kind}_*.json asserting >=1 RawDoc with correct source_kind, url,
# non-empty text, and budget respected".  A frozen metric cannot read that directory:
# `tests/` is the gradee's own writable scope, so grading against it would let the thing
# being measured write its own answer key.  So the recording lives HERE, served at
# httpx's transport boundary, and the ten per-kind tests below are the frozen equivalent
# of that acceptance criterion.
#
# The subject is fictional (TASKS T-1 acceptance 5: "no real people"); the payload
# SHAPES are the real ones each API returns, because a connector that cannot parse the
# real shape is not a working connector.  Two deliberate design choices keep this from
# grading a guess about endpoints rather than the connector:
#
#   1. Where an API has more than one plausible entry point, the recorded payload is the
#      UNION of the shapes (wikidata's `search` + `entities`, wikipedia's `query.search`
#      + `query.pages`), so a connector that reached for either one finds its data.
#   2. Any URL the router does not recognise still gets a successful, parseable answer -
#      a generic page for a document URL, a generic result envelope for an API-looking
#      URL.  An unrecognised endpoint therefore costs the connector nothing, and an
#      empty result can only mean the connector never parsed a successful response.
#
# The failure message of every per-kind test prints the URLs the connector actually
# requested, so a red here names the endpoint that went unrecognised instead of leaving
# the implementer to guess.
# --------------------------------------------------------------------------------------

# The ten kinds TASKS T-1 acceptance 2 enumerates, in the order it names them. This
# tuple is the criterion: `all_connectors` must return exactly these, no more (the
# excluded fec/courtlistener/uspto/youtube/podcast of SPEC Q4) and no fewer (a partial
# fan-out is the failure mode the old `>= 5` count could not see).
_EXPECTED_CONNECTOR_KINDS = (
    "search",
    "wikidata",
    "wikipedia",
    "github",
    "edgar",
    "wayback",
    "propublica",
    "hn",
    "openalex",
    "self_page",
)

# Big enough that a connector is never truncated by the budget in the positive test, and
# small enough that `len(docs) <= budget` still says something.
_GENEROUS_BUDGET = 5

_SUBJECT = "Pell Marrowby"
_COMPANY = "Pelmyre Works"
_SITE = "https://pelmyre.example.org/"
_SITE_HOST = "pelmyre.example.org"
_GH_LOGIN = "pmarrowby"
_QID = "Q79104553"
_CIK = "0001899432"
_EIN = 862049117
_WIKI_TITLE = "Pell Marrowby"
_WIKI_URL = "https://en.wikipedia.org/wiki/Pell_Marrowby"

_LINES = (
    "Pell Marrowby co-founded Pelmyre Works in Austin in 2019 and still writes the "
    "release notes by hand.",
    "Pelmyre Works publishes its release notes as plain text every Thursday, a habit "
    "Marrowby started in the first office.",
    "Marrowby ran the freight-scheduling working group at Bellhaven Polytechnic before "
    "the company existed.",
)

_TITLES = (
    "Pell Marrowby - Pelmyre Works",
    "Release notes, May 2024 - Pelmyre Works",
    "Freight-scheduling working group",
)

_URLS = (
    "https://pelmyre.example.org/team/pell-marrowby",
    "https://pelmyre.example.org/notes/2024-05-release",
    "https://pelmyre.example.org/notes/2023-11-working-group",
)


def _json_spec(payload):
    return {
        "body": json.dumps(payload),
        "content_type": "application/json; charset=utf-8",
    }


def _text_spec(body, content_type="text/plain; charset=utf-8"):
    return {"body": body, "content_type": content_type}


def _page_spec(title, paragraphs, extra=""):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return {
        "body": (
            f"<html><head><title>{title}</title></head><body>"
            f"<h1>{title}</h1>{body}{extra}</body></html>"
        ),
        "content_type": "text/html; charset=utf-8",
    }


def _inverted(sentence):
    """OpenAlex serialises abstracts as an inverted index; reproduce that, not a guess."""
    index = {}
    for position, word in enumerate(sentence.split()):
        index.setdefault(word, []).append(position)
    return index


# --- search: Tavily first (a key is configured), DuckDuckGo-lite as the fallback -------

_TAVILY = {
    "query": f"{_SUBJECT} {_COMPANY}",
    "answer": _LINES[0],
    "follow_up_questions": None,
    "images": [],
    "results": [
        {
            "title": _TITLES[i],
            "url": _URLS[i],
            "content": _LINES[i],
            "raw_content": _LINES[i],
            "score": score,
            "published_date": date,
        }
        for i, (score, date) in enumerate(
            ((0.97, "2024-05-02"), (0.91, "2024-05-02"), (0.84, "2023-11-14"))
        )
    ],
    "response_time": 0.42,
}


def _ddg_spec():
    rows = "".join(
        f'<tr><td><a rel="nofollow" class="result-link result__a" href="{url}">{title}</a>'
        f'</td></tr><tr><td class="result-snippet result__snippet">{line}</td></tr>'
        for url, title, line in zip(_URLS, _TITLES, _LINES, strict=True)
    )
    return {
        "body": f"<html><body><table>{rows}</table></body></html>",
        "content_type": "text/html; charset=utf-8",
    }


# --- wikidata --------------------------------------------------------------------------

_WIKIDATA_SPARQL = {
    "head": {
        "vars": ["item", "itemLabel", "itemDescription", "affiliationLabel", "website", "article"]
    },
    "results": {
        "bindings": [
            {
                "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{_QID}"},
                "itemLabel": {"xml:lang": "en", "type": "literal", "value": _SUBJECT},
                "itemDescription": {
                    "xml:lang": "en",
                    "type": "literal",
                    "value": f"co-founder of {_COMPANY}, Austin",
                },
                "affiliationLabel": {"xml:lang": "en", "type": "literal", "value": affiliation},
                "website": {"type": "uri", "value": _SITE},
                "article": {"type": "uri", "value": _WIKI_URL},
            }
            for affiliation in (_COMPANY, "Bellhaven Polytechnic", "Foundry Seed 2019")
        ]
    },
}

_WIKIDATA_API = {
    # wbsearchentities
    "searchinfo": {"search": _SUBJECT},
    "search": [
        {
            "id": _QID,
            "title": _QID,
            "pageid": 82304991,
            "concepturi": f"http://www.wikidata.org/entity/{_QID}",
            "url": f"//www.wikidata.org/wiki/{_QID}",
            "label": _SUBJECT,
            "description": f"co-founder of {_COMPANY}, Austin",
            "match": {"type": "label", "language": "en", "text": _SUBJECT},
        }
    ],
    # wbgetentities, same response object so either call finds what it came for
    "entities": {
        _QID: {
            "type": "item",
            "id": _QID,
            "labels": {"en": {"language": "en", "value": _SUBJECT}},
            "descriptions": {
                "en": {"language": "en", "value": f"co-founder of {_COMPANY}, Austin"}
            },
            "aliases": {},
            "claims": {
                "P31": [
                    {
                        "mainsnak": {
                            "snaktype": "value",
                            "property": "P31",
                            "datatype": "wikibase-item",
                            "datavalue": {
                                "value": {"entity-type": "item", "numeric-id": 5, "id": "Q5"},
                                "type": "wikibase-entityid",
                            },
                        },
                        "type": "statement",
                        "rank": "normal",
                        "id": f"{_QID}$instance-of",
                    }
                ],
                "P856": [
                    {
                        "mainsnak": {
                            "snaktype": "value",
                            "property": "P856",
                            "datatype": "url",
                            "datavalue": {"value": _SITE, "type": "string"},
                        },
                        "type": "statement",
                        "rank": "normal",
                        "id": f"{_QID}$official-website",
                    }
                ],
            },
            "sitelinks": {
                "enwiki": {"site": "enwiki", "title": _WIKI_TITLE, "url": _WIKI_URL},
            },
        }
    },
    "success": 1,
}


# --- wikipedia -------------------------------------------------------------------------

_WIKIPEDIA_SUMMARY = {
    "type": "standard",
    "title": _WIKI_TITLE,
    "displaytitle": _WIKI_TITLE,
    "namespace": {"id": 0, "text": ""},
    "wikibase_item": _QID,
    "titles": {"canonical": "Pell_Marrowby", "normalized": _WIKI_TITLE, "display": _WIKI_TITLE},
    "pageid": 74920011,
    "lang": "en",
    "description": f"co-founder of {_COMPANY}",
    "extract": " ".join(_LINES),
    "extract_html": "<p>" + " ".join(_LINES) + "</p>",
    "content_urls": {
        "desktop": {"page": _WIKI_URL},
        "mobile": {"page": "https://en.m.wikipedia.org/wiki/Pell_Marrowby"},
    },
}

_WIKIPEDIA_API = {
    "batchcomplete": "",
    "query": {
        "searchinfo": {"totalhits": 3},
        "search": [
            {
                "ns": 0,
                "title": title,
                "pageid": pageid,
                "size": 8123,
                "wordcount": 1204,
                "snippet": line,
                "timestamp": "2024-05-02T09:14:00Z",
            }
            for title, pageid, line in zip(
                (_WIKI_TITLE, _COMPANY, "Freight scheduling"),
                (74920011, 74920012, 74920013),
                _LINES,
                strict=True,
            )
        ],
        "pages": {
            "74920011": {
                "pageid": 74920011,
                "ns": 0,
                "title": _WIKI_TITLE,
                "extract": " ".join(_LINES),
                "fullurl": _WIKI_URL,
            }
        },
    },
}


# --- github ----------------------------------------------------------------------------

_GH_USER = {
    "login": _GH_LOGIN,
    "id": 41200931,
    "type": "User",
    "url": f"https://api.github.com/users/{_GH_LOGIN}",
    "html_url": f"https://github.com/{_GH_LOGIN}",
    "name": _SUBJECT,
    "company": _COMPANY,
    "blog": _SITE,
    "location": "Austin, Texas",
    "bio": _LINES[0],
    "public_repos": 24,
    "followers": 311,
    "created_at": "2018-03-04T00:00:00Z",
}

_GH_SEARCH_USERS = {"total_count": 1, "incomplete_results": False, "items": [_GH_USER]}

_GH_REPOS = [
    {
        "id": 812345600 + i,
        "name": name,
        "full_name": f"{_GH_LOGIN}/{name}",
        "html_url": f"https://github.com/{_GH_LOGIN}/{name}",
        "description": line,
        "language": "Python",
        "stargazers_count": 148 - 20 * i,
        "fork": False,
        "pushed_at": "2024-05-02T09:14:00Z",
        "owner": {"login": _GH_LOGIN, "html_url": f"https://github.com/{_GH_LOGIN}"},
    }
    for i, (name, line) in enumerate(
        zip(
            ("pelmyre-freight", "plaintext-release-notes", "bellhaven-scheduling"),
            _LINES,
            strict=True,
        )
    )
]

_GH_EVENTS = [
    {
        "id": f"3829501200{i}",
        "type": event_type,
        "actor": {"login": _GH_LOGIN, "display_login": _GH_LOGIN},
        "repo": {
            "id": 812345600 + i,
            "name": f"{_GH_LOGIN}/{repo['name']}",
            "url": f"https://api.github.com/repos/{_GH_LOGIN}/{repo['name']}",
        },
        "payload": {
            "size": 3,
            "commits": [{"sha": f"deadbeef{i}", "message": line}],
            "action": "published",
        },
        "public": True,
        "created_at": "2024-05-02T09:14:00Z",
    }
    for i, (event_type, repo, line) in enumerate(
        zip(
            ("PushEvent", "PullRequestEvent", "ReleaseEvent"), _GH_REPOS, _LINES, strict=True
        )
    )
]


# --- edgar -----------------------------------------------------------------------------

_EDGAR_FTS = {
    "took": 21,
    "timed_out": False,
    "hits": {
        "total": {"value": 3, "relation": "eq"},
        "hits": [
            {
                "_index": "edgar_file",
                "_id": f"0001899432-24-00003{i}:primary_doc.xml",
                "_source": {
                    "ciks": [_CIK],
                    "root_forms": [form],
                    "form": form,
                    "file_type": form,
                    "file_date": date,
                    "adsh": f"0001899432-24-00003{i}",
                    "display_names": [
                        f"{_SUBJECT} (CIK {_CIK})",
                        f"{_COMPANY} Inc. (CIK {_CIK})",
                    ],
                    "file_description": (
                        f"FORM {form} - {_SUBJECT}, {_COMPANY} Inc. {line}"
                    ),
                },
            }
            for i, (form, date, line) in enumerate(
                zip(
                    ("4", "D", "3"),
                    ("2024-05-14", "2023-09-08", "2022-01-31"),
                    _LINES,
                    strict=True,
                )
            )
        ],
    },
}

_EDGAR_SUBMISSIONS = {
    "cik": _CIK.lstrip("0"),
    "name": f"{_COMPANY} Inc.",
    "tickers": [],
    "exchanges": [],
    "addresses": {"business": {"city": "Austin", "stateOrCountry": "TX"}},
    "filings": {
        "recent": {
            "accessionNumber": ["0001899432-24-000030", "0001899432-23-000031"],
            "form": ["4", "D"],
            "filingDate": ["2024-05-14", "2023-09-08"],
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml"],
            "primaryDocDescription": [
                f"FORM 4 {_SUBJECT} {_COMPANY} Inc.",
                f"FORM D {_COMPANY} Inc.",
            ],
        },
        "files": [],
    },
}

_EDGAR_ATOM = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    f"<title>EDGAR filings for {_COMPANY} Inc.</title>"
    + "".join(
        f"<entry><title>4 - {_SUBJECT} ({_COMPANY} Inc.)</title>"
        f'<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/'
        f'{_CIK}/000189943224-00003{i}-index.htm"/>'
        f"<summary>{line}</summary><updated>2024-05-14T00:00:00-04:00</updated></entry>"
        for i, line in enumerate(_LINES)
    )
    + "</feed>"
)


# --- wayback ---------------------------------------------------------------------------

_CDX_ROWS = [
    ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
    [
        "org,example,pelmyre)/",
        "20190412104500",
        _SITE,
        "text/html",
        "200",
        "QNZQGSRDUSGRSDJKNAAAAAAAAAAAAAAA",
        "5231",
    ],
    [
        "org,example,pelmyre)/team/pell-marrowby",
        "20200902113000",
        _URLS[0],
        "text/html",
        "200",
        "BNZQGSRDUSGRSDJKNAAAAAAAAAAAAAAA",
        "4180",
    ],
    [
        "org,example,pelmyre)/notes/2023-11-working-group",
        "20211130090000",
        _URLS[2],
        "text/html",
        "200",
        "CNZQGSRDUSGRSDJKNAAAAAAAAAAAAAAA",
        "6122",
    ],
]

_WAYBACK_AVAILABLE = {
    "url": _SITE,
    "archived_snapshots": {
        "closest": {
            "status": "200",
            "available": True,
            "url": f"http://web.archive.org/web/20190412104500/{_SITE}",
            "timestamp": "20190412104500",
        }
    },
}


# --- propublica ------------------------------------------------------------------------

_PP_SEARCH = {
    "total_results": 2,
    "cur_page": 0,
    "page_offset": 0,
    "per_page": 25,
    "num_pages": 1,
    "search_query": _COMPANY,
    "organizations": [
        {
            "ein": _EIN,
            "strein": "86-2049117",
            "name": f"{_COMPANY.upper()} FOUNDATION",
            "sub_name": "",
            "city": "AUSTIN",
            "state": "TX",
            "ntee_code": "S41",
            "subseccd": 3,
            "have_filings": True,
            "have_pdfs": True,
            "score": 15.2,
        },
        {
            "ein": _EIN + 1,
            "strein": "86-2049118",
            "name": "BELLHAVEN POLYTECHNIC ALUMNI FUND",
            "sub_name": "",
            "city": "AUSTIN",
            "state": "TX",
            "ntee_code": "B43",
            "subseccd": 3,
            "have_filings": True,
            "have_pdfs": True,
            "score": 9.4,
        },
    ],
}

_PP_ORG = {
    "organization": {
        "id": _EIN,
        "ein": _EIN,
        "name": f"{_COMPANY.upper()} FOUNDATION",
        "address": "1 Pelmyre Way",
        "city": "AUSTIN",
        "state": "TX",
        "zipcode": "78701",
        "subseccd": 3,
        "ntee_code": "S41",
        "tax_period": 202312,
        "officers": [
            {"name": _SUBJECT, "title": "Board chair", "compensation": 0, "hours": 2.0},
            {"name": "Sil Vantorre", "title": "Treasurer", "compensation": 0, "hours": 1.0},
            {"name": "Runa Okonkwo", "title": "Director", "compensation": 0, "hours": 1.0},
        ],
    },
    "filings_with_data": [
        {
            "tax_prd_yr": year,
            "formtype": 0,
            "pdf_url": (
                "https://projects.propublica.org/nonprofits/download-filing?path="
                f"{_EIN}-{year}"
            ),
            "totrevenue": 1250000,
            "totfuncexpns": 980000,
            "officers": [
                {"name": _SUBJECT, "title": "Board chair", "compensation": 0},
                {"name": "Sil Vantorre", "title": "Treasurer", "compensation": 0},
            ],
        }
        for year in (2023, 2022, 2021)
    ],
    "filings_without_data": [],
    "data_source": "IRS Form 990",
}


# --- hn --------------------------------------------------------------------------------

_HN = {
    "hits": [
        {
            "objectID": f"3920441{i}",
            "title": _TITLES[i],
            "url": _URLS[i],
            "author": _GH_LOGIN,
            "points": 142 - 30 * i,
            "num_comments": 37 - 10 * i,
            "created_at": "2024-02-02T15:04:00.000Z",
            "created_at_i": 1706886240,
            "story_text": None,
            "comment_text": _LINES[i],
            "story_title": _TITLES[i],
            "story_url": _URLS[i],
            "_tags": ["story", f"author_{_GH_LOGIN}", f"story_3920441{i}"],
            "_highlightResult": {
                "title": {"value": _TITLES[i], "matchLevel": "full"},
                "author": {"value": _GH_LOGIN, "matchLevel": "none"},
            },
        }
        for i in range(3)
    ],
    "nbHits": 3,
    "page": 0,
    "nbPages": 1,
    "hitsPerPage": 20,
    "query": _SUBJECT,
    "params": "",
}


# --- openalex --------------------------------------------------------------------------

_OA_AUTHOR = {
    "id": "https://openalex.org/A5031927451",
    "orcid": None,
    "display_name": _SUBJECT,
    "display_name_alternatives": [_SUBJECT, "P. Marrowby"],
    "works_count": 6,
    "cited_by_count": 41,
    "last_known_institution": {
        "id": "https://openalex.org/I4210099999",
        "display_name": "Bellhaven Polytechnic",
        "country_code": "US",
        "type": "education",
    },
    "last_known_institutions": [
        {
            "id": "https://openalex.org/I4210099999",
            "display_name": "Bellhaven Polytechnic",
            "country_code": "US",
            "type": "education",
        }
    ],
    "x_concepts": [
        {"display_name": "Freight logistics", "score": 71.2},
        {"display_name": "Scheduling", "score": 44.0},
    ],
    "summary_stats": {"h_index": 4, "i10_index": 2},
    "works_api_url": "https://api.openalex.org/works?filter=author.id:A5031927451",
}

_OA_AUTHORS = {
    "meta": {"count": 1, "db_response_time_ms": 12, "page": 1, "per_page": 25},
    "results": [_OA_AUTHOR],
}

_OA_WORKS = {
    "meta": {"count": 3, "db_response_time_ms": 14, "page": 1, "per_page": 25},
    "results": [
        {
            "id": f"https://openalex.org/W274180980{i}",
            "doi": f"https://doi.org/10.5555/pelmyre.202{i}.1",
            "title": _TITLES[i],
            "display_name": _TITLES[i],
            "publication_year": 2021 + i,
            "publication_date": f"202{1 + i}-11-14",
            "cited_by_count": 12 - 3 * i,
            "abstract": _LINES[i],
            "abstract_inverted_index": _inverted(_LINES[i]),
            "primary_location": {
                "landing_page_url": _URLS[i],
                "source": {"display_name": "Bellhaven Working Papers"},
            },
            "authorships": [
                {
                    "author": {
                        "id": "https://openalex.org/A5031927451",
                        "display_name": _SUBJECT,
                    },
                    "institutions": [{"display_name": "Bellhaven Polytechnic"}],
                }
            ],
        }
        for i in range(3)
    ],
}


# --- self_page and the generic fallbacks -----------------------------------------------

_RSS = (
    '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
    f"<title>{_COMPANY}</title><link>{_SITE}</link>"
    f"<description>Release notes from {_COMPANY}.</description>"
    + "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<pubDate>Thu, 02 May 2024 09:14:00 GMT</pubDate>"
        f"<description>{line}</description></item>"
        for title, url, line in zip(_TITLES, _URLS, _LINES, strict=True)
    )
    + "</channel></rss>"
)

_GENERIC_ITEMS = [
    {
        "id": f"frozen-{i}",
        "title": _TITLES[i],
        "name": _TITLES[i],
        "display_name": _TITLES[i],
        "url": _URLS[i],
        "link": _URLS[i],
        "href": _URLS[i],
        "text": _LINES[i],
        "content": _LINES[i],
        "snippet": _LINES[i],
        "description": _LINES[i],
        "summary": _LINES[i],
        "extract": _LINES[i],
        "abstract": _LINES[i],
        "author": _GH_LOGIN,
        "date": "2024-05-02",
        "created_at": "2024-05-02T09:14:00Z",
        "published_at": "2024-05-02",
    }
    for i in range(3)
]

# One envelope carrying every container key the nine JSON APIs above use, so a connector
# that reached an endpoint this router does not recognise still finds its results where
# it looks for them. This is the fallback that keeps an unanticipated endpoint from
# reading as "the connector returned nothing".
_GENERIC_JSON = {
    "query": _SUBJECT,
    "total": 3,
    "count": 3,
    "nbHits": 3,
    "total_count": 3,
    "total_results": 3,
    "success": 1,
    "results": _GENERIC_ITEMS,
    "items": _GENERIC_ITEMS,
    "hits": _GENERIC_ITEMS,
    "docs": _GENERIC_ITEMS,
    "data": _GENERIC_ITEMS,
    "entries": _GENERIC_ITEMS,
    "records": _GENERIC_ITEMS,
    "search": _GENERIC_ITEMS,
    "organizations": _GENERIC_ITEMS,
    "meta": {"count": 3, "page": 1, "per_page": 25},
}


def _looks_like_an_api(host, path, query):
    return (
        host.startswith("api.")
        or "/api/" in path
        or path.endswith(".json")
        or "json" in query.lower()
        or "format=json" in query.lower()
    )


def _recorded_spec(url):
    """Map any URL onto the recorded corpus. Never fails; never returns an error status.

    Recognised hosts get the real response shape for that API. Everything else gets a
    generic page or a generic result envelope, so a connector that reaches for an
    endpoint this router did not anticipate is still answered rather than starved.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path
    query = parts.query
    lowered = url.lower()

    # search
    if "tavily" in host:
        return _json_spec(_TAVILY)
    if "duckduckgo" in host:
        return _ddg_spec()

    # wikidata
    if "wikidata.org" in host:
        if "sparql" in path or host.startswith("query."):
            return _json_spec(_WIKIDATA_SPARQL)
        if path.startswith("/wiki/"):
            return _page_spec(f"{_SUBJECT} ({_QID}) - Wikidata", _LINES)
        return _json_spec(_WIKIDATA_API)

    # wikipedia
    if "wikipedia.org" in host:
        if "/api/rest_v1/page/summary" in path:
            return _json_spec(_WIKIPEDIA_SUMMARY)
        if path.startswith("/wiki/"):
            return _page_spec(f"{_WIKI_TITLE} - Wikipedia", _LINES)
        return _json_spec(_WIKIPEDIA_API)

    # github
    if host == "api.github.com":
        if path.startswith("/search/users"):
            return _json_spec(_GH_SEARCH_USERS)
        if path.startswith("/search/"):
            return _json_spec({"total_count": 3, "incomplete_results": False,
                               "items": _GH_REPOS})
        if "/events" in path:
            return _json_spec(_GH_EVENTS)
        if path.rstrip("/").endswith("/repos"):
            return _json_spec(_GH_REPOS)
        if path.startswith("/users/"):
            return _json_spec(_GH_USER)
        return _json_spec(dict(_GH_SEARCH_USERS, **_GH_USER))
    if "github.com" in host or "githubusercontent.com" in host:
        if path.endswith(".atom"):
            return _text_spec(_EDGAR_ATOM.replace("EDGAR filings", "GitHub activity"),
                              "application/atom+xml; charset=utf-8")
        return _page_spec(f"{_SUBJECT} ({_GH_LOGIN}) - GitHub", _LINES)

    # edgar
    if "sec.gov" in host:
        if host.startswith("efts.") or "search-index" in path or "/search" in path:
            return _json_spec(_EDGAR_FTS)
        if "/submissions/" in path:
            return _json_spec(_EDGAR_SUBMISSIONS)
        if "browse-edgar" in path:
            return _text_spec(_EDGAR_ATOM, "application/atom+xml; charset=utf-8")
        if "/Archives/" in path:
            return _page_spec(f"FORM 4 - {_SUBJECT} - {_COMPANY} Inc.", _LINES)
        return _json_spec(_EDGAR_FTS)

    # wayback
    if "archive.org" in host:
        if "/cdx" in path:
            if "output=json" in query.lower():
                return _json_spec(_CDX_ROWS)
            return _text_spec("\n".join(" ".join(row) for row in _CDX_ROWS[1:]))
        if "/wayback/available" in path:
            return _json_spec(_WAYBACK_AVAILABLE)
        if path.startswith("/web/"):
            return _page_spec(
                f"{_COMPANY} (archived capture)",
                _LINES,
                extra=f'<p><a href="{_SITE}">{_SITE}</a></p>',
            )
        return _json_spec(_CDX_ROWS)

    # propublica
    if "propublica.org" in host:
        if "/organizations/" in path:
            return _json_spec(_PP_ORG)
        if "search" in path:
            return _json_spec(_PP_SEARCH)
        return _json_spec(dict(_PP_SEARCH, **_PP_ORG))

    # hn
    if "algolia" in host:
        return _json_spec(_HN)
    if "ycombinator.com" in host:
        return _page_spec(f"{_TITLES[0]} | Hacker News", _LINES)

    # openalex
    if "openalex.org" in host:
        if "/works" in path:
            return _json_spec(_OA_WORKS)
        return _json_spec(_OA_AUTHORS)

    # self_page and anything else
    if any(token in lowered for token in ("/feed", "/rss", "atom.xml", "index.xml", ".rss")):
        return _text_spec(_RSS, "application/rss+xml; charset=utf-8")
    if _looks_like_an_api(host, path, query):
        return _json_spec(_GENERIC_JSON)
    return _page_spec(
        _TITLES[0],
        _LINES,
        extra=f'<p><a href="{_SITE}">{_COMPANY}</a></p>',
    )


def _recorded_transport(monkeypatch):
    """Install the recorded corpus at httpx's transport boundary; return the URL log."""
    return _install_transport(monkeypatch, _recorded_spec)


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


# The one number TASKS T-1 acceptance 1 actually writes down for a host with no special
# rule: "default 2/s".  It is used below only as a LOWER bound on waiting, so a stricter
# limiter (1/s, or a politer default) still passes; only a limiter that is faster than
# the documented default fails.
_DEFAULT_HOST_RATE_PER_SEC = 2.0
# Long enough that any sane bucket has emptied, so the measured window starts drained.
_BURST_PROBE_REQUESTS = 48
_THROTTLED_WINDOW = 12


def test_fetch_text_throttles_one_host_at_the_documented_rate_once_its_burst_is_spent(
    monkeypatch, tmp_path
):
    """SPEC C5 / T-1 acceptance 1: a PER-HOST token bucket, measured on an injected clock.

    WHAT THIS DELIBERATELY DOES NOT PIN, and why the obvious version was wrong.  The
    previous form of this test fetched six URLs from one host and asserted the clock
    advanced by >= 1.0s.  For a token bucket of capacity C refilling at R = 2/s, six
    serial requests cost exactly (6 - C)/R seconds, so `>= 1.0` is satisfied only when
    C <= 4.  Burst capacity is stated NOWHERE - not in SPEC C5, not in TASKS T-1, not in
    DESIGN - so that assertion quietly made a documented, correct implementation with a
    burst of five permanently red, and it would have stayed red through every retry
    because the implementation was not the thing that was wrong.

    So capacity is MEASURED here rather than assumed: the probe run counts how many
    leading requests cost nothing (that count IS C), and the graded window runs after
    the bucket is provably empty, where the cost of K more requests is K/R for every
    value of C.  The only externally documented number left in the assertion is R, and
    it is a lower bound, so a politer limiter passes and only a faster-than-documented
    one fails.

    Distinct URLs throughout, so the disk cache can never answer one of them.
    """
    cache = _isolate(monkeypatch, tmp_path)
    _stub_transport(monkeypatch, {}, default={"body": _HTML})
    # A limiter that waits in small increments issues many more sleeps than requests; the
    # cap is a spin-loop guard, not a budget, so give it room before it becomes a lie.
    clock = _virtual_clock(monkeypatch, cap=100_000)

    from arrival.http.client import fetch_text

    async def _fetch_each(urls):
        """Fetch serially, returning (doc, clock delta) per url."""
        kwargs = _fetch_kwargs(fetch_text, cache)
        out = []
        for url in urls:
            before = clock["now"]
            doc = await _resolve(fetch_text(url, **kwargs))
            out.append((doc, clock["now"] - before))
        return out

    async def _inner():
        probe = await _fetch_each(
            [f"https://frozen-bucket.example.org/probe-{i}" for i in range(_BURST_PROBE_REQUESTS)]
        )
        start = clock["now"]
        window = await _fetch_each(
            [f"https://frozen-bucket.example.org/window-{i}" for i in range(_THROTTLED_WINDOW)]
        )
        window_elapsed = clock["now"] - start
        start = clock["now"]
        spread = await _fetch_each(
            [f"https://frozen-h{i}.example.org/page" for i in range(_THROTTLED_WINDOW)]
        )
        spread_elapsed = clock["now"] - start
        return probe, window, window_elapsed, spread, spread_elapsed

    probe, window, window_elapsed, spread, spread_elapsed = asyncio.run(_inner())

    # Control: a limiter measurement over failed fetches measures nothing.
    for label, batch in (("probe", probe), ("window", window), ("cross-host", spread)):
        assert all(doc is not None for doc, _ in batch), f"{label} fetches did not succeed"

    # The burst, measured rather than assumed: leading requests that cost no time at all.
    free = 0
    for _doc, delta in probe:
        if delta > 0:
            break
        free += 1
    assert free < _BURST_PROBE_REQUESTS, (
        f"all {_BURST_PROBE_REQUESTS} consecutive requests to one host were served with "
        "no waiting whatsoever, so there is no per-host limit in force at all. SPEC C5 "
        "requires one. (This test does not care how large the burst is - only that the "
        "bucket is finite and eventually throttles.)"
    )

    # After a burst of `free`, the bucket is empty; from there K more requests to the
    # same host cost K/R whatever C was. Allow one interval of slack for whether the
    # implementation charges the wait before or after the request.
    floor = (_THROTTLED_WINDOW - 1) / _DEFAULT_HOST_RATE_PER_SEC
    assert window_elapsed >= floor, (
        f"after {_BURST_PROBE_REQUESTS} requests had drained the bucket (the free burst "
        f"measured {free} requests), {_THROTTLED_WINDOW} further requests to the same "
        f"host advanced the clock by only {window_elapsed:.3f}s; at the default rate "
        f"TASKS T-1 pins at {_DEFAULT_HOST_RATE_PER_SEC:g}/s they owe at least "
        f"{floor:.3f}s. A slower limiter passes this; only a faster one fails it."
    )
    assert spread_elapsed < window_elapsed, (
        f"{_THROTTLED_WINDOW} requests to {_THROTTLED_WINDOW} different hosts waited "
        f"{spread_elapsed:.3f}s, no less than the same number to a single throttled host "
        f"({window_elapsed:.3f}s); C5's limit is PER HOST, not a blanket sleep on every "
        "request, and a blanket sleep would make the fan-out in T-6 pointlessly serial"
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
    # A COUNT, not a set, was the old control here (`>= 5`), and it could not tell a
    # complete fan-out from half of one. The set is asserted in full by
    # test_all_connectors_returns_exactly_the_ten_kinds_tasks_t1_names; this test needs
    # only enough of it to keep its own sweep from being vacuous.
    assert len(connectors) == len(_EXPECTED_CONNECTOR_KINDS), (
        f"all_connectors returned {len(connectors)} connectors "
        f"({[getattr(c, 'kind', None) for c in connectors]}); TASKS T-1 acceptance 2 "
        f"names exactly {len(_EXPECTED_CONNECTOR_KINDS)}: "
        f"{list(_EXPECTED_CONNECTOR_KINDS)}"
    )
    nonconforming = [c for c in connectors if not isinstance(c, Connector)]
    assert nonconforming == [], (
        f"these do not satisfy contracts.Connector (kind + async search): {nonconforming}"
    )
    # `isinstance` against a runtime_checkable Protocol tests for the PRESENCE of the
    # attributes and nothing else, so it is satisfied by `kind = None` and a `search`
    # that is not callable. These two lines cost nothing and close that; what each
    # connector actually DOES is graded per kind against the recorded corpus below.
    bad_kind = [c for c in connectors if not isinstance(getattr(c, "kind", None), str)]
    assert bad_kind == [], f"these connectors have a non-string `kind`: {bad_kind}"
    not_callable = [c for c in connectors if not callable(getattr(c, "search", None))]
    assert not_callable == [], f"these connectors have a non-callable `search`: {not_callable}"


def test_all_connectors_omits_fec_and_courtlistener_while_keeping_the_display_sources(
    monkeypatch,
):
    """SPEC Q4 / R11 / C1 + T-1 acceptance 4: the withheld sources are not even built."""
    from arrival.connectors import all_connectors

    kinds = [c.kind for c in all_connectors(_settings(monkeypatch))]

    # Positive control first: without it, an empty list would satisfy the exclusions.
    # It is the FULL expected set, not a five-element sample of it: a sample lets four
    # missing connectors through while still proving "the list is not empty".
    required = set(_EXPECTED_CONNECTOR_KINDS)
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
    # Control: "they all returned []" is only meaningful over the whole fan-out. A count
    # was the old control and a count cannot see WHICH connector went missing - and a
    # missing connector is exactly how a lane could make this test pass by shipping less.
    missing = [k for k in _EXPECTED_CONNECTOR_KINDS
               if k not in {getattr(c, "kind", None) for c in connectors}]
    assert not missing, (
        f"only {len(connectors)} connectors to exercise; {missing} were never built, so "
        "this sweep would grade the ones that are missing as degrading correctly"
    )

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

def test_all_connectors_returns_exactly_the_ten_kinds_tasks_t1_names(monkeypatch):
    """T-1 acceptance 2 + 4: the fan-out is exactly these ten kinds - no more, no fewer.

    This replaces a `>= 5` count. A count cannot tell "nine connectors and one missing"
    from "five connectors", and TASKS T-1 acceptance 2 enumerates the set by name, so the
    set is what the criterion is. Extras matter too: SPEC Q4 leaves fec, courtlistener,
    uspto, youtube and podcast unbuilt, so an eleventh kind is a scope breach, not a
    bonus.
    """
    from arrival.connectors import all_connectors

    kinds = [getattr(c, "kind", None) for c in all_connectors(_settings(monkeypatch))]

    missing = [k for k in _EXPECTED_CONNECTOR_KINDS if k not in kinds]
    extra = [k for k in kinds if k not in _EXPECTED_CONNECTOR_KINDS]
    assert not missing, (
        f"all_connectors() is missing {missing}; TASKS T-1 acceptance 2 names all of "
        f"{list(_EXPECTED_CONNECTOR_KINDS)} and got {kinds}"
    )
    assert not extra, (
        f"all_connectors() returned unexpected kinds {extra}; SPEC Q4 leaves fec, "
        f"courtlistener, uspto, youtube and podcast unbuilt, so the list is exactly "
        f"{list(_EXPECTED_CONNECTOR_KINDS)}"
    )
    assert len(kinds) == len(set(kinds)), f"duplicate connector kinds in the list: {kinds}"


@pytest.mark.parametrize("kind", _EXPECTED_CONNECTOR_KINDS)
def test_connector_returns_cited_rawdocs_from_the_recorded_transport_and_honours_budget(
    kind, monkeypatch, tmp_path
):
    """T-1 acceptance 2, per connector: >=1 RawDoc, right source_kind, url, text, budget.

    THE DEFECT THIS EXISTS TO CLOSE. Before this test the whole of T-1 was graded by
    three assertions that a set of stubs whose every `search` returns `[]` satisfies in
    full: `isinstance(c, Connector)` on a `runtime_checkable` Protocol checks for the
    PRESENCE of `kind` and `search`, never that either does anything; `len(connectors)
    >= 5` counts objects; and "every connector returns [] when the transport fails" is
    trivially true of a connector that returns [] always. Nine empty stubs scored 100%.

    Paired with `test_every_connector_returns_an_empty_list_when_its_transport_fails`,
    which is the same assertion inverted, this also closes the other half: a connector
    that FABRICATES documents without touching the network passes the positive test and
    fails the degradation test, and one that returns [] always does the reverse. Only a
    connector that actually reads its source passes both. The `assert requested` below
    localises that to this kind rather than leaving it to the paired test.

    The budget half is checked on a SECOND connector instance from a second
    `all_connectors()` call, so a connector that legitimately keeps per-instance state
    (a seen-set, a one-shot cursor) is not failed for it.
    """
    _isolate(monkeypatch, tmp_path)
    requested = _recorded_transport(monkeypatch)
    _no_real_sleep(monkeypatch)

    from arrival.contracts import RawDoc

    def _connector_for(kind_wanted):
        from arrival.connectors import all_connectors

        found = [c for c in all_connectors(_settings(monkeypatch))
                 if getattr(c, "kind", None) == kind_wanted]
        assert found, (
            f"all_connectors() returned no connector with kind {kind_wanted!r}; TASKS "
            f"T-1 acceptance 2 names all of {list(_EXPECTED_CONNECTOR_KINDS)}"
        )
        return found[0]

    person = _person()

    async def _inner():
        generous = await _resolve(_connector_for(kind).search(person, _GENEROUS_BUDGET))
        limited = await _resolve(_connector_for(kind).search(person, 1))
        return generous, limited

    generous, limited = asyncio.run(_inner())

    seen = sorted(set(requested))
    assert isinstance(generous, list), (
        f"{kind}.search must return a list of RawDoc, got {type(generous).__name__}"
    )
    assert seen, (
        f"the {kind} connector returned {len(generous)} document(s) without making a "
        "single HTTP request. A connector that does not read its source is a fixture "
        "wearing a connector's name (DESIGN Decision 8 presumes a real fetch)."
    )
    assert len(generous) >= 1, (
        f"the {kind} connector returned no documents from the recorded corpus. It "
        f"requested {seen!r}. The frozen transport answers EVERY url with a 200 - the "
        "recognised endpoints get that API's real response shape, anything else gets a "
        "generic page or a generic result envelope - so an empty list here means the "
        "connector never parsed a result out of a successful response."
    )
    assert len(generous) <= _GENEROUS_BUDGET, (
        f"the {kind} connector returned {len(generous)} documents for budget="
        f"{_GENEROUS_BUDGET}; DESIGN §Interfaces defines budget as the maximum number "
        "of docs to return"
    )

    for doc in generous:
        assert isinstance(doc, RawDoc), (
            f"{kind}.search returned a {type(doc).__name__}, not a contracts.RawDoc"
        )
        assert doc.source_kind == kind, (
            f"the {kind} connector stamped source_kind={doc.source_kind!r} on {doc.url!r};"
            " a citation naming the wrong source is worse than no citation, because "
            "T-3's non-obvious rule and R11's display rules both key off source_kind"
        )
        assert doc.url and doc.url.startswith(("http://", "https://")), (
            f"the {kind} connector returned a doc with url={doc.url!r}; every RawDoc is "
            "a citation and a citation needs a fetchable address"
        )
        assert doc.text.strip(), (
            f"the {kind} connector returned an empty-text doc for {doc.url!r}; DESIGN "
            "§Interfaces: RawDoc.text is 'extracted plain text, <= 20k chars, never "
            "empty', and the citation check in T-3 substring-matches against it"
        )
        assert len(doc.text) <= 20_000, (
            f"the {kind} connector returned {len(doc.text)} chars of text for "
            f"{doc.url!r}; DESIGN §Interfaces caps RawDoc.text at 20k"
        )
        assert doc.doc_id == hashlib.sha1(doc.url.encode()).hexdigest()[:16], (
            f"the {kind} connector's doc_id for {doc.url!r} is {doc.doc_id!r}, not "
            "sha1(url)[:16] as DESIGN §Interfaces defines it; the whole corpus is "
            "addressed by that id, so a private scheme silently breaks dedup and the "
            "cache"
        )

    assert isinstance(limited, list), (
        f"{kind}.search must return a list of RawDoc, got {type(limited).__name__}"
    )
    assert len(limited) <= 1, (
        f"the {kind} connector returned {len(limited)} documents for budget=1 and "
        f"{len(generous)} for budget={_GENEROUS_BUDGET}; it is ignoring its budget "
        "argument. Budget is what stops one talkative source from eating the whole "
        "research allowance for a person (DESIGN §Budget, docs_per_connector)."
    )
    assert len(limited) == 1, (
        f"the {kind} connector returned nothing for budget=1 while returning "
        f"{len(generous)} document(s) for budget={_GENEROUS_BUDGET}; a budget is a cap, "
        "not an off switch"
    )
