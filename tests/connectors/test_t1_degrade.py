"""TASKS T-1 acceptance 3: every connector returns [] -- never raises -- when it fails.

DESIGN Decision 8 is the reason this is a hard rule rather than a nicety.  T-6 fans out
over ten sources for every person on the roster; if one dead API can take down a run, the
operator's only recovery is to retry the whole build and hope, and "half the internet is
down" is a normal Tuesday for a fan-out over free public endpoints.

This is the inverted twin of `test_t1_connector_fixtures.py`.  A connector that fabricates
documents without touching the network passes the positive test and fails this one; a
connector whose `search` is `return []` does the reverse.  Only one that actually reads
its source passes both, which is why the two files have to be read together.
"""

from __future__ import annotations

import asyncio

import pytest
from t1_recorded import KINDS, install_transport, load, no_real_sleep, settings_for

from arrival.connectors import all_connectors
from arrival.contracts import PersonRef, RawDoc

pytestmark = pytest.mark.ticket("T-1")

#: Failure modes that must leave the connector with nothing to say.
SILENT_FAILURES = ("connect", "timeout", "500", "empty", "absent")


def _connector(kind, settings):
    return next(c for c in all_connectors(settings) if c.kind == kind)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fail", SILENT_FAILURES)
def test_connectors_degrade(kind, fail, monkeypatch, tmp_path):
    """A dead source is an empty list, not an exception and not a partial dossier."""
    recording = load(kind)
    requested = install_transport(monkeypatch, recording, fail=fail)
    no_real_sleep(monkeypatch)
    connector = _connector(kind, settings_for(tmp_path))

    try:
        docs = asyncio.run(connector.search(recording.person, 5))
    except Exception as exc:  # noqa: BLE001 - reporting the breach is the whole test
        pytest.fail(
            f"the {kind} connector raised {type(exc).__name__}({exc}) on a "
            f"{fail!r} failure. DESIGN Decision 8: a connector must log and return [], "
            "because one dead API cannot be allowed to take down a whole build."
        )

    assert docs == [], (
        f"the {kind} connector returned {len(docs)} document(s) from a {fail!r} failure: "
        f"{[d.url for d in docs]!r}. Nothing was successfully fetched, so any document "
        "here was invented rather than read."
    )
    assert requested, (
        f"the {kind} connector never even attempted a request under {fail!r}; this test "
        "cannot distinguish 'degraded correctly' from 'does nothing at all' unless it "
        "tried"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_a_body_that_is_not_what_it_claims_to_be_is_survivable(kind, monkeypatch, tmp_path):
    """A 200 whose body is not the advertised JSON must not raise.

    Unlike the modes above this one does not have to produce `[]` -- an HTML-reading
    connector handed an HTML body has legitimately received a document -- so the assertion
    is the never-raise contract plus "whatever comes back is still a well-formed citation".
    """
    recording = load(kind)
    install_transport(monkeypatch, recording, fail="garbage")
    no_real_sleep(monkeypatch)
    connector = _connector(kind, settings_for(tmp_path))

    try:
        docs = asyncio.run(connector.search(recording.person, 5))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the {kind} connector raised {type(exc).__name__}({exc}) on a bad body")

    assert isinstance(docs, list)
    for doc in docs:
        assert isinstance(doc, RawDoc) and doc.source_kind == kind
        assert doc.text.strip()


@pytest.mark.parametrize("kind", KINDS)
def test_a_person_the_roster_says_almost_nothing_about_is_survivable(
    kind, monkeypatch, tmp_path
):
    """Most club members have a name and nothing else. That is the common case, not an edge."""
    recording = load(kind)
    install_transport(monkeypatch, recording)
    no_real_sleep(monkeypatch)
    connector = _connector(kind, settings_for(tmp_path))

    bare = PersonRef(person_id="ovid-thrale", name="Ovid Thrale", details=[])

    try:
        docs = asyncio.run(connector.search(bare, 5))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"the {kind} connector raised {type(exc).__name__}({exc}) for a person with "
            "no details. A roster row is allowed to be just a name."
        )
    assert isinstance(docs, list)
    for doc in docs:
        assert doc.source_kind == kind and doc.text.strip()


@pytest.mark.parametrize("kind", KINDS)
def test_a_connector_with_no_credentials_configured_still_behaves(kind, monkeypatch, tmp_path):
    """Settings: "a missing key disables a capability, never crashes"."""
    recording = load(kind)
    install_transport(monkeypatch, recording)
    no_real_sleep(monkeypatch)
    settings = settings_for(tmp_path, tavily_api_key=None, github_token=None)
    connector = _connector(kind, settings)

    try:
        docs = asyncio.run(connector.search(recording.person, 5))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the {kind} connector raised {type(exc).__name__}({exc}) with no keys set")
    assert isinstance(docs, list)


def test_one_dead_connector_does_not_stop_the_others(monkeypatch, tmp_path):
    """The fan-out's whole point: nine live sources still build a dossier.

    Graded on the real fan-out rather than one connector at a time, because "each
    connector degrades" and "the fan-out survives a degraded connector" are different
    claims and only the second one is what T-6 depends on.
    """
    recordings = {kind: load(kind) for kind in KINDS}
    no_real_sleep(monkeypatch)

    # Serve every connector's corpus at once, except propublica's, which 404s throughout.
    merged = []
    for kind, recording in recordings.items():
        if kind != "propublica":
            merged.extend(recording.responses)
    combined = recordings["self_page"].__class__(
        kind="all",
        subject=recordings["self_page"].subject,
        provenance="merged",
        note="",
        responses=merged,
        path=recordings["self_page"].path,
    )
    install_transport(monkeypatch, combined)

    person = recordings["self_page"].person
    connectors = all_connectors(settings_for(tmp_path))

    async def fan_out():
        return await asyncio.gather(*(c.search(person, 3) for c in connectors))

    results = asyncio.run(fan_out())
    by_kind = {c.kind: docs for c, docs in zip(connectors, results, strict=True)}

    assert by_kind["propublica"] == [], "the source with nothing recorded must be empty"
    alive = [kind for kind, docs in by_kind.items() if docs]
    assert len(alive) >= 8, (
        f"only {alive} produced documents while one source was dark. A fan-out where one "
        "dead endpoint costs the others is not a fan-out."
    )
