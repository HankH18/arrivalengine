"""FROZEN acceptance tests for ticket T-6 — the research pipeline and the build CLI.

Graded requirements: R1 (one dossier per roster person, persisted, reported), R2 (an
unresolved person stores no facts and is not researched further), S1 (the build runs
end to end offline against doubles), and DESIGN Decisions 2 and 8 (pre-compute offline;
a failed source is reported, never fatal).

Rules this module obeys (see the frozen harness brief):

* Product imports are LAZY — inside the test bodies. At cycle 0 `arrival` does not exist;
  a module-scope import would turn an unbuilt feature into a COLLECTION error and take the
  whole file out of both the numerator and the denominator.
* The connector and LLM doubles are written HERE. `tests/doubles.py` is inside a graded
  ticket's scope, and a gradee that can write the double can write the answer.
* Every byte this module writes goes to pytest's `tmp_path`. Nothing touches the repo.
* No network and no subprocess: the CLI is exercised in-process through the injectable
  `main(argv, *, connectors=None, llm=None)` signature DESIGN pins for `__main__.py`.
* Async is driven with `asyncio.run(...)`; the frozen suite runs with `-o addopts=` and its
  own rootdir, so the project's `asyncio_mode = auto` is deliberately not relied upon.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import re
import typing

import pytest
import yaml

pytestmark = pytest.mark.t6

try:  # 3.10+ writes `X | None` as types.UnionType, 3.9 as typing.Union
    from types import UnionType as _UnionType
except ImportError:  # pragma: no cover - Python < 3.10
    _UnionType = None


# Two synthetic people, invented for this module and used nowhere else in the corpus.
ROSTER = [
    ("nils-havergal", "Nils Havergal", ["co-founder, Quarrystone Labs", "Austin"]),
    ("petra-solvang", "Petra Solvang", ["platform lead, Quarrystone Labs", "Austin"]),
]
# A person whose details name no company domain, no handle and no institution, so the
# resolver has no strong key available and must decide on document verdicts alone.
VAGUE_PERSON = ("wexler-brune", "Wexler Brune", ["photographer or software engineer, unclear"])

_SENTINEL_RE = re.compile(r"DOCSENTINEL-([0-9a-f]{16})")

_MISSING = object()

# Non-required fields worth filling anyway: an extractor or resolver schema may declare
# them with a default, and leaving them empty would starve the stage under test.
_ALWAYS_FILL = {
    "doc_id", "url", "quote", "evidence", "disambiguator", "text", "confidence",
    "match", "label", "hub_id", "category", "name", "facts", "hubs", "verdicts",
}


# ---------------------------------------------------------------------------
# synthetic corpus
# ---------------------------------------------------------------------------


def _doc_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _employer_line(name):
    return f"{name} leads the platform team at Quarrystone Labs"


def _city_line(name):
    return f"{name} has lived in Austin since 2014"


def _raw_doc(kind, url, name):
    """A RawDoc whose text contains both resolver anchors and a machine-readable sentinel.

    The sentinel is how the LLM double learns WHICH document a prompt is about without
    knowing anything about the prompt format the pipeline chooses, and how these tests
    count how many documents the pipeline actually put in front of the model.
    """
    from arrival.contracts import RawDoc

    did = _doc_id(url)
    text = (
        f"{_employer_line(name)}. {_city_line(name)}. "
        f"DOCSENTINEL-{did} marks this record for the offline acceptance harness. "
        "Quarrystone Labs has published its build tooling under an open licence since "
        "2019, and the platform team writes a short public note whenever the command "
        "line tool changes in a way that anyone outside the company would notice."
    )
    return RawDoc(
        doc_id=did,
        source_kind=kind,
        url=url,
        title=f"{name} - {kind} record",
        text=text,
        published_at=dt.date(2026, 1, 5),
        fetched_at=dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.timezone.utc),
    )


def _corpus(people, kinds, per_kind):
    """Build (docs_by_kind_and_person, registry) for the given people.

    `docs_by_kind_and_person[kind][person_id]` is the list a connector of that kind returns.
    `registry[doc_id]` is what the LLM double needs to answer about that document.
    """
    docs = {kind: {} for kind in kinds}
    registry = {}
    for person_id, name, _details in people:
        for kind in kinds:
            batch = []
            for i in range(per_kind):
                url = f"https://example.com/{person_id}/{kind}/{i}"
                doc = _raw_doc(kind, url, name)
                batch.append(doc)
                # Alternate the disambiguator so ">= 2 yes verdicts citing DIFFERENT
                # disambiguators" (DESIGN Decision 4, branch b) is reachable.
                disambiguator = "employer" if i % 2 == 0 else "city"
                registry[doc.doc_id] = {
                    "doc_id": doc.doc_id,
                    "url": url,
                    "name": name,
                    "kind": kind,
                    "disambiguator": disambiguator,
                    "evidence": (
                        _employer_line(name) if disambiguator == "employer" else _city_line(name)
                    ),
                }
            docs[kind][person_id] = batch
    return docs, registry


def _write_roster(tmp_path, people):
    path = tmp_path / "roster_synthetic.yaml"
    path.write_text(
        yaml.safe_dump(
            {"people": [{"name": n, "details": list(d)} for _pid, n, d in people]},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class ConnectorStub:
    """A `contracts.Connector`: a `kind` plus `async search(person, budget)`.

    It deliberately IGNORES `budget` and returns everything it holds, so that any capping
    the tests observe is capping the pipeline performed rather than capping the double
    was polite enough to do for it.
    """

    def __init__(self, kind, docs_by_person, *, raises=False):
        self.kind = kind
        self.docs_by_person = dict(docs_by_person)
        self.raises = raises
        self.budgets = []
        self.people = []

    async def search(self, person, budget):
        self.budgets.append(budget)
        self.people.append(person.person_id)
        if self.raises:
            raise RuntimeError("stub connector failure: the build must survive this")
        return list(self.docs_by_person.get(person.person_id, []))

    def doc_ids(self):
        out = set()
        for batch in self.docs_by_person.values():
            out.update(d.doc_id for d in batch)
        return out


def _is_union(origin):
    return origin is typing.Union or (_UnionType is not None and origin is _UnionType)


def _is_model_list(ann):
    from pydantic import BaseModel

    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin in (list, set, tuple, frozenset) and args:
        inner = args[0]
        return isinstance(inner, type) and issubclass(inner, BaseModel)
    return False


def _name_default(field_name):
    n = field_name.lower()
    if "confidence" in n or "score" in n:
        return 0.9
    if "recency" in n:
        return 1.0
    if n == "match" or n.endswith("_match"):
        return "yes"
    if "category" in n:
        return "current_work"
    if "source_kind" in n or n == "kind":
        return "self_page"
    if n == "type" or n.endswith("_type"):
        return "company"
    if "hub_id" in n:
        return "company:quarrystone-labs"
    if "label" in n or "title" in n:
        return "Quarrystone Labs"
    return _MISSING


def _ctx_default(field_name, ctx):
    if not ctx:
        return _MISSING
    n = field_name.lower()
    if "doc_id" in n:
        return ctx["doc_id"]
    if "url" in n:
        return ctx["url"]
    if "disambiguator" in n:
        return ctx["disambiguator"]
    if any(k in n for k in ("quote", "evidence", "span", "snippet", "supporting", "excerpt")):
        return ctx["evidence"]
    if n in ("text", "fact", "fact_text", "statement", "sentence", "claim", "summary"):
        return ctx["evidence"]
    if n == "name" or n.endswith("_name"):
        return ctx["name"]
    return _MISSING


def _scalar(field_name, ctx, overrides):
    if field_name in overrides:
        return overrides[field_name]
    value = _ctx_default(field_name, ctx)
    if value is not _MISSING:
        return value
    return _name_default(field_name)


def _synth_value(ann, field_name, ctx, overrides, doc_ctxs):
    from pydantic import BaseModel

    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    if origin is typing.Literal:
        want = _scalar(field_name, ctx, overrides)
        return want if want in args else args[0]
    if field_name in overrides:
        return overrides[field_name]
    if _is_union(origin):
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if not non_none:
            return None
        return _synth_value(non_none[0], field_name, ctx, overrides, doc_ctxs)
    if origin in (list, set, tuple, frozenset):
        inner = args[0] if args else str
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            # One element per document the prompt is about, so a batched resolver or
            # extractor call gets one answer per document rather than a single answer.
            contexts = doc_ctxs if doc_ctxs else [ctx]
            return [_synth(inner, c, overrides, None) for c in contexts]
        item = _synth_value(inner, field_name, ctx, overrides, doc_ctxs)
        return [] if item is _MISSING else [item]
    if origin is dict:
        return {}
    if isinstance(ann, type):
        if issubclass(ann, BaseModel):
            return _synth(ann, ctx, overrides, doc_ctxs)
        if issubclass(ann, bool):
            return False
        if issubclass(ann, str):
            value = _scalar(field_name, ctx, overrides)
            return "Quarrystone Labs" if value is _MISSING else value
        if issubclass(ann, float):
            value = _scalar(field_name, ctx, overrides)
            return 0.9 if value is _MISSING else float(value)
        if issubclass(ann, int):
            value = _scalar(field_name, ctx, overrides)
            return 1 if value is _MISSING else int(value)
        if issubclass(ann, dt.datetime):
            return dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.timezone.utc)
        if issubclass(ann, dt.date):
            return dt.date(2026, 1, 5)
    return _MISSING


def _synth(model_cls, ctx, overrides, doc_ctxs):
    kwargs = {}
    for field_name, field in model_cls.model_fields.items():
        if not field.is_required():
            if field_name.lower() not in _ALWAYS_FILL and not _is_model_list(field.annotation):
                continue
        value = _synth_value(field.annotation, field_name, ctx, overrides, doc_ctxs)
        if value is not _MISSING:
            kwargs[field_name] = value
    return model_cls(**kwargs)


class LLMStub:
    """A `contracts.LLMClient` double that answers about whatever documents the prompt names.

    It cannot know T-2's, T-3's or T-4's internal response schemas, so it fills the schema
    it is handed generically: string fields that read like a quote or a span get a sentence
    that really is in the document (so the T-3 citation check and the T-2 evidence check
    pass), document ids and urls come from the sentinel in the prompt, and `Literal` fields
    take the value asked for when it is legal and the first member otherwise.

    `overrides` forces a field by name — `{"match": "no"}` scripts every verdict negative.
    """

    def __init__(self, registry, overrides=None):
        self.registry = dict(registry)
        self.overrides = dict(overrides or {})
        self.calls = []

    async def structured(self, *, system, user, schema, max_tokens=2000, cache_prefix=True):
        prompt = user or ""
        self.calls.append(
            {"schema": getattr(schema, "__name__", str(schema)), "system": system, "user": prompt}
        )
        seen = []
        for doc_id in _SENTINEL_RE.findall(prompt):
            if doc_id in self.registry and doc_id not in seen:
                seen.append(doc_id)
        contexts = [self.registry[d] for d in seen]
        return _synth(schema, contexts[0] if contexts else None, self.overrides, contexts or None)

    def doc_ids_seen(self):
        """Every document that actually reached the model, read off the prompts."""
        seen = set()
        for call in self.calls:
            seen.update(_SENTINEL_RE.findall(call["user"]))
        return seen


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _budget(**kw):
    from arrival.contracts import Budget

    return Budget(**kw)


def _person_ref(person_id, name, details):
    from arrival.contracts import PersonRef

    return PersonRef(person_id=person_id, name=name, details=list(details))


def _load_dossier(path):
    from arrival.contracts import Dossier

    return Dossier.model_validate_json(path.read_text(encoding="utf-8"))


def _run_build_all(roster, out_dir, connectors, llm, budget, force=False, only=None):
    from arrival.research import build_all

    kwargs = dict(connectors=connectors, llm=llm, budget=budget, force=force)
    if only is not None:
        kwargs["only"] = only
    return asyncio.run(build_all(roster, out_dir, **kwargs))


def _run_build_dossier(person, connectors, llm, budget):
    from arrival.research import build_dossier

    return asyncio.run(build_dossier(person, connectors, llm, budget))


def _standard_setup(tmp_path, kinds=("self_page", "search"), per_kind=2):
    docs, registry = _corpus(ROSTER, kinds, per_kind)
    connectors = [ConnectorStub(kind, docs[kind]) for kind in kinds]
    return _write_roster(tmp_path, ROSTER), connectors, LLMStub(registry)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_build_all_writes_one_validated_dossier_per_person_and_a_report(tmp_path):
    """S1 + R1: the build produces one validated dossier per roster person plus a report."""
    from arrival.contracts import BuildReport

    roster, connectors, llm = _standard_setup(tmp_path)
    out_dir = tmp_path / "data" / "dossiers"

    report = _run_build_all(roster, out_dir, connectors, llm, _budget())

    assert isinstance(report, BuildReport), f"build_all returned {type(report)!r}"
    assert len(report.people) == 2, f"expected one report row per roster person: {report.people}"
    reported = {row["person_id"] for row in report.people}
    assert reported == {"nils-havergal", "petra-solvang"}, reported

    for row in report.people:
        for key in ("person_id", "status", "confidence", "facts_kept", "facts_excluded",
                    "hubs", "zero_result_sources"):
            assert key in row, f"BuildReport row is missing {key!r}: {row}"
        assert row["status"] in ("resolved", "unresolved")

    for person_id, _name, _details in ROSTER:
        path = out_dir / f"{person_id}.json"
        found = sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else []
        assert path.exists(), f"no dossier written for {person_id}; out_dir holds {found}"
        dossier = _load_dossier(path)
        assert dossier.person.person_id == person_id
        assert dossier.schema_version == 1


def test_build_all_writes_every_accepted_rawdoc_to_the_sibling_docs_dir(tmp_path):
    """T-6 acceptance 3: accepted documents land in out_dir/../docs so citations replay offline."""
    from arrival.contracts import RawDoc

    roster, connectors, llm = _standard_setup(tmp_path)
    out_dir = tmp_path / "data" / "dossiers"

    _run_build_all(roster, out_dir, connectors, llm, _budget())

    accepted = set()
    for person_id, _name, _details in ROSTER:
        accepted.update(_load_dossier(out_dir / f"{person_id}.json").resolution.accepted_doc_ids)
    # Positive control: without at least one accepted document the loop below is vacuous
    # and a pipeline that wrote no documents at all would pass.
    assert accepted, (
        "no document was accepted for either synthetic person, so 'every accepted RawDoc "
        "is written' cannot be measured"
    )

    docs_dir = out_dir.parent / "docs"
    assert docs_dir.is_dir(), f"no sibling docs directory at {docs_dir}"
    for doc_id in sorted(accepted):
        path = docs_dir / f"{doc_id}.json"
        assert path.exists(), f"accepted document {doc_id} was cited but not committed"
        doc = RawDoc.model_validate_json(path.read_text(encoding="utf-8"))
        assert doc.doc_id == doc_id
        assert doc.text.strip(), f"{doc_id} was written with empty text"


def test_unresolved_person_stores_no_facts_and_does_not_run_extraction(tmp_path):
    """R2: an unresolved person is not researched further — not merely stored empty.

    'We kept no facts' and 'we never went looking' are different guarantees. The second is
    measured by running the identical inputs twice, once scripted to resolve and once
    scripted not to, and requiring the unresolved run to make strictly fewer model calls.
    """
    person_id, name, details = VAGUE_PERSON
    docs, registry = _corpus([(person_id, name, details)], ("search",), 3)
    person = _person_ref(person_id, name, details)
    budget = _budget()

    yes_connectors = [ConnectorStub("search", docs["search"])]
    yes_llm = LLMStub(registry)
    resolved = _run_build_dossier(person, yes_connectors, yes_llm, budget)

    no_connectors = [ConnectorStub("search", docs["search"])]
    no_llm = LLMStub(registry, overrides={"match": "no", "confidence": 0.95})
    unresolved = _run_build_dossier(person, no_connectors, no_llm, budget)

    assert unresolved.resolution.status == "unresolved"
    assert unresolved.facts == [], f"facts stored for an unresolved person: {unresolved.facts}"
    assert unresolved.hubs == [], f"hubs stored for an unresolved person: {unresolved.hubs}"
    assert unresolved.resolution.accepted_doc_ids == []

    # Positive control: the resolvable run really did go on to research, so the comparison
    # below is between "researched" and "did not research", not between two empty runs.
    assert resolved.resolution.status == "resolved", (
        "control run did not resolve, so the call-count comparison proves nothing"
    )
    assert len(no_llm.calls) < len(yes_llm.calls), (
        f"the unresolved run made {len(no_llm.calls)} model calls and the resolved run "
        f"{len(yes_llm.calls)}: extraction was not skipped"
    )


def test_max_llm_calls_caps_the_build_without_raising(tmp_path):
    """T-6 acceptance 4: at the budget cap the pipeline keeps what it has and stops."""
    from arrival.contracts import Dossier

    person_id, name, details = ROSTER[0]
    docs, registry = _corpus([ROSTER[0]], ("self_page", "search"), 3)
    person = _person_ref(person_id, name, details)

    generous_llm = LLMStub(registry)
    _run_build_dossier(
        person,
        [ConnectorStub(k, docs[k]) for k in ("self_page", "search")],
        generous_llm,
        _budget(docs_per_connector=8, max_docs_total=40, max_llm_calls=80),
    )

    capped_llm = LLMStub(registry)
    capped = _run_build_dossier(
        person,
        [ConnectorStub(k, docs[k]) for k in ("self_page", "search")],
        capped_llm,
        _budget(docs_per_connector=8, max_docs_total=40, max_llm_calls=3),
    )

    # Positive control: an uncapped run of these same inputs needs more than three calls,
    # so the cap below is actually binding rather than never reached.
    assert len(generous_llm.calls) > 3, (
        f"the uncapped run only made {len(generous_llm.calls)} calls, so a cap of 3 "
        "measures nothing"
    )
    assert len(capped_llm.calls) <= 3, (
        f"budget.max_llm_calls was 3 and the pipeline made {len(capped_llm.calls)} calls"
    )
    assert isinstance(capped, Dossier), "hitting the budget cap must yield a dossier, not a raise"


def test_docs_per_connector_and_max_docs_total_are_both_respected(tmp_path):
    """T-6 acceptance 1: both document budgets bite when connectors return more."""
    person_id, name, details = ROSTER[0]
    kinds = ("self_page", "search", "github")
    docs, registry = _corpus([ROSTER[0]], kinds, 10)
    person = _person_ref(person_id, name, details)

    tight_connectors = [ConnectorStub(k, docs[k]) for k in kinds]
    tight_llm = LLMStub(registry)
    _run_build_dossier(
        person,
        tight_connectors,
        tight_llm,
        _budget(docs_per_connector=2, max_docs_total=4, max_llm_calls=200),
    )
    tight_seen = tight_llm.doc_ids_seen()

    loose_connectors = [ConnectorStub(k, docs[k]) for k in kinds]
    loose_llm = LLMStub(registry)
    _run_build_dossier(
        person,
        loose_connectors,
        loose_llm,
        _budget(docs_per_connector=10, max_docs_total=40, max_llm_calls=400),
    )
    loose_seen = loose_llm.doc_ids_seen()

    # Positive control: with the budgets opened up the same connectors feed many more
    # documents through, so the small numbers below are the budget and not the fixture.
    assert len(loose_seen) > 4, (
        f"only {len(loose_seen)} documents reached the model even with an open budget, "
        "so the tight-budget assertions measure nothing"
    )

    assert len(tight_seen) <= 4, (
        f"max_docs_total was 4 but {len(tight_seen)} documents reached the model: "
        f"{sorted(tight_seen)}"
    )
    for connector in tight_connectors:
        from_this_source = tight_seen & connector.doc_ids()
        assert len(from_this_source) <= 2, (
            f"docs_per_connector was 2 but {len(from_this_source)} documents came from "
            f"the {connector.kind} connector"
        )
        for asked in connector.budgets:
            assert asked <= 2, (
                f"the {connector.kind} connector was asked for {asked} documents with "
                "docs_per_connector set to 2"
            )


def test_a_raising_connector_is_reported_as_zero_result_not_a_build_failure(tmp_path):
    """DESIGN Decision 8: a failed source is named in the report; the build still finishes."""
    docs, registry = _corpus(ROSTER, ("self_page", "github"), 2)
    connectors = [
        ConnectorStub("self_page", docs["self_page"]),
        ConnectorStub("github", docs["github"], raises=True),
    ]
    roster = _write_roster(tmp_path, ROSTER)
    out_dir = tmp_path / "data" / "dossiers"

    report = _run_build_all(roster, out_dir, connectors, LLMStub(registry), _budget())

    for person_id, _name, _details in ROSTER:
        path = out_dir / f"{person_id}.json"
        assert path.exists(), f"a raising connector aborted the build for {person_id}"
        _load_dossier(path)

    for row in report.people:
        zero = list(row["zero_result_sources"])
        assert "github" in zero, (
            f"the connector that raised is not reported as zero-result for "
            f"{row['person_id']}: {zero}"
        )
        # Positive control: the working source must NOT be listed, or "zero_result_sources"
        # would be a constant rather than a diagnosis.
        assert "self_page" not in zero, (
            f"a source that returned documents is reported as zero-result: {zero}"
        )


def test_cli_build_returns_zero_and_writes_dossiers_in_process(tmp_path):
    """T-6 acceptance 5 + S1: `main(['build', ...])` runs offline with injected doubles."""
    from arrival.__main__ import main

    roster, connectors, llm = _standard_setup(tmp_path)
    out_dir = tmp_path / "cli-out"

    rc = main(
        ["build", "--roster", str(roster), "--out", str(out_dir)],
        connectors=connectors,
        llm=llm,
    )

    assert rc == 0, f"`build` returned {rc}"
    for person_id, _name, _details in ROSTER:
        path = out_dir / f"{person_id}.json"
        assert path.exists(), f"the CLI returned 0 without writing {path}"
        _load_dossier(path)


@pytest.mark.guard
def test_cli_rejects_an_unknown_command_with_exit_code_two():
    """T-0 CLI contract that T-6 must not break when it fills in `build`.

    Marked `guard`: it goes green the moment T-0 lands, so it is excluded from T-6's
    scored count and cannot hand this ticket a free point.
    """
    from arrival.__main__ import main

    assert main(["definitely-not-a-command"]) == 2


def test_build_all_skips_an_existing_dossier_unless_forced(tmp_path):
    """T-6 acceptance 3: a person with a dossier on disk is skipped until `force` is set."""
    docs, registry = _corpus(ROSTER, ("self_page", "search"), 2)
    roster = _write_roster(tmp_path, ROSTER)
    out_dir = tmp_path / "data" / "dossiers"

    first_llm = LLMStub(registry)
    _run_build_all(
        roster, out_dir,
        [ConnectorStub(k, docs[k]) for k in ("self_page", "search")],
        first_llm, _budget(),
    )
    written = {
        person_id: (out_dir / f"{person_id}.json").read_bytes()
        for person_id, _name, _details in ROSTER
    }
    assert first_llm.calls, "the first build did no work, so 'skipped' cannot be distinguished"

    skip_llm = LLMStub(registry)
    _run_build_all(
        roster, out_dir,
        [ConnectorStub(k, docs[k]) for k in ("self_page", "search")],
        skip_llm, _budget(), force=False,
    )
    assert skip_llm.calls == [], (
        f"an existing dossier was rebuilt without --force ({len(skip_llm.calls)} model calls)"
    )
    for person_id, _name, _details in ROSTER:
        assert (out_dir / f"{person_id}.json").read_bytes() == written[person_id], (
            f"{person_id}.json was rewritten by a run that should have skipped it"
        )

    force_llm = LLMStub(registry)
    _run_build_all(
        roster, out_dir,
        [ConnectorStub(k, docs[k]) for k in ("self_page", "search")],
        force_llm, _budget(), force=True,
    )
    assert force_llm.calls, "--force did not rebuild an existing dossier"


def test_cli_only_builds_the_named_person(tmp_path):
    """T-6 acceptance 5: `--only <person_id>` restricts the build to that one person."""
    from arrival.__main__ import main

    roster, connectors, llm = _standard_setup(tmp_path)
    out_dir = tmp_path / "only-out"

    rc = main(
        ["build", "--roster", str(roster), "--out", str(out_dir), "--only", "petra-solvang"],
        connectors=connectors,
        llm=llm,
    )

    assert rc == 0, f"`build --only` returned {rc}"
    assert (out_dir / "petra-solvang.json").exists(), "--only did not build the named person"
    assert not (out_dir / "nils-havergal.json").exists(), (
        "--only built a person it was not asked for"
    )
