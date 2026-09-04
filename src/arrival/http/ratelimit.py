"""A per-host token bucket (SPEC C5).

WHY PER HOST AND NOT GLOBAL.  T-6 fans out over ten connectors for every person on the
roster.  A blanket sleep on every request would make that fan-out pointlessly serial: the
only reason to slow down is that one *server* is being asked for too much, and ten servers
are ten independent budgets.  So the state is keyed by hostname and nothing else.

THE ALGORITHM.  A token bucket of `capacity` tokens refilling at `rate` tokens/second,
implemented in its virtual-scheduling (GCRA) form: instead of storing a token count and a
timestamp, store the *theoretical arrival time* of the next request.  Two properties come
free and both are load-bearing here:

* **No spin loop.**  The wait is computed once, in synchronous code, and slept once.  A
  limiter that loops `while not allowed: await sleep(small)` re-reads the clock, and under
  a test that freezes the clock (which is how a rate limiter must be tested — see
  `test_client_rate_limit`) it never terminates.
* **Correct under concurrency without a lock.**  The reservation is made in one
  uninterrupted synchronous block, so two coroutines racing for the same host get two
  *different* slots rather than the same one.  A lock would be the obvious alternative and
  is the wrong tool: `asyncio.Lock` binds to the event loop that first awaits it, and this
  table outlives any single `asyncio.run()`.

RATES.  TASKS T-1 acceptance 1 writes down: SEC 10/s, arXiv 1/3s, USPTO 45/min,
Wayback 1/s, default 2/s.  They are host-suffix matched, so `efts.sec.gov` and
`www.sec.gov` share one budget, as the operator of sec.gov would expect.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_RATE_PER_SEC",
    "HOST_RATE_PER_SEC",
    "HostRateLimiter",
    "host_of",
    "limiter",
    "rate_for_host",
]

#: Requests per second for a host with no special rule (TASKS T-1 acceptance 1).
DEFAULT_RATE_PER_SEC = 2.0

#: Host suffix -> requests per second. Every value is the *published* courtesy limit of
#: that service, not a guess: exceeding them is how a free source stops being free.
HOST_RATE_PER_SEC: dict[str, float] = {
    "sec.gov": 10.0,  # SEC EDGAR fair-access policy
    "archive.org": 1.0,  # Wayback / CDX
    "arxiv.org": 1.0 / 3.0,
    "uspto.gov": 45.0 / 60.0,
    "api.crossref.org": 1.0,
}

#: Burst allowance, in seconds of the host's own rate. Two seconds of credit lets a
#: connector open with a search + a detail fetch without waiting, and still throttles.
_BURST_SECONDS = 2.0

#: Never let the burst exceed this, or a fast host (SEC at 10/s) would bank 20 requests.
_MAX_BURST_TOKENS = 8.0


def host_of(url: str) -> str:
    """The hostname a rate limit is keyed by. Port and userinfo are not part of it."""
    return (urlsplit(url).hostname or "").lower()


def rate_for_host(host: str) -> float:
    """Requests/second allowed for `host`, matched on domain suffix."""
    host = host.lower()
    for suffix, rate in HOST_RATE_PER_SEC.items():
        if host == suffix or host.endswith("." + suffix):
            return rate
    return DEFAULT_RATE_PER_SEC


@dataclass
class _Bucket:
    rate: float
    capacity: float
    #: Theoretical arrival time (monotonic seconds) of the next request to this host.
    tat: float = 0.0
    #: Last clock reading observed, used only to notice an injected clock.
    seen: float = 0.0


@dataclass
class HostRateLimiter:
    """The per-host buckets. One instance is shared process-wide; see `limiter`."""

    buckets: dict[str, _Bucket] = field(default_factory=dict)

    def _bucket(self, host: str) -> _Bucket:
        bucket = self.buckets.get(host)
        if bucket is None:
            rate = rate_for_host(host)
            capacity = max(1.0, min(_MAX_BURST_TOKENS, rate * _BURST_SECONDS))
            bucket = _Bucket(rate=rate, capacity=capacity)
            self.buckets[host] = bucket
        return bucket

    def reserve(self, host: str) -> float:
        """Claim the next slot for `host` and return the seconds to wait before using it.

        Synchronous on purpose: everything between reading the clock and writing the new
        arrival time happens without an await, which is what makes concurrent callers
        queue instead of colliding.
        """
        bucket = self._bucket(host)
        now = time.monotonic()
        if now < bucket.seen:
            # `time.monotonic` never goes backwards for real, so this means the clock
            # under us was replaced (a test injecting a virtual one). Carrying the old
            # schedule across that boundary would bill a fresh clock for old requests.
            bucket.tat = now
        bucket.seen = now

        interval = 1.0 / bucket.rate
        arrival = bucket.tat if bucket.tat > now else now
        earliest = arrival - bucket.capacity * interval
        bucket.tat = arrival + interval
        wait = earliest - now
        return wait if wait > 0.0 else 0.0

    async def acquire(self, url: str) -> float:
        """Wait out this host's rate limit. Returns the seconds actually slept."""
        wait = self.reserve(host_of(url))
        if wait > 0.0:
            await asyncio.sleep(wait)
        return wait

    def reset(self) -> None:
        """Forget every bucket. For tests and for a long-lived process changing config."""
        self.buckets.clear()


#: Process-wide limiter. Politeness is a property of this process's relationship with a
#: host, so it cannot live on a per-request client object.
limiter = HostRateLimiter()
