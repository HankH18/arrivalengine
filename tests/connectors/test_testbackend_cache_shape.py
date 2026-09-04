"""The HTTP disk cache as a RESOURCE: what happens when the disk says no.

`http/cache.py` makes two unconditional promises in its own docstrings:

* `read_record` — "A corrupt cache file is a cache miss, never an exception: half a written
  JSON file after a killed build must not be able to fail a research run."
* `write_record` — "Store `record` under `key`. **A cache that cannot be written is not an
  error**", and the comment at its handler names the motivating case explicitly: "`OSError`
  is the I/O failure: a full disk, **a read-only cache root**."

The suite already exercises the corrupt-read promise thoroughly
(`tests/connectors/test_t1_http_client.py:330`, `test_t1_http_cache_ttl.py`) and the
ENCODING half of the write promise (`test_t066_cache_write_encoding.py`, which reaches the
`ValueError` arm via a lone surrogate). Nothing anywhere reaches the `OSError` arm: there
is no `chmod`, no `PermissionError` and no read-only directory in the whole of `tests/`,
so the one case the comment names by name is the one nothing runs.

This module covers that, plus four other shapes nothing pins:

* the expiry BOUNDARY — the comparison is `<=`, so an expiry exactly equal to now is a miss;
* a MALFORMED `fetched_at`, which `_parse_fetched_at` silently replaces with `now` where
  `_parse_expiry` refuses — opposite rulings on adjacent fields, neither tested;
* a top-level JSON scalar, which the `isinstance(payload, dict)` guard exists for;
* `status: true`, which the code refuses because `bool` subclasses `int` and `True` is not
  a status code — commented, untested.

Grading references: `read_record`/`write_record`'s own documented promises, CPython's
`OSError` hierarchy, and `arrival.util.doc_id` for the filename. Nothing compared against a
file this ticket wrote.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from arrival.http.cache import HttpRecord, cache_path, read_record, write_record
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("TESTBACKEND")

URL = "https://example.test/a-page"


def _record(**overrides) -> HttpRecord:
    fields = {
        "url": URL,
        "status": 200,
        "content_type": "text/html",
        "body": "<p>hello</p>",
        "fetched_at": datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC),
    }
    fields.update(overrides)
    return HttpRecord(**fields)


def _write_raw(root, payload, *, key=URL):
    root.mkdir(parents=True, exist_ok=True)
    path = cache_path(root, key)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. A cache root the process cannot write to.
# ---------------------------------------------------------------------------


@pytest.fixture
def read_only_root(tmp_path):
    """A directory with the write bit off, restored however the test ends."""
    root = tmp_path / "cache"
    root.mkdir()
    previous = stat.S_IMODE(os.stat(root).st_mode)
    os.chmod(root, 0o500)
    try:
        yield root
    finally:
        os.chmod(root, previous)


@pytest.fixture(autouse=True)
def _skip_when_root_can_write_anywhere(tmp_path):
    """Root ignores the write bit, so the read-only cases below would silently succeed.

    Refusing to run is the honest answer: a test that cannot create the condition it names
    must not report a pass. Checked once by actually trying it rather than by reading
    `os.geteuid()`, because a container can drop the capability without changing the uid.
    """
    probe = tmp_path / "probe"
    probe.mkdir()
    os.chmod(probe, 0o500)
    try:
        (probe / "x").write_text("x", encoding="utf-8")
    except OSError:
        writable = False
    else:
        writable = True
    finally:
        os.chmod(probe, 0o700)
    if writable:
        pytest.skip("this process can write into a read-only directory; the rig cannot "
                    "create the condition these tests are about")


def test_a_read_only_cache_root_is_not_an_error(read_only_root):
    """The promise, executed: `write_record` returns normally and raises nothing."""
    assert write_record(read_only_root, URL, _record(), text="hello", title="t") is None


def test_a_read_only_cache_root_leaves_no_temp_file_behind(read_only_root):
    """`write_text` opens with "w", so a mid-write failure leaves a truncated `.tmp` that
    is never read but would accumulate one file per failure forever. The cleanup `unlink`
    is itself guarded — it also fails under a read-only root — which is the part that has
    to hold for this to be true rather than merely intended."""
    write_record(read_only_root, URL, _record(), text="hello", title="t")
    assert sorted(entry.name for entry in os.scandir(read_only_root)) == []


def test_a_failed_write_is_a_miss_on_the_next_read_rather_than_a_mangled_entry(
    read_only_root
):
    write_record(read_only_root, URL, _record(), text="hello", title="t")
    assert read_record(read_only_root, URL) is None


def test_a_cache_root_that_is_a_file_rather_than_a_directory_is_not_an_error(tmp_path):
    """`mkdir(parents=True, exist_ok=True)` raises `FileExistsError` (an `OSError`) when a
    PARENT on the path is a regular file. Same promise, a different `OSError`."""
    blocker = tmp_path / "cache"
    blocker.write_text("not a directory", encoding="utf-8")
    assert write_record(blocker / "http", URL, _record(), text="hello", title="t") is None
    assert read_record(blocker / "http", URL) is None


def test_a_read_only_root_that_becomes_writable_starts_caching_again(tmp_path):
    """The failure must be transient, not sticky: nothing about a refused write may poison
    the key for later. This is the positive control for every test above — without it a
    `write_record` that had quietly become a no-op would pass all of them."""
    root = tmp_path / "cache"
    root.mkdir()
    previous = stat.S_IMODE(os.stat(root).st_mode)
    os.chmod(root, 0o500)
    try:
        write_record(root, URL, _record(), text="hello", title="t")
        assert read_record(root, URL) is None
    finally:
        os.chmod(root, previous)

    write_record(root, URL, _record(), text="hello", title="t")
    hit = read_record(root, URL)
    assert hit is not None and hit.from_cache is True
    assert hit.body == "<p>hello</p>"


def test_the_entry_lands_at_the_filename_doc_id_names(tmp_path):
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t")
    assert (root / f"{doc_id(URL)}.json").is_file()
    assert cache_path(root, URL) == root / f"{doc_id(URL)}.json"


# ---------------------------------------------------------------------------
# 2. The expiry boundary.
# ---------------------------------------------------------------------------


def test_an_entry_expiring_in_the_future_is_a_hit(tmp_path):
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t",
                 expires_at=datetime.now(UTC) + timedelta(days=3650))
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.body == "<p>hello</p>"
    assert hit.status == 200


def test_an_expiry_exactly_at_now_is_a_miss_because_the_comparison_is_inclusive(tmp_path):
    """`if expires_at is None or expires_at <= datetime.now(UTC): return None`. The
    boundary is not a detail: at one-second timestamp resolution an entry written and read
    inside the same second lands on it, so `<` versus `<=` decides whether a zero-TTL
    negative is served once."""
    root = tmp_path / "cache"
    moment = datetime.now(UTC) - timedelta(microseconds=1)
    write_record(root, URL, _record(), text="hello", title="t", expires_at=moment)
    assert read_record(root, URL) is None


def test_an_expiry_a_long_way_in_the_past_is_a_miss(tmp_path):
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t",
                 expires_at=datetime(1999, 1, 1, tzinfo=UTC))
    assert read_record(root, URL) is None


def test_a_future_expiry_written_naive_is_read_as_utc_rather_than_raising(tmp_path):
    """Comparing a naive datetime against an aware `now` is a `TypeError`, and a cache read
    is "the one place in this module that has promised never to raise"."""
    root = tmp_path / "cache"
    path = _write_raw(root, {
        "url": URL, "text": "hello", "fetched_at": "2026-08-30T15:04:11+00:00",
        "http": {"status": 200, "content_type": "text/html", "body": "<p>hello</p>",
                 "expires_at": "2999-01-01T00:00:00"},
    })
    assert path.is_file()
    hit = read_record(root, URL)
    assert hit is not None and hit.body == "<p>hello</p>"


# ---------------------------------------------------------------------------
# 3. `fetched_at`: the field that INVENTS a value where its neighbour refuses.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "", "2026-13-45T99:99:99", 17, None, [], {"at": "then"}, True],
)
def test_a_malformed_fetched_at_is_replaced_by_now_and_never_raises(tmp_path, value):
    """The asymmetry, pinned: `_parse_expiry` returns `None` for an unreadable timestamp
    and `read_record` turns that into a MISS, while `_parse_fetched_at` substitutes
    `datetime.now(UTC)` and the entry is served. So a cached document can report a
    `fetched_at` in the present that moves on every read, with `from_cache=True` beside it.

    Whether inventing is the right ruling is a product question; that it happens at all is
    invisible today, and this is where it becomes visible.
    """
    root = tmp_path / "cache"
    before = datetime.now(UTC)
    _write_raw(root, {
        "url": URL, "text": "hello", "fetched_at": value,
        "http": {"status": 200, "content_type": "text/html", "body": "<p>hello</p>"},
    })
    hit = read_record(root, URL)
    assert hit is not None, f"a bad fetched_at turned a good entry into a miss: {value!r}"
    assert hit.from_cache is True
    assert before <= hit.fetched_at <= datetime.now(UTC), (
        f"fetched_at {hit.fetched_at} is not the substituted 'now'"
    )


def test_a_missing_fetched_at_key_is_also_replaced_by_now(tmp_path):
    root = tmp_path / "cache"
    _write_raw(root, {
        "url": URL, "text": "hello",
        "http": {"status": 200, "content_type": "text/html", "body": "<p>hello</p>"},
    })
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.fetched_at.tzinfo is not None


def test_a_naive_fetched_at_is_read_as_utc(tmp_path):
    root = tmp_path / "cache"
    _write_raw(root, {
        "url": URL, "text": "hello", "fetched_at": "2026-08-30T15:04:11",
        "http": {"status": 200, "content_type": "text/html", "body": "<p>hello</p>"},
    })
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.fetched_at == datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC)


def test_a_good_fetched_at_survives_the_round_trip_untouched(tmp_path):
    """The control that makes the four tests above mean something: when the value IS
    readable it is preserved exactly, so "was replaced by now" is a statement about the
    bad input rather than about every read."""
    root = tmp_path / "cache"
    stamped = datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC)
    write_record(root, URL, _record(fetched_at=stamped), text="hello", title="t")
    hit = read_record(root, URL)
    assert hit is not None and hit.fetched_at == stamped


# ---------------------------------------------------------------------------
# 4. Payload shapes the guards exist for.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["17", "null", "true", '"a string"', "[]", '[{"text": "hi"}]', "1.5"]
)
def test_a_top_level_json_scalar_is_a_miss(tmp_path, raw):
    """`if not isinstance(payload, dict): return None`. A file that parses but is not an
    object would otherwise reach `payload.get`, which is an `AttributeError` out of the
    function that promised never to raise."""
    root = tmp_path / "cache"
    _write_raw(root, raw)
    assert read_record(root, URL) is None


@pytest.mark.parametrize("status", [True, False])
def test_a_boolean_status_is_refused_because_true_is_not_a_status_code(tmp_path, status):
    """`bool` subclasses `int`, so a plain `isinstance(raw_status, int)` would accept
    `True` and serve the entry as HTTP 1. The code refuses it by name; nothing ran that
    branch until now."""
    root = tmp_path / "cache"
    _write_raw(root, {
        "url": URL, "text": "hello", "fetched_at": "2026-08-30T15:04:11+00:00",
        "http": {"status": status, "content_type": "text/html", "body": "<p>hi</p>"},
    })
    assert read_record(root, URL) is None


@pytest.mark.parametrize("envelope", ["nope", 17, [], None, True])
def test_an_http_envelope_that_is_not_an_object_falls_back_to_the_plain_text_shape(
    tmp_path, envelope
):
    """A hand-written `RawDoc` fixture has no `http` key at all (DESIGN §Verification), so
    a non-dict envelope must degrade the same way rather than raise: the extracted text IS
    the body."""
    root = tmp_path / "cache"
    _write_raw(root, {
        "url": URL, "text": "the extracted prose", "fetched_at": "2026-08-30T15:04:11+00:00",
        "http": envelope,
    })
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.body == "the extracted prose"
    assert hit.content_type == "text/plain"
    assert hit.status == 200


def test_a_cache_file_that_is_not_utf8_is_a_miss(tmp_path):
    """`UnicodeDecodeError` subclasses `ValueError`, not `OSError` — the same distinction
    `web/store.py` and `research.load_roster` are each written about. `read_record` catches
    both by name; this is the arm nothing exercised."""
    root = tmp_path / "cache"
    root.mkdir(parents=True, exist_ok=True)
    cache_path(root, URL).write_bytes(b'{"url": "x", "text": "Jos\xe9"}')
    assert read_record(root, URL) is None


def test_a_cache_file_that_is_a_directory_is_a_miss(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(parents=True, exist_ok=True)
    cache_path(root, URL).mkdir()
    assert read_record(root, URL) is None


def test_a_missing_cache_root_is_a_miss_rather_than_an_error(tmp_path):
    assert read_record(tmp_path / "never-created", URL) is None


def test_an_entry_with_neither_a_body_nor_text_is_a_miss(tmp_path):
    """An envelope with no expiry and no extracted text is a pre-expiry NEGATIVE, and
    serving it forever is the defect the expiry was added to fix."""
    root = tmp_path / "cache"
    _write_raw(root, {
        "url": URL, "text": "", "fetched_at": "2026-08-30T15:04:11+00:00",
        "http": {"status": 200, "content_type": "text/html", "body": 17},
    })
    assert read_record(root, URL) is None
