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
means durable, never expired — that is the shape of every file written before expiries
existed and of every hand-written fixture, and a reader that read absence as "expired"
would throw the whole warm cache away.  This module stores the moment and compares it;
WHICH moment is `client`'s policy decision, not this one's.

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


def _parse_fetched_at(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


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

    envelope = payload.get("http")
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

        # T-025: an entry may carry an expiry, and an expired entry is a MISS.
        #
        # ABSENT MEANS DURABLE, not expired. Every file already on disk was written before
        # this key existed, and a reader that treated a missing expiry as "expired" would
        # throw the entire warm cache away the first time it ran -- which is the opposite
        # of the defect being fixed. An UNREADABLE expiry is a miss, on the same ruling as
        # the status above: a file we cannot read in full is a file we should not serve,
        # and a miss costs one re-fetch while a guess is wrong for the entry's whole life.
        raw_expiry = envelope.get("expires_at")
        if raw_expiry is not None:
            expires_at = _parse_expiry(raw_expiry)
            if expires_at is None or expires_at <= datetime.now(UTC):
                return None
    elif isinstance(payload.get("text"), str) and payload["text"].strip():
        # A hand-written fixture in plain `RawDoc` shape: its extracted text IS the body.
        body = payload["text"]
        content_type = "text/plain"
        status = 200
    else:
        return None

    return HttpRecord(
        url=str(payload.get("url") or key),
        status=status,
        content_type=content_type,
        body=body,
        fetched_at=_parse_fetched_at(payload.get("fetched_at")),
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a killed process leaves either the old file or the new one,
        # never a truncated one that every later run has to re-diagnose.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return
