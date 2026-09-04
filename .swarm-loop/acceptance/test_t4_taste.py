"""FROZEN acceptance tests for ticket T-4 -- the taste filter (the scored differentiator).

Requirements graded here: R11 (never display the six categories), R12 (display whitelist,
confidence floor), R13 (one-paragraph exclusion policy), SPEC S3 (100% agreement with the
human-approved taste corpus), DESIGN Decision 6 (two-stage: rules, then LLM only on
unsure, fail closed).

GROUND TRUTH IS `fixtures/taste_cases_frozen.yaml`, the orchestrator-owned corpus. This
file NEVER reads `tests/fixtures/taste_cases.yaml`: that one is inside T-4's own write
scope, and a filter graded against a fixture its own author can edit is not being graded.

Every product import is INSIDE a test body on purpose. At cycle 0 `arrival.taste` does not
exist; a module-scope import would turn an unbuilt feature into a collection error and
delete this whole file from both halves of the pass-rate fraction, which reads green over
a suite that never ran.
"""

from __future__ import annotations

import asyncio
import enum
import json
import types
import typing
from datetime import datetime

import pytest

# Two markers, deliberately. `t4` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t4`), and every scored metric
# selects on it. `ticket("T-4")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t4, pytest.mark.ticket("T-4")]


# --------------------------------------------------------------------------- constants

#: The six R11 taste categories, as `contracts.ExclusionReason` literals.
SIX_CATEGORIES = (
    "home_or_property",
    "family",
    "health",
    "legal",
    "wealth",
    "political",
)

#: DESIGN §Data models pins this whitelist verbatim. It is the contract, not an
#: implementation detail: R12 says only these source kinds may ever be displayed.
DESIGN_DISPLAYABLE_KINDS = frozenset(
    {
        "self_page",
        "search",
        "wikidata",
        "wikipedia",
        "github",
        "edgar",
        "uspto",
        "propublica",
        "wayback",
        "hn",
        "openalex",
        "youtube",
        "podcast",
    }
)

#: C1 permits these as capability but R11/DESIGN forbid them from ever being displayed.
NEVER_DISPLAYABLE_KINDS = frozenset({"fec", "courtlistener"})

#: Words that satisfy each R11 category in the EXCLUSION_POLICY paragraph (R13). Any one
#: alternative counts, so the policy's prose is not over-specified.
POLICY_WORDS = {
    "home_or_property": ("home", "address", "propert"),
    "family": ("family", "families", "spouse", "children", "relationship"),
    "health": ("health", "medical"),
    "legal": ("legal", "litigation", "court", "criminal", "divorce"),
    "wealth": ("wealth", "net worth", "compensation", "salary"),
    "political": ("political", "politics", "donation", "affiliation"),
}

#: A literal timestamp: SPEC C7/determinism forbids wall-clock values in the harness.
_FIXED_RETRIEVED_AT = datetime.fromisoformat("2026-02-21T08:30:00+00:00")


# ------------------------------------------------------------------ corpus -> Fact objects


def _load_cases(frozen_fixtures) -> list[dict]:
    import yaml  # lazy: module-scope third-party imports make a missing wheel a COLLECTION error, which reports numbers instead of dying
    """Read the orchestrator-owned taste corpus."""
    path = frozen_fixtures / "taste_cases_frozen.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = doc["cases"]
    # Sanity, not a graded assertion: an empty or unreadable corpus would make every
    # negative test below vacuously green, which is the failure mode rule 7 exists for.
    assert len(cases) >= 36, f"frozen taste corpus is too small to grade S3: {len(cases)}"
    return cases


def _expected_verdict(case: dict) -> str:
    """The taste verdict the corpus says this case deserves."""
    return case["reason"] if case["expect"] == "exclude" else "keep"


def _script_for(cases: list[dict]) -> dict[str, tuple[str, str]]:
    """text -> (fact_id, verdict) for the scripted LLM double.

    `rule_layer: llm` cases are scripted with the corpus' own `llm_returns` value, which
    is exactly why that field exists. `deterministic` cases are scripted with their
    expected verdict so that an implementation which wrongly routes them to the LLM fails
    the STAGING test rather than corrupting the OUTCOME tests -- the two properties stay
    orthogonal.
    """
    script = {}
    for case in cases:
        verdict = case["llm_returns"] if case["rule_layer"] == "llm" else _expected_verdict(case)
        script[case["text"]] = (case["id"], verdict)
    return script


def _facts_from_cases(cases: list[dict]) -> list:
    """Build one `contracts.Fact` per corpus case.

    Every fact is given a WHITELISTED source kind at confidence 0.9, well over the 0.7
    display floor, so `excluded` / `exclusion_reason` is provably the only thing the
    taste filter can be acting on.
    """
    from arrival.contracts import Fact, Provenance

    facts = []
    for case in cases:
        facts.append(
            Fact(
                fact_id=case["id"],
                text=case["text"],
                category="interest",
                provenance=Provenance(
                    doc_id="00000000frozen00",
                    url="https://example.org/frozen/taste-corpus",
                    source_kind="search",
                    quote=case["text"],
                    published_at=None,
                    retrieved_at=_FIXED_RETRIEVED_AT,
                    confidence=0.9,
                ),
            )
        )
    return facts


def _by_id(facts) -> dict[str, object]:
    return {f.fact_id: f for f in facts}


def _run_apply_taste(facts, llm):
    """`apply_taste` is async; the frozen suite runs with `-o addopts=` and its own
    rootdir, so it can never rely on the project's `asyncio_mode = auto`."""

    async def _inner():
        from arrival.taste import apply_taste

        return await apply_taste(facts, llm)

    return asyncio.run(_inner())


# ------------------------------------------------------------------- scripted LLM double


class _ScriptedTasteLLM:
    """A `contracts.LLMClient` stand-in for the taste classifier stage.

    It is defined here, not imported from `tests/doubles.py`, because that module is
    inside T-0's write scope and the frozen suite must not depend on anything a graded
    worker can edit.

    It recognises which fact(s) a prompt is about by looking for their text in `user`, and
    returns an instance of whatever `schema` the implementation asked for, carrying the
    scripted verdict. DESIGN leaves the taste classifier's internal schema to T-4, so the
    payload is built by reflection over `schema`'s fields; when that cannot be done the
    double raises an AssertionError that NAMES the schema and its fields rather than
    failing obscurely.
    """

    def __init__(self, script: dict[str, tuple[str, str]], default: str = "unsure"):
        self._script = dict(script)
        self._default = default
        self.calls: list[dict] = []

    @property
    def texts_seen(self) -> list[str]:
        seen: list[str] = []
        for call in self.calls:
            seen.extend(call["texts"])
        return seen

    def ids_seen(self) -> set[str]:
        return {self._script[t][0] for t in self.texts_seen}

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema,
        max_tokens: int = 2000,
        cache_prefix: bool = True,
    ):
        located = []
        for text, (fact_id, verdict) in self._script.items():
            idx = user.find(text)
            if idx < 0:
                idx = user.find(text[:40])
            if idx >= 0:
                located.append((idx, fact_id, text, verdict))
        located.sort()
        pairs = [(fid, txt, v) for _, fid, txt, v in located]
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "texts": [t for _, t, _ in pairs]}
        )
        if not pairs:
            pairs = [("unknown", "", self._default)]
        return _scripted_payload(schema, pairs)


def _model_fields(model) -> dict:
    fields = getattr(model, "model_fields", None)
    if fields is None:  # pragma: no cover - pydantic v1 shim
        fields = getattr(model, "__fields__", {})
    out = {}
    for name, info in fields.items():
        out[name] = getattr(info, "annotation", getattr(info, "outer_type_", str))
    return out


def _as_model(annotation):
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
        return annotation
    return None


def _strip_annotated(annotation):
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    return annotation


def _list_item_model(annotation):
    annotation = _strip_annotated(annotation)
    if typing.get_origin(annotation) in (list, set, tuple):
        args = typing.get_args(annotation)
        if args:
            return _as_model(_strip_annotated(args[0]))
    return None


def _is_reasonish(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("reason", "category", "exclusion", "verdict", "label", "class"))


def _choose_literal(members: list, name: str, verdict: str):
    if verdict in members:
        return verdict
    lowered = {str(m).lower(): m for m in members}
    if verdict == "keep":
        preferred = ("keep", "keeps", "display", "show", "allow", "none", "no", "safe", "public")
    elif verdict == "unsure":
        preferred = ("unsure", "unknown", "uncertain", "maybe", "ambiguous", "low_confidence")
    else:
        preferred = (verdict, "exclude", "excluded", "withhold", "yes", "redact", "private")
    for cand in preferred:
        if cand in lowered:
            return lowered[cand]
    return members[0]


def _bool_for(name: str, verdict: str) -> bool:
    n = name.lower()
    if any(k in n for k in ("unsure", "uncertain", "ambig")):
        return verdict == "unsure"
    if any(k in n for k in ("exclud", "withh", "block", "redact", "sensitive", "private")):
        return verdict != "keep"
    if any(k in n for k in ("keep", "display", "show", "allow", "safe", "public")):
        return verdict == "keep"
    return False


def _str_for(name: str, fact_id: str, text: str, verdict: str) -> str:
    n = name.lower()
    if n == "id" or n.endswith("_id"):
        return fact_id
    if "text" in n or "quote" in n or "sentence" in n:
        return text
    if _is_reasonish(n) or any(k in n for k in ("decision", "result", "status", "answer")):
        return verdict
    if any(k in n for k in ("rationale", "explan", "note", "why", "justif")):
        return "frozen acceptance stub"
    return ""


def _value_for(name: str, annotation, fact_id: str, text: str, verdict: str):
    annotation = _strip_annotated(annotation)
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Literal:
        return _choose_literal(list(args), name, verdict)
    if origin is typing.Union or origin is getattr(types, "UnionType", ()):
        non_none = [a for a in args if a is not type(None)]
        optional = len(non_none) != len(args)
        if optional and verdict == "keep" and _is_reasonish(name):
            return None
        if not non_none:
            return None
        return _value_for(name, non_none[0], fact_id, text, verdict)
    if origin in (list, set, tuple):
        item_model = _list_item_model(annotation)
        if item_model is not None:
            return [_model_payload(item_model, fact_id, text, verdict)]
        return []
    if origin is dict:
        return {}

    nested = _as_model(annotation)
    if nested is not None:
        return _model_payload(nested, fact_id, text, verdict)
    if annotation is bool:
        return _bool_for(name, verdict)
    if annotation is int:
        return 1 if _bool_for(name, verdict) else 0
    if annotation is float:
        return 0.9
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        for member in annotation:
            if str(member.value) == verdict:
                return member.value
        return list(annotation)[0].value
    return _str_for(name, fact_id, text, verdict)


def _model_payload(model, fact_id: str, text: str, verdict: str) -> dict:
    return {
        name: _value_for(name, annotation, fact_id, text, verdict)
        for name, annotation in _model_fields(model).items()
    }


def _can_express(schema, verdict: str, _seen=None) -> bool:
    """Can `schema` carry this verdict at all?

    DESIGN Decision 6 requires the LLM stage to be able to answer `unsure` so that the
    filter can fail closed; a classifier schema with no way to say so is a contract
    violation, and this turns it into a legible failure instead of a silent wrong answer.
    """
    _seen = _seen or set()
    if schema in _seen:
        return False
    _seen.add(schema)
    for name, annotation in _model_fields(schema).items():
        annotation = _strip_annotated(annotation)
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin is typing.Literal:
            lowered = {str(m).lower() for m in args}
            if verdict in lowered:
                return True
            if verdict == "unsure" and lowered & {"unsure", "unknown", "uncertain", "maybe"}:
                return True
        if origin is typing.Union or origin is getattr(types, "UnionType", ()):
            for arg in args:
                if arg is type(None):
                    continue
                if _can_express_annotation(name, arg, verdict, _seen):
                    return True
            continue
        if _can_express_annotation(name, annotation, verdict, _seen):
            return True
    return False


def _can_express_annotation(name, annotation, verdict, _seen) -> bool:
    annotation = _strip_annotated(annotation)
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        lowered = {str(m).lower() for m in typing.get_args(annotation)}
        return verdict in lowered or (
            verdict == "unsure" and bool(lowered & {"unsure", "unknown", "uncertain", "maybe"})
        )
    if origin in (list, set, tuple):
        item_model = _list_item_model(annotation)
        return item_model is not None and _can_express(item_model, verdict, _seen)
    nested = _as_model(annotation)
    if nested is not None:
        return _can_express(nested, verdict, _seen)
    if annotation is str and _is_reasonish(name):
        return True
    if annotation is bool and verdict == "unsure" and "unsure" in name.lower():
        return True
    return False


def _scripted_payload(schema, pairs: list[tuple[str, str, str]]):
    if not (isinstance(schema, type) and hasattr(schema, "model_fields")):
        raise AssertionError(
            "taste classifier called llm.structured with a non-pydantic schema "
            f"{schema!r}; contracts.LLMClient.structured requires `schema: type[BaseModel]`"
        )
    fields = _model_fields(schema)
    described = ", ".join(f"{n}: {a}" for n, a in fields.items())

    for _, _, verdict in pairs:
        if verdict == "unsure" and not _can_express(schema, "unsure"):
            raise AssertionError(
                f"the taste classifier's output schema {schema.__name__} cannot express an "
                "'unsure' verdict, so DESIGN Decision 6's fail-closed rule can never be "
                f"reached from the LLM stage. Fields: {described}"
            )

    for name, annotation in fields.items():
        item_model = _list_item_model(annotation)
        if item_model is not None:
            payload = {
                name: [_model_payload(item_model, fid, txt, v) for fid, txt, v in pairs],
            }
            for other, other_ann in fields.items():
                if other != name:
                    payload[other] = _value_for(other, other_ann, *pairs[0])
            return _validate(schema, payload, described)

    return _validate(schema, _model_payload(schema, *pairs[0]), described)


def _validate(schema, payload: dict, described: str):
    try:
        return schema.model_validate(payload)
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise AssertionError(
            f"the frozen taste stub could not script {schema.__name__}. "
            f"Fields: {described}. Attempted payload: {json.dumps(payload, default=str)}. "
            f"Validation error: {exc}"
        ) from exc


# ------------------------------------------------------------------------------- tests


def test_every_must_exclude_case_is_excluded_with_the_corpus_reason(frozen_fixtures):
    """SPEC S3 / R11: 100% of the must-exclude cases come out excluded, with the right reason."""
    cases = _load_cases(frozen_fixtures)
    excludes = [c for c in cases if c["expect"] == "exclude"]
    assert excludes, "corpus carries no must-exclude cases"

    facts = _facts_from_cases(cases)
    llm = _ScriptedTasteLLM(_script_for(cases))
    result = _by_id(_run_apply_taste(facts, llm))

    missing = [c["id"] for c in excludes if c["id"] not in result]
    assert not missing, f"apply_taste dropped facts instead of flagging them: {missing}"

    not_excluded = [c["id"] for c in excludes if not result[c["id"]].excluded]
    wrong_reason = [
        f"{c['id']}: expected {c['reason']!r}, got {result[c['id']].exclusion_reason!r}"
        for c in excludes
        if result[c["id"]].excluded and result[c["id"]].exclusion_reason != c["reason"]
    ]
    assert not not_excluded, (
        f"{len(not_excluded)}/{len(excludes)} must-exclude cases were SHOWN: {not_excluded}"
    )
    assert not wrong_reason, f"excluded with the wrong reason: {wrong_reason}"


def test_every_must_keep_case_is_kept(frozen_fixtures):
    """SPEC S3: 100% of the must-keep professional cases survive the filter.

    This is the half that stops a filter which excludes everything from scoring 100%.
    """
    cases = _load_cases(frozen_fixtures)
    keeps = [c for c in cases if c["expect"] == "keep"]
    assert keeps, "corpus carries no must-keep cases"

    facts = _facts_from_cases(cases)
    llm = _ScriptedTasteLLM(_script_for(cases))
    result = _by_id(_run_apply_taste(facts, llm))

    missing = [c["id"] for c in keeps if c["id"] not in result]
    assert not missing, f"apply_taste dropped facts instead of returning them: {missing}"

    over_excluded = [
        f"{c['id']} ({result[c['id']].exclusion_reason})" for c in keeps if result[c["id"]].excluded
    ]
    assert not over_excluded, (
        f"{len(over_excluded)}/{len(keeps)} must-keep professional facts were WITHHELD: "
        f"{over_excluded}"
    )


def test_rule_layer_alone_decides_every_deterministic_case_without_an_llm_call(frozen_fixtures):
    """DESIGN Decision 6: the cheap deterministic stage settles the unambiguous cases."""
    from arrival.taste import apply_taste_rules

    cases = _load_cases(frozen_fixtures)
    deterministic = [c for c in cases if c["rule_layer"] == "deterministic"]
    assert deterministic, "corpus carries no deterministic cases"

    facts = _facts_from_cases(deterministic)
    ruled = _by_id(apply_taste_rules(facts))

    diverged = [
        f"{c['id']}: expected excluded={c['expect'] == 'exclude'} reason={c['reason']!r}, "
        f"got excluded={ruled[c['id']].excluded} reason={ruled[c['id']].exclusion_reason!r}"
        for c in deterministic
        if c["id"] in ruled
        and (
            ruled[c["id"]].excluded != (c["expect"] == "exclude")
            or (c["expect"] == "exclude" and ruled[c["id"]].exclusion_reason != c["reason"])
        )
    ]
    assert not diverged, f"the rule layer alone did not decide: {diverged}"

    llm = _ScriptedTasteLLM(_script_for(deterministic))
    _run_apply_taste(_facts_from_cases(deterministic), llm)
    assert llm.calls == [], (
        f"{len(llm.calls)} LLM call(s) made for cases the rule layer must decide alone; "
        f"facts sent: {sorted(llm.ids_seen())}"
    )


def test_only_llm_layer_cases_reach_the_llm_stage(frozen_fixtures):
    """DESIGN Decision 6: the LLM classifier sees only what the rules marked unsure."""
    cases = _load_cases(frozen_fixtures)
    deterministic_ids = {c["id"] for c in cases if c["rule_layer"] == "deterministic"}
    fail_closed_ids = {c["id"] for c in cases if c.get("fail_closed")}
    assert fail_closed_ids, "corpus carries no fail-closed cases"

    llm = _ScriptedTasteLLM(_script_for(cases))
    _run_apply_taste(_facts_from_cases(cases), llm)
    seen = llm.ids_seen()

    leaked = sorted(seen & deterministic_ids)
    assert not leaked, (
        f"{len(leaked)} case(s) the rule layer must settle were still sent to the LLM: {leaked}"
    )
    # Sabotage companion: an implementation that never calls the LLM at all would satisfy
    # the assertion above vacuously. The fail-closed cases are unsure by construction, so
    # they MUST reach the second stage.
    never_asked = sorted(fail_closed_ids - seen)
    assert not never_asked, (
        f"the LLM stage was never asked about the genuinely ambiguous cases {never_asked}; "
        "DESIGN Decision 6's second stage is not being exercised"
    )


def test_unsure_llm_verdicts_fail_closed_with_low_confidence(frozen_fixtures):
    """T-4 acceptance 5: anything still unsure after both stages is excluded (fail closed)."""
    cases = _load_cases(frozen_fixtures)
    by_case_id = {c["id"]: c for c in cases}
    all_unsure = {c["text"]: (c["id"], "unsure") for c in cases}

    llm = _ScriptedTasteLLM(all_unsure, default="unsure")
    result = _by_id(_run_apply_taste(_facts_from_cases(cases), llm))
    asked = llm.ids_seen()
    assert asked, "no fact reached the LLM stage, so fail-closed was never exercised"

    not_failed_closed = [
        f"{fid}: excluded={result[fid].excluded} reason={result[fid].exclusion_reason!r}"
        for fid in sorted(asked)
        if fid in result
        and not (result[fid].excluded and result[fid].exclusion_reason == "low_confidence")
    ]
    assert not not_failed_closed, (
        "facts the LLM answered 'unsure' for were not excluded with reason low_confidence: "
        f"{not_failed_closed}"
    )

    # Sabotage companion: a filter that simply excludes everything would pass the above.
    # Deterministic keeps are never sent to the LLM and must still be kept.
    still_kept = [
        cid
        for cid in result
        if cid not in asked
        and by_case_id[cid]["expect"] == "keep"
        and by_case_id[cid]["rule_layer"] == "deterministic"
    ]
    assert still_kept, "no deterministic keep case survived; the filter is excluding everything"
    wrongly_excluded = [cid for cid in still_kept if result[cid].excluded]
    assert not wrongly_excluded, (
        f"an all-unsure LLM must not change facts it was never asked about: {wrongly_excluded}"
    )


def test_is_displayable_gates_on_exclusion_confidence_and_source_kind(frozen_fixtures):
    """R12: three independent gates -- excluded, confidence < 0.7, source_kind off the whitelist."""
    from arrival.contracts import Dossier
    from arrival.taste import is_displayable

    raw = json.loads(
        (frozen_fixtures / "dossiers" / "runa-okonkwo.json").read_text(encoding="utf-8")
    )
    facts = {f.fact_id: f for f in Dossier.model_validate(raw).facts}

    control = facts["runa-okonkwo-f01"]  # self_page, confidence 0.93, not excluded
    excluded = facts["runa-okonkwo-f12"]  # excluded family, search, confidence 0.90
    low_conf = facts["runa-okonkwo-f14"]  # search (whitelisted), confidence 0.55
    bad_kind = facts["runa-okonkwo-f15"]  # fec, confidence 0.92, NOT excluded

    # The control: without it, an is_displayable that always returns False passes.
    assert is_displayable(control) is True, (
        "a whitelisted, high-confidence, non-excluded fact must be displayable"
    )

    assert excluded.excluded is True and excluded.provenance.confidence >= 0.7
    assert is_displayable(excluded) is False, "R11: an excluded fact is never displayable"

    assert low_conf.excluded is False and low_conf.provenance.source_kind in (
        DESIGN_DISPLAYABLE_KINDS
    )
    assert is_displayable(low_conf) is False, (
        "R12: confidence 0.55 is below the 0.7 floor even on a whitelisted source"
    )

    assert bad_kind.excluded is False and bad_kind.provenance.confidence >= 0.7
    assert is_displayable(bad_kind) is False, (
        "R12: source_kind 'fec' is never displayable, independent of taste and confidence"
    )


def test_displayable_kinds_equals_the_design_whitelist():
    """R12 / DESIGN §Data models: the display whitelist is pinned, so a widening is caught."""
    from arrival.taste import DISPLAYABLE_KINDS

    actual = set(DISPLAYABLE_KINDS)
    assert actual == set(DESIGN_DISPLAYABLE_KINDS), (
        f"whitelist drifted; unexpectedly allowed: {sorted(actual - DESIGN_DISPLAYABLE_KINDS)}, "
        f"missing: {sorted(DESIGN_DISPLAYABLE_KINDS - actual)}"
    )
    forbidden = actual & NEVER_DISPLAYABLE_KINDS
    assert not forbidden, f"C1/R11: these source kinds may never be displayed: {sorted(forbidden)}"


def test_exclusion_policy_names_all_six_r11_categories():
    """R13: the digest shows a one-paragraph policy stating what is never surfaced."""
    from arrival.taste import EXCLUSION_POLICY

    assert isinstance(EXCLUSION_POLICY, str)
    text = EXCLUSION_POLICY.strip()
    assert text, "EXCLUSION_POLICY is empty"
    assert "\n\n" not in text, "R13 asks for ONE paragraph; this text has a blank-line break"

    lowered = text.lower()
    unnamed = [
        category
        for category in SIX_CATEGORIES
        if not any(word in lowered for word in POLICY_WORDS[category])
    ]
    assert not unnamed, (
        f"the exclusion policy does not name these R11 categories: {unnamed}. Policy: {text!r}"
    )
