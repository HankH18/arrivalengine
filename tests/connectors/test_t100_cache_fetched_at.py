"""T-100: `fetched_at` stops inventing a value, and `write_record` stops raising.

**The ruling, and why it went this way.**  `http/cache.py` had two opposite answers for the
same question on two fields of the same record:

* `_parse_expiry` — an unreadable `expires_at` is a MISS, "on the same ruling as the status
  below: a file we cannot read in full is a file we should not serve, and a miss costs one
  re-fetch while a guess is wrong for the entry's whole life."
* `_parse_fetched_at` — an unreadable `fetched_at` was silently replaced with
  `datetime.now(UTC)`, and the record was served with `from_cache=True` beside it.

Only one can be right, and it is the miss, for four reasons the module itself supplies:

1. The "a file we cannot read in full is a file we should not serve" rule is already
   applied to `status`, which — like `fetched_at` and unlike `expires_at` — gates nothing
   inside `read_record` and is purely descriptive.  So "descriptive fields get a default"
   is not the module's convention; it was `fetched_at`'s alone.
2. `fetched_at` is not decoration.  It reaches `RawDoc.fetched_at`, which
   `contracts.RawDoc` declares REQUIRED, and it is displayed on the digest as the retrieval
   date.  A fabricated value is a fabricated citation.
3. The fabrication was not even stable: it was `now()`, so the same file answered
   differently on every read.  A cached document reported being fetched in the present,
   forever, moving.
4. The cost of the miss was measured, not assumed: every one of the 150 committed
   cache-shaped JSON documents under `data/docs/`, `tests/fixtures/` and
   `.swarm-loop/acceptance/fixtures/` carries a readable ISO `fetched_at`, and
   `write_record` cannot produce a record without one.  Nothing that exists is turned into
   a miss; a miss is self-healing anyway — one re-fetch and `write_record` writes a
   correct stamp.

ABSENT is a miss too, on the same footing as malformed, and that is the one place this
departs from the module's "absent means the shape a fixture intends" convention: an absent
`expires_at` means durable and an absent `status` means 200 because both of those are
legal, complete `RawDoc` dumps, while `RawDoc.fetched_at` has no default at all — a
document without one does not validate as a `RawDoc` and so is not the documented shape.

**The second half** covers two escapes in `write_record`, which promises "a cache that
cannot be written is not an error" without qualification.  `record.fetched_at.isoformat()`
sat outside the `try` (an `AttributeError` for a `str` stamp) and `json.dumps` raises
`TypeError` for a non-serialisable field while the handler caught only `(OSError,
ValueError)`.  These were reachable only by a programmer error — the sole production
constructor never builds a wrong `HttpRecord` — and the finding lane declined to test them
on the grounds that a test would grade a caller that does not exist.  Closed here anyway,
because `HttpRecord` is a public, exported, UNVALIDATED dataclass: a caller constructing
one directly is using the documented API, the promise is written over `record` rather than
over "records `client.py` happens to produce", and an unconditional promise that holds only
for one caller's inputs is the T-066 defect over again.

Grading references: `read_record`/`write_record`'s own documented promises,
`contracts.RawDoc`'s field declarations, and CPython's exception hierarchy.  Nothing is
compared against a file this ticket wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from arrival.contracts import RawDoc
from arrival.http.cache import HttpRecord, cache_path, read_record, write_record

pytestmark = pytest.mark.ticket("CLIFIX")

URL = "https://example.test/t100"
STAMP = datetime(2026, 8, 30, 15, 4, 11, tzinfo=UTC)


def _record(**overrides) -> HttpRecord:
    fields = {
        "url": URL,
        "status": 200,
        "content_type": "text/html",
        "body": "<p>hello</p>",
        "fetched_at": STAMP,
    }
    fields.update(overrides)
    return HttpRecord(**fields)


def _write_raw(root, payload: dict) -> None:
    path = cache_path(root, URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _payload(**overrides) -> dict:
    payload = {
        "url": URL,
        "text": "hello",
        "fetched_at": STAMP.isoformat(),
        "http": {"status": 200, "content_type": "text/html", "body": "<p>hello</p>"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The premise the ruling rests on: `RawDoc` requires `fetched_at`.
# ---------------------------------------------------------------------------


def test_rawdoc_declares_fetched_at_required_with_no_default():
    """Graded against `contracts.RawDoc` itself. If `fetched_at` ever gains a default, the
    "an entry without one is not a `RawDoc` dump" half of the ruling stops holding."""
    field = RawDoc.model_fields["fetched_at"]
    assert field.is_required(), "RawDoc.fetched_at is no longer required"
    assert field.annotation is datetime


# ---------------------------------------------------------------------------
# 1. An unreadable `fetched_at` is a miss, exactly as an unreadable expiry is.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "", "   ", "2026-13-45T99:99:99", 17, None, [], {"at": "then"}, True],
)
def test_a_malformed_fetched_at_is_a_miss_rather_than_an_invented_now(tmp_path, value):
    """THE REPRODUCTION. This used to return a record stamped `datetime.now(UTC)` with
    `from_cache=True` — a citation date the system made up, that moved on every read."""
    root = tmp_path / "cache"
    _write_raw(root, _payload(fetched_at=value))
    assert read_record(root, URL) is None, f"a bad fetched_at {value!r} was still served"


def test_a_missing_fetched_at_key_is_a_miss(tmp_path):
    """An entry with no stamp at all is not a `RawDoc` dump, so it is not the documented
    cache shape either."""
    root = tmp_path / "cache"
    payload = _payload()
    del payload["fetched_at"]
    _write_raw(root, payload)
    assert read_record(root, URL) is None


def test_the_two_adjacent_fields_now_agree(tmp_path):
    """The point of the ticket, stated as one assertion: the same corruption on either
    field of the same record produces the same answer."""
    root = tmp_path / "cache"

    _write_raw(root, _payload(fetched_at="not-a-date"))
    bad_stamp = read_record(root, URL)

    _write_raw(root, _payload(http={
        "status": 200, "content_type": "text/html", "body": "<p>hello</p>",
        "expires_at": "not-a-date",
    }))
    bad_expiry = read_record(root, URL)

    assert bad_stamp is bad_expiry is None


def test_a_miss_from_a_bad_stamp_is_a_read_and_never_an_exception(tmp_path):
    """`read_record`'s standing promise: "A corrupt cache file is a cache miss, never an
    exception". A new miss must not become a new raise."""
    root = tmp_path / "cache"
    for value in ("not-a-date", 17, None, [], {}, True, 1.5):
        _write_raw(root, _payload(fetched_at=value))
        assert read_record(root, URL) is None


# ---------------------------------------------------------------------------
# 2. The controls: readable stamps are untouched, and the cache still works.
# ---------------------------------------------------------------------------


def test_a_good_fetched_at_survives_the_round_trip_untouched(tmp_path):
    """Without this, "unreadable is a miss" could be implemented as "everything is a miss"
    and every test above would still pass."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t")
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.fetched_at == STAMP
    assert hit.from_cache is True
    assert hit.body == "<p>hello</p>"


def test_a_naive_fetched_at_is_still_read_as_utc_rather_than_refused(tmp_path):
    """A stamp with no timezone is READABLE, merely under-specified, and the module has
    always read it as UTC. "Unreadable is a miss" must not swallow that."""
    root = tmp_path / "cache"
    _write_raw(root, _payload(fetched_at="2026-08-30T15:04:11"))
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.fetched_at == STAMP


def test_a_hand_written_plain_rawdoc_fixture_with_a_stamp_still_reads(tmp_path):
    """DESIGN §Verification's "point the cache dir at recorded fixtures" — the envelope-free
    shape, which every committed document under `data/docs/` uses."""
    root = tmp_path / "cache"
    _write_raw(root, {
        "doc_id": "x", "source_kind": "self_page", "url": URL,
        "title": "t", "text": "the extracted prose", "published_at": None,
        "fetched_at": STAMP.isoformat(),
    })
    hit = read_record(root, URL)
    assert hit is not None
    assert hit.body == "the extracted prose"
    assert hit.fetched_at == STAMP


def test_a_written_entry_with_a_future_expiry_still_reads(tmp_path):
    root = tmp_path / "cache"
    write_record(
        root, URL, _record(), text="hello", title="t",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    hit = read_record(root, URL)
    assert hit is not None and hit.fetched_at == STAMP


def test_a_bad_stamp_heals_on_the_next_write(tmp_path):
    """The whole cost of the ruling, bounded: one re-fetch, after which the entry is
    correct and durable."""
    root = tmp_path / "cache"
    _write_raw(root, _payload(fetched_at="not-a-date"))
    assert read_record(root, URL) is None

    write_record(root, URL, _record(), text="hello", title="t")
    hit = read_record(root, URL)
    assert hit is not None and hit.fetched_at == STAMP


# ---------------------------------------------------------------------------
# 3. `write_record`'s promise, made unconditional.
# ---------------------------------------------------------------------------


def test_a_record_whose_stamp_is_a_string_is_a_silent_miss_not_an_attribute_error(tmp_path):
    """`record.fetched_at.isoformat()` sat OUTSIDE the try. `HttpRecord` is a public,
    exported, unvalidated dataclass, so this is a legal construction of the documented
    type — and "a cache that cannot be written is not an error" carries no exceptions."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(fetched_at="2026-08-30T15:04:11+00:00"),
                 text="hello", title="t")
    assert read_record(root, URL) is None
    assert not cache_path(root, URL).exists()


def test_a_record_carrying_a_non_serialisable_body_is_a_silent_miss(tmp_path):
    """`json.dumps` raises `TypeError`, which is neither `OSError` nor `ValueError`, so it
    escaped the handler and came out of `fetch_record` — the one function whose docstring
    promises a never-raising door to the network."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(body=object()), text="hello", title="t")
    assert read_record(root, URL) is None


def test_a_non_serialisable_title_or_text_is_a_silent_miss(tmp_path):
    """The same escape through the two arguments a caller supplies rather than the record."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text=object(), title="t")
    assert read_record(root, URL) is None
    write_record(root, URL, _record(), text="hello", title=object())
    assert read_record(root, URL) is None


def test_a_bad_expiry_argument_is_a_silent_miss(tmp_path):
    """`expires_at.isoformat()` was outside the try as well."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t", expires_at="tomorrow")
    assert read_record(root, URL) is None


def test_a_failed_write_leaves_no_temporary_file_behind(tmp_path):
    """The handler's own job, unchanged: a mid-write failure must not accumulate one
    orphaned `.json.tmp` per failure forever."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(body=object()), text="hello", title="t")
    root.mkdir(parents=True, exist_ok=True)
    assert [p.name for p in root.iterdir()] == []


def test_a_good_write_still_writes(tmp_path):
    """The positive control for the whole of section 3."""
    root = tmp_path / "cache"
    write_record(root, URL, _record(), text="hello", title="t")
    assert cache_path(root, URL).is_file()
    stored = json.loads(cache_path(root, URL).read_text(encoding="utf-8"))
    assert stored["fetched_at"] == STAMP.isoformat()
    assert stored["http"]["body"] == "<p>hello</p>"
