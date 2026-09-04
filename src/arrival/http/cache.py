"""The on-disk HTTP cache: `.cache/http/{doc_id}.json` (DESIGN §Data models).

WHAT IS STORED, and why it is not simply a `RawDoc` dump.  DESIGN calls the cache file "a
`RawDoc` dump", and this writes one — plus an `http` envelope carrying the untouched
response body and content type.  Two measured reasons:

1. `RawDoc.text` is capped at 20k and, for HTML, is *extracted* text with the markup gone.
   A connector that needs to parse the API response it just fetched cannot do it from a
   lossy projection, so re-fetching would be the only alternative, which is the exact cost
   the cache exists to avoid.
2. `RawDoc.source_kind` is a property of *who asked*, not of the URL.  The wayback
   connector and the self_page connector can legitimately fetch the same address; keying
   the file by `doc_id(url)` and freezing one connector's `source_kind` into it would hand
   the second caller a citation naming the wrong source.  So the stored `source_kind` is
   advisory and the caller's is re-stamped on read.

Pydantic ignores unknown keys by default, so the file still validates as a `RawDoc`, which
keeps DESIGN §Verification's "point the cache dir at recorded fixtures" honest: a
hand-written file with no `http` envelope is read back as a plain-text document.

LIFETIME.  The envelope may carry an `expires_at`, and an entry past it is a MISS.  ABSENT
means durable — that is the shape of every file written before expiries existed and of
every hand-written fixture, and a reader that read absence as "expired" would throw the
whole warm cache away — but durable ONLY FOR AN ENTRY THAT HAS A DOCUMENT.  An envelope
with no expiry and no extracted text is a negative written before expiries existed, and
serving it forever is the exact defect the expiry was added to fix (T-046).  This module
stores the moment and compares it; WHICH moment is `client`'s policy decision, not this
one's.

The directory is gitignored (`.gitignore` carries `.cache/`) and is CWD-relative by
default — that is `Settings.cache_dir`'s documented shape, and hazard: a process started
from a subdirectory therefore uses a different cache root.  Every caller here takes the
root as an argument rather than reaching for the setting itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from arrival.util import doc_id

__all__ = ["HttpRecord", "cache_path", "read_record", "write_record"]


@dataclass(frozen=True)
class HttpRecord:
    """One fetched response, as it came off the wire (or off the disk)."""

    url: str
    status: int
    content_type: str
    body: str
    fetched_at: datetime
    from_cache: bool = False


def cache_path(root: Path, key: str) -> Path:
    """`{root}/{doc_id(key)}.json`. `key` is the request identity, normally the URL."""
    return Path(root) / f"{doc_id(key)}.json"


def _parse_fetched_at(value: object) -> datetime | None:
    """`value` as an aware datetime, or None when it is not one.

    T-100. This used to return `datetime.now(UTC)` for anything it could not read, which
    made `read_record` serve the entry anyway with a fetch time IN THE PRESENT that moved
    on every read, `from_cache=True` beside it. The value reaches `RawDoc.fetched_at` and
    is displayed on the digest as the retrieval date, so that was a fabricated citation.

    It now refuses, on the module's own rule, already stated twice for two other fields of
    this same record: an unreadable `expires_at` is a miss, and a present-but-unreadable
    `status` is a miss, because "a file we cannot read in full is a file we should not
    serve" and "a miss costs one re-fetch while a guess is wrong for the entry's whole
    life". `status` is, like this field and unlike the expiry, purely descriptive and
    gates nothing here — so "descriptive fields get a default" was never the convention;
    it was this field's alone.

    ABSENT is refused too, which is the one place this departs from the module's
    "absence is the shape a fixture intends" reading, and the reason is that this field
    HAS NO HONEST DEFAULT. Absence is expressible for both neighbours: no expiry means
    durable, a real state with a real representation (`None`), and no status means 200,
    which is what a fixture carrying no envelope metadata actually asserts.
    `HttpRecord.fetched_at` is a non-optional `datetime`, so there is no way to say "I do
    not know when this was fetched" — every tolerated absence must INVENT a moment, and an
    invented moment is precisely the provenance claim this change exists to stop.
    `RawDoc.fetched_at` is required for the same reason, so a payload without one is not a
    `RawDoc` dump and was never the documented shape. Measured before ruling: all 150 committed
    cache-shaped documents under `data/docs/`, `tests/fixtures/` and the frozen fixtures
    carry a readable ISO stamp, and `write_record` cannot produce one that does not — so
    nothing that exists is turned into a miss, and a miss heals on the next write.

    A NAIVE stamp is readable, merely under-specified, and is still read as UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_expiry(value: object) -> datetime | None:
    """`value` as an aware datetime, or None when it is not one.

    A naive timestamp is read as UTC, the same way `fetched_at` is: comparing a naive
    datetime against an aware `now` raises `TypeError`, and a cache read is the one place
    in this module that has promised never to raise.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def read_record(root: Path, key: str) -> HttpRecord | None:
    """The cached response for `key`, or None when there is none (or it is unreadable).

    A corrupt cache file is a cache miss, never an exception: half a written JSON file
    after a killed build must not be able to fail a research run.
    """
    path = cache_path(root, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    # T-100: read BEFORE anything else about the body, because this is a judgement on the
    # whole payload rather than on one arm of the branch below -- a record with no
    # readable fetch time is not a complete record, whichever shape its body arrives in.
    fetched_at = _parse_fetched_at(payload.get("fetched_at"))
    if fetched_at is None:
        return None

    envelope = payload.get("http")
    stored_text = payload.get("text")
    has_document = isinstance(stored_text, str) and bool(stored_text.strip())

    # T-050: the lifetime is a property of the ENVELOPE, so it is read BEFORE the branch
    # that decides where the body comes from -- not inside one of them. It used to sit
    # inside the `isinstance(envelope["body"], str)` arm, so an entry whose envelope was
    # present and EXPIRED but whose `body` was not a string fell through to the plain-text
    # arm below and was served past its expiry. `write_record` cannot produce that shape,
    # so this closes a contract hole rather than a live path -- but the hole is in the one
    # function whose answer everything else in this module is derived from.
    raw_expiry = envelope.get("expires_at") if isinstance(envelope, dict) else None
    if raw_expiry is not None:
        expires_at = _parse_expiry(raw_expiry)
        # An UNREADABLE expiry is a miss, on the same ruling as the status below: a file we
        # cannot read in full is a file we should not serve, and a miss costs one re-fetch
        # while a guess is wrong for the entry's whole life.
        if expires_at is None or expires_at <= datetime.now(UTC):
            return None

    if isinstance(envelope, dict) and isinstance(envelope.get("body"), str):
        body = envelope["body"]
        content_type = str(envelope.get("content_type") or "text/plain")
        # Type-checked rather than coerced. `int("ok")` and `int({"a": 1})` both raise,
        # and this runs BEFORE `fetch_record`'s try block, so either one escaped all the
        # way out of `fetch_text` and turned a corrupt cache file into a crashed build --
        # the exact thing this function's docstring promises cannot happen.
        #
        # A present-but-unreadable status is a MISS, not a defaulted 200: the status is
        # part of the record, and a file we cannot read in full is a file we should not
        # serve. A miss costs one re-fetch; a wrong status is wrong for the life of the
        # cache entry. `bool` is refused because it subclasses `int` and `True` is not a
        # status code. An ABSENT status still means 200, which is what a hand-written
        # fixture with no envelope metadata intends.
        raw_status = envelope.get("status")
        if raw_status is None:
            status = 200
        elif isinstance(raw_status, int) and not isinstance(raw_status, bool):
            status = raw_status
        else:
            return None

        # T-025/T-046: an entry may carry an expiry, and an expired entry is a MISS. The
        # expiry itself was checked above; what is left is what ABSENCE means.
        #
        # ABSENT MEANS DURABLE -- but only for an entry that HAS a document. That
        # qualification is T-046, and without it T-025's headline fix was invisible on
        # every cache directory that already existed: "no expiry" is the shape of both a
        # file written before expiries existed AND a pre-fix NEGATIVE, so a JS-shell 200
        # frozen into a warm cache stayed frozen and the claim that "a transient failure
        # heals" was true only of entries written after the fix. An envelope with no
        # expiry and no extracted text is that pre-fix negative, and it is a MISS.
        #
        # This cannot reach the recorded-fixture path below: that branch already requires
        # non-empty text, so `has_document` is true for every entry that can take it.
        # Nor can it orphan anything `write_record` produced -- `client._expiry_for`
        # returns None (writes no key) only for `durable=True`, which is exactly
        # `bool(text.strip())`. The writer already held the invariant; only the reader
        # failed to, which is why the defect survived on old directories alone.
        if raw_expiry is None and not has_document:
            return None
    elif has_document:
        # A hand-written fixture in plain `RawDoc` shape: its extracted text IS the body.
        body = str(stored_text)
        content_type = "text/plain"
        status = 200
    else:
        return None

    return HttpRecord(
        url=str(payload.get("url") or key),
        status=status,
        content_type=content_type,
        body=body,
        fetched_at=fetched_at,
        from_cache=True,
    )


def write_record(
    root: Path,
    key: str,
    record: HttpRecord,
    *,
    text: str,
    title: str,
    expires_at: datetime | None = None,
) -> None:
    """Store `record` under `key`. A cache that cannot be written is not an error.

    `expires_at=None` writes a DURABLE entry -- no expiry key at all, which is also the
    shape of every file written before expiries existed and of every hand-written fixture
    (DESIGN §Verification). A caller that wants the entry to age out passes the moment it
    should stop being served; `client.fetch_record` is the only one that does.
    """
    path = cache_path(root, key)
    # Write-then-rename: a killed process leaves either the old file or the new one,
    # never a truncated one that every later run has to re-diagnose. Named OUTSIDE the
    # try so the handler can always clean it up.
    temporary = path.with_suffix(".json.tmp")
    # T-100: the payload is BUILT INSIDE the try, and it used to be built outside. Two
    # escapes lived in those lines, and both contradicted this function's unconditional
    # docstring the same way T-066's `UnicodeEncodeError` did:
    #
    #   * `record.fetched_at.isoformat()` is an `AttributeError` for an `HttpRecord`
    #     whose stamp is a `str`, and `expires_at.isoformat()` the same for a `str` expiry;
    #   * `json.dumps` raises `TypeError` -- neither `OSError` nor `ValueError` -- for a
    #     non-serialisable `body`, `text` or `title`.
    #
    # Reaching either needs a caller mistake: `HttpRecord` is an unvalidated dataclass and
    # `client.fetch_record`, its only production constructor, never builds one wrongly. It
    # is nonetheless PUBLIC and exported, the promise above is written over `record` rather
    # than over one caller's habits, and an unconditional promise that holds only for the
    # inputs today's sole caller happens to produce is exactly the shape of the defect
    # T-066 closed. A miss is the right answer here for the same reason it is below.
    try:
        envelope: dict[str, object] = {
            "status": record.status,
            "content_type": record.content_type,
            "body": record.body,
        }
        if expires_at is not None:
            envelope["expires_at"] = expires_at.isoformat()
        payload = {
            "doc_id": doc_id(record.url),
            "source_kind": "self_page",  # advisory only; re-stamped by the caller on read
            "url": record.url,
            "title": title,
            "text": text,
            "published_at": None,
            "fetched_at": record.fetched_at.isoformat(),
            "http": envelope,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    # BOTH arms are load-bearing, and this docstring's promise -- "a cache that cannot be
    # written is not an error" -- was only ever true of the first (T-066). `OSError` is
    # the I/O failure: a full disk, a read-only cache root. `UnicodeEncodeError` is the
    # ENCODING failure, and it subclasses `ValueError`, NOT `OSError`, so it escaped this
    # handler entirely and came out of `fetch_record` -- the one function whose module
    # docstring promises a never-raising door to the network.
    #
    # The chain is not hypothetical; it was measured end to end. A remote JSON body may
    # contain the escape `"\ud800"`, which is pure ASCII on the wire and decodes cleanly.
    # `json.loads` turns it into a real lone surrogate, `extract.json_to_text` re-emits it
    # as one because it serialises with `ensure_ascii=False` (extract.py:210), and that
    # string arrives here as `text`. `write_text(encoding="utf-8")` is strict, so it
    # raises `UnicodeEncodeError: surrogates not allowed`.
    #
    # A miss is the right answer, not a sanitised write: a body rewritten to get past the
    # codec would answer differently warm than cold, which `read_record` above calls a
    # worse defect than the one being fixed. The cost is one re-fetch per affected url.
    #
    # `TypeError` and `AttributeError` are T-100's two, and they are scoped as tightly as
    # the arms above: the only calls in this block that can raise them are the two
    # `.isoformat()`s and `json.dumps`.
    except (OSError, TypeError, ValueError, AttributeError):
        # `write_text` opens with "w", so a mid-write failure leaves a TRUNCATED .tmp on
        # disk. It is never read (readers only open `{doc_id}.json`), but it would
        # accumulate one file per failure forever. `research.py:_write_json` cleans up the
        # same way. The unlink is itself guarded: failing to tidy up is not an error either.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return
