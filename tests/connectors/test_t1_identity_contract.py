"""T-028: the identity contract, asserted for EVERY connector `all_connectors()` returns.

WHY THIS IS ONE TEST AND NOT SIX.  The same defect — **a name match treated as an identity
match** — has now been found ten times in ten connectors.  Four were repaired one at a
time, each with its own private copy of the predicate; a sweep found six more.  Repairing
those six individually would produce instances seven through twelve of one bug and leave
the eleventh connector, whenever somebody writes it, free to reintroduce it.  What was
missing was never six fixes.  It was a shared contract with a test that grades every
connector against it.

So the loop below iterates `all_connectors(settings)` rather than a hand-written list of
kinds.  **A connector added to the registry tomorrow is graded by this test the moment it
is registered**, with no edit here and nobody having to remember.  That property is the
whole point, and it is verified directly: `test_the_oracle_catches_a_naive_new_connector`
registers a deliberately naive eleventh connector and requires this contract to reject it.
A contract that only the ten known connectors satisfy is ten more cases wearing a nicer
name.

THE PROPERTY.  Given a response containing a same-name stranger, a connector emits no
document about that stranger.  Three rosters exercise the three shapes a member's details
actually take — a private domain, no domain at all, and a page on a shared platform — and
`t1_decoy` answers every request in all three with a world containing two people of the
identical name, the stranger ranked first everywhere.

WHY THE STRANGER IS RANKED FIRST.  "Take the top hit" is the defect.  A corpus that puts
the right answer at rank 1 grades a broken connector green, which is exactly what the
recorded corpus (one candidate per source) and the frozen suite (likewise) both do today.
"""

from __future__ import annotations

import asyncio

import pytest
from t1_ambiguity import install_router
from t1_decoy import (
    PERSON_NO_SITE,
    PERSON_SHARED_SITE,
    PERSON_WITH_SITE,
    about_the_stranger,
    decoy_router,
)
from t1_recorded import no_real_sleep, settings_for

from arrival.connectors import all_connectors
from arrival.contracts import RawDoc

pytestmark = pytest.mark.ticket("T-1")

#: The three roster shapes, and what each one is here to reach.
SCENARIOS = {
    "own_domain": PERSON_WITH_SITE,
    "no_domain": PERSON_NO_SITE,
    "shared_platform": PERSON_SHARED_SITE,
}

BUDGET = 5


def _drive(kind, person, monkeypatch, tmp_path):
    """Run one connector against the decoy world. Returns (docs, urls requested)."""
    requested = install_router(monkeypatch, decoy_router)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path)
    found = [c for c in all_connectors(settings) if getattr(c, "kind", None) == kind]
    assert found, f"all_connectors() returned no connector with kind {kind!r}"
    docs = asyncio.run(found[0].search(person, BUDGET))
    return docs, requested


def _kinds(tmp_path):
    return [getattr(c, "kind", None) for c in all_connectors(settings_for(tmp_path))]


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_no_connector_emits_a_document_about_a_same_name_stranger(
    scenario, monkeypatch, tmp_path
):
    """The contract, for every registered connector, in one of the three roster shapes.

    A connector that cannot tell the member from a stranger with her name declines: the
    cost function is not symmetric.  A document wrongly withheld costs one paragraph of
    one dossier.  A stranger wrongly accepted is written into the hub graph, where T-5
    joins every other person on the roster onto it — so a single false positive
    contaminates the matching for everybody and keeps producing confident matches off the
    merge, inside a digest that looks exactly like a correct one.
    """
    person = SCENARIOS[scenario]
    offenders: dict[str, list[str]] = {}

    for kind in _kinds(tmp_path):
        docs, _ = _drive(kind, person, monkeypatch, tmp_path)
        wrong = [f"{doc.url} :: {doc.title or doc.text[:60]}" for doc in docs
                 if about_the_stranger(doc)]
        if wrong:
            offenders[kind] = wrong

    assert not offenders, (
        f"[{scenario}] these connectors emitted documents about a DIFFERENT person who "
        f"happens to share the member's name: {offenders}. The decoy corpus offers two "
        "candidates under the identical name and ranks the stranger first; not one roster "
        "detail (Thornfield Loom / Providence / Rhode Island / thornfieldloom.example.com) "
        "appears anywhere in the stranger's half of it. A name is not an identifier, and "
        "'the API returned it for my query' is evidence about the query."
    )


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_connector_still_reads_its_source_in_the_decoy_world(
    scenario, monkeypatch, tmp_path
):
    """The other half: declining must not become the way every connector passes.

    Paired with the test above, this is the same trap the frozen suite closes for the
    recorded corpus. `return []` satisfies "emits nothing about the stranger" perfectly
    and is not a fix; a connector that never asks its source is a fixture wearing a
    connector's name. The decoy router answers EVERY url with a parseable 200, so a
    connector that made a request and returned nothing made a judgement, which is allowed
    — one that made no request at all did not.

    `wayback` is exempted in the `no_domain` scenario, and only there: its documented
    input is a host from `details` (TASKS T-1 acceptance 2), so with no URL on the roster
    it has nothing to query and returning [] before any request is the correct behaviour,
    not a short circuit.
    """
    person = SCENARIOS[scenario]
    silent = []
    for kind in _kinds(tmp_path):
        if kind == "wayback" and scenario == "no_domain":
            continue
        _, requested = _drive(kind, person, monkeypatch, tmp_path)
        if not requested:
            silent.append(kind)

    assert not silent, (
        f"[{scenario}] these connectors made no HTTP request at all: {silent}. Declining "
        "a stranger is a judgement made ON a response; a connector that never fetches one "
        "passes the identity contract vacuously."
    )


def test_the_decoy_world_still_lets_connectors_find_the_actual_member(monkeypatch, tmp_path):
    """A third guard: the contract must not be satisfiable only by refusing everybody.

    The decoy corpus contains the member as well as the stranger, corroborated by every
    detail her roster line carries.  If NO connector can find her in a world that contains
    her, the predicate has stopped discriminating and started refusing — which passes both
    tests above and ships a product that returns empty dossiers.
    """
    found: dict[str, int] = {}
    for kind in _kinds(tmp_path):
        docs, _ = _drive(kind, PERSON_WITH_SITE, monkeypatch, tmp_path)
        hers = [doc for doc in docs if isinstance(doc, RawDoc) and not about_the_stranger(doc)]
        if hers:
            found[kind] = len(hers)

    assert len(found) >= 5, (
        "only these connectors found the member in a corpus that contains her, fully "
        f"corroborated: {found}. A predicate that refuses everyone passes the "
        "stranger test and is not a fix."
    )
