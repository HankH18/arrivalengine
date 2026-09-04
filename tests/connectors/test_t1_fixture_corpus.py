"""TASKS T-1 acceptance 5: a recorded, redacted response per connector, no real people.

The "no real people" half is a product constraint, not test hygiene.  This repository is
public and its whole subject matter is what can be found out about a named individual from
free sources; a fixture recorded off a real person's real pages would make the test corpus
itself the thing the product exists to handle carefully.  So the subject is invented, the
prose is hand-written, and every host is either the public API being imitated or an
RFC 2606 reserved name that can never resolve to somebody's actual site.

These assertions are cheap and they are the only thing standing between "we were careful
once" and "the corpus stays clean while nine other tickets edit around it".
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest
from t1_recorded import (
    ALLOWED_HOSTS,
    FIXTURE_DIR,
    KINDS,
    RESERVED_DOMAINS,
    RESERVED_TLDS,
    fixture_path,
    is_reserved_host,
    load,
)

pytestmark = pytest.mark.ticket("T-1")

#: T-0 owns these; T-1 owns `{kind}_*.json`. Disjoint by filename prefix.
NOT_OURS = "fixture_dossier_docs"


def _host_is_safe(host: str) -> bool:
    return host.lower() in ALLOWED_HOSTS or is_reserved_host(host)


def test_there_is_exactly_one_recorded_corpus_per_connector():
    for kind in KINDS:
        path = fixture_path(kind)
        assert path.exists()
        assert path.name.startswith(f"{kind}_"), (
            f"{path.name} does not start with its kind, so the ownership split in "
            "tickets.json ('T-0 owns fixture_dossier_docs_*.json, T-1 owns "
            "{kind}_*.json') no longer holds"
        )


def test_the_corpus_does_not_reach_into_t0s_half_of_the_directory():
    ours = {fixture_path(kind).name for kind in KINDS}
    theirs = {p.name for p in FIXTURE_DIR.glob(f"{NOT_OURS}*.json")}

    assert theirs, "T-0's dossier docs should still be here; T-1 must not have removed them"
    assert not (ours & theirs), f"a T-1 fixture collided with a T-0 one: {ours & theirs}"


@pytest.mark.parametrize("kind", KINDS)
def test_each_recording_declares_its_provenance_and_is_well_formed(kind):
    recording = load(kind)

    assert recording.kind == kind
    assert "FICTIONAL" in recording.provenance.upper(), (
        f"{recording.path.name} does not say in the file that its subject is invented. "
        "The next person to read it has to be able to tell without asking."
    )
    assert recording.responses, f"{recording.path.name} records no responses at all"

    for entry in recording.responses:
        assert entry["url"].startswith(("http://", "https://")), entry["url"]
        assert entry.get("method", "GET") in ("GET", "POST")
        assert int(entry.get("status", 200)) == 200, (
            "a recording is of a SUCCESSFUL response; failure modes are injected by "
            "t1_recorded.install_transport(fail=...), not baked into the corpus"
        )
        assert ("json" in entry) ^ ("body" in entry), (
            f"{entry['url']} must carry exactly one of `json` or `body`"
        )
        assert entry.get("content_type"), f"{entry['url']} records no content type"
        if "json" in entry:
            json.dumps(entry["json"])  # raises if it is not serialisable
        else:
            assert entry["body"].strip(), f"{entry['url']} records an empty body"


@pytest.mark.parametrize("kind", KINDS)
def test_every_recording_is_about_the_same_synthetic_person(kind):
    recording = load(kind)
    person = recording.person

    assert person.name == "Marisol Quennebeck"
    assert person.person_id == "marisol-quennebeck", (
        "person_id == slug(name) is the product contract (DESIGN §Interfaces)"
    )
    assert "Thornfield Loom" in " ".join(person.details)
    assert any("thornfieldloom.example.com" in detail for detail in person.details), (
        "the subject's own site has to be a reserved-domain address, or a test run could "
        "in principle send a request somewhere real"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_no_recorded_url_can_ever_point_at_a_real_persons_site(kind):
    """Every host is a public API being imitated or an RFC 2606 reserved name."""
    recording = load(kind)

    for url in recording.urls():
        host = (urlsplit(url).hostname or "").lower()
        assert host, f"{recording.path.name} records a url with no host: {url!r}"
        assert _host_is_safe(host), (
            f"{recording.path.name} names the host {host!r} (in {url!r}). A fixture may "
            f"only reference the API it is imitating ({sorted(ALLOWED_HOSTS)}) or a "
            f"reserved documentation name ({[*RESERVED_DOMAINS, *RESERVED_TLDS]}). Anything "
            "else suggests the recording came off a real page about a real person."
        )


@pytest.mark.parametrize("kind", KINDS)
def test_no_recording_carries_a_credential(kind):
    """A recorded response must never contain the key that was used to obtain it."""
    raw = fixture_path(kind).read_text(encoding="utf-8")

    for secret in ("sk-ant", "tvly-", "ghp_", "ghs_", "github_pat_", "Bearer "):
        assert secret not in raw, (
            f"{fixture_path(kind).name} contains {secret!r}. Recordings are committed; a "
            "credential in one is a leaked credential."
        )


def test_the_subject_is_not_one_of_the_dossier_fixtures_people():
    """Independence: the T-1 corpus must not lean on another ticket's cast.

    A fixture that reuses T-0's people would make a T-1 test pass or fail for reasons
    living in someone else's scope.
    """
    others = {"Teodoro Vance", "Pell Marrowby"}
    for kind in KINDS:
        raw = fixture_path(kind).read_text(encoding="utf-8")
        for name in others:
            assert name not in raw, f"{kind}'s recording mentions {name!r} from another corpus"
