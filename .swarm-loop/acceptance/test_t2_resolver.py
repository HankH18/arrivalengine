"""FROZEN acceptance tests for ticket T-2 — entity resolution and the production LLM client.

Graded requirements: SPEC R2 (refuse to guess), SPEC S4 (same-name decoy), SPEC C6/C7,
DESIGN Decision 4 (strong key OR two independent attributes; negative evidence hard-rejects;
confidences are never averaged) and DESIGN Decision 5 (evidence must be a verbatim span).

Everything is driven from the orchestrator-owned corpus in `fixtures/resolve_cases/`, which
no worker may write. Two tests build *variants* of a frozen case in memory — each variant
changes exactly one dimension of the committed case so the assertion isolates one rule.

Product imports are deliberately inside function bodies: at cycle 0 `arrival` does not
exist, and a module-scope import would turn an unbuilt feature into a collection error,
which silently removes these tests from both sides of the pass-rate fraction.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import pytest

pytestmark = pytest.mark.t2

_ACCEPTANCE_DIR = Path(__file__).resolve().parent
_RESOLVE_CASE_DIR = _ACCEPTANCE_DIR / "fixtures" / "resolve_cases"

# Parametrisation ids must exist at collection time, so they are read from disk here.
# The bodies still load the case through the `frozen_fixtures` session fixture.
_RESOLVE_CASE_IDS = sorted(p.stem for p in _RESOLVE_CASE_DIR.glob("*.json")) or [
    "__no_frozen_resolve_cases_found__"
]

_DUMMY_KEY = "frozen-acceptance-dummy-key-never-used"


# --------------------------------------------------------------------------------------
# normalisation, mirroring `util.normalize_ws` WITHOUT importing it
#
# The frozen suite must not measure the gradee with the gradee's own ruler: if
# `normalize_ws` were broken, importing it here would make a fixture pre-condition agree
# with the bug instead of exposing it.
# --------------------------------------------------------------------------------------
def _norm(text: object) -> str:
    return " ".join(str(text).split()).casefold()


# --------------------------------------------------------------------------------------
# a local LLMClient stub (tests/doubles.py is inside ticket T-0's scope; the frozen suite
# must not depend on a tree the graded workers can edit)
# --------------------------------------------------------------------------------------
_VERDICT_ALIASES = {
    # schema field name -> key in the scripted verdict payload
    "verdict": "match",
    "decision": "match",
    "result": "match",
    "is_match": "match",
    "quote": "evidence",
    "supporting_quote": "evidence",
    "evidence_quote": "evidence",
    "reason": "evidence",
    "detail": "disambiguator",
    "attribute": "disambiguator",
    "disambiguating_detail": "disambiguator",
    "document_id": "doc_id",
    "id": "doc_id",
    "score": "confidence",
}


def _unwrap_optional(annotation):
    if get_origin(annotation) is Union:
        for arg in get_args(annotation):
            if arg is not type(None):
                return arg
    return annotation


def _literal_options(annotation):
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    return []


def _list_item_model(annotation):
    annotation = _unwrap_optional(annotation)
    if get_origin(annotation) is list:
        args = get_args(annotation)
        if args and hasattr(args[0], "model_fields"):
            return args[0]
    return None


def _default_for(annotation):
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin is list:
        return []
    if origin is dict:
        return {}
    options = _literal_options(annotation)
    if options:
        return options[0]
    if annotation is str:
        return ""
    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    return None


def _fill(model, payload, aliases):
    """Instantiate `model` from a flat payload, matching by field name then by alias."""
    fields = getattr(model, "model_fields", None)
    if fields is None:
        raise AssertionError(f"scripted LLM was handed a non-pydantic schema: {model!r}")
    kwargs = {}
    for name, field in fields.items():
        if name in payload:
            kwargs[name] = payload[name]
            continue
        alias = aliases.get(name)
        if alias is not None and alias in payload:
            kwargs[name] = payload[alias]
            continue
        annotation = _unwrap_optional(field.annotation)
        if hasattr(annotation, "model_fields"):
            kwargs[name] = _fill(annotation, payload, aliases)
        elif field.is_required():
            kwargs[name] = _default_for(annotation)
    return model(**kwargs)


def _build(schema, payloads, aliases):
    """Build whatever `schema` the implementation asked for out of scripted payloads."""
    fields = getattr(schema, "model_fields", None)
    if fields is None:
        raise AssertionError(f"scripted LLM was handed a non-pydantic schema: {schema!r}")
    for name, field in fields.items():
        item_model = _list_item_model(field.annotation)
        if item_model is None:
            continue
        kwargs = {name: [_fill(item_model, p, aliases) for p in payloads]}
        for other, other_field in fields.items():
            if other != name and other_field.is_required():
                kwargs[other] = _default_for(other_field.annotation)
        return schema(**kwargs)
    return _fill(schema, payloads[0], aliases)


def _text_probe(doc) -> str:
    return _norm(doc.text)[:70]


def _docs_named_in(docs, prompt):
    prompt_n = _norm(prompt)
    named = [d for d in docs if _norm(d.doc_id) in prompt_n or _norm(d.url) in prompt_n]
    if named:
        return named
    return [d for d in docs if _text_probe(d) and _text_probe(d) in prompt_n]


class _ScriptedVerdictLLM:
    """Satisfies `contracts.LLMClient`: returns the scripted verdict for the doc in the prompt."""

    def __init__(self, docs, verdicts):
        self.docs = list(docs)
        self.scripted = {v["doc_id"]: dict(v) for v in verdicts}
        self.calls: list[dict] = []

    async def structured(
        self, *, system: str, user: str, schema, max_tokens: int = 2000, cache_prefix: bool = True
    ):
        self.calls.append({"schema": schema, "system": system, "user": user})
        payloads = [
            self.scripted[d.doc_id] for d in _docs_named_in(self.docs, user) if d.doc_id in self.scripted
        ]
        if not payloads:
            raise AssertionError(
                "the resolver asked the scripted LLM about no recognisable document "
                f"(schema={getattr(schema, '__name__', schema)!r}); prompt began: {user[:220]!r}"
            )
        return _build(schema, payloads, _VERDICT_ALIASES)


# --------------------------------------------------------------------------------------
# corpus helpers
# --------------------------------------------------------------------------------------
def _load_case(frozen_fixtures: Path, case_id: str) -> dict:
    path = frozen_fixtures / "resolve_cases" / f"{case_id}.json"
    assert path.is_file(), f"frozen resolver case is missing from the corpus: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_resolve(case: dict, verdicts=None, doc_ids=None):
    """Run `resolve` over a frozen case, optionally over a variant verdict set / doc subset."""
    scripted = case["scripted_verdicts"] if verdicts is None else verdicts
    raw = case["docs"] if doc_ids is None else [d for d in case["docs"] if d["doc_id"] in set(doc_ids)]
    assert {v["doc_id"] for v in scripted} == {d["doc_id"] for d in raw}, (
        "every document handed to the resolver must carry exactly one scripted verdict"
    )

    async def _inner():
        from arrival.contracts import PersonRef, RawDoc
        from arrival.resolve import resolve

        person = PersonRef.model_validate(case["person"])
        docs = [RawDoc.model_validate(d) for d in raw]
        return await resolve(person, docs, _ScriptedVerdictLLM(docs, scripted))

    return asyncio.run(_inner())


def _quoted_verdicts(case: dict) -> list[dict]:
    """The case's verdicts whose evidence really is a verbatim span of its own document."""
    texts = {d["doc_id"]: d["text"] for d in case["docs"]}
    return [v for v in case["scripted_verdicts"] if _norm(v["evidence"]) in _norm(texts[v["doc_id"]])]


# --------------------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", _RESOLVE_CASE_IDS)
def test_resolver_reproduces_the_frozen_case_outcome(frozen_fixtures, case_id):
    """R2 / S4 / T-2 acceptance 2+4: status, accepted docs and strong keys per frozen case."""
    case = _load_case(frozen_fixtures, case_id)
    expect = case["expect"]
    resolution = _run_resolve(case)

    assert resolution.status == expect["status"], (
        f"{case_id}: expected status {expect['status']!r}, got {resolution.status!r}. "
        f"Frozen rationale: {expect['note']}"
    )
    assert sorted(resolution.accepted_doc_ids) == sorted(expect["accepted_doc_ids"]), (
        f"{case_id}: accepted_doc_ids {sorted(resolution.accepted_doc_ids)} != "
        f"{sorted(expect['accepted_doc_ids'])}. Frozen rationale: {expect['note']}"
    )
    if expect["strong_keys_present"]:
        for key in expect["strong_keys_present"]:
            assert key in resolution.strong_keys, (
                f"{case_id}: strong key {key!r} missing from {dict(resolution.strong_keys)}"
            )
    else:
        assert dict(resolution.strong_keys) == {}, (
            f"{case_id}: no strong key is earnable here, got {dict(resolution.strong_keys)}"
        )


def test_decoy_namesake_docs_are_rejected_while_target_docs_are_accepted(frozen_fixtures):
    """SPEC S4: the same-name decoy's documents stay out AND the target's documents come in."""
    case = _load_case(frozen_fixtures, "decoy-deceased-namesake")
    decoy_ids = [v["doc_id"] for v in case["scripted_verdicts"] if v["match"] == "no"]
    target_ids = list(case["expect"]["accepted_doc_ids"])
    assert decoy_ids and target_ids, "frozen decoy case must contain both decoy and target docs"

    accepted = set(_run_resolve(case).accepted_doc_ids)

    for doc_id in decoy_ids:
        assert doc_id not in accepted, f"decoy document {doc_id} was accepted"
    # The other half: asserting only the rejection would pass a resolver that rejects
    # everything, which is not resolution, it is abstention.
    for doc_id in target_ids:
        assert doc_id in accepted, f"target document {doc_id} was not accepted"


def test_conflicting_evidence_verdict_is_what_keeps_a_namesake_doc_out(frozen_fixtures):
    """DESIGN Decision 4 / T-2 acceptance 3: a `no` on conflicting employer/city hard-rejects."""
    case = _load_case(frozen_fixtures, "decoy-deceased-namesake")
    decoy_ids = [v["doc_id"] for v in case["scripted_verdicts"] if v["match"] == "no"]

    resolution = _run_resolve(case)
    rejected_ids = {v.doc_id for v in resolution.rejected}
    for doc_id in decoy_ids:
        assert doc_id not in set(resolution.accepted_doc_ids)
        assert doc_id in rejected_ids, (
            f"vetoed document {doc_id} must be retained in Resolution.rejected for /debug"
        )
    # The decoy verdicts are MORE confident (0.96/0.94/0.91) than the two target verdicts
    # (0.74/0.69): a resolver that pooled or averaged confidence would land on the decoy.
    assert resolution.status == "resolved"

    # Sabotage companion: identical documents, identical evidence, identical confidences —
    # only the `no` becomes `yes`. If those documents are now accepted, then the sole cause
    # of their rejection above was the negative verdict, and nothing else about them.
    flipped = [dict(v, match="yes") if v["match"] == "no" else dict(v) for v in case["scripted_verdicts"]]
    accepted_when_not_vetoed = set(_run_resolve(case, verdicts=flipped).accepted_doc_ids)
    for doc_id in decoy_ids:
        assert doc_id in accepted_when_not_vetoed, (
            f"{doc_id} stayed out even with a `yes` verdict, so the earlier rejection was "
            "not caused by the negative evidence and this test proves nothing"
        )


def test_two_yes_verdicts_on_the_same_disambiguator_do_not_resolve(frozen_fixtures):
    """DESIGN Decision 4 / T-2 acceptance 2: corroborating ONE attribute is not independence."""
    same_attribute = _load_case(frozen_fixtures, "evidence-not-in-doc")
    # Drop the one verdict whose evidence is not a verbatim span, so this test measures the
    # independence rule alone rather than re-measuring the citation check.
    surviving = _quoted_verdicts(same_attribute)
    yes_disambiguators = {v["disambiguator"] for v in surviving if v["match"] == "yes"}
    assert len(yes_disambiguators) == 1, (
        "fixture pre-condition: after the unquoted verdict is dropped the remaining `yes` "
        f"verdicts must all cite one disambiguator, got {sorted(yes_disambiguators)}"
    )
    assert len([v for v in surviving if v["match"] == "yes"]) >= 2, (
        "fixture pre-condition: there must be two `yes` verdicts, or nothing is being tested"
    )

    resolution = _run_resolve(
        same_attribute, verdicts=surviving, doc_ids=[v["doc_id"] for v in surviving]
    )
    assert resolution.status == "unresolved", (
        "two `yes` verdicts citing the same disambiguator are corroboration of one attribute, "
        "not two independent attributes; DESIGN Decision 4 requires them to DIFFER"
    )
    assert list(resolution.accepted_doc_ids) == [], "R2: an unresolved person stores no documents"

    # Sabotage companion: the identical rule applied to two DIFFERENT disambiguators must
    # resolve. Without this half, a resolver that never resolves anything passes.
    independent = _load_case(frozen_fixtures, "two-independent-attributes")
    control = _run_resolve(independent)
    assert control.status == "resolved"
    assert sorted(control.accepted_doc_ids) == sorted(independent["expect"]["accepted_doc_ids"])


def test_verdict_evidence_absent_from_its_doc_cannot_carry_a_resolution(frozen_fixtures):
    """T-2 acceptance 5 / DESIGN Decision 5: unquoted evidence is downgraded to `unsure`."""
    case = _load_case(frozen_fixtures, "two-independent-attributes")

    # Control half: as frozen, two `yes` verdicts on employer and city, both verbatim.
    control = _run_resolve(case)
    assert control.status == "resolved"
    assert sorted(control.accepted_doc_ids) == sorted(case["expect"]["accepted_doc_ids"])

    # Sabotage half: the SAME documents and the SAME verdicts, with exactly one dimension
    # changed — the city verdict now cites a sentence that appears in no document.
    fabricated = "Bram Teasdale has run the Portland office of Copperline Freight since 2014."
    assert all(_norm(fabricated) not in _norm(d["text"]) for d in case["docs"]), (
        "fixture pre-condition: the fabricated span must not occur in any document"
    )
    tampered = [
        dict(v, evidence=fabricated) if v["match"] == "yes" and v["disambiguator"] == "city" else dict(v)
        for v in case["scripted_verdicts"]
    ]
    assert any(v["evidence"] == fabricated for v in tampered), "fixture pre-condition: nothing tampered"

    resolution = _run_resolve(case, verdicts=tampered)
    assert resolution.status == "unresolved", (
        "a verdict whose evidence is not a normalize_ws substring of its document must be "
        "downgraded to `unsure`, leaving one `yes` and therefore no resolution"
    )
    assert list(resolution.accepted_doc_ids) == []


def test_anthropic_client_satisfies_the_llm_client_protocol():
    """T-2 acceptance 1 / DESIGN Interfaces: the production client IS an `LLMClient`."""
    from arrival.contracts import LLMClient
    from arrival.llm.client import AnthropicClient

    client = _construct_offline(AnthropicClient)
    assert isinstance(client, LLMClient), (
        "AnthropicClient must satisfy the runtime-checkable LLMClient Protocol so the "
        "double and the real client are interchangeable everywhere downstream"
    )


class _AnySetting:
    """Stands in for a Settings object: any attribute reads back as a harmless string."""

    def __getattr__(self, name):
        return _DUMMY_KEY


def _construct_offline(cls):
    """Build `cls` without a network call, trying the plausible constructor shapes."""
    import inspect

    errors = []
    for args, kwargs in (((), {"api_key": _DUMMY_KEY}), ((), {}), ((_DUMMY_KEY,), {}), ((_AnySetting(),), {})):
        try:
            return cls(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - report every shape that failed
            errors.append(f"{cls.__name__}(*{args!r}, **{sorted(kwargs)}) -> {exc!r}")
    try:
        parameters = inspect.signature(cls).parameters
        kwargs = {}
        for name, param in parameters.items():
            if param.default is not inspect.Parameter.empty:
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            lowered = name.lower()
            if any(token in lowered for token in ("key", "token", "model", "url", "name")):
                kwargs[name] = _DUMMY_KEY
            else:
                kwargs[name] = _AnySetting()
        return cls(**kwargs)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"signature-driven -> {exc!r}")
    raise AssertionError("could not construct AnthropicClient offline: " + " | ".join(errors))
