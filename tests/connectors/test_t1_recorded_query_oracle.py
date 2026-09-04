"""T-042: the recorded corpus must grade the QUESTION, not only the path.

`t1_recorded.install_transport` answers a request from `tests/fixtures/http/{kind}_*.json`
when the recording's method, host, path and named parameters agree.  Until this ticket the
`github`, `hn`, `self_page` and `edgar` recordings named **no** parameters at all, so the
"named parameters agree" clause ran over an empty mapping and was vacuously true: the
oracle compared method, host and path and nothing else.  Measured on the tree before the
repair, each of these left `test_t1_connector_fixtures.py` at 44 passed —

    edgar.FORMS      -> "10-K"                        44 passed
    hn   tags        -> "poll"                        44 passed
    github q         -> a nonsense string             44 passed
    wayback filter   -> a nonsense string             44 passed

— which is how four capabilities came to ship with acceptance criteria naming endpoints
and parameters no code asked for.  A connector that asked the right question and one that
asked nothing produced the same green.

WHAT THIS MODULE GRADES, AND WHAT IT DELIBERATELY DOES NOT.  It grades the ORACLE — the
matching rule in `t1_recorded.matches` — against literal requests written here, and it
holds the corpus to the policy that rule implements.  It does not grade any connector
against a fixture, and it must not: the fixtures and the connectors are in one ticket's
write scope, so a test comparing one to the other would be this ticket marking its own
homework.  The thing that actually catches a connector asking the wrong question is
`test_t1_connector_fixtures.py`, which is outside this ticket's scope and now goes red
under all four sabotages above.

THE POLICY, RESTATED SO IT CAN BE TESTED.  Every parameter a recording names must be
present in the request with exactly that value; parameters it does not name are ignored;
and a recording may never name a parameter that only sizes the answer (`SIZING_PARAMS`),
because `budget` and `Settings` move those between two runs of the same test.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest
from t1_recorded import (
    KINDS,
    SIZING_PARAMS,
    install_transport,
    load,
    matches,
    no_real_sleep,
    query_of,
    required_body,
    required_query,
    settings_for,
)

from arrival.connectors import all_connectors

pytestmark = pytest.mark.ticket("T-1")

GENEROUS = 5

#: A GET recording with two named parameters and a POST recording with a named body
#: field. Written here as literals rather than read out of the corpus, so what the
#: matching rule is asked to do does not move when a fixture does.
GET_ENTRY = {
    "method": "GET",
    "url": "https://efts.sec.gov/LATEST/search-index",
    "query": {"q": '"Marisol Quennebeck"', "forms": "3,4,5,D,13F-HR,13F-NT"},
}
POST_ENTRY = {
    "method": "POST",
    "url": "https://api.tavily.com/search",
    "request_json": {"query": "Marisol Quennebeck Thornfield Loom"},
}
RIGHT_URL = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22Marisol+Quennebeck%22&forms=3%2C4%2C5%2CD%2C13F-HR%2C13F-NT&hits=5"
)


# --- the matching rule itself ---------------------------------------------------------


def test_the_recording_answers_the_request_it_was_recorded_for():
    assert matches(GET_ENTRY, "GET", RIGHT_URL), (
        "the request the fixture was recorded for must still be answered, or every "
        "connector test fails for a reason that has nothing to do with the connector"
    )


@pytest.mark.parametrize(
    ("label", "url"),
    [
        (
            "a wrong value for a named parameter",
            "https://efts.sec.gov/LATEST/search-index"
            "?q=%22Marisol+Quennebeck%22&forms=10-K&hits=5",
        ),
        (
            "a named parameter dropped entirely",
            "https://efts.sec.gov/LATEST/search-index?q=%22Marisol+Quennebeck%22&hits=5",
        ),
        (
            "the whole query dropped",
            "https://efts.sec.gov/LATEST/search-index",
        ),
        (
            "somebody else's name",
            "https://efts.sec.gov/LATEST/search-index"
            "?q=%22Pell+Marrowby%22&forms=3%2C4%2C5%2CD%2C13F-HR%2C13F-NT",
        ),
        (
            "an empty value where a value was recorded",
            "https://efts.sec.gov/LATEST/search-index?q=&forms=3%2C4%2C5%2CD%2C13F-HR%2C13F-NT",
        ),
    ],
)
def test_the_recording_refuses_a_request_that_asks_a_different_question(label, url):
    """The whole point of T-042: a wrong or missing parameter must not be answered."""
    assert not matches(GET_ENTRY, "GET", url), (
        f"the recording answered a request with {label}. That is the vacuous oracle this "
        "ticket exists to close: a connector could send an arbitrarily wrong q or forms "
        "and its recorded test would still pass."
    )


def test_the_recording_ignores_parameters_it_does_not_name():
    """The chosen middle policy, stated as an assertion so it cannot drift by accident.

    Exact query equality was rejected because `budget` sizes `hits`/`per_page`/`srlimit`
    and `Settings` sizes `mailto`: the same fixture is driven at budget 0, 1 and 5 by
    `test_connector_respects_its_budget`, and an exact match would answer at one of them.
    """
    with_extras = RIGHT_URL + "&format=json&sort=relevance&dateRange=custom"
    assert matches(GET_ENTRY, "GET", with_extras), (
        "a request carrying every recorded parameter plus extras must still be answered; "
        "otherwise every fixture stops matching the first time a connector adds a "
        "parameter, and the failure reads as 'the connector is broken'"
    )


def test_parameter_order_is_not_part_of_the_question():
    reordered = (
        "https://efts.sec.gov/LATEST/search-index"
        "?forms=3%2C4%2C5%2CD%2C13F-HR%2C13F-NT&hits=5&q=%22Marisol+Quennebeck%22"
    )
    assert matches(GET_ENTRY, "GET", reordered)


def test_method_host_and_path_are_still_part_of_the_match():
    assert not matches(GET_ENTRY, "POST", RIGHT_URL), "a POST is not a GET"
    assert not matches(
        GET_ENTRY, "GET", RIGHT_URL.replace("efts.sec.gov", "www.sec.gov")
    ), "a different host is a different source"
    assert not matches(
        GET_ENTRY, "GET", RIGHT_URL.replace("/LATEST/search-index", "/LATEST/other")
    ), "a different path is a different endpoint"


# --- the POST half: the search connector puts its question in a body ------------------


def test_a_recorded_request_body_is_matched_the_same_way_a_query_is():
    """`search` POSTs its query to Tavily, so without this its recording is as vacuous
    as the four GET recordings this ticket repairs: any body at all would match."""
    right = b'{"query":"Marisol Quennebeck Thornfield Loom","max_results":10}'
    wrong = b'{"query":"Pell Marrowby","max_results":10}'
    missing = b'{"max_results":10}'

    assert matches(POST_ENTRY, "POST", "https://api.tavily.com/search", right)
    assert not matches(POST_ENTRY, "POST", "https://api.tavily.com/search", wrong), (
        "the recording answered a POST asking about somebody else"
    )
    assert not matches(POST_ENTRY, "POST", "https://api.tavily.com/search", missing), (
        "the recording answered a POST that asked no question at all"
    )
    assert not matches(POST_ENTRY, "POST", "https://api.tavily.com/search", b"not json"), (
        "a body that is not JSON cannot satisfy a recorded JSON field"
    )


def test_a_recording_that_names_no_body_field_still_answers_any_body():
    """Only the fields a recording NAMES are graded — the same rule as for the query."""
    entry = {"method": "POST", "url": "https://api.tavily.com/search"}
    assert matches(entry, "POST", "https://api.tavily.com/search", b'{"anything":1}')


# --- the corpus is held to the policy -------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_no_recording_pins_a_parameter_that_only_sizes_the_answer(kind):
    """`budget` and `Settings` move these, so pinning one would break at another budget."""
    for entry in load(kind).responses:
        pinned = set(required_query(entry)) & SIZING_PARAMS
        assert not pinned, (
            f"{kind}'s recording of {entry['url']} pins {sorted(pinned)}. Those say how "
            "BIG the answer should be, not what was asked; `budget` and `Settings` move "
            "them, and `test_connector_respects_its_budget` drives every connector at "
            "budget 0, 1 and 5."
        )
        pinned_body = set(required_body(entry)) & SIZING_PARAMS
        assert not pinned_body, (
            f"{kind}'s recording of {entry['url']} pins {sorted(pinned_body)} in its body"
        )


def _same_endpoint(entry: dict, url: str) -> bool:
    """Could this recording, ignoring parameters entirely, be the answer to `url`?"""
    want, got = urlsplit(str(entry["url"])), urlsplit(url)
    return (want.hostname or "").lower() == (got.hostname or "").lower() and want.path == got.path


@pytest.mark.parametrize("kind", KINDS)
def test_every_question_a_connector_actually_asks_is_pinned_by_its_recording(
    kind, monkeypatch, tmp_path
):
    """The ratchet. Drive the connector, then require its recording to constrain what it
    asked — so a fixture cannot quietly go back to matching on the path alone.

    Self-calibrating on purpose: what must be pinned is derived from the request the
    connector really made, not from a hand-written list of kinds. A connector that sends
    no parameters at all (`self_page` asks only for pages) is exempt by measurement rather
    than by exception, and one that grows a parameter is caught the day it does.
    """
    recording = load(kind)
    requested = install_transport(monkeypatch, recording)
    no_real_sleep(monkeypatch)
    connector = next(c for c in all_connectors(settings_for(tmp_path)) if c.kind == kind)
    asyncio.run(connector.search(recording.person, GENEROUS))

    unpinned: list[str] = []
    for url in requested:
        asked = {k: v for k, v in query_of(url).items() if k not in SIZING_PARAMS}
        if not asked:
            continue
        answering = [e for e in recording.responses if _same_endpoint(e, url)]
        if answering and not any(required_query(entry) for entry in answering):
            unpinned.append(url)

    assert not unpinned, (
        f"the {kind} recording answers {unpinned!r} without naming a single one of the "
        "parameters that request carries. That is the T-042 defect exactly: the corpus "
        "grades the path and lets any question through. Add the parameters that carry "
        "the QUESTION to that entry's `query` mapping (never the sizing ones)."
    )
