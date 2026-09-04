"""The three endpoints added by T-021/T-022/T-024, graded by the SHARED identity contract.

WHY THIS MODULE EXISTS SEPARATELY FROM THE PER-TICKET ONES.  Each of those drives a corpus
written to exercise one connector.  This one drives `t1_decoy`, the world every connector
is already graded against, and asserts the property that world was built for — a
same-name stranger is never emitted — **specifically on the code paths that did not exist
when it was written**.

AND WHY IT IS NOT REDUNDANT WITH `test_t1_identity_contract.py`.  That module iterates
`all_connectors()` and would have graded these paths automatically, which is exactly the
property it was designed for — but only if the decoy router ANSWERS the new endpoints.  It
did not.  `/users/{login}/events/public` fell into the branch that returns a user object,
the Algolia user endpoint received a search payload, and `{origin}/feed` was answered with
HTML.  In all three cases a correct connector and a connector that read nothing produced
the same `[]`, so the contract passed vacuously and the new capability was ungraded.  The
decoy module's own docstring names this failure mode and calls the generic fallback
load-bearing for precisely this reason; this file is the assertion that the fallback is not
what these three are getting.

So the two tests below are a vacuity check first and an identity check second: the endpoint
must be REACHED, and what comes back must still be the member.
"""

from __future__ import annotations

import asyncio

import pytest
from t1_ambiguity import install_router
from t1_decoy import (
    PERSON_MIRROR,
    PERSON_NO_SITE,
    PERSON_SHARED_SITE,
    PERSON_WITH_SITE,
    about_the_member,
    about_the_stranger,
    decoy_router,
)
from t1_recorded import no_real_sleep, settings_for

from arrival.connectors import all_connectors

pytestmark = pytest.mark.ticket("T-1")

BUDGET = 5

SCENARIOS = {
    "own_domain": PERSON_WITH_SITE,
    "no_domain": PERSON_NO_SITE,
    "shared_platform": PERSON_SHARED_SITE,
}

#: kind -> (what a request to the added endpoint looks like, which scenarios must reach it).
#: `self_page` is exempt on a shared platform ON PURPOSE and that exemption is itself an
#: assertion: `linkedin.com/feed` is the platform's own timeline, so the conventional guess
#: is one the connector must NOT make there.
ADDED_ENDPOINTS = {
    "github": ("/events", ("own_domain", "no_domain", "shared_platform")),
    "hn": ("/api/v1/users/", ("own_domain", "no_domain", "shared_platform")),
    "self_page": ("/feed", ("own_domain", "no_domain")),
}


def _drive(kind, person, monkeypatch, tmp_path):
    requested = install_router(monkeypatch, decoy_router)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path)
    found = [c for c in all_connectors(settings) if getattr(c, "kind", None) == kind]
    assert found, f"all_connectors() returned no connector with kind {kind!r}"
    docs = asyncio.run(found[0].search(person, BUDGET))
    return docs, requested


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("kind", sorted(ADDED_ENDPOINTS))
def test_the_added_endpoint_is_actually_reached_in_the_decoy_world(
    kind, scenario, monkeypatch, tmp_path
):
    """The vacuity check. A path nothing requests cannot be graded by anything."""
    marker, wanted_in = ADDED_ENDPOINTS[kind]
    docs, requested = _drive(kind, SCENARIOS[scenario], monkeypatch, tmp_path)

    reached = [url for url in requested if marker in url]
    if scenario in wanted_in:
        assert reached, (
            f"[{scenario}] the {kind} connector never requested anything matching "
            f"{marker!r}; it asked {requested!r}. TASKS T-1 acceptance 2 names this "
            "endpoint, and an endpoint no test reaches is graded by nothing — the "
            "identity contract passes it vacuously."
        )
    else:
        assert not reached, (
            f"[{scenario}] the {kind} connector asked {reached!r}. The roster names a page "
            "on a SHARED platform, where the conventional feed path belongs to the "
            "platform and not to the member."
        )
    assert docs or scenario == "shared_platform", (
        f"[{scenario}] {kind} found nothing at all in a world that contains the member"
    )


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("kind", sorted(ADDED_ENDPOINTS))
def test_the_added_endpoints_do_not_emit_the_same_name_stranger(
    kind, scenario, monkeypatch, tmp_path
):
    """The contract itself, on the new paths. Unverifiable is a no; a name is not an id."""
    docs, _ = _drive(kind, SCENARIOS[scenario], monkeypatch, tmp_path)

    wrong = [f"{doc.url} :: {doc.title or doc.text[:60]}" for doc in docs
             if about_the_stranger(doc)]
    assert not wrong, (
        f"[{scenario}] the {kind} connector emitted documents about a DIFFERENT person who "
        f"shares the member's name: {wrong}. Not one roster detail (Thornfield Loom / "
        "Providence / Rhode Island / thornfieldloom.example.com) appears anywhere in the "
        "stranger's half of this corpus, and the stranger is ranked first in it."
    )


@pytest.mark.parametrize("kind", sorted(ADDED_ENDPOINTS))
def test_the_added_endpoints_follow_the_roster_and_not_the_corpus(kind, monkeypatch, tmp_path):
    """The anti-cheat, applied to the new paths: same bytes, other roster, inverted answer.

    A connector that passed the test above by blocking a host, a company name or a login
    would pass it here too and fail this one, because nothing it can observe changed and
    the required output did.
    """
    docs, requested = _drive(kind, PERSON_MIRROR, monkeypatch, tmp_path)

    theirs = [doc.url for doc in docs if about_the_member(doc)]
    assert not theirs, (
        f"with the roster naming Halvard Freight Systems in Tucson, the {kind} connector "
        f"still returned the Thornfield person's documents: {theirs}. The corpus did not "
        "change; the roster did."
    )
    marker, _ = ADDED_ENDPOINTS[kind]
    assert any(marker in url for url in requested), (
        f"the {kind} connector did not reach {marker!r} for the mirror roster either, so "
        f"the inversion above proves nothing about the added path. Asked: {requested!r}"
    )
    assert [doc for doc in docs if about_the_stranger(doc)], (
        f"the {kind} connector found nobody at all for a roster this corpus fully "
        "corroborates. A predicate that refuses everyone passes the stranger test and is "
        "not a fix."
    )
