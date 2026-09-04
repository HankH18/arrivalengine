"""T-067: `doc_id` was partial, and three of its four call paths were unguarded.

THE DEFECT, measured before the fix.  `util.doc_id` was `hashlib.sha1(url.encode())`, and
a bare `.encode()` is utf-8 in STRICT mode:

    File ".../arrival/util.py", line 60, in doc_id
      return hashlib.sha1(url.encode()).hexdigest()[:16]
    UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud800' in position 20:
      surrogates not allowed

A whole-file sweep of the 51 encoding decision sites in `src/arrival/` found six that could
raise with no enclosing handler, and FOUR of the six were this one function reached from an
unguarded call site:

    util.py:60          the function itself
    http/cache.py:62    `cache_path`, called from `read_record`/`write_record` ABOVE their
                        own try blocks
    http/cache.py:209   `doc_id(record.url)` in `write_record`, above the try at :218
    http/client.py:489  `doc_id(record.url)` in `fetch_text`, which has no try at all

Only `connectors/base.py:240` was covered, by an `except Exception` two frames up.

WHY THE FUNCTION AND NOT THE CALLERS.  `doc_id` is the join key for the entire pipeline --
the cache filename, `RawDoc.doc_id`, and what `Dossier.resolution.accepted_doc_ids` cites
-- so the objection to touching it is that changing what it returns has reach.  It does not
change what it returns.  `errors="surrogatepass"` differs from strict ONLY on surrogate
code points, which strict refuses outright, so every id the function has ever produced it
still produces, byte for byte; it only DEFINES a return where there was an exception.  That
claim is not asserted here, it is executed: the identity is checked over every non-surrogate
code point from U+0000 to U+10FFF plus a random corpus, against `hashlib` directly.

Hardening the three call sites instead would leave the fourth caller broken, leave the next
caller broken, and -- if any site chose to substitute a different string -- give one url two
different ids depending on who asked, which is the one thing a join key may never do.

Graded against `hashlib`, the CPython exception hierarchy, and the id literal pinned in
`util.doc_id`'s own doctest since T-0.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import random
from datetime import UTC, datetime

import pytest

from arrival.http import client as client_module
from arrival.http.cache import HttpRecord, cache_path, read_record, write_record
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("T-067")

LONE_SURROGATE = "\ud800"
SURROGATE_URL = f"https://example.com/bio/{LONE_SURROGATE}/page"
URL_WITHOUT_SURROGATE = "https://example.com/bio/page"

#: Pinned by `doc_id`'s doctest since T-0, and by `tests/test_t0_util.py`. The single most
#: load-bearing literal in this module: if the encoding change moved any existing id, it
#: moved this one.
EXAMPLE_COM_ID = "b559c7edd3fb6737"


# --- 1. the change is invisible to every url that already worked ----------------------


def test_the_id_pinned_since_t0_is_unchanged():
    assert doc_id("https://example.com/") == EXAMPLE_COM_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com",
        "https://northgatelabs.example/team/teodoro-vance",
        "https://example.com/José",
        "https://example.com/日本語",
        "https://example.com/emoji/\U0001f600",
        "https://example.com/path?q=a+b&r=c%20d#frag",
        "",
    ],
)
def test_doc_id_still_equals_the_strict_utf8_sha1_prefix(url):
    """The independent answer key is `hashlib` computing what the OLD code computed."""
    assert doc_id(url) == hashlib.sha1(url.encode()).hexdigest()[:16]


def test_doc_id_equals_the_strict_answer_for_every_non_surrogate_code_point():
    """Exhaustive across the boundary the change lives on, plus a random corpus.

    Surrogates are U+D800-U+DFFF. Every code point on either side of that gap must hash
    exactly as it did before, or the join key moved under the whole corpus.
    """
    mismatches = []
    for code_point in itertools.chain(range(0, 0xD800), range(0xE000, 0x11000)):
        url = f"https://example.com/{chr(code_point)}"
        if doc_id(url) != hashlib.sha1(url.encode()).hexdigest()[:16]:
            mismatches.append(hex(code_point))

    random.seed(20670)
    alphabet = [chr(c) for c in range(0x20, 0x2FF)] + ["日", "é", "\U0001f600", "/", "?"]
    for _ in range(2000):
        url = "".join(random.choice(alphabet) for _ in range(random.randint(0, 40)))
        if doc_id(url) != hashlib.sha1(url.encode()).hexdigest()[:16]:
            mismatches.append(repr(url))

    assert mismatches == [], (
        f"{len(mismatches)} urls hash differently than they did before the encoding "
        f"change, e.g. {mismatches[:5]}. Every cache file and every cited doc_id in the "
        "committed corpus is keyed by this function"
    )


# --- 2. it is now total -----------------------------------------------------------------


def test_a_bare_encode_really_does_refuse_a_lone_surrogate():
    """The premise, executed: this is what the old line did, in the stdlib alone."""
    with pytest.raises(UnicodeEncodeError):
        SURROGATE_URL.encode()


def test_doc_id_answers_for_a_url_holding_a_lone_surrogate():
    result = doc_id(SURROGATE_URL)

    assert len(result) == 16
    assert all(c in "0123456789abcdef" for c in result)


def test_surrogate_urls_still_discriminate():
    """Totality is worthless if every pathological url collapses to one id."""
    assert doc_id(f"https://a.example/{LONE_SURROGATE}") != doc_id(
        f"https://b.example/{LONE_SURROGATE}"
    )
    assert doc_id(SURROGATE_URL) != doc_id("https://example.com/bio/page")


def test_doc_id_is_stable_across_calls():
    assert doc_id(SURROGATE_URL) == doc_id(SURROGATE_URL)


# --- 3. the three call sites the sweep found unguarded --------------------------------


def test_cache_path_survives_a_surrogate_key(tmp_path):
    """`http/cache.py:62`. Reached from `read_record` and `write_record` ABOVE both of
    their try blocks, so it took `fetch_record` down with it."""
    path = cache_path(tmp_path, SURROGATE_URL)

    assert path.suffix == ".json"
    assert path.parent == tmp_path


def test_read_record_treats_a_surrogate_key_as_a_plain_miss(tmp_path):
    assert read_record(tmp_path, SURROGATE_URL) is None


def _document_record(url: str) -> HttpRecord:
    return HttpRecord(
        url=url,
        status=200,
        content_type="text/html",
        body="<html><body><p>a document</p></body></html>",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_write_record_stores_an_entry_addressed_by_a_surrogate_key(tmp_path):
    """`http/cache.py:200`: the FILENAME comes from `doc_id(key)`, above the try at :218.

    The key alone carries the surrogate here, so the payload is perfectly encodable and
    the entry must genuinely round-trip. This is the sharp form of the `doc_id` fix: an
    unaddressable request became an addressable one, and nothing else changed.
    """
    write_record(tmp_path, SURROGATE_URL, _document_record(URL_WITHOUT_SURROGATE),
                 text="a document", title="T")

    stored = read_record(tmp_path, SURROGATE_URL)
    assert stored is not None, (
        "a key containing a surrogate is now addressable, so the entry should round-trip"
    )
    assert stored.url == URL_WITHOUT_SURROGATE
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]


def test_a_url_that_cannot_be_encoded_degrades_to_a_miss_rather_than_a_raise(tmp_path):
    """`http/cache.py:209`: `doc_id(record.url)` is built into the payload, and that
    statement sits OUTSIDE the try that was supposed to make a cache write harmless.

    Hardening `doc_id` does not make this entry storable, and it was never supposed to:
    `record.url` also goes into the JSON payload verbatim, so the file itself has no
    UTF-8 representation. The two fixes compose -- T-067 stops the raise at :209, T-066
    turns the still-doomed `write_text` into the documented miss -- and the outcome is
    the one the docstring promises: no exception, no entry, no debris.
    """
    write_record(tmp_path, SURROGATE_URL, _document_record(SURROGATE_URL),
                 text="a document", title="T")

    assert read_record(tmp_path, SURROGATE_URL) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == [], (
        "a write that could not complete left debris in the cache root"
    )


def test_fetch_text_survives_a_record_whose_resolved_url_holds_a_surrogate(
    monkeypatch, tmp_path
):
    """`http/client.py:489`: `doc_id=doc_id(record.url)` in `fetch_text`, which has no
    try/except of its own, so the raise came straight out of the never-raising door.

    `record.url` is the RESOLVED url -- `str(response.url)` after redirects -- so the
    seam is stubbed at exactly the point the real value arrives.
    """
    record = HttpRecord(
        url=SURROGATE_URL,
        status=200,
        content_type="text/html",
        body="<html><body><p>Chief of staff at Northgate Labs</p></body></html>",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def fake_fetch_record(url, **_):
        return record

    monkeypatch.setattr(client_module, "fetch_record", fake_fetch_record)

    doc = asyncio.run(client_module.fetch_text(SURROGATE_URL))

    assert doc is not None, "fetch_text degraded a perfectly good document to None"
    assert doc.doc_id == doc_id(SURROGATE_URL)
    assert "Northgate Labs" in doc.text
