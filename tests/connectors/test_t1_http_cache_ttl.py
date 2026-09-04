"""T-025: the HTTP cache had no TTL and no request identity beyond the URL.

Three defects, all measured against the code as it stood before this module existed:

1. **An unextractable 200 was cached permanently.**  A page that answered once with a
   JavaScript shell -- `<div id=root></div><script>boot()</script>`, which the light
   extractor correctly reduces to nothing -- was written to `.cache/http/{doc_id}.json`
   with `fetched_at` recorded and never compared against anything.  Measured: after the
   site recovered and started serving real prose, `fetch_text` returned `None` and made
   **zero** requests.  One transient response poisoned that URL for the life of the cache
   directory, and the research build is explicitly designed around re-running against a
   warm cache, so the damage compounds with use.

2. **The cache key ignored the HTTP method** whenever there was no body.  Measured:
   `_request_key(url, "GET", None) == _request_key(url, "HEAD", None)`, and a `HEAD`
   followed by a `GET` of one URL made **one** request -- the `GET` was served the `HEAD`'s
   empty body from disk.

3. **The cache key ignored request headers entirely.**  Measured: an authenticated fetch
   (`Authorization: Bearer ...`) and an anonymous fetch of the same URL shared one file, so
   the anonymous caller was handed the authenticated response's private payload.

THE POLICY THIS MODULE PINS.  A response that yielded usable text caches durably -- the
cache is what makes a rebuild cheap and expiring successes would throw that away.  A
response that yielded nothing caches only briefly, so a second connector reaching the same
dead URL in the same run is still free while a transient failure heals by itself.  The
mechanism exists for both classes (`POSITIVE_TTL_SECONDS`, `NEGATIVE_TTL_SECONDS`); only
the default policy differs, and both defaults are asserted below.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from t1_recorded import settings_for

from arrival.http import cache as cache_module
from arrival.http import client as client_module
from arrival.http import ratelimit as ratelimit_module
from arrival.http.client import fetch_json, fetch_record, fetch_text
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("T-1")

_URL = "https://ttl.example.com/profile"

#: A single-page app before its bundle runs: a 200, valid HTML, and no prose whatsoever.
#: The extractor is right to return nothing for it; the cache was wrong to keep the answer.
_JS_SHELL = (
    "<html><head><title></title></head><body>"
    "<div id='root'></div><script>window.boot();</script>"
    "</body></html>"
)
_REAL_PAGE = (
    "<html><head><title>Thornfield Loom</title></head><body>"
    "<p>Thornfield Loom publishes a monthly maintenance almanac.</p>"
    "</body></html>"
)
_SENTENCE = "Thornfield Loom publishes a monthly maintenance almanac."


def _serve(monkeypatch, respond):
    """Install `respond(request) -> httpx.Response` at the transport seam."""
    seen: list[httpx.Request] = []

    async def handle(self, request, **_):
        seen.append(request)
        return respond(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    ratelimit_module.limiter.reset()
    return seen


def _html(body: str):
    def respond(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=body.encode(),
            request=request,
        )

    return respond


# --- 1. an unextractable 200 must not poison the URL forever ------------------------


def test_a_transient_js_shell_does_not_poison_the_url_for_the_life_of_the_cache(
    monkeypatch, tmp_path
):
    """The reproduction, with the negative entry's lifetime driven to zero.

    `NEGATIVE_TTL_SECONDS` is the knob under test: the defect was that no knob existed and
    the entry lived forever, so the test that catches it has to be able to move time.
    """
    settings = settings_for(tmp_path)
    monkeypatch.setattr(client_module, "NEGATIVE_TTL_SECONDS", 0.0)

    _serve(monkeypatch, _html(_JS_SHELL))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None, (
        "a JS shell extracts to nothing, so the first call is legitimately None"
    )

    seen = _serve(monkeypatch, _html(_REAL_PAGE))
    healed = asyncio.run(fetch_text(_URL, settings=settings))

    assert len(seen) == 1, (
        "the second call made no request at all: the empty answer was cached with no "
        "expiry, so one transient JS-shell response makes this URL return None forever"
    )
    assert healed is not None and _SENTENCE in healed.text, (
        "once the origin serves real prose the cache must let it through"
    )


def test_an_empty_200_is_not_cached_forever_either(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)
    monkeypatch.setattr(client_module, "NEGATIVE_TTL_SECONDS", 0.0)

    _serve(monkeypatch, _html(""))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None

    seen = _serve(monkeypatch, _html(_REAL_PAGE))
    healed = asyncio.run(fetch_text(_URL, settings=settings))

    assert len(seen) == 1, "an empty 200 was cached permanently"
    assert healed is not None and _SENTENCE in healed.text


def test_a_successful_response_is_untouched_by_the_negative_expiry(monkeypatch, tmp_path):
    """The thing that must NOT be thrown away to fix the edge case.

    With the negative lifetime driven to zero, a page that DID extract must still be a
    hit: the two classes are separate, and a rebuild against a warm cache stays free.
    """
    settings = settings_for(tmp_path)
    monkeypatch.setattr(client_module, "NEGATIVE_TTL_SECONDS", 0.0)

    _serve(monkeypatch, _html(_REAL_PAGE))
    first = asyncio.run(fetch_text(_URL, settings=settings))
    assert first is not None

    def refuse(request):
        raise AssertionError(f"a successful response must stay cached; refetched {request.url}")

    _serve(monkeypatch, refuse)
    second = asyncio.run(fetch_text(_URL, settings=settings))

    assert second is not None and second.text == first.text


def test_the_default_policy_is_durable_successes_and_short_lived_misses():
    """The policy itself, stated as an assertion rather than only as prose.

    `None` means "never expires". Successes are durable because the cache is the whole
    reason a rebuild after a prompt change does not re-hammer ten APIs, and because
    DESIGN §Verification's recorded fixtures carry whatever `fetched_at` they were
    recorded with -- a positive expiry would make the offline verification path rot.
    """
    assert client_module.POSITIVE_TTL_SECONDS is None, (
        "a successful, extractable response must cache durably"
    )
    assert client_module.NEGATIVE_TTL_SECONDS is not None
    assert 0 < client_module.NEGATIVE_TTL_SECONDS <= 3600, (
        "an empty or unextractable answer must expire soon enough that a transient "
        "failure heals inside the same working session"
    )


def test_under_the_DEFAULT_policy_a_miss_gets_an_expiry_and_a_success_does_not(
    monkeypatch, tmp_path
):
    """The default policy, exercised with nothing monkeypatched.

    White-box on the stored envelope on purpose. Every other test in this file drives the
    lifetime by moving `NEGATIVE_TTL_SECONDS`, which means all of them would still pass if
    the shipped DEFAULT were `None` -- the exact defect T-025 reports. Reading what was
    actually written is the only assertion here that fails when the default regresses.
    """
    settings = settings_for(tmp_path)

    _serve(monkeypatch, _html(_JS_SHELL))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is None
    empty_entry = json.loads(
        (settings.cache_dir / f"{doc_id(_URL)}.json").read_text(encoding="utf-8")
    )

    good_url = "https://ttl.example.com/real"
    _serve(monkeypatch, _html(_REAL_PAGE))
    assert asyncio.run(fetch_text(good_url, settings=settings)) is not None
    good_entry = json.loads(
        (settings.cache_dir / f"{doc_id(good_url)}.json").read_text(encoding="utf-8")
    )

    assert empty_entry["http"].get("expires_at"), (
        "an unextractable 200 was stored with no expiry, so it is cached permanently: "
        "one transient JS-shell response poisons this URL for the life of the cache"
    )
    assert good_entry["http"].get("expires_at") is None, (
        "a successful, extractable response must cache durably; expiring it would undo "
        "the reason the cache exists and would rot the recorded-fixture path"
    )


def test_the_positive_expiry_mechanism_exists_even_though_the_default_declines_to_use_it(
    monkeypatch, tmp_path
):
    """A knob that is documented but not wired is not a knob."""
    settings = settings_for(tmp_path)
    monkeypatch.setattr(client_module, "POSITIVE_TTL_SECONDS", 0.0)

    _serve(monkeypatch, _html(_REAL_PAGE))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is not None

    seen = _serve(monkeypatch, _html(_REAL_PAGE))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is not None
    assert len(seen) == 1, (
        "with POSITIVE_TTL_SECONDS at zero a successful entry must expire; it did not, "
        "so the expiry is not consulted for the durable class at all"
    )


# --- 2. the request key must carry the method ---------------------------------------


def test_the_cache_key_distinguishes_the_http_method(monkeypatch, tmp_path):
    """A HEAD and a GET of one URL are two requests with two different answers."""
    settings = settings_for(tmp_path)

    def by_method(request):
        body = b"" if request.method == "HEAD" else _REAL_PAGE.encode()
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=body,
            request=request,
        )

    seen = _serve(monkeypatch, by_method)

    async def both():
        await fetch_record(_URL, method="HEAD", settings=settings)
        return await fetch_record(_URL, method="GET", settings=settings)

    got = asyncio.run(both())

    assert len(seen) == 2, (
        f"HEAD then GET of one URL made {len(seen)} request(s): the key ignores the "
        "method whenever there is no body, so GET/POST/HEAD/DELETE all collide"
    )
    assert got is not None and _SENTENCE in got.body, (
        "the GET was served the HEAD's empty body out of the shared cache file"
    )


def test_the_cache_key_distinguishes_a_bodyless_post_from_a_get(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)

    def by_method(request):
        marker = f"<p>{request.method} answered here.</p>"
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"},
            content=marker.encode(), request=request,
        )

    _serve(monkeypatch, by_method)

    async def both():
        get = await fetch_record(_URL, method="GET", settings=settings)
        post = await fetch_record(_URL, method="POST", settings=settings)
        return get, post

    get, post = asyncio.run(both())

    assert get is not None and post is not None
    assert "GET answered" in get.body
    assert "POST answered" in post.body, (
        "a bodyless POST took the GET's cache entry: `json_body is None` short-circuited "
        "the method out of the key"
    )


# --- 3. the request key must carry the caller's headers ------------------------------


def test_an_authenticated_fetch_and_an_anonymous_one_do_not_share_a_cache_file(
    monkeypatch, tmp_path
):
    """The GitHub case named in T-025, and the direction that actually leaks.

    The authenticated response is the richer one, so the collision hands an anonymous
    caller a payload the origin would never have given it.
    """
    settings = settings_for(tmp_path)

    def by_auth(request):
        if request.headers.get("authorization"):
            payload = {"login": "vance", "email": "private@example.org", "total_private_repos": 7}
        else:
            payload = {"login": "vance"}
        return httpx.Response(
            200, headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(), request=request,
        )

    seen = _serve(monkeypatch, by_auth)

    async def both():
        authed = await fetch_json(
            _URL, headers={"Authorization": "Bearer ghp-token"}, settings=settings
        )
        anonymous = await fetch_json(_URL, settings=settings)
        return authed, anonymous

    authed, anonymous = asyncio.run(both())

    assert len(seen) == 2, (
        f"the anonymous fetch made {2 - len(seen)} fewer request(s) than it should: the "
        "key ignores headers, so one cache file serves both identities"
    )
    assert authed == {"login": "vance", "email": "private@example.org", "total_private_repos": 7}
    assert anonymous == {"login": "vance"}, (
        f"the anonymous caller was handed the authenticated payload: {anonymous}"
    )


def test_two_different_credentials_do_not_share_a_cache_file(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)

    def by_token(request):
        token = request.headers.get("authorization", "")
        return httpx.Response(
            200, headers={"content-type": "application/json"},
            content=json.dumps({"seen": token}).encode(), request=request,
        )

    _serve(monkeypatch, by_token)

    async def both():
        one = await fetch_json(_URL, headers={"Authorization": "Bearer aaa"}, settings=settings)
        two = await fetch_json(_URL, headers={"Authorization": "Bearer bbb"}, settings=settings)
        return one, two

    one, two = asyncio.run(both())
    assert one == {"seen": "Bearer aaa"}
    assert two == {"seen": "Bearer bbb"}, (
        "two identities shared one cache entry; the second caller read the first's answer"
    )


def test_a_header_name_is_matched_case_insensitively_so_one_identity_is_one_entry(
    monkeypatch, tmp_path
):
    """HTTP header names are case-insensitive; the cache key must agree, or the same
    request spelled two ways costs two fetches."""
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, _html(_REAL_PAGE))

    async def both():
        await fetch_text(_URL, headers={"Authorization": "Bearer aaa"}, settings=settings)
        await fetch_text(_URL, headers={"authorization": "Bearer aaa"}, settings=settings)

    asyncio.run(both())
    assert len(seen) == 1, (
        "`Authorization` and `authorization` are the same header and must be the same "
        "cache entry"
    )


def test_a_plain_header_free_get_still_lands_at_the_doc_id_filename_design_names(
    monkeypatch, tmp_path
):
    """The identity DESIGN pins, kept intact by the key change.

    `.cache/http/{doc_id}.json` with the same `doc_id` the resulting `RawDoc` carries is
    the shape TASKS T-1 acceptance 1 writes down; only requests that genuinely need
    disambiguating may move off it.
    """
    settings = settings_for(tmp_path)
    _serve(monkeypatch, _html(_REAL_PAGE))

    doc = asyncio.run(fetch_text(_URL, settings=settings))

    assert doc is not None
    assert (settings.cache_dir / f"{doc_id(_URL)}.json").exists()


# --- 4. a key change must degrade to a re-fetch, never to an error -------------------


def test_a_cache_file_written_under_the_old_key_scheme_degrades_to_a_refetch(
    monkeypatch, tmp_path
):
    """Changing the key orphans every file written under the old one, by design.

    An orphan is a MISS -- the reader looks at a path that is not there -- so the only
    thing that may happen is one extra fetch. Simulated here the way it actually happens
    on disk: a warm entry sitting at the URL-only filename while the request now carries
    headers.
    """
    settings = settings_for(tmp_path)
    seen = _serve(monkeypatch, _html(_REAL_PAGE))
    assert asyncio.run(fetch_text(_URL, settings=settings)) is not None
    assert (settings.cache_dir / f"{doc_id(_URL)}.json").exists()

    seen = _serve(monkeypatch, _html(_REAL_PAGE))
    doc = asyncio.run(
        fetch_text(_URL, headers={"Authorization": "Bearer ghp"}, settings=settings)
    )

    assert doc is not None and _SENTENCE in doc.text, (
        "an orphaned cache entry must be a miss and a clean re-fetch, never an error"
    )
    assert len(seen) == 1


def test_an_entry_written_before_expiries_existed_is_still_a_hit(tmp_path):
    """Every file already on disk lacks the expiry key; absent must mean durable."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id(_URL),
                "source_kind": "self_page",
                "url": _URL,
                "title": "Thornfield Loom",
                "text": _SENTENCE,
                "published_at": None,
                "fetched_at": "2024-05-02T10:00:00+00:00",
                "http": {"status": 200, "content_type": "text/html", "body": _REAL_PAGE},
            }
        ),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)

    assert record is not None, (
        "an envelope with no `expires_at` is an entry written before expiries existed; "
        "it must read back as durable, not as expired"
    )
    assert record.body == _REAL_PAGE


def test_an_unparseable_expiry_is_a_miss_and_never_an_exception(monkeypatch, tmp_path):
    """Same ruling as the unreadable status: a file we cannot read in full is a file we
    do not serve. A miss costs one fetch; guessing costs the life of the entry."""
    settings = settings_for(tmp_path)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    path = settings.cache_dir / f"{doc_id(_URL)}.json"

    for broken in ("not-a-date", "", 17, [], {"at": "later"}):
        path.write_text(
            json.dumps(
                {
                    "url": _URL,
                    "text": _SENTENCE,
                    "fetched_at": "2024-05-02T10:00:00+00:00",
                    "http": {
                        "status": 200,
                        "content_type": "text/html",
                        "body": _REAL_PAGE,
                        "expires_at": broken,
                    },
                }
            ),
            encoding="utf-8",
        )
        assert cache_module.read_record(settings.cache_dir, _URL) is None, (
            f"expires_at={broken!r} is unreadable, so the entry is a miss"
        )

        seen = _serve(monkeypatch, _html(_REAL_PAGE))
        doc = asyncio.run(fetch_text(_URL, settings=settings))
        assert doc is not None and _SENTENCE in doc.text, (
            f"an entry with expires_at={broken!r} must be re-fetched, not fatal"
        )
        assert len(seen) == 1


def test_an_expired_entry_is_a_miss(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "url": _URL,
                "text": _SENTENCE,
                "fetched_at": "2024-05-02T10:00:00+00:00",
                "http": {
                    "status": 200,
                    "content_type": "text/html",
                    "body": _REAL_PAGE,
                    "expires_at": "2024-05-02T10:15:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    assert cache_module.read_record(root, _URL) is None


def test_a_live_expiry_is_still_a_hit(tmp_path):
    """Positive control for the test above: the expiry must be COMPARED, not merely
    present. A reader that treats any `expires_at` as expired would pass that test and
    would also throw the whole cache away."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "url": _URL,
                "text": _SENTENCE,
                "fetched_at": "2024-05-02T10:00:00+00:00",
                "http": {
                    "status": 200,
                    "content_type": "text/html",
                    "body": _REAL_PAGE,
                    "expires_at": "2999-01-01T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)
    assert record is not None and record.body == _REAL_PAGE


def test_a_naive_expiry_timestamp_is_read_as_utc_rather_than_crashing(tmp_path):
    """`fetched_at` already tolerates a missing timezone; the expiry must too, or
    comparing it to an aware `now` raises `TypeError` out of a cache read."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "url": _URL,
                "text": _SENTENCE,
                "http": {
                    "status": 200,
                    "content_type": "text/html",
                    "body": _REAL_PAGE,
                    "expires_at": "2999-01-01T00:00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)
    assert record is not None and record.body == _REAL_PAGE


# --- 5. T-046: the fix had no effect on any cache directory that already existed -----
#
# T-025 shipped "an entry that yielded nothing expires in 900s, so a transient failure
# heals". True of every entry written AFTER it, and of nothing already on disk: "no
# `expires_at`" is the shape of BOTH a file written before expiries existed AND a pre-fix
# negative, and the reader read every one of them as durable. So the JS-shell 200 that
# motivated the whole ticket stayed frozen in exactly the caches that had one.
#
# The discriminator is the extracted text. `client._expiry_for` writes no expiry key only
# for `durable=True`, which is precisely `bool(text.strip())` -- so absent-expiry AND
# non-empty text is a pre-expiry SUCCESS and must stay durable, while absent-expiry and no
# text is a pre-expiry NEGATIVE and must be a miss.


def _write_legacy_entry(root, *, text, body=_REAL_PAGE, url=_URL):
    """A cache file in the pre-expiry shape: an `http` envelope with no `expires_at`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{doc_id(url)}.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id(url),
                "source_kind": "self_page",
                "url": url,
                "title": "",
                "text": text,
                "published_at": None,
                "fetched_at": "2024-05-02T10:00:00+00:00",
                "http": {"status": 200, "content_type": "text/html", "body": body},
            }
        ),
        encoding="utf-8",
    )


def test_a_pre_expiry_negative_entry_is_a_miss_rather_than_a_permanent_answer(tmp_path):
    """The T-025 defect, reproduced on the directory shape T-025 could not reach."""
    root = tmp_path / "cache"
    _write_legacy_entry(root, text="", body=_JS_SHELL)

    assert cache_module.read_record(root, _URL) is None, (
        "an envelope with no `expires_at` and no extracted text is a negative written "
        "before expiries existed. Reading it as durable is the permanent poisoning T-025 "
        "was raised to fix, still in force on every cache directory that predates the fix"
    )


def test_a_pre_expiry_negative_entry_heals_on_the_next_fetch(tmp_path, monkeypatch):
    """The product-level consequence: the site recovered, and the build must see it."""
    settings = settings_for(tmp_path)
    _write_legacy_entry(settings.cache_dir, text="", body=_JS_SHELL)
    seen = _serve(monkeypatch, _html(_REAL_PAGE))

    doc = asyncio.run(fetch_text(_URL, settings=settings))

    assert doc is not None and _SENTENCE in doc.text, (
        "the site is serving prose again and the poisoned pre-fix entry still answered "
        "for it"
    )
    assert len(seen) == 1, "the healed URL must actually have been re-fetched"


def test_a_whitespace_only_pre_expiry_entry_is_a_miss_too(tmp_path):
    """`"   "` is not a document; `bool(text.strip())` is the writer's own test."""
    root = tmp_path / "cache"
    _write_legacy_entry(root, text="   \n\t ", body=_JS_SHELL)

    assert cache_module.read_record(root, _URL) is None


def test_a_pre_expiry_entry_with_no_text_key_at_all_is_a_miss(tmp_path):
    """A hand-written envelope may simply omit `text`. Absent is not a document either."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps({"url": _URL, "http": {"status": 200, "body": _JS_SHELL}}),
        encoding="utf-8",
    )

    assert cache_module.read_record(root, _URL) is None


def test_a_pre_expiry_success_is_still_durable(tmp_path):
    """The constraint on the T-046 fix, and the reason it is keyed on the text rather than
    on the absent key: a reader that treated every missing expiry as expired would throw
    the entire warm cache away on its first run, which is worse than the defect."""
    root = tmp_path / "cache"
    _write_legacy_entry(root, text=_SENTENCE)

    record = cache_module.read_record(root, _URL)

    assert record is not None, (
        "an envelope with no `expires_at` but a real extracted document was written before "
        "expiries existed and is durable; this is the whole warm cache"
    )
    assert record.body == _REAL_PAGE


def test_a_recorded_fixture_with_no_envelope_is_untouched_by_the_expiry_rule(tmp_path):
    """DESIGN §Verification tests connectors by pointing the cache dir at recorded
    fixtures, which are plain `RawDoc` files with no `http` envelope at all. Breaking that
    path would break connector testing repo-wide, so it gets its own assertion."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps({"url": _URL, "title": "Thornfield Loom", "text": _SENTENCE}),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)

    assert record is not None and record.body == _SENTENCE
    assert record.content_type == "text/plain"


# --- 6. T-050: the expiry belonged to a branch instead of to the envelope ------------


def test_an_expired_envelope_is_a_miss_even_when_its_body_is_not_a_string(tmp_path):
    """A contract hole rather than a live path -- `write_record` cannot produce this shape
    -- but it is in the function every other answer in this module is derived from.

    The expiry check used to sit INSIDE the arm that reads `envelope["body"]` as a string.
    An envelope that was present and long expired, but whose `body` was (say) a number,
    fell out of that arm, landed in the plain-text arm below it, and was served -- expiry
    and all. The `text` here is a real document, so nothing else in the reader rejects it.
    """
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "url": _URL,
                "text": _SENTENCE,
                "http": {
                    "status": 200,
                    "content_type": "text/html",
                    "body": 17,
                    "expires_at": "2001-01-01T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    assert cache_module.read_record(root, _URL) is None, (
        "an expiry is a property of the ENVELOPE; an entry that is past it may not be "
        "served by whichever branch happens to find a body somewhere else"
    )


def test_an_unexpired_envelope_with_an_unusable_body_still_falls_back_to_the_text(
    tmp_path,
):
    """The control on the hoist: moving the expiry check earlier must not turn every
    odd-shaped envelope into a miss. This one is not expired, so the fallback still runs."""
    root = tmp_path / "cache"
    root.mkdir()
    (root / f"{doc_id(_URL)}.json").write_text(
        json.dumps(
            {
                "url": _URL,
                "text": _SENTENCE,
                "http": {"status": 200, "body": 17, "expires_at": "2999-01-01T00:00:00+00:00"},
            }
        ),
        encoding="utf-8",
    )

    record = cache_module.read_record(root, _URL)

    assert record is not None and record.body == _SENTENCE


def test_an_unreadable_expiry_is_a_miss_whatever_shape_the_body_has(tmp_path):
    """Same ruling as the unreadable status, applied at the envelope level: a file we
    cannot read in full is a file we should not serve."""
    root = tmp_path / "cache"
    root.mkdir()
    for expiry in ("not-a-date", 17, [], {"at": "soon"}):
        (root / f"{doc_id(_URL)}.json").write_text(
            json.dumps(
                {"url": _URL, "text": _SENTENCE, "http": {"status": 200, "body": 17,
                                                          "expires_at": expiry}}
            ),
            encoding="utf-8",
        )
        assert cache_module.read_record(root, _URL) is None, expiry
