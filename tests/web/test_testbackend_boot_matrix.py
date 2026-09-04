"""Boot and configuration: what `create_app()` does with every shape of `DOSSIER_DIR`.

The documented contract, stated in three places and graded here as one:

* `web/store.py:DossierLoadError` — "The message always begins with the offending path.
  This is the whole point of the exception type: T-8 acceptance 1 is not 'boot fails', it
  is 'boot fails and the operator is told which file to look at'."
* `web/store.py:load` — "A missing directory is an EMPTY corpus, not an error ... A file
  that exists and is broken is a different thing entirely, and raises."
* `web/app.py:create_app` — "Settings are read at factory time, never at import time."

`tests/web/test_t8_store.py` grades the store; `tests/test_t054_contract_validators.py`
grades the validators. Neither grades the CROSSING, which is what a deploy actually hits:
a corpus violating one of the new `contracts.py` validators must abort the boot **through
`create_app`** with **that file's path in the message**. Every validator below is exercised
that way, and the assertion is on the path — a `DossierLoadError` that says "some dossier
is invalid" would pass a naive `pytest.raises` and is exactly the failure mode the
exception exists to prevent.

Grading references: `contracts.py`'s own `Probability`/`NonBlank` annotations (src,
read-only to this ticket), the literal filenames this module writes, and CPython's
`OSError`/`ValueError` hierarchy. No fixture owned by this ticket is an answer key.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from arrival.config import Settings, get_settings
from arrival.web.app import DossierLoadError, create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("TESTBACKEND")

KNOWN_ID = "alpha"


@pytest.fixture
def fixture_dir(request):
    return request.config.rootpath / "tests/fixtures/dossiers"


@pytest.fixture
def corpus(tmp_path, fixture_dir):
    destination = tmp_path / "dossiers"
    destination.mkdir()
    for path in sorted(fixture_dir.glob("*.json")):
        (destination / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


@pytest.fixture(autouse=True)
def _no_debug(monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    monkeypatch.delenv("DOSSIER_DIR", raising=False)
    get_settings.cache_clear()


def _one(fixture_dir, destination, name=KNOWN_ID, **mutate):
    """Write one fixture dossier into `destination`, optionally mutated."""
    destination.mkdir(parents=True, exist_ok=True)
    raw = json.loads((fixture_dir / f"{name}.json").read_text(encoding="utf-8"))
    for dotted, value in mutate.items():
        target = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if part.isdigit() else target[part]
        last = parts[-1]
        target[int(last) if last.isdigit() else last] = value
    (destination / f"{name}.json").write_text(json.dumps(raw), encoding="utf-8")
    return destination


# ---------------------------------------------------------------------------
# 1. Directories that are not a corpus. None of these is an error.
# ---------------------------------------------------------------------------


def test_a_missing_directory_boots_with_an_empty_roster(tmp_path):
    """`uvicorn arrival.web.app:app` has to come up on a fresh checkout where
    `data/dossiers` does not exist yet, and serve an empty roster rather than refusing."""
    app = create_app(dossier_dir=tmp_path / "not-here", llm=LLMDouble())
    assert len(app.state.store) == 0
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/").status_code == 200
        assert client.get("/building", headers={"accept": "application/json"}).json() == {
            "present": [], "count": 0
        }
        assert client.get("/graph").status_code == 200
        assert client.get("/corpus").status_code == 200
        assert client.post("/arrive", json={"person_id": KNOWN_ID}).status_code == 404


def test_an_empty_directory_boots_with_an_empty_roster(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    app = create_app(dossier_dir=empty, llm=LLMDouble())
    assert len(app.state.store) == 0


def test_a_path_that_is_a_file_rather_than_a_directory_boots_empty(tmp_path):
    """`DossierStore.load` gates on `directory.is_dir()`, so a `DOSSIER_DIR` pointed at a
    regular file is the same as a missing one. Pinned because the alternative reading — a
    file being globbed or opened — would be an unhandled `NotADirectoryError` at import."""
    a_file = tmp_path / "dossiers"
    a_file.write_text("this is not a directory", encoding="utf-8")
    app = create_app(dossier_dir=a_file, llm=LLMDouble())
    assert len(app.state.store) == 0


def test_a_directory_holding_no_json_at_all_boots_empty(tmp_path):
    directory = tmp_path / "d"
    directory.mkdir()
    (directory / "README.md").write_text("nothing here", encoding="utf-8")
    (directory / "alpha.json.bak").write_text("{not json", encoding="utf-8")
    (directory / ".alpha.json.swp").write_text("{not json", encoding="utf-8")
    app = create_app(dossier_dir=directory, llm=LLMDouble())
    assert len(app.state.store) == 0, (
        "`glob('*.json')` matches neither `.json.bak` nor a `.swp`; a corpus loader that "
        "widened the glob would start failing the boot on an editor's leftovers"
    )


# ---------------------------------------------------------------------------
# 2. Files that ARE a corpus and are broken. Every one names its path.
# ---------------------------------------------------------------------------


def _boot_error(directory) -> str:
    with pytest.raises(DossierLoadError) as raised:
        create_app(dossier_dir=directory, llm=LLMDouble())
    return str(raised.value)


@pytest.mark.parametrize(
    "label,contents",
    [
        ("truncated json", "{"),
        ("empty file", ""),
        ("whitespace only", "   \n  "),
        ("json that is a list", "[]"),
        ("json that is a number", "17"),
        ("json that is a string", '"alpha"'),
        ("json that is null", "null"),
        ("valid json, not a Dossier", '{"hello": "world"}'),
        ("a Dossier missing hubs", '{"person": {"person_id": "x", "name": "X"}}'),
        ("trailing comma", '{"person": {"person_id": "x"},}'),
        ("nan literal", '{"person": {"person_id": NaN}}'),
    ],
)
def test_a_broken_dossier_aborts_the_boot_and_the_message_names_its_path(
    tmp_path, fixture_dir, label, contents
):
    directory = _one(fixture_dir, tmp_path / "d")
    broken = directory / "zz-broken.json"
    broken.write_text(contents, encoding="utf-8")
    message = _boot_error(directory)
    assert str(broken) in message, f"{label}: the path is missing from {message!r}"


def test_a_directory_named_like_a_dossier_is_a_named_error_not_a_traceback(tmp_path,
                                                                          fixture_dir):
    """`Is a directory` is an `OSError`, which `_read_one` catches by name. Without that
    arm the boot dies with a raw `IsADirectoryError` at import and the operator gets a
    stack trace instead of a filename."""
    directory = _one(fixture_dir, tmp_path / "d")
    (directory / "adir.json").mkdir()
    message = _boot_error(directory)
    assert str(directory / "adir.json") in message


@pytest.mark.parametrize("codec", ["latin-1", "utf-16"])
def test_a_dossier_in_the_wrong_codec_is_a_named_error(tmp_path, fixture_dir, codec):
    """`UnicodeDecodeError` subclasses `ValueError`, not `OSError` — the distinction
    `_read_one`'s comment is written about. Graded against CPython's own hierarchy."""
    directory = _one(fixture_dir, tmp_path / "d")
    bad = directory / "zz-codec.json"
    bad.write_bytes('{"person": {"name": "José"}}'.encode(codec))
    message = _boot_error(directory)
    assert str(bad) in message


def test_the_first_offending_file_is_named_even_among_many_good_ones(tmp_path, fixture_dir):
    directory = tmp_path / "d"
    for name in ("alpha", "bravo", "charlie", "delta"):
        _one(fixture_dir, directory, name)
    bad = directory / "bbb-broken.json"
    bad.write_text("{", encoding="utf-8")
    message = _boot_error(directory)
    assert str(bad) in message
    for good in ("alpha.json", "charlie.json", "delta.json"):
        assert str(directory / good) not in message, (
            "the message must name the file to look at, not every file that was read"
        )


# ---------------------------------------------------------------------------
# 3. contracts.py's validators, reached through the BOOT path.
#
# Each case below is a value `contracts.py` declares illegal. The point of the test is not
# that pydantic rejects it — `tests/test_t054_contract_validators.py` grades that — but
# that the rejection surfaces as a `DossierLoadError` naming the file, rather than as a
# raw `ValidationError` out of `import arrival.web.app`.
# ---------------------------------------------------------------------------

VALIDATOR_VIOLATIONS = [
    ("Hub.recency above 1", {"hubs.0.recency": 1.7}),
    ("Hub.recency below 0", {"hubs.0.recency": -0.1}),
    ("Hub.recency is NaN", {"hubs.0.recency": float("nan")}),
    ("Hub.recency is infinity", {"hubs.0.recency": float("inf")}),
    ("Hub.hub_id blank", {"hubs.0.hub_id": ""}),
    ("Hub.hub_id all whitespace", {"hubs.0.hub_id": "   "}),
    ("Resolution.confidence above 1", {"resolution.confidence": 5.0}),
    ("Resolution.confidence below 0", {"resolution.confidence": -1.0}),
    ("Provenance.confidence is NaN", {"facts.0.provenance.confidence": float("nan")}),
    ("Provenance.quote blank", {"facts.0.provenance.quote": ""}),
    ("Provenance.quote all whitespace", {"facts.0.provenance.quote": "  \t "}),
    ("Verdict.confidence above 1", {"resolution.rejected.0.confidence": 2.0}),
    ("RawDoc-style bad source_kind", {"facts.0.provenance.source_kind": "telepathy"}),
    ("Resolution.status not in the literal", {"resolution.status": "maybe"}),
    ("Fact.category not in the literal", {"facts.0.category": "gossip"}),
    ("Hub.type not in the literal", {"hubs.0.type": "vibe"}),
    ("ExclusionReason not in the literal", {"facts.0.exclusion_reason": "because"}),
]


@pytest.mark.parametrize("label,mutation", VALIDATOR_VIOLATIONS, ids=[v[0] for v in
                                                                     VALIDATOR_VIOLATIONS])
def test_a_dossier_violating_a_contract_validator_aborts_boot_naming_its_path(
    tmp_path, fixture_dir, label, mutation
):
    directory = tmp_path / "d"
    _one(fixture_dir, directory, "bravo")           # a good neighbour, so the corpus is real
    _one(fixture_dir, directory, KNOWN_ID, **mutation)
    message = _boot_error(directory)
    assert str(directory / f"{KNOWN_ID}.json") in message, (
        f"{label}: {message!r} does not name the offending file"
    )
    assert str(directory / "bravo.json") not in message


def test_the_boot_error_is_the_type_a_deploy_is_told_to_catch(tmp_path, fixture_dir):
    """`web/app.py:__all__` re-exports `DossierLoadError` so a caller of `create_app` can
    catch a bad corpus without importing the store module. Pinned because a bare
    `RuntimeError` or a leaked `ValidationError` would both still "fail loudly" while
    breaking every `except DossierLoadError` a deploy wrote against that promise."""
    directory = _one(fixture_dir, tmp_path / "d", **{"hubs.0.recency": 9.0})
    with pytest.raises(DossierLoadError) as raised:
        create_app(dossier_dir=directory, llm=LLMDouble())
    assert isinstance(raised.value, RuntimeError)


def test_a_dossier_whose_person_id_disagrees_with_its_filename_is_keyed_by_the_dossier(
    tmp_path, fixture_dir
):
    """The store keys on `d.person.person_id`, never on the stem. So a file renamed on disk
    changes nothing, and two files carrying the SAME `person_id` silently collapse to one —
    which `contracts.PersonRef`'s comment names as deliberate ("the damage a bad id does is
    COLLISION, which is a property of the SET of ids") and nothing else in the suite pins."""
    directory = tmp_path / "d"
    _one(fixture_dir, directory, "bravo")
    raw = json.loads((fixture_dir / "alpha.json").read_text(encoding="utf-8"))
    (directory / "zzz-not-alpha.json").write_text(json.dumps(raw), encoding="utf-8")

    app = create_app(dossier_dir=directory, llm=LLMDouble())
    assert sorted(app.state.store.dossiers) == ["alpha", "bravo"]
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/arrive", json={"person_id": "alpha"}).status_code == 200
        assert client.post("/arrive", json={"person_id": "zzz-not-alpha"}).status_code == 404


def test_two_files_declaring_one_person_id_collapse_to_a_single_roster_entry(
    tmp_path, fixture_dir
):
    directory = tmp_path / "d"
    _one(fixture_dir, directory, KNOWN_ID)
    raw = json.loads((fixture_dir / "alpha.json").read_text(encoding="utf-8"))
    raw["person"]["name"] = "A Different Person Entirely"
    (directory / "zz-duplicate.json").write_text(json.dumps(raw), encoding="utf-8")

    app = create_app(dossier_dir=directory, llm=LLMDouble())
    assert len(app.state.store) == 1, (
        "duplicate ids collapse silently; if this ever becomes an error the change is "
        "visible here rather than as a roster that quietly lost somebody"
    )
    # `sorted(glob)` fixes which one wins: the LAST file read replaces the earlier.
    assert app.state.store.get(KNOWN_ID).person.name == "A Different Person Entirely"


# ---------------------------------------------------------------------------
# 4. Two apps in one process.
# ---------------------------------------------------------------------------


def test_two_apps_in_one_process_share_no_store_no_presence_and_no_digests(
    tmp_path, fixture_dir
):
    """Every handler closes over its own `app` (`_register_routes`'s docstring). This is
    the property a module-level store would break, and it is the reason the frozen harness
    can build several apps against different corpora in one interpreter."""
    left = _one(fixture_dir, tmp_path / "left", KNOWN_ID)
    right = _one(fixture_dir, tmp_path / "right", "charlie")

    a = create_app(dossier_dir=left, llm=LLMDouble())
    b = create_app(dossier_dir=right, llm=LLMDouble())

    assert a.state.store is not b.state.store
    assert a.state.presence is not b.state.presence
    assert a.state.digests is not b.state.digests

    with TestClient(a, raise_server_exceptions=False) as ca, \
            TestClient(b, raise_server_exceptions=False) as cb:
        assert ca.post("/arrive", json={"person_id": KNOWN_ID}).status_code == 200
        assert cb.post("/arrive", json={"person_id": KNOWN_ID}).status_code == 404
        assert cb.post("/arrive", json={"person_id": "charlie"}).status_code == 200
        assert ca.get("/building", headers={"accept": "application/json"}).json()["count"] == 1
        assert cb.get("/building", headers={"accept": "application/json"}).json()["count"] == 1
        assert [p["person_id"] for p in
                ca.get("/building", headers={"accept": "application/json"}).json()["present"]
                ] == [KNOWN_ID]
        assert [p["person_id"] for p in
                cb.get("/building", headers={"accept": "application/json"}).json()["present"]
                ] == ["charlie"]


def test_a_second_app_reads_the_environment_as_it_is_when_that_app_is_built(
    tmp_path, fixture_dir, monkeypatch
):
    """"Settings are read at factory time, never at import time" — the claim `create_app`'s
    docstring makes, tested the only way it can be: build two apps across a change."""
    corpus_one = _one(fixture_dir, tmp_path / "one", KNOWN_ID)
    corpus_two = _one(fixture_dir, tmp_path / "two", "charlie")

    monkeypatch.setenv("DOSSIER_DIR", str(corpus_one))
    get_settings.cache_clear()
    first = create_app(llm=LLMDouble())

    monkeypatch.setenv("DOSSIER_DIR", str(corpus_two))
    get_settings.cache_clear()
    second = create_app(llm=LLMDouble())

    assert sorted(first.state.store.dossiers) == [KNOWN_ID]
    assert sorted(second.state.store.dossiers) == ["charlie"]


# ---------------------------------------------------------------------------
# 5. DEBUG_VIEWS: R15's switch, and every truthiness edge of it.
# ---------------------------------------------------------------------------

#: pydantic-settings parses a bool from a fixed vocabulary. These are the values an
#: operator plausibly types, split by what the parser actually does with them.
DEBUG_ON = ["1", "true", "True", "TRUE", "yes", "YES", "on", "y", "t"]
DEBUG_OFF = ["0", "false", "False", "FALSE", "no", "NO", "off", "n", "f"]
DEBUG_REFUSED = ["", "2", "-1", "maybe", " 1 ", "1.0", "null", "None", "enabled"]


@pytest.mark.parametrize("value", DEBUG_ON)
def test_debug_views_on_serves_the_operator_view(corpus, monkeypatch, value):
    monkeypatch.setenv("DEBUG_VIEWS", value)
    get_settings.cache_clear()
    app = create_app(dossier_dir=corpus, llm=LLMDouble())
    assert app.state.debug_views is True
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(f"/debug/{KNOWN_ID}").status_code == 200


@pytest.mark.parametrize("value", DEBUG_OFF)
def test_debug_views_off_makes_the_route_a_404_rather_than_a_403(corpus, monkeypatch, value):
    """R15: "It is a switch, not auth." A 403 would confirm that a dossier is there to see,
    so the off state must be indistinguishable from a route that does not exist — including
    for a person who IS on the roster."""
    monkeypatch.setenv("DEBUG_VIEWS", value)
    get_settings.cache_clear()
    app = create_app(dossier_dir=corpus, llm=LLMDouble())
    assert app.state.debug_views is False
    with TestClient(app, raise_server_exceptions=False) as client:
        on_roster = client.get(f"/debug/{KNOWN_ID}")
        off_roster = client.get("/debug/nobody-at-all")
        assert on_roster.status_code == off_roster.status_code == 404
        assert on_roster.text == off_roster.text, (
            "a known person and an unknown one must produce the SAME page when the switch "
            "is off; a different body is the confirmation the 404 exists to withhold"
        )


def test_the_switch_is_absent_by_default(corpus, monkeypatch):
    monkeypatch.delenv("DEBUG_VIEWS", raising=False)
    get_settings.cache_clear()
    app = create_app(dossier_dir=corpus, llm=LLMDouble())
    assert app.state.debug_views is False
    assert Settings(_env_file=None).debug_views is False


@pytest.mark.parametrize("value", DEBUG_REFUSED)
def test_a_debug_views_value_pydantic_cannot_read_fails_the_boot_naming_the_field(
    corpus, monkeypatch, value
):
    """The edge the packet asks about, and it is NOT a 404 — it is a dead app.

    `get_settings` deliberately re-raises `ValidationError` unwrapped ("It already IS the
    diagnosis this guard exists to supply"), and `web/app.py` ends with `app = create_app()`,
    so `DEBUG_VIEWS=` — an env var exported with no value, which is what `export
    DEBUG_VIEWS=` and a bare `DEBUG_VIEWS=` line in a `.env` both produce — takes the whole
    service down at import rather than turning the switch off.

    The assertion is on the DIAGNOSIS, which is the part that has to be true for an
    operator to recover: the error names `debug_views` and quotes the value it refused.
    Whether failing is the right answer at all is a product question this ticket reports
    rather than decides.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("DEBUG_VIEWS", value)
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as raised:
        create_app(dossier_dir=corpus, llm=LLMDouble())
    message = str(raised.value)
    assert "debug_views" in message
    assert repr(value) in message or value in message


def test_the_switch_is_read_once_at_factory_time_and_does_not_flip_under_a_running_app(
    corpus, monkeypatch
):
    """`web/app.py:150-153`: "R15 calls it a switch rather than auth, and a switch that
    could flip under a running process would be a worse contract than one that is read once
    and reported honestly." """
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    get_settings.cache_clear()
    app = create_app(dossier_dir=corpus, llm=LLMDouble())
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(f"/debug/{KNOWN_ID}").status_code == 200
        monkeypatch.setenv("DEBUG_VIEWS", "0")
        get_settings.cache_clear()
        assert client.get(f"/debug/{KNOWN_ID}").status_code == 200, (
            "the running app keeps the value it was built with"
        )
    get_settings.cache_clear()
    rebuilt = create_app(dossier_dir=corpus, llm=LLMDouble())
    with TestClient(rebuilt, raise_server_exceptions=False) as client:
        assert client.get(f"/debug/{KNOWN_ID}").status_code == 404


# ---------------------------------------------------------------------------
# 6. The boot path an actual deploy takes: `import arrival.web.app`.
# ---------------------------------------------------------------------------


def test_importing_the_module_over_a_broken_corpus_prints_the_path_and_not_a_traceback(
    tmp_path, fixture_dir
):
    """Out of process, because `app = create_app()` runs at IMPORT and `uvicorn
    arrival.web.app:app` is the command Render runs. In-process the module is already
    imported and the interesting failure cannot happen.

    Complements `tests/web/test_t058_store_encoding.py`'s codec test with the case a
    validator produces: a syntactically fine dossier carrying an illegal VALUE.
    """
    directory = _one(fixture_dir, tmp_path / "d", **{"hubs.0.recency": 4.2})
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    completed = subprocess.run(
        [sys.executable, "-c", "import arrival.web.app"],
        env={**os.environ, "DOSSIER_DIR": str(directory), "PYTHONPATH": os.path.join(root, "src")},
        cwd=root, capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode != 0, completed.stdout
    assert "DossierLoadError" in completed.stderr, completed.stderr[-2000:]
    assert str(directory / f"{KNOWN_ID}.json") in completed.stderr, completed.stderr[-2000:]


def test_importing_the_module_over_a_good_corpus_serves_that_corpus(tmp_path, fixture_dir):
    """The positive control for the test above. Without it, a boot that failed for an
    unrelated reason (a typo'd PYTHONPATH, say) would read as the defect being caught."""
    directory = _one(fixture_dir, tmp_path / "d", "charlie")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    completed = subprocess.run(
        [sys.executable, "-c",
         "import arrival.web.app as m; print(sorted(m.app.state.store.dossiers))"],
        env={**os.environ, "DOSSIER_DIR": str(directory), "PYTHONPATH": os.path.join(root, "src")},
        cwd=root, capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "['charlie']" in completed.stdout, completed.stdout
