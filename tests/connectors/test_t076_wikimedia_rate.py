"""T-076 / SPEC C5: the two Wikimedia hosts had no rule at all.

SPEC C5 names five services -- "SEC <= 10/s, Wikidata <= a few concurrent, arXiv <= 1/3s,
USPTO <= 45/min, Wayback ~= 1/s" -- and `HOST_RATE_PER_SEC` carried four of them plus
Crossref.  Wikidata was the one entry in C5's own list with no rule, and Wikipedia (which
the roster's connectors also call) is not in the table at all, so both fell through to
`DEFAULT_RATE_PER_SEC`.  Nothing was being hammered; what was missing was that a named
constraint was being honoured by accident rather than on purpose, and a change to the
default would have silently moved it.

THE PUBLISHED LIMIT.  Both connectors talk to the MediaWiki Action API
(`/w/api.php`), whose unauthenticated limit the Wikimedia Robot policy states as a PAIR:
"keep the concurrency of your requests to 1 at a time, and below 5 requests per second
overall" -- https://wikitech.wikimedia.org/wiki/Robot_policy

A TOKEN BUCKET CANNOT EXPRESS THE FIRST HALF, and these tests do not pretend otherwise.
The limiter schedules ARRIVALS and never observes a completion, so nothing in it bounds
requests in flight; capping that needs a semaphore held across the request in `client`.
What is asserted here is the part the mechanism can carry: both hosts have an EXPLICIT
rule, it is inside the published ceiling, and it is no faster than the default they used
to fall through to.

The answer key is deliberately outside this lane: the hosts come from the connector
modules' own url constants (`connectors/wikidata.py`, `connectors/wikipedia.py`), and the
ceiling is the published figure quoted above -- never a number read back out of
`ratelimit.py`.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from arrival.connectors import wikidata as wikidata_connector
from arrival.connectors import wikipedia as wikipedia_connector
from arrival.http.ratelimit import (
    DEFAULT_RATE_PER_SEC,
    HOST_RATE_PER_SEC,
    HostRateLimiter,
    host_of,
    rate_for_host,
)

pytestmark = pytest.mark.ticket("T-076")

#: "below 5 requests per second overall", Wikimedia Robot policy, Action API,
#: unauthenticated. A published figure, not a preference.
PUBLISHED_ACTION_API_CEILING = 5.0

#: Every Wikimedia url the connectors actually build, read from THEIR modules so this test
#: cannot drift away from what the product calls.
CONNECTOR_URLS = [
    wikidata_connector.API,
    wikidata_connector.ENTITY_URL,
    wikipedia_connector.API,
    wikipedia_connector.SUMMARY,
    wikipedia_connector.ARTICLE,
]


def test_the_connectors_really_do_call_wikimedia_hosts():
    """Control: if these constants were renamed or repointed, everything below is vacuous."""
    hosts = {urlsplit(url).hostname for url in CONNECTOR_URLS}
    assert hosts == {"www.wikidata.org", "en.wikipedia.org"}, hosts


@pytest.mark.parametrize("url", CONNECTOR_URLS)
def test_every_wikimedia_url_matches_an_explicit_rule(url):
    """The defect itself: neither host matched any suffix in the table, so both silently
    took the default. A rule that exists by accident is not a rule."""
    host = host_of(url)
    matched = [
        suffix
        for suffix in HOST_RATE_PER_SEC
        if host == suffix or host.endswith("." + suffix)
    ]

    assert matched, (
        f"{host} matches no entry in HOST_RATE_PER_SEC, so it falls through to "
        f"DEFAULT_RATE_PER_SEC. SPEC C5 names this service explicitly"
    )


@pytest.mark.parametrize("url", CONNECTOR_URLS)
def test_the_wikimedia_rate_is_within_the_published_ceiling(url):
    """Bounded above by the figure Wikimedia publishes, not by anything in this repo."""
    rate = rate_for_host(host_of(url))

    assert 0.0 < rate <= PUBLISHED_ACTION_API_CEILING, (
        f"{host_of(url)} is limited to {rate}/s; the Wikimedia Robot policy publishes "
        f"'below {PUBLISHED_ACTION_API_CEILING:g} requests per second overall' for an "
        "unauthenticated Action API client"
    )


@pytest.mark.parametrize("url", CONNECTOR_URLS)
def test_wikimedia_is_no_faster_than_the_default_it_used_to_fall_through_to(url):
    """C5's constraint on Wikidata is a CONCURRENCY cap of 1, which is stricter than
    anything a rate can say. Adding an explicit entry that RAISED the rate would honour
    the letter of the ticket and make the process less polite than it was."""
    assert rate_for_host(host_of(url)) <= DEFAULT_RATE_PER_SEC


def test_the_rule_is_matched_on_suffix_so_every_subdomain_shares_one_budget():
    """`en.` and `de.` wikipedia are one operator and one budget, the same ruling
    `efts.sec.gov` and `www.sec.gov` already get."""
    assert rate_for_host("en.wikipedia.org") == rate_for_host("wikipedia.org")
    assert rate_for_host("de.wikipedia.org") == rate_for_host("en.wikipedia.org")
    assert rate_for_host("www.wikidata.org") == rate_for_host("wikidata.org")
    assert rate_for_host("query.wikidata.org") == rate_for_host("www.wikidata.org")


def test_an_unrelated_host_still_gets_the_default():
    """CONTROL: the new entries must not have widened into a global slow-down. T-6 fans
    out over ten connectors and a global limiter would serialise that."""
    assert rate_for_host("nabeelqu.co") == DEFAULT_RATE_PER_SEC
    assert rate_for_host("notwikipedia.org.example.com") == DEFAULT_RATE_PER_SEC


def test_wikimedia_requests_are_actually_throttled_harder_than_a_default_host():
    """Behavioural, not just tabular: the bucket has to bill the new rate.

    `reserve` returns the wait it would impose without sleeping, so this measures the
    schedule directly rather than through an injected clock.
    """
    limiter = HostRateLimiter()
    wikidata_wait = sum(limiter.reserve("www.wikidata.org") for _ in range(12))
    default_wait = sum(limiter.reserve("other.example.com") for _ in range(12))

    assert wikidata_wait > 0.0, "twelve consecutive Wikidata requests cost no time at all"
    assert wikidata_wait >= default_wait, (
        f"twelve requests to Wikidata were scheduled in {wikidata_wait:.2f}s of waiting "
        f"against {default_wait:.2f}s for a host with no rule; the explicit entry is "
        "looser than the default it replaced"
    )
