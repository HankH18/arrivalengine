"""T-8: the boot-time corpus — validation, the roster index, and the graph population.

These pin the properties the frozen acceptance suite cannot see because it only ever hands
the app a well-formed five-person corpus: a MISSING directory, an UNRESOLVED dossier, and a
roster whose `person_id` deliberately is not `slug(name)`.
"""

from __future__ import annotations

import json

import pytest

from arrival.config import get_settings
from arrival.web.store import DossierLoadError, DossierStore

pytestmark = pytest.mark.ticket("T-8")

FIXTURES = "tests/fixtures/dossiers"


@pytest.fixture
def corpus(tmp_path, request):
    """A private copy of the unit fixture corpus.

    Copied rather than used in place: the app is handed a directory and told it is its data
    store, and a test that pointed it at the committed fixtures would let one implementation
    bug rewrite every other test's inputs.
    """
    root = request.config.rootpath / FIXTURES
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(root.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def test_a_missing_dossier_directory_is_an_empty_corpus_not_an_error(tmp_path):
    """SPEC C4 / known hazard: `data/dossiers` does not exist until T-9 commits it.

    `uvicorn arrival.web.app:app` has to come up on a fresh checkout and serve an empty
    roster. Refusing to boot would make the module-level `app` un-importable for everybody.
    """
    store = DossierStore.load(tmp_path / "nothing-here")
    assert len(store) == 0
    assert store.people() == []
    assert store.resolve("anybody") is None


def test_every_dossier_is_validated_at_boot_and_a_bad_one_names_its_path(corpus):
    """T-8 acceptance 1. The path is the whole point: "some dossier is invalid" is unactionable."""
    good = DossierStore.load(corpus)
    assert len(good) == 4

    (corpus / "wrecked.json").write_text('{"person": {"person_id": "wrecked"', encoding="utf-8")
    with pytest.raises(DossierLoadError) as excinfo:
        DossierStore.load(corpus)
    assert "wrecked.json" in str(excinfo.value)


def test_a_schema_violation_aborts_boot_as_loudly_as_broken_json(corpus):
    """Valid JSON that is not a `Dossier` is just as unservable, and just as named."""
    (corpus / "shapeless.json").write_text(json.dumps({"person": {}}), encoding="utf-8")
    with pytest.raises(DossierLoadError) as excinfo:
        DossierStore.load(corpus)
    message = str(excinfo.value)
    assert "shapeless.json" in message
    assert "Dossier" in message


def test_lookup_accepts_both_a_display_name_and_a_person_id(corpus):
    """Standing ruling 1: implement `slug(name)` lookup; do NOT infer the id from fixtures.

    `tests/fixtures/dossiers/alpha.json` deliberately carries `person_id="alpha"` with
    `name="Teodoro Vance"`, so a store that only indexed `slug(name)` could not answer
    `person_id="alpha"` and one that only indexed ids could not answer a webhook that sends
    a name. Both spellings resolve to the same person.
    """
    store = DossierStore.load(corpus)
    assert store.resolve("Teodoro Vance") == "alpha"
    assert store.resolve("alpha") == "alpha"
    assert store.resolve("  teodoro vance  ") == "alpha"
    assert store.resolve("Wendell Ashgrove-Pike") is None
    assert store.resolve("") is None


def test_an_unresolved_dossier_stays_on_the_roster_but_out_of_the_graph(corpus):
    """`build_graph`: "an unresolved dossier must be left out by the caller, or it perturbs N".

    N is the IDF denominator, so one unresolved person silently moves every score on every
    digest. They still belong on the roster and can still arrive — they simply match nobody,
    which is the honest answer for someone the resolver could not identify.
    """
    baseline = DossierStore.load(corpus)
    baseline_n = baseline.graph.graph["n_people"]

    stranger = json.loads((corpus / "alpha.json").read_text(encoding="utf-8"))
    stranger["person"] = {"person_id": "echo", "name": "Echo Unknown", "details": []}
    stranger["resolution"] = {
        "person_id": "echo",
        "status": "unresolved",
        "strong_keys": {},
        "accepted_doc_ids": [],
        "rejected": [],
        "confidence": 0.2,
    }
    stranger["facts"] = []
    stranger["hubs"] = []
    (corpus / "echo.json").write_text(json.dumps(stranger), encoding="utf-8")

    store = DossierStore.load(corpus)
    assert store.resolve("Echo Unknown") == "echo"
    assert "echo" in {person.person_id for person in store.people()}
    assert store.graph.graph["n_people"] == baseline_n, (
        "the unresolved dossier entered the graph population and moved N, which moves every "
        "IDF and therefore every score on every digest"
    )


def test_the_store_never_writes_back_into_the_corpus_directory(corpus):
    """The app is handed a data directory; it must treat it as read-only.

    An implementation that wrote a cache or a re-serialised dossier back would corrupt the
    operator's committed corpus, and under the frozen harness would rewrite the answer key.
    """
    before = {path.name: path.read_bytes() for path in sorted(corpus.glob("*.json"))}
    store = DossierStore.load(corpus)
    store.people()
    store.resolve("Teodoro Vance")
    after = {path.name: path.read_bytes() for path in sorted(corpus.glob("*.json"))}
    assert after == before


def test_settings_supply_the_dossier_directory_when_none_is_passed(monkeypatch, corpus):
    """`DOSSIER_DIR` is read at call time, never snapshotted at import time.

    The frozen suite gets no settings-cache reset — that fixture lives in `tests/harness.py`,
    which its `--confcutdir` excludes — so a module that captured `Settings` at import would
    pass here and fail there, against whichever corpus the first test in the process set.
    """
    monkeypatch.setenv("DOSSIER_DIR", str(corpus))
    get_settings.cache_clear()
    assert get_settings().dossier_dir == corpus

    store = DossierStore.load(get_settings().dossier_dir)
    assert len(store) == 4
