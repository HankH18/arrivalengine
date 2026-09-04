"""FROZEN ACCEPTANCE - ticket T-8: the web app (presence, digest render, debug view).

Graded requirements: R3, R4, R5, R6, R7, R8, R10, R13, R15, S2, C4.

Everything here is exercised through the public HTTP surface pinned by DESIGN's route
table (POST /arrive, POST /leave, GET /building, GET /digest/{id},
GET /debug/{person_id}, GET /) against the ORCHESTRATOR-OWNED fixture corpus in
`.swarm-loop/acceptance/fixtures/dossiers/`. No test reads `tests/fixtures/`: those
files are inside T-8's own reads-scope and a gradee that can rewrite the answer key is
not being graded.

THE APPLICATION SEAM - read this before implementing T-8. IT IS A HARNESS REQUIREMENT,
NOT A DESIGN ONE.
--------------------------------------------------------------------------------------
DESIGN names the module (`arrival.web.app`) and a route table and stops there: it pins
NOTHING about how the app object is constructed, and T-0's `Settings` ships no
dossier-directory field (its module docstring forbids downstream tickets from widening
it, so that needs an escalation, not a quiet edit). TASKS T-8 nonetheless requires the
app to be pointed at a fixture dossier directory under test, and R4 requires proving
that an off-roster arrival triggers no live research.

So this harness ADDS a construction contract. It is written out here, in full, so a
T-8 worker reads a specification instead of guessing, and so the human owner can see
at review exactly what T-8 is being held to that no design document promises:

  1. env var `DOSSIER_DIR` (the `Settings`-style env name for a `dossier_dir` field) is
     set before the app is constructed, and the harness DROPS every cached `arrival.*`
     module first, so an app built at import time is re-built against it. Both shapes
     below are given a fair chance; neither is quietly excluded by import caching.
  2. EITHER an application factory in `arrival.web.app` - any of `create_app`,
     `make_app`, `build_app`, `create_application`, `get_app`, `app_factory` - taking
     the keywords it wants from `dossier_dir=Path` (aliases: `dossiers_dir`,
     `dossier_directory`) and `llm=<client>` (alias: `llm_client`), or `**kwargs`;
     OR a module-level instance named `app` (aliases: `application`, `api`).
  3. The `llm=` keyword is MANDATORY for every test that hands the app a client -
     which is every test except the boot test. It is not a convenience: R4's "no live
     research" and the offline say-out-loud call of DESIGN Decision 12 are only
     observable when the app uses a client the harness constructed and can count. An
     app that reaches for `ANTHROPIC_API_KEY` itself is ungradeable here, so a
     module-level-only app fails those tests BY NAME, with this contract quoted, not
     with a mystery. Only a factory can take an injected client, so for those tests a
     factory is genuinely required and the harness says so out loud.

`DEBUG_VIEWS` stays an environment variable because SPEC R15 names it as one.

The frozen suite ships its own recording LLM stub below rather than importing
`tests/doubles.py`, which is worker-writable.

Rendering assumptions, stated so they are arguable rather than hidden:
  * each R7 section is introduced by SOME machine-locatable section anchor: a heading
    element (<h1>..<h6>, <summary>, <legend>, <caption>, <dt>, <th>, <figcaption>)
    whose text names it, OR a sectioning container (<section>, <article>, <div>,
    <main>, <aside>, <details>, <header>, <footer>, <table>, <ul>, <ol>, <dl>,
    <fieldset>) whose `id`, `class`, `aria-label` or `data-*` names it. DESIGN pins no
    markup, so anything that makes the six sections separately addressable counts;
    what the harness genuinely needs is that they are DISTINCT and ORDERED.
  * TASKS T-8 acceptance 3 pins the attribute `data-reasoning` as the reasoning
    affordance R10 asks for, so the reasoning test looks for it by name.
  * numbers are compared NUMERICALLY, with a tolerance, never by matching their
    rendering: "0.51", "0.5108" and "1.50" are all acceptable spellings.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import time
import traceback
from pathlib import Path

import pytest

# Two markers, deliberately. `t8` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t8`), and every scored metric
# selects on it. `ticket("T-8")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t8, pytest.mark.ticket("T-8")]


# --------------------------------------------------------------------------------
# Frozen corpus facts these tests key on (measured from the committed fixture files).
# --------------------------------------------------------------------------------
ARRIVING_NAME = "Runa Okonkwo"
ARRIVING_ID = "runa-okonkwo"

# Everyone else in the frozen corpus, name -> person_id.
CAST = {
    "Sil Vantorre": "sil-vantorre",       # matches Runa at exactly 100
    "Jem Arrowood": "jem-arrowood",       # matches Runa at exactly 67
    "Mira Hollowell": "mira-hollowell",   # 0
    "Theo Baptiste": "theo-baptiste",     # 0
}

OFF_ROSTER_NAME = "Wendell Ashgrove-Pike"

# Material the host must NEVER see (R11 taste categories, R12 confidence floor and
# source-kind whitelist). Each string appears in exactly one fixture fact.
WITHHELD = (
    "their spouse Delia Moreno-Vance",              # excluded: family
    "1442 Quarrystone Lane",                        # excluded: home_or_property
    "a low-confidence claim about ferry schedules",  # kept, confidence 0.55 (< 0.7)
    "a contribution recorded in a filing",           # kept, source_kind fec (never shown)
)

# Material the host MUST see. Without these the "withheld" test would pass on a blank
# page, which is the classic way a negative-space test measures nothing.
DISPLAYED = (
    "Quarrystone Labs shipped a public status page in 2017",  # the one non_obvious fact
    "Opened the Quarrystone platform team roadmap to customers as a public page.",
)

# R7 section names, in the order R7 requires them.
SECTION_KEYS = (
    "who",
    "meet",
    "lately",
    "not on the first page",
    "say out loud",
    "why we know this",
)

# The say-out-loud line the stub returns: an invitation, no digits, no parentheses, no
# URL, under thirty words (R14, R18), so a correct implementation can render it as-is.
STUB_SAY_OUT_LOUD = (
    "Ask about the public status page Quarrystone Labs shipped years "
    "before anyone else bothered."
)

# --------------------------------------------------------------------------------
# THE CONSTRUCTION CONTRACT. Named here, once, so every seam failure can quote it
# verbatim instead of leaving a T-8 worker to reverse-engineer it from a traceback.
# DESIGN pins none of this - see THE APPLICATION SEAM in the module docstring.
# --------------------------------------------------------------------------------
FACTORY_NAMES = (
    "create_app",
    "make_app",
    "build_app",
    "create_application",
    "get_app",
    "app_factory",
)
INSTANCE_NAMES = ("app", "application", "api")
DOSSIER_KWARGS = ("dossier_dir", "dossiers_dir", "dossier_directory")
LLM_KWARGS = ("llm", "llm_client")

_SEAM_CONTRACT = (
    "The harness constructs the app like this (DESIGN pins none of it; see THE "
    "APPLICATION SEAM in this module's docstring):\n"
    "  * env DOSSIER_DIR and DEBUG_VIEWS are set, ANTHROPIC_API_KEY is removed, and "
    "every cached `arrival.*` module is dropped, so an app built at import time is "
    "re-built against the directory under test;\n"
    f"  * then `arrival.web.app` is imported and the FIRST of {FACTORY_NAMES} that is "
    "callable is used as an application factory, receiving whichever of "
    f"{DOSSIER_KWARGS} and {LLM_KWARGS} its signature declares (or all of them, if it "
    "declares **kwargs);\n"
    f"  * failing that, the first module-level attribute in {INSTANCE_NAMES} is used "
    "as an already-built app, configured from DOSSIER_DIR."
)

_LLM_SEAM_MESSAGE = (
    "NO LLM INJECTION SEAM. This test hands the app an LLM client it constructed and "
    "then makes assertions about that client, so the app must actually use it.\n\n"
    "Why it is mandatory rather than convenient: R4 says an off-roster arrival must "
    "trigger no live research, and 'no live research' is only observable by counting "
    "calls on a client the harness owns. DESIGN Decision 12 puts one say-out-loud call "
    "on the arrival path, and C4 plus the deleted ANTHROPIC_API_KEY mean it has to run "
    "offline. An app that reaches for its own client is not slower here, it is "
    "UNGRADEABLE.\n\n"
    "This is the one place the harness genuinely requires a FACTORY: an app object "
    "built at import time has nowhere to receive an injected client. Expose\n"
    "    arrival.web.app.create_app(dossier_dir: Path, llm=None)\n"
    "and have every LLM call on the request path go through the `llm` it was given.\n\n"
    + _SEAM_CONTRACT
)

_NO_SEAM_AT_ALL_MESSAGE = (
    "`arrival.web.app` exposes no way to obtain an application: none of "
    f"{FACTORY_NAMES} is callable and none of {INSTANCE_NAMES} is defined, so the "
    "service cannot be booted at all.\n\n" + _SEAM_CONTRACT
)


# --------------------------------------------------------------------------------
# Local doubles and helpers. Nothing here imports product code at module scope: at
# cycle 0 none of it exists, and a module-scope import would turn an unbuilt feature
# into a collection error that silently removes this whole file from the denominator.
# --------------------------------------------------------------------------------
def _sample_value(annotation, line):
    text = str(annotation)
    if "list" in text.lower():
        return []
    if "dict" in text.lower():
        return {}
    if "bool" in text:
        return True
    if "float" in text:
        return 1.0
    if "int" in text:
        return 1
    return line


def _instantiate(schema, line):
    """Build a plausible instance of an arbitrary Pydantic response schema."""
    fields = getattr(schema, "model_fields", None) or {}
    values = {
        name: _sample_value(getattr(field, "annotation", str), line)
        for name, field in fields.items()
    }
    try:
        return schema(**values)
    except Exception:
        try:
            return schema.model_construct(**values)
        except Exception:
            return schema.model_construct()


class _RecordingLLM:
    """An offline `LLMClient` that records every call it is given.

    Deliberately defined here rather than imported from `tests/doubles.py`: that file
    is inside a ticket's write scope, and a stub the gradee controls cannot witness
    whether the gradee called it.
    """

    def __init__(self, line=STUB_SAY_OUT_LOUD):
        self.line = line
        self.calls = []

    async def structured(self, *, system, user, schema, max_tokens=2000, cache_prefix=True):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": getattr(schema, "__name__", str(schema)),
            }
        )
        return _instantiate(schema, self.line)


def _clear_settings_cache():
    """Drop any cached Settings so a freshly set env var is actually read."""
    try:
        import importlib

        config = importlib.import_module("arrival.config")
    except Exception:
        return
    for name in ("get_settings", "settings", "Settings"):
        clear = getattr(getattr(config, name, None), "cache_clear", None)
        if callable(clear):
            clear()


def _purge_product_modules():
    """Drop every cached `arrival.*` module so the next import re-reads the environment.

    `importlib.import_module` is a CACHE HIT after the first call. Without this, an
    implementation that builds its `app` at import time - the ordinary FastAPI shape,
    and the one the project's own README documents (`uvicorn arrival.web.app:app`) -
    would be bound to whichever dossier directory the first test in the process
    happened to set, FOREVER. `test_boot_rejects_a_corrupt_dossier_file_and_names_its_path`
    boots a clean directory and then a corrupt one in the same process, so it could
    never pass at all; the other tests in this module would silently read the wrong
    corpus and grade the wrong page. Re-executing the package gives a module-level
    `app` a genuine chance instead of making the factory mandatory by accident.

    The whole `arrival.*` tree goes, not just `arrival.web.app`: any module under it
    may have cached a directory or a `Settings` at import, and purging a subset would
    leave the fresh web modules wired to stale ones.
    """
    import sys

    for name in [n for n in list(sys.modules) if n == "arrival" or n.startswith("arrival.")]:
        del sys.modules[name]


# --------------------------------------------------------------------------------
# HARNESS-OWNED SEAM - the human owner should read this at review.
#
# DESIGN does NOT pin how the web app is constructed. It names the module
# (`arrival.web.app`, DESIGN's Interfaces table) and a route table, and stops. T-0's
# `Settings` carries no dossier-directory field and forbids downstream tickets from
# widening it. Everything below - the factory names, the `dossier_dir=` and `llm=`
# keywords, the `DOSSIER_DIR` env var, the module-level instance names - is a contract
# THIS HARNESS ADDS so that (a) the app can be pointed at an orchestrator-owned corpus
# instead of the gradee's own fixtures, and (b) R4's "no live research" is observable.
# It is deliberately as WIDE as it can be while still measuring those two things, and
# every rejection below quotes it. The one thing it cannot be talked out of is LLM
# injection, and that requirement is stated where it bites rather than implied.
# --------------------------------------------------------------------------------
def _make_app(monkeypatch, dossier_dir, *, llm=None, debug_views=False):
    """Construct the app pointed at `dossier_dir`. See THE APPLICATION SEAM above."""
    import importlib
    import inspect

    dossier_dir = Path(dossier_dir)
    monkeypatch.setenv("DOSSIER_DIR", str(dossier_dir))
    monkeypatch.setenv("DEBUG_VIEWS", "1" if debug_views else "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_settings_cache()
    _purge_product_modules()

    module = importlib.import_module("arrival.web.app")

    factory = next(
        (getattr(module, name) for name in FACTORY_NAMES if callable(getattr(module, name, None))),
        None,
    )

    if factory is not None:
        params = inspect.signature(factory).parameters
        var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
        kwargs = {}
        declared_dossier = next((n for n in DOSSIER_KWARGS if n in params), None)
        if declared_dossier is not None:
            kwargs[declared_dossier] = dossier_dir
        elif var_kw:
            kwargs["dossier_dir"] = dossier_dir
        # else: the factory takes no directory argument, which is fine - DOSSIER_DIR is
        # set and a factory is entitled to read its own configuration.
        if llm is not None:
            declared_llm = next((n for n in LLM_KWARGS if n in params), None)
            if declared_llm is not None:
                kwargs[declared_llm] = llm
            elif var_kw:
                kwargs["llm"] = llm
            else:
                pytest.fail(
                    f"{module.__name__}.{getattr(factory, '__name__', factory)}"
                    f"{inspect.signature(factory)} declares no LLM-client keyword "
                    f"(any of {LLM_KWARGS}, or **kwargs).\n\n" + _LLM_SEAM_MESSAGE
                )
        return factory(**kwargs)

    instance = next(
        (
            getattr(module, name)
            for name in INSTANCE_NAMES
            if getattr(module, name, None) is not None
        ),
        None,
    )
    if instance is None:
        pytest.fail(_NO_SEAM_AT_ALL_MESSAGE)
    if llm is not None:
        pytest.fail(
            f"`arrival.web.app` exposes a module-level app but none of {FACTORY_NAMES} "
            "is callable, so there is nowhere to hand the recording LLM client this "
            "test constructed.\n\n" + _LLM_SEAM_MESSAGE
        )
    return instance


@contextlib.contextmanager
def _running(monkeypatch, dossier_dir, **kwargs):
    """Boot the app and yield a TestClient with startup/shutdown events fired."""
    app = _make_app(monkeypatch, dossier_dir, **kwargs)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client


def _copy_corpus(frozen_fixtures, destination, *, include_unresolved=False):
    destination.mkdir(parents=True, exist_ok=True)
    for src in sorted((frozen_fixtures / "dossiers").glob("*.json")):
        shutil.copy(src, destination / src.name)
    if include_unresolved:
        for src in sorted((frozen_fixtures / "dossiers_unresolved").glob("*.json")):
            shutil.copy(src, destination / src.name)
    return destination


def _corpus(frozen_fixtures, tmp_path, *, include_unresolved=False):
    """A PRIVATE per-test copy of the frozen corpus.

    Never boot against `frozen_fixtures/dossiers` itself. The app is handed a writable
    directory and told to treat it as its data store; one implementation that writes a
    cache, a lock file or a re-serialised dossier back into it would silently rewrite
    the orchestrator-owned answer key for every other test in the run, and the
    contaminated tests would still be graded. A copy per test costs five small files.
    """
    return _copy_corpus(
        frozen_fixtures, tmp_path / "corpus", include_unresolved=include_unresolved
    )


def _corpus_with_withheld_facts_dated_newest(frozen_fixtures, destination):
    """The frozen corpus, with the arriving person's withheld facts re-dated newest.

    Ordering must not be what hides them. In the corpus as committed, every withheld
    fact is older than the three most recent displayable ones, so a page that simply
    showed the three newest facts would look clean while filtering nothing at all -
    measured: a build with `is_displayable` hard-wired to True still renders a clean
    digest. Re-dating them removes that accident. A correct implementation is
    unaffected (it drops these facts on `excluded`, on the 0.7 confidence floor and on
    the source-kind whitelist, none of which is a date), while any implementation that
    filters on recency alone now puts the withheld material at the top of the page.
    """
    _copy_corpus(frozen_fixtures, destination)
    path = destination / f"{ARRIVING_ID}.json"
    dossier = json.loads(path.read_text(encoding="utf-8"))
    bumped = []
    for fact in dossier["facts"]:
        if any(secret.lower() in fact["text"].lower() for secret in WITHHELD):
            fact["provenance"]["published_at"] = "2026-02-19"
            bumped.append(fact["fact_id"])
    assert len(bumped) == len(WITHHELD), (
        f"expected to re-date {len(WITHHELD)} withheld facts, re-dated {bumped}; the "
        "frozen corpus has changed and this test no longer discriminates"
    )
    path.write_text(json.dumps(dossier, indent=2) + "\n", encoding="utf-8")
    return destination


def _arrive(client, name):
    response = client.post("/arrive", json={"name": name})
    assert response.status_code == 200, (
        f"POST /arrive {name!r} -> {response.status_code}: {response.text[:400]}"
    )
    return response.json()


def _digest_html(client, digest_id):
    response = client.get(f"/digest/{digest_id}")
    assert response.status_code == 200, (
        f"GET /digest/{digest_id} -> {response.status_code}: {response.text[:400]}"
    )
    return response.text


def _staged_digest(client, present_names, arriving_name=ARRIVING_NAME):
    """Put `present_names` in the building, then arrive `arriving_name`."""
    for name in present_names:
        _arrive(client, name)
    body = _arrive(client, arriving_name)
    digest_id = body.get("digest_id")
    assert digest_id, f"POST /arrive returned no digest_id: {body!r}"
    return digest_id, _digest_html(client, digest_id)


def _building_blob(client):
    """Lowercased JSON text of GET /building - shape-tolerant presence evidence."""
    response = client.get("/building", headers={"Accept": "application/json"})
    assert response.status_code == 200, (
        f"GET /building -> {response.status_code}: {response.text[:400]}"
    )
    return response.text.lower()


def _person_id(name):
    if name == ARRIVING_NAME:
        return ARRIVING_ID
    person_id = CAST.get(name)
    assert person_id, f"{name!r} is not part of the frozen corpus"
    return person_id


def _listed(blob, name):
    """True when GET /building names this person, by id or by display name."""
    return _person_id(name) in blob or name.lower() in blob


_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[#0-9a-zA-Z]+;")

# --------------------------------------------------------------------------------
# LOCATING THE R7 SECTIONS.
#
# DESIGN pins no markup for the digest page at all - SPEC's non-goals say "no design
# system", TASKS says "no CSS beyond a few inline rules", and the only markup token any
# document names is the `data-reasoning` attribute. So requiring an <h1>-<h6> heading
# per section, as this harness used to, invented a contract: a page that sectioned
# itself with `<section id="meet">` or `<details><summary>` was correct by every
# document in the repo and red here forever.
#
# What the harness ACTUALLY needs from R7 ("exactly these sections, in order") is that
# the six sections are separately addressable, distinct and ordered. So a section
# anchor is now either:
#   * a labelling element whose TEXT names the section - <h1>..<h6>, <summary>,
#     <legend>, <caption>, <dt>, <th>, <figcaption>; or
#   * a sectioning CONTAINER whose identity attributes name it - id / class /
#     aria-label / any data-* on <section>, <article>, <div>, <main>, <aside>,
#     <details>, <header>, <footer>, <table>, <tbody>, <ul>, <ol>, <dl>, <fieldset>.
#
# `<a>`, `<nav>`, `<button>`, `<li>` and `<span>` are deliberately NOT anchors: an
# in-page table-of-contents link like `<a href="#meet">` would otherwise plant a
# "meet" anchor above the real one and silently move every section boundary. `href` is
# never read for the same reason.
# --------------------------------------------------------------------------------
_LABEL_ELEMENT_RE = re.compile(
    r"<(h[1-6]|summary|legend|caption|dt|th|figcaption)\b[^>]*>(.*?)</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CONTAINER_RE = re.compile(
    r"<(?:section|article|div|main|aside|details|header|footer|table|tbody|ul|ol|dl|fieldset"
    r"|h[1-6])\b([^>]*)>",
    re.IGNORECASE,
)
_IDENTITY_ATTR_RE = re.compile(
    r"""\b(id|class|aria-label|data-[\w-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)


def _plain(fragment):
    """Tags and entities stripped, lowercased, punctuation collapsed to single spaces."""
    text = _TAG_RE.sub(" ", fragment)
    text = _ENTITY_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def _anchors(html):
    """(start, end, label) for every section anchor in the page, in document order.

    `start` is where the anchor begins and `end` is where the section's content begins:
    after `</h2>` for a labelling element, after the `>` of a container's start tag.
    """
    found = []
    for m in _LABEL_ELEMENT_RE.finditer(html):
        found.append((m.start(), m.end(), _plain(m.group(2))))
    for m in _CONTAINER_RE.finditer(html):
        values = " ".join(
            attr.group(2).strip("\"'") for attr in _IDENTITY_ATTR_RE.finditer(m.group(1))
        )
        label = _plain(values)
        if label:
            found.append((m.start(), m.end(), label))
    return sorted(found, key=lambda item: item[0])


def _section_anchors(html):
    """The FIRST anchor for each R7 section key, plus every anchor label seen."""
    found = _anchors(html)
    located = {}
    for key in SECTION_KEYS:
        for start, end, label in found:
            if key in label:
                located[key] = (start, end)
                break
    return located, [label for _s, _e, label in found]


def _section_offsets(html):
    """First document offset at which each R7 section is anchored, by key."""
    located, labels = _section_anchors(html)
    return {key: span[0] for key, span in located.items()}, labels


def _section_span(html, key, next_key=None):
    """The HTML of the `key` section: from its anchor to the next SECTION's anchor.

    Boundaries are drawn only at the six R7 sections, so a heading nested inside a Meet
    row cannot truncate the span - which the old any-heading rule had to special-case.
    """
    located, labels = _section_anchors(html)
    if key not in located:
        pytest.fail(
            f"no section anchor names {key!r}. A section anchor is a heading-like "
            "element (<h1>-<h6>, <summary>, <legend>, <caption>, <dt>, <th>, "
            "<figcaption>) whose text names the section, or a sectioning container "
            "(<section>, <article>, <div>, <main>, <aside>, <details>, <header>, "
            "<footer>, <table>, <ul>, <ol>, <dl>, <fieldset>) whose id / class / "
            "aria-label / data-* names it. R7 needs the six sections to be distinct "
            f"and ordered; any of those spellings will do. Anchors found: {labels}"
        )
    start = located[key][1]
    later = sorted(
        span[0] for other, span in located.items() if other != key and span[0] > start
    )
    if not later:
        return html[start:]
    if next_key is not None and next_key in located and located[next_key][0] > start:
        return html[start : located[next_key][0]]
    return html[start : later[0]]


# --------------------------------------------------------------------------------
# Numbers are compared NUMERICALLY. DESIGN pins the score arithmetic (Decision 3) and
# nothing at all about how a float is rendered, so a regex over its spelling grades
# formatting rather than correctness - and two such regexes in this file could not both
# be satisfied by any single format: `0\.51` accepted "0.5108" while `\b1\.5\b`
# REJECTED "1.50", so a page formatting every weight to two places was red on one
# assertion and a page using repr() was red on the other.
# --------------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# ln(5 / (1 + 2)) - DESIGN Decision 3's idf = max(0, ln(N / (1 + n_people_on_hub))),
# with N = 5 people in the frozen corpus and 2 of them on `investor:foundry-seed-2019`.
IDF_INVESTOR_HUB = 0.5108256
TYPE_BOOST_INVESTOR = 1.5
# Half of one unit in the second decimal place: any rendering carrying two or more
# significant decimals passes ("0.51", "0.511", "0.5108256", "1.5", "1.50", "1.500"),
# a one-decimal "0.5" does not. R10 asks for the weight to be visible, not approximated.
NUMBER_TOLERANCE = 0.005


def _numbers(fragment):
    """Every numeric literal in `fragment`, as floats - spelling-agnostic."""
    return [float(m.group()) for m in _NUMBER_RE.finditer(fragment)]


def _shows_number(fragment, expected, tolerance=NUMBER_TOLERANCE):
    return any(abs(value - expected) <= tolerance for value in _numbers(fragment))


# --------------------------------------------------------------------------------
# The demo driver. TASKS T-8 acceptance 6 and DESIGN's route table both promise that
# `GET /` is a roster with WORKING plain-HTML arrive/leave forms; `python-multipart` is
# pinned in pyproject for exactly that. Parsed with the stdlib HTMLParser rather than a
# regex, because a form's controls are nested inside it and the page is free to lay
# them out however it likes - one form per person, or one form with a <select>.
# --------------------------------------------------------------------------------
def _forms(page):
    """Every <form> as {"action", "method", "fields": {name: value}, "options": {...}}."""
    from html.parser import HTMLParser

    class _Collector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.forms = []
            self._form = None
            self._select = None
            self._option_needs_text = False

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            a = {k.lower(): v for k, v in attrs}
            if tag == "form":
                self._form = {
                    "action": a.get("action") or "",
                    "method": (a.get("method") or "get").lower(),
                    "fields": {},
                    "options": {},
                }
                self.forms.append(self._form)
                return
            if self._form is None:
                return
            if tag in ("input", "button", "textarea") and a.get("name"):
                self._form["fields"].setdefault(a["name"], a.get("value") or "")
            elif tag == "select" and a.get("name"):
                self._select = a["name"]
                self._form["options"].setdefault(self._select, [])
                self._form["fields"].setdefault(self._select, "")
            elif tag == "option" and self._select:
                if a.get("value") is None:
                    self._option_needs_text = True  # <option>Text</option>
                else:
                    self._record_option(a["value"])

        def _record_option(self, value):
            self._form["options"][self._select].append(value)
            if not self._form["fields"].get(self._select):
                self._form["fields"][self._select] = value

        def handle_data(self, data):
            if self._option_needs_text and data.strip():
                self._option_needs_text = False
                self._record_option(data.strip())

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag == "option":
                self._option_needs_text = False
            elif tag == "select":
                self._select = None
            elif tag == "form":
                self._form = None
                self._select = None
                self._option_needs_text = False

    collector = _Collector()
    collector.feed(page)
    collector.close()
    return collector.forms


def _pick_form(page, route, person_name):
    """The form on `page` that drives `route` for `person_name`, ready to submit.

    A form qualifies when a path segment of its `action` is the route name and the
    person is identified either in that path or in one of the form's own controls
    (hidden input, button value, or a <select> option). The returned dict's `fields`
    already carry the person's option selected, so it can be posted as-is.
    """
    person_id = _person_id(person_name)
    wanted = (person_id.lower(), person_name.lower())
    for form in _forms(page):
        path = form["action"].split("?")[0].split("#")[0]
        if route not in [segment for segment in path.split("/") if segment]:
            continue
        for select, values in form["options"].items():
            match = next((v for v in values if v.strip().lower() in wanted), None)
            if match is not None:
                form["fields"][select] = match
        haystack = " ".join(
            [path, *form["fields"], *(str(v) for v in form["fields"].values())]
        ).lower()
        if any(token in haystack for token in wanted):
            return form
    pytest.fail(
        f"GET / carries no plain-HTML form that would {route} {person_name!r}. TASKS "
        "T-8 acceptance 6 and DESIGN's route table both promise the demo driver is a "
        f"roster with WORKING arrive/leave forms posting to the routes, so a <form "
        f'method="post" action="/{route}"> identifying the person (hidden input, '
        "button value, <select> option, or the action path) must be on the page. "
        f"Forms found: {[(f['method'], f['action'], f['fields']) for f in _forms(page)]}"
    )


def _submit(client, form, route):
    """POST a parsed form the way a browser would, and return the response."""
    action = form["action"].split("#")[0].strip()
    if not action:
        target = f"/{route}"
    elif action.startswith("/"):
        target = action
    else:
        target = "/" + action.lstrip("./")
    assert form["method"] == "post", (
        f'the /{route} form declares method="{form["method"]}"; {route} changes server '
        "state and DESIGN's route table makes it a POST, so a GET form cannot drive it"
    )
    response = client.post(target, data=form["fields"])
    assert response.status_code < 400, (
        f"submitting the demo driver's {route} form ({target}, {form['fields']}) "
        f"answered {response.status_code}: {response.text[:400]}"
    )
    return response


# --------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------
def test_boot_rejects_a_corrupt_dossier_file_and_names_its_path(
    monkeypatch, tmp_path, frozen_fixtures
):
    """T-8 acceptance 1 / C4: boot validates every dossier JSON and aborts naming the bad file."""
    clean = _copy_corpus(frozen_fixtures, tmp_path / "clean", include_unresolved=True)
    with _running(monkeypatch, clean) as client:
        # Positive control: a directory of valid dossiers - including a schema-valid
        # UNRESOLVED one - must boot. Without this the abort assertion below would be
        # satisfied by an app that refuses to start on anything at all.
        assert client.get("/building", headers={"Accept": "application/json"}).status_code == 200

    corrupt = _copy_corpus(frozen_fixtures, tmp_path / "corrupt")
    (corrupt / "broken-dossier.json").write_text(
        '{"person": {"person_id": "broken-dossier", "name": "Broken"',
        encoding="utf-8",
    )
    with pytest.raises(BaseException) as excinfo:  # noqa: PT011 - SystemExit is a valid abort
        with _running(monkeypatch, corrupt):
            pass
    # A harness-seam failure is also a BaseException (pytest's own outcomes derive from
    # it), and swallowing one here would report "boot did not name the file" for an app
    # that could not be constructed at all. Let it out unchanged.
    if isinstance(excinfo.value, (pytest.fail.Exception, pytest.skip.Exception)):
        raise excinfo.value
    report = "".join(traceback.format_exception(excinfo.value))
    assert "broken-dossier.json" in report, (
        "boot aborted, but the error does not name the offending file; the operator "
        f"cannot find it. Error was:\n{report[-2000:]}"
    )


def test_arrive_returns_a_digest_id_within_three_seconds_and_records_presence(
    monkeypatch, tmp_path, frozen_fixtures
):
    """S2 / R3: with three present, a fourth arrival returns a digest id in under 3 s."""
    llm = _RecordingLLM()
    with _running(monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=llm) as client:
        for name in ("Sil Vantorre", "Jem Arrowood", "Mira Hollowell"):
            _arrive(client, name)

        started = time.perf_counter()
        response = client.post("/arrive", json={"name": ARRIVING_NAME})
        elapsed = time.perf_counter() - started

        assert response.status_code == 200, response.text[:400]
        body = response.json()
        assert body.get("digest_id"), f"POST /arrive returned no digest_id: {body!r}"
        assert body.get("person_id") == ARRIVING_ID, body
        assert elapsed < 3.0, f"POST /arrive took {elapsed:.2f}s; R3 budget is 3 s"
        assert _listed(_building_blob(client), ARRIVING_NAME), (
            "the arriving person is not in the presence set afterwards"
        )


def test_arrive_for_an_unknown_name_is_404_and_triggers_no_llm_call(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R4: an off-roster arrival is refused and does NOT trigger live research."""
    llm = _RecordingLLM()
    with _running(monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=llm) as client:
        response = client.post("/arrive", json={"name": OFF_ROSTER_NAME})
        assert response.status_code == 404, (
            f"off-roster arrival returned {response.status_code}: {response.text[:400]}"
        )
        assert llm.calls == [], (
            f"R4: an off-roster arrival made {len(llm.calls)} LLM call(s): "
            f"{[c['schema'] for c in llm.calls]}"
        )

        # Companion control: the injected client IS the one the app uses, so the empty
        # call list above is evidence of restraint rather than evidence of a dead seam.
        assert client.post("/arrive", json={"name": ARRIVING_NAME}).status_code == 200
        assert len(llm.calls) >= 1, (
            "a roster arrival made no LLM call either, so the assertion above proves "
            "nothing: the injected client is never consulted (DESIGN Decision 12 puts "
            "one say-out-loud call on the arrival path)."
        )


def test_digest_page_renders_the_six_r7_sections_in_order(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R7: the digest page carries exactly the six named sections, in the required order."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    offsets, rendered = _section_offsets(html)
    missing = [key for key in SECTION_KEYS if key not in offsets]
    assert not missing, (
        f"R7 sections with no locatable anchor: {missing}. Any heading-like element "
        "naming the section, or a sectioning container whose id / class / aria-label / "
        f"data-* names it, counts. Anchors found: {rendered}"
    )

    ordered = [offsets[key] for key in SECTION_KEYS]
    assert ordered == sorted(ordered), (
        "R7 sections are present but out of order. Offsets: "
        + ", ".join(f"{key}@{offsets[key]}" for key in SECTION_KEYS)
    )


def test_digest_meet_section_is_capped_at_three_rows_each_with_a_score_and_a_why(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R7: with four others present the Meet section shows the top three, scored and explained."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    meet = _section_span(html, "meet", "lately")
    named = [name for name in CAST if name.lower() in meet.lower()]
    assert len(named) == 3, (
        f"R7 caps Meet at three rows; four people were present and {len(named)} are "
        f"named in the section: {named}"
    )
    assert "Sil Vantorre" in named and "Jem Arrowood" in named, (
        f"the two people who actually share a rare hub with the arriving person are "
        f"not both in the top three: {named}"
    )
    assert re.search(r"\b100\b", meet), "Sil Vantorre's score of 100 is not rendered"
    assert re.search(r"\b67\b", meet), "Jem Arrowood's score of 67 is not rendered"
    assert "Foundry Seed" in meet, (
        "no Meet row names the shared thing; R7 requires a one-sentence why that names it"
    )


def test_meet_row_reasoning_exposes_hub_label_weight_recency_and_type_boost(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R10: each Meet row exposes its score components behind the data-reasoning affordance."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    meet = _section_span(html, "meet", "lately")
    assert "data-reasoning" in meet, (
        "R10 / TASKS T-8: no data-reasoning block in the Meet section, so the score "
        "components are not exposed anywhere"
    )
    low = meet.lower()
    assert "foundry seed 2019" in low, "the shared hub's label is not in the reasoning block"
    assert "weight" in low or "idf" in low, "the hub weight is not labelled"
    assert "recency" in low, "the recency multiplier is not labelled"
    assert "boost" in low or "type" in low, "the type boost is not labelled"
    # Numeric, not textual: any spelling carrying the value to two decimals passes.
    rendered_numbers = _numbers(meet)
    assert _shows_number(meet, IDF_INVESTOR_HUB), (
        f"the investor hub's IDF weight, ln(5 / (1 + 2)) = {IDF_INVESTOR_HUB:.4f}, is "
        f"not shown anywhere in the Meet section to within {NUMBER_TOLERANCE} (so at "
        f"least two decimals: 0.51, 0.511 and 0.5108256 all pass). Numbers rendered "
        f"there were {rendered_numbers}"
    )
    assert _shows_number(meet, TYPE_BOOST_INVESTOR), (
        f"the investor/board/company type boost of {TYPE_BOOST_INVESTOR} is not shown "
        f"anywhere in the Meet section ('1.5', '1.50' and '1.500' all pass). Numbers "
        f"rendered there were {rendered_numbers}"
    )


def test_digest_sources_are_numbered_and_carry_hrefs_and_retrieval_dates(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R7 / R9: 'Why we know this' is a numbered source list with URLs and retrieval dates."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    sources = _section_span(html, "why we know this")
    assert sources.lower().count("href=") >= 2, (
        f"the source list carries {sources.lower().count('href=')} link(s); every shown "
        "fact needs an openable URL"
    )
    assert "https://example.org/tradepress/2026/quarrystone-platform-roadmap" in sources, (
        "the source behind the arriving person's most recent displayed fact is missing "
        "from the numbered list"
    )
    assert "<ol" in sources.lower() or re.search(r"(^|[^0-9])1[.)\]]", sources), (
        "the source list is not numbered, so a citation marker cannot refer to it"
    )
    assert re.search(r"2026-02-20|20 Feb\w* 2026|Feb\w* 20,? 2026", sources, re.IGNORECASE), (
        "R7 requires a retrieval date beside every source; none is rendered"
    )


def test_meet_section_says_nobody_is_present_rather_than_padding(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R8: when nobody else is in the building the Meet section says so instead of padding."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        alone_id, alone_html = _staged_digest(client, [])
        assert alone_id
        alone_meet = _section_span(alone_html, "meet", "lately")
        padded = [name for name in CAST if name.lower() in alone_meet.lower()]
        assert not padded, f"nobody else was present, yet the Meet section names {padded}"
        assert len(_plain(alone_meet)) >= 8, (
            "the Meet section is empty rather than explicitly saying nobody else is here"
        )

        # Companion control: the same extraction DOES find a name when one is present,
        # so the emptiness above is the app's answer and not a broken selector.
        company_id = _arrive(client, "Sil Vantorre").get("digest_id")
        company_meet = _section_span(_digest_html(client, company_id), "meet", "lately")
        assert ARRIVING_NAME.lower() in company_meet.lower(), (
            "with one other person present the Meet section still names nobody"
        )


def test_digest_page_states_the_exclusion_policy(monkeypatch, tmp_path, frozen_fixtures):
    """R13: the digest page carries a paragraph naming everything the system never surfaces."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    low = html.lower()
    categories = {
        "home/property": r"address|propert",
        "family": r"family|relationship|children",
        "health": r"health|medical",
        "legal": r"litigation|court|criminal|divorce|legal",
        "wealth": r"wealth|net worth|compensation|salar",
        "political": r"political|donation",
    }
    missing = [label for label, pattern in categories.items() if not re.search(pattern, low)]
    assert not missing, f"the exclusion policy paragraph never names: {missing}"
    assert re.search(r"never|will not|won't|does not (show|display|surface)", low), (
        "R13 asks for a statement of what the system will NEVER surface; the page "
        "names the categories but makes no such statement"
    )


def test_withheld_facts_never_appear_on_any_host_facing_page(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R11 / R12: excluded and non-displayable facts reach no host-facing page."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        _digest_id, digest_html = _staged_digest(client, list(CAST))
        building = client.get("/building")
        assert building.status_code == 200, building.text[:400]
        index = client.get("/")
        assert index.status_code == 200, index.text[:400]

    # Positive control FIRST: an empty or errored page trivially satisfies a negative
    # assertion, so prove the digest actually rendered the material it is allowed to.
    for shown in DISPLAYED:
        assert shown.lower() in digest_html.lower(), (
            f"the digest does not show the displayable fact {shown!r}; the absence "
            "checks below would then prove nothing"
        )

    pages = {
        "/digest/{id}": digest_html,
        "/building": building.text,
        "/": index.text,
    }
    leaks = [
        f"{route} leaks {secret!r}"
        for route, page in pages.items()
        for secret in WITHHELD
        if secret.lower() in page.lower()
    ]
    assert not leaks, "R11/R12 violated: " + "; ".join(leaks)


def test_withheld_facts_stay_hidden_even_when_they_are_the_newest_material(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R11 / R12: the taste filter hides withheld facts, not the recency ordering."""
    corpus = _corpus_with_withheld_facts_dated_newest(frozen_fixtures, tmp_path / "redated")
    with _running(monkeypatch, corpus, llm=_RecordingLLM()) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    low = html.lower()
    assert DISPLAYED[1].lower() in low, (
        "the digest no longer shows the most recent DISPLAYABLE fact, so the absence "
        "checks below would prove nothing"
    )
    leaks = [secret for secret in WITHHELD if secret.lower() in low]
    assert not leaks, (
        "R11/R12: with the withheld facts re-dated to be the newest material available, "
        f"the digest shows {leaks}. They were only ever hidden by the date ordering, "
        "not by the exclusion flag, the confidence floor or the source-kind whitelist."
    )


def test_leave_removes_a_person_from_presence_and_from_the_next_digest(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R5 / R6: /leave clears presence, /building lists exactly who remains, digests follow."""
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        for name in ("Sil Vantorre", "Jem Arrowood", "Mira Hollowell"):
            _arrive(client, name)
        _arrive(client, ARRIVING_NAME)

        blob = _building_blob(client)
        for name in ("Sil Vantorre", "Jem Arrowood", "Mira Hollowell", ARRIVING_NAME):
            assert _listed(blob, name), f"GET /building does not list {name}, who is present"
        assert not _listed(blob, "Theo Baptiste"), (
            "GET /building lists Theo Baptiste, who never arrived"
        )

        response = client.post("/leave", json={"person_id": "sil-vantorre"})
        assert response.status_code == 200, response.text[:400]
        after = _building_blob(client)
        assert not _listed(after, "Sil Vantorre"), "the person who left is still listed as present"
        assert _listed(after, "Jem Arrowood"), "/leave removed somebody who did not leave"

        assert client.post("/leave", json={"person_id": ARRIVING_ID}).status_code == 200
        next_id = _arrive(client, ARRIVING_NAME).get("digest_id")
        next_meet = _section_span(_digest_html(client, next_id), "meet", "lately")

    assert "Sil Vantorre" not in next_meet, (
        "R5: the next digest still proposes Sil Vantorre, who has left the building"
    )
    assert "Jem Arrowood" in next_meet, (
        "the next digest proposes nobody at all, so the assertion above proves nothing"
    )


def test_debug_view_is_env_gated_and_shows_the_withheld_facts_with_reasons(
    monkeypatch, tmp_path, frozen_fixtures
):
    """R15: /debug is 404 without DEBUG_VIEWS and, with it, shows what was withheld and why."""
    corpus = _corpus(frozen_fixtures, tmp_path)
    with _running(monkeypatch, corpus, llm=_RecordingLLM()) as client:
        closed = client.get(f"/debug/{ARRIVING_ID}")
    assert closed.status_code == 404, (
        f"/debug/{ARRIVING_ID} answered {closed.status_code} with DEBUG_VIEWS unset; "
        "R15 makes it a switch that is off by default"
    )

    with _running(monkeypatch, corpus, llm=_RecordingLLM(), debug_views=True) as client:
        opened = client.get(f"/debug/{ARRIVING_ID}")
    assert opened.status_code == 200, (
        f"/debug/{ARRIVING_ID} answered {opened.status_code} with DEBUG_VIEWS=1: "
        f"{opened.text[:400]}"
    )

    html = opened.text
    low = html.lower()
    # This is the ONE place the withheld material is allowed to appear, and the demo
    # depends on it: "we found it and we withheld it" is unshowable otherwise.
    for secret in ("their spouse Delia Moreno-Vance", "1442 Quarrystone Lane"):
        assert secret.lower() in low, (
            f"/debug does not show the withheld fact {secret!r}, so the operator "
            "cannot see where the line was drawn"
        )
    assert "family" in low, "/debug shows the withheld fact but not its exclusion reason"
    assert "home_or_property" in low or "home or property" in low, (
        "/debug shows the withheld address fact but not its exclusion reason"
    )
    assert "e4ba96415536ce5f" in low, (
        "R15 asks for the rejected candidate documents; the resolver's one rejected "
        "doc for this person is not shown"
    )


def test_index_lists_the_roster_and_its_arrive_leave_forms_actually_work(
    monkeypatch, tmp_path, frozen_fixtures
):
    """T-8 acceptance 6: GET / is a roster with WORKING plain-HTML arrive/leave forms.

    Previously `GET /` was graded once, incidentally, for status 200 and the absence of
    four strings - which a completely blank page satisfies. The demo driver is the one
    surface a human actually touches, so it is exercised here the way a browser would:
    parse the page's own forms, post them, and read the presence set back.
    """
    with _running(
        monkeypatch, _corpus(frozen_fixtures, tmp_path), llm=_RecordingLLM()
    ) as client:
        index = client.get("/")
        assert index.status_code == 200, index.text[:400]
        page = index.text

        roster = [ARRIVING_NAME, *CAST]
        low = page.lower()
        unlisted = [n for n in roster if _person_id(n) not in low and n.lower() not in low]
        assert not unlisted, (
            f"GET / does not list {unlisted}; T-8 acceptance 6 asks the demo driver to "
            "list the roster, and a driver that omits people cannot start the demo"
        )

        # Arrive through the page's own form, not through the JSON API.
        _submit(client, _pick_form(page, "arrive", ARRIVING_NAME), "arrive")
        assert _listed(_building_blob(client), ARRIVING_NAME), (
            "the demo driver's arrive form submitted cleanly but the person is not in "
            "the presence set afterwards, so the form is decorative"
        )

        # And back out again. The page is re-read first: a roster row is entitled to
        # offer "leave" only once the person is actually in the building.
        _submit(client, _pick_form(client.get("/").text, "leave", ARRIVING_NAME), "leave")
        assert not _listed(_building_blob(client), ARRIVING_NAME), (
            "the demo driver's leave form submitted cleanly but the person is still "
            "listed as present"
        )
