"""FROZEN ACCEPTANCE - ticket T-8: the web app (presence, digest render, debug view).

Graded requirements: R3, R4, R5, R6, R7, R8, R10, R13, R15, S2, C4.

Everything here is exercised through the public HTTP surface pinned by DESIGN's route
table (POST /arrive, POST /leave, GET /building, GET /digest/{id},
GET /debug/{person_id}, GET /) against the ORCHESTRATOR-OWNED fixture corpus in
`.swarm-loop/acceptance/fixtures/dossiers/`. No test reads `tests/fixtures/`: those
files are inside T-8's own reads-scope and a gradee that can rewrite the answer key is
not being graded.

THE APPLICATION SEAM - read this before implementing T-8.
--------------------------------------------------------
Neither SPEC nor DESIGN documents how the app is pointed at a dossier directory, and
T-0's `Settings` ships no dossier-directory field (its module docstring forbids
downstream tickets from widening it, so this needs an escalation, not a quiet edit).
TASKS T-8 nonetheless requires the app to be pointed at a fixture dossier directory
under test, so this harness pins the two seams that the config surface's own
conventions imply, and accepts either:

  1. env var `DOSSIER_DIR` (the `Settings`-style env name for a `dossier_dir` field),
     set before the app is constructed; and
  2. an application factory in the module DESIGN names, `arrival.web.app`, called as
     `create_app(dossier_dir=Path, llm=LLMClient | None)`.

`DEBUG_VIEWS` stays an environment variable because SPEC R15 names it as one.

The `llm=` keyword is a real requirement, not a convenience: R4 says an off-roster
arrival must not trigger live research, and "no live research" is only observable by
handing the app an LLM client whose calls can be counted. The frozen suite ships its
own recording stub below rather than importing `tests/doubles.py`, which is worker-
writable.

Rendering assumptions, stated so they are arguable rather than hidden:
  * each R7 section is introduced by an HTML heading element (<h1>..<h6>) carrying its
    name - that is how "exactly these sections, in order" is checkable at all;
  * TASKS T-8 acceptance 3 pins the attribute `data-reasoning` as the reasoning
    affordance R10 asks for, so the reasoning test looks for it by name.
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

_LLM_SEAM_MESSAGE = (
    "R4 requires proving that an off-roster arrival triggers NO live research, which "
    "is only observable when the app accepts an injected LLM client. Expose the seam "
    "this harness pins: arrival.web.app.create_app(dossier_dir=..., llm=...)."
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


def _make_app(monkeypatch, dossier_dir, *, llm=None, debug_views=False, require_llm_seam=False):
    """Construct the app pointed at `dossier_dir`. See THE APPLICATION SEAM above."""
    import importlib
    import inspect

    dossier_dir = Path(dossier_dir)
    monkeypatch.setenv("DOSSIER_DIR", str(dossier_dir))
    monkeypatch.setenv("DEBUG_VIEWS", "1" if debug_views else "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_settings_cache()

    module = importlib.import_module("arrival.web.app")

    factory = None
    for candidate in ("create_app", "make_app", "build_app"):
        obj = getattr(module, candidate, None)
        if callable(obj):
            factory = obj
            break

    if factory is None:
        instance = getattr(module, "app", None)
        if instance is None:
            pytest.fail(
                "arrival.web.app exposes neither an application factory (create_app) "
                "nor a module-level `app`; there is no way to boot the service."
            )
        if require_llm_seam:
            pytest.fail(_LLM_SEAM_MESSAGE)
        return instance

    params = inspect.signature(factory).parameters
    var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
    kwargs = {}
    if "dossier_dir" in params or var_kw:
        kwargs["dossier_dir"] = dossier_dir
    if llm is not None and ("llm" in params or var_kw):
        kwargs["llm"] = llm
    if require_llm_seam and "llm" not in kwargs:
        pytest.fail(_LLM_SEAM_MESSAGE)
    return factory(**kwargs)


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


_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[#0-9a-zA-Z]+;")


def _plain(fragment):
    """Tags and entities stripped, lowercased, punctuation collapsed to single spaces."""
    text = _TAG_RE.sub(" ", fragment)
    text = _ENTITY_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def _headings(html):
    """(start_offset, end_offset, normalised text) for every heading, in document order."""
    return [(m.start(), m.end(), _plain(m.group(1))) for m in _HEADING_RE.finditer(html)]


def _heading_offsets(html):
    """First document offset at which each R7 section heading appears, by key."""
    heads = _headings(html)
    offsets = {}
    for key in SECTION_KEYS:
        for start, _end, text in heads:
            if key in text:
                offsets[key] = start
                break
    return offsets, [h[2] for h in heads]


def _section_span(html, key, next_key=None):
    """The HTML between the heading containing `key` and the one containing `next_key`."""
    heads = _headings(html)
    start_index = next((i for i, h in enumerate(heads) if key in h[2]), None)
    if start_index is None:
        pytest.fail(
            f"no <h1>-<h6> heading contains {key!r}; headings rendered were "
            f"{[h[2] for h in heads]}"
        )
    start = heads[start_index][1]
    following = heads[start_index + 1 :]
    if not following:
        return html[start:]
    if next_key is None:
        return html[start : following[0][0]]
    # Stop at the named next section; a nested heading inside a row must not truncate
    # the span. Fall back to the next heading of any kind if the named one is absent.
    stop = next((h[0] for h in following if next_key in h[2]), following[0][0])
    return html[start:stop]


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
    report = "".join(traceback.format_exception(excinfo.value))
    assert "broken-dossier.json" in report, (
        "boot aborted, but the error does not name the offending file; the operator "
        f"cannot find it. Error was:\n{report[-2000:]}"
    )


def test_arrive_returns_a_digest_id_within_three_seconds_and_records_presence(
    monkeypatch, frozen_fixtures
):
    """S2 / R3: with three present, a fourth arrival returns a digest id in under 3 s."""
    llm = _RecordingLLM()
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=llm) as client:
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
    monkeypatch, frozen_fixtures
):
    """R4: an off-roster arrival is refused and does NOT trigger live research."""
    llm = _RecordingLLM()
    with _running(
        monkeypatch, frozen_fixtures / "dossiers", llm=llm, require_llm_seam=True
    ) as client:
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


def test_digest_page_renders_the_six_r7_sections_in_order(monkeypatch, frozen_fixtures):
    """R7: the digest page carries exactly the six named sections, in the required order."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
        _digest_id, html = _staged_digest(client, list(CAST))

    offsets, rendered = _heading_offsets(html)
    missing = [key for key in SECTION_KEYS if key not in offsets]
    assert not missing, f"R7 sections with no heading: {missing}; headings were {rendered}"

    ordered = [offsets[key] for key in SECTION_KEYS]
    assert ordered == sorted(ordered), (
        "R7 sections are present but out of order. Offsets: "
        + ", ".join(f"{key}@{offsets[key]}" for key in SECTION_KEYS)
    )


def test_digest_meet_section_is_capped_at_three_rows_each_with_a_score_and_a_why(
    monkeypatch, frozen_fixtures
):
    """R7: with four others present the Meet section shows the top three, scored and explained."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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
    monkeypatch, frozen_fixtures
):
    """R10: each Meet row exposes its score components behind the data-reasoning affordance."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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
    assert re.search(r"0\.51", meet), (
        "the investor hub's IDF weight (ln(5/3) = 0.5108) is not shown to at least "
        "two decimal places"
    )
    assert re.search(r"\b1\.5\b", meet), "the investor/board/company type boost of 1.5 is not shown"


def test_digest_sources_are_numbered_and_carry_hrefs_and_retrieval_dates(
    monkeypatch, frozen_fixtures
):
    """R7 / R9: 'Why we know this' is a numbered source list with URLs and retrieval dates."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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


def test_meet_section_says_nobody_is_present_rather_than_padding(monkeypatch, frozen_fixtures):
    """R8: when nobody else is in the building the Meet section says so instead of padding."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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


def test_digest_page_states_the_exclusion_policy(monkeypatch, frozen_fixtures):
    """R13: the digest page carries a paragraph naming everything the system never surfaces."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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


def test_withheld_facts_never_appear_on_any_host_facing_page(monkeypatch, frozen_fixtures):
    """R11 / R12: excluded and non-displayable facts reach no host-facing page."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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
    monkeypatch, frozen_fixtures
):
    """R5 / R6: /leave clears presence, /building lists exactly who remains, digests follow."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
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
    monkeypatch, frozen_fixtures
):
    """R15: /debug is 404 without DEBUG_VIEWS and, with it, shows what was withheld and why."""
    with _running(monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM()) as client:
        closed = client.get(f"/debug/{ARRIVING_ID}")
    assert closed.status_code == 404, (
        f"/debug/{ARRIVING_ID} answered {closed.status_code} with DEBUG_VIEWS unset; "
        "R15 makes it a switch that is off by default"
    )

    with _running(
        monkeypatch, frozen_fixtures / "dossiers", llm=_RecordingLLM(), debug_views=True
    ) as client:
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
