"""FROZEN acceptance tests for ticket T-4 -- the taste filter (the scored differentiator).

Requirements graded here: R11 (never display the six categories), R12 (display whitelist,
confidence floor), R13 (one-paragraph exclusion policy), SPEC S3 (100% agreement with the
human-approved taste corpus), DESIGN Decision 6 (two-stage: rules, then LLM only on
unsure, fail closed).

GROUND TRUTH IS `fixtures/taste_cases_frozen.yaml`, the orchestrator-owned corpus. This
file NEVER reads `tests/fixtures/taste_cases.yaml`: that one is inside T-4's own write
scope, and a filter graded against a fixture its own author can edit is not being graded.

THE CORPUS PUBLISHES ITS OWN ANSWERS, AND T-4's VERIFY COMMAND POINTS THE WORKER AT IT.
So a `dict[text] -> (excluded, exclusion_reason)` read straight out of the fixture is
pure, deterministic, decides every deterministic case with no LLM call, fails closed on
everything else -- and scored 8/8 here before this module was repaired. Three tests exist
to make that fail, and none of them adds an answer to memorise:

  * test_rulings_survive_entity_renaming_and_reframing rewrites all 56 cases at RUN TIME
    into ~150 sentences that exist on no disk, each INHERITING the ruling of the case it
    came from (so no new judgment is introduced and nothing new needs owner approval);
  * test_the_rule_layer_decides_renamed_sentences_without_an_llm_call requires a pure
    rename to stay inside the deterministic stage, which a table cannot do;
  * test_held_back_minimal_pairs_are_ruled_apart grades twelve unseen sentences in six
    near-identical pairs whose rulings live in the corpus as digests, not as text.

Staging is graded in both directions too: every `rule_layer: llm` case must REACH the
classifier, not merely be allowed to. Eleven of the fourteen never had to before.

Every product import is INSIDE a test body on purpose. At cycle 0 `arrival.taste` does not
exist; a module-scope import would turn an unbuilt feature into a collection error and
delete this whole file from both halves of the pass-rate fraction, which reads green over
a suite that never ran.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import re
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


# ------------------------------------------------------- held-back rulings (digests)

#: Everything `contracts.ExclusionReason` can hold, plus "keep". The held-back rulings
#: in the corpus are committed as digests over exactly this alphabet.
_VERDICT_ALPHABET = (
    "keep",
    "home_or_property",
    "family",
    "health",
    "legal",
    "wealth",
    "political",
    "low_confidence",
    "source_kind_not_displayable",
)

#: Must match `held_back.digest_scheme` in the corpus, byte for byte.
_DIGEST_PREFIX = "arrival-taste-v1|"


def _digest(member_id: str, verdict: str) -> str:
    return hashlib.sha256(f"{_DIGEST_PREFIX}{member_id}|{verdict}".encode()).hexdigest()


def _recover(member: dict) -> str:
    """The ruling behind a held-back digest.

    Nine hashes. That is the honest measure of what the digest buys: it stops the
    answer being READ off the corpus -- which is the hole T-4's own ticket opens by
    pointing the worker at that file -- and it stops nothing else. The defence that
    actually holds is that these sentences appear nowhere, and that each pair is graded
    on the two members being ruled APART, which is a property and has no answer to leak.
    """
    for verdict in _VERDICT_ALPHABET:
        if _digest(member["id"], verdict) == member["verdict_sha256"]:
            return verdict
    raise AssertionError(
        f"the held-back ruling for {member['id']} hashes to none of {_VERDICT_ALPHABET}; "
        "the corpus digest and the scheme in this module have drifted apart"
    )


def _load_held_back(frozen_fixtures) -> list[dict]:
    """The held-back minimal pairs: sentences in plaintext, rulings as digests."""
    import yaml  # lazy: see _load_cases

    path = frozen_fixtures / "taste_cases_frozen.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    pairs = doc["held_back"]["minimal_pairs"]
    assert len(pairs) >= 4, f"held-back set too small to grade generalisation: {len(pairs)}"
    return pairs


# ------------------------------------------------------------- run-time case rewriting


#: Type-preserving renames of the corpus' invented entities: street for street, company
#: for company, person for person, year for year. Applied longest-first so that
#: "Ninebark Ventures" is not half-rewritten by the rule for "Ninebark Analytics", and
#: on word boundaries so "March" cannot reach inside "Marchbanks Street".
#:
#: The point of renaming: it changes every byte a lookup table keys on and NOTHING a
#: rule can legitimately act on. "3120 Hallendale Court" is a street address for exactly
#: the reasons "3120 Willowmere Court" is. A filter whose answer moves is not applying
#: the rule it appeared to apply. Numbers that carry meaning are deliberately absent
#: from this map -- "990" is the name of a tax form, "type 1 diabetes" is a diagnosis.
_ENTITY_RENAMES = (
    ("Alderwick Instruments", "Bramblecote Instruments"),
    ("Sable Creek Software", "Pennyroyal Software"),
    ("Kettleback Systems", "Underbough Systems"),
    ("Pecan Hollow Road", "Juniper Bend Road"),
    ("Marchbanks Street", "Fennimore Street"),
    ("Ninebark Analytics", "Harrowgate Analytics"),
    ("Ninebark Ventures", "Harrowgate Ventures"),
    ("Willowmere Court", "Hallendale Court"),
    ("Quarrystone Labs", "Fenwright Labs"),
    ("Ravensworth Hill", "Kestrelmoor Hill"),
    ("Copperhead Grid", "Larkspur Grid"),
    ("Calder County", "Aldermarsh County"),
    ("Bouldin Creek", "Trellin Park"),
    ("Corin Aldaz", "Devrin Halvorsen"),
    ("Tannery Row", "Cooperage Row"),
    ("Marlingate", "Thistlewood"),
    ("Ambervale", "Rookwood"),
    ("Lisbon", "Valencia"),
    ("Porto", "Bilbao"),
    ("March", "January"),
    ("3120", "1748"),
    ("2011", "2014"),
    ("2019", "2017"),
    ("2008", "2006"),
    ("2022", "2020"),
    ("2021", "2018"),
    ("1.8", "2.4"),
)

#: Re-framings. Both are back-references that add no information: they must not tell a
#: disclosure-sensitive filter anything new about WHO made the fact public, or they
#: would move the ruling on excl-health-05 and keep-tricky-burnout-podcast and the
#: invariance would be a lie rather than a test.
_FRAME_SUFFIX = ", according to the same source."
_FRAME_PREFIX = "Per one profile, "

#: Sentences whose first word can open a subordinate clause. `excl-home-01` and
#: `excl-home-02` are skipped for this family: "Per one profile, lives at ..." is not a
#: sentence, and a malformed input tests the parser, not the filter.
_PREFIX_OPENERS = frozenset({"They", "Their", "A", "An", "Two", "Court", "Federal"})

#: The four rewrites, applied to every case at run time. 150-odd sentences that exist
#: on no disk anywhere.
VARIANT_FAMILIES = ("rename", "rename_suffix", "suffix", "prefix")


def _renamed(text: str) -> str:
    for old, new in _ENTITY_RENAMES:
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    return text


def _suffixed(text: str) -> str:
    return text.rstrip(".") + _FRAME_SUFFIX


def _prefixed(text: str):
    if text.split(" ", 1)[0] not in _PREFIX_OPENERS:
        return None
    return _FRAME_PREFIX + text[0].lower() + text[1:]


def _variant_text(case: dict, family: str):
    """`case` rewritten into `family`, or None where the family does not apply."""
    text = case["text"]
    renamed = _renamed(text)
    if family in ("rename", "rename_suffix"):
        # A case with no invented entity in it is not renamed, and grading an unchanged
        # sentence as if it were a variant would inflate the count with nothing.
        if renamed == text:
            return None
        return renamed if family == "rename" else _suffixed(renamed)
    if family == "suffix":
        return _suffixed(text)
    if family == "prefix":
        return _prefixed(text)
    raise AssertionError(f"unknown variant family {family!r}")


def _variant_cases(cases: list[dict], family: str) -> list[dict]:
    """Corpus cases rewritten into `family`, each INHERITING its own ruling.

    Inheriting matters: no new judgment is introduced, so nothing here needs owner
    approval and nothing here can be wrong in a way the corpus is not already wrong.
    """
    out = []
    for case in cases:
        text = _variant_text(case, family)
        if text is None:
            continue
        variant = dict(case)
        variant["id"] = f"{case['id']}::{family}"
        variant["text"] = text
        variant["base_id"] = case["id"]
        out.append(variant)
    assert out, f"variant family {family!r} produced no sentences"
    return out


def _observed_verdict(fact) -> str:
    """What the filter actually said about a fact, in the corpus' own vocabulary."""
    if not fact.excluded:
        return "keep"
    return fact.exclusion_reason or "excluded_with_no_reason"


def _probe_fact(
    fact_id: str, confidence: float, *, source_kind: str = "search", excluded: bool = False
):
    """A minimal, whitelisted, professional Fact carrying one chosen confidence.

    Built here rather than read out of `dossiers/`: those fixtures belong to another
    ticket and carry only 0.55 and >= 0.90, which is precisely why the R12 floor was
    never pinned.
    """
    from arrival.contracts import Fact, Provenance

    sentence = "They chair a working group drafting an interoperability standard."
    return Fact(
        fact_id=fact_id,
        text=sentence,
        category="affiliation",
        provenance=Provenance(
            doc_id="00000000frozen00",
            url="https://example.org/frozen/r12-boundary",
            source_kind=source_kind,
            quote=sentence,
            published_at=None,
            retrieved_at=_FIXED_RETRIEVED_AT,
            confidence=confidence,
        ),
        excluded=excluded,
    )


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
        self._probes = _unique_probes(self._script)
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
        spans = []
        for text, (fact_id, verdict) in self._script.items():
            idx = user.find(text)
            if idx >= 0:
                located.append((idx, fact_id, text, verdict))
                spans.append((idx, idx + len(text)))
        exact = {fact_id for _, fact_id, _, _ in located}
        for text, (fact_id, verdict) in self._script.items():
            if fact_id in exact:
                continue
            idx = user.find(self._probes[text])
            # A prefix landing INSIDE a sentence already matched in full belongs to that
            # sentence, not to this one; without this the held-back minimal pairs, which
            # share their opening clause by construction, cross-script each other.
            if idx >= 0 and not any(start <= idx < end for start, end in spans):
                located.append((idx, fact_id, text, verdict))
        located.sort()
        pairs = [(fid, txt, v) for _, fid, txt, v in located]
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "texts": [t for _, t, _ in pairs]}
        )
        if not pairs:
            pairs = [("unknown", "", self._default)]
        return _scripted_payload(schema, pairs)


def _unique_probes(script: dict, floor: int = 40) -> dict[str, str]:
    """text -> the shortest prefix of at least `floor` chars that no other scripted
    sentence contains.

    The probe is the double's tolerance for an implementation that reformats fact text
    instead of quoting it. A FIXED 40-character probe silently attributes one sentence's
    verdict to another whenever two scripted sentences share an opening -- and a minimal
    pair shares its opening BY CONSTRUCTION; a pair that diverges in the first forty
    characters is not minimal. So the length is computed per script rather than assumed.
    """
    texts = list(script)
    probes: dict[str, str] = {}
    for text in texts:
        others = [other for other in texts if other != text]
        chosen = text
        for size in range(min(floor, len(text)), len(text) + 1):
            head = text[:size]
            if not any(head in other for other in others):
                chosen = head
                break
        probes[text] = chosen
    return probes


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
    """DESIGN Decision 6: the classifier sees EXACTLY what the rules marked unsure.

    Both directions are graded. Only requiring the fail-closed cases to arrive let an
    implementation settle eleven of the fourteen `rule_layer: llm` cases with a keyword
    and still score full marks -- which makes that marking decorative, and makes the
    two-stage design DESIGN Decision 6 asks for unobservable from outside.
    """
    cases = _load_cases(frozen_fixtures)
    deterministic_ids = {c["id"] for c in cases if c["rule_layer"] == "deterministic"}
    llm_ids = {c["id"] for c in cases if c["rule_layer"] == "llm"}
    fail_closed_ids = {c["id"] for c in cases if c.get("fail_closed")}
    assert fail_closed_ids, "corpus carries no fail-closed cases"
    assert len(llm_ids) > len(fail_closed_ids), (
        "every llm-layer case in the corpus is also a fail-closed case, so this test "
        "cannot tell a two-stage filter from one that special-cases the unsure ones"
    )

    llm = _ScriptedTasteLLM(_script_for(cases))
    _run_apply_taste(_facts_from_cases(cases), llm)
    seen = llm.ids_seen()

    leaked = sorted(seen & deterministic_ids)
    assert not leaked, (
        f"{len(leaked)} case(s) the rule layer must settle were still sent to the LLM: {leaked}"
    )
    # Sabotage companion: an implementation that never calls the LLM at all would satisfy
    # the assertion above vacuously. Every `rule_layer: llm` case is unsure by
    # construction -- a category-shaped word fires and nothing in the corpus exempts it,
    # so the ruling turns on who the sentence is about or who disclosed it -- and each
    # one MUST therefore reach the second stage. The corpus states this as a hard
    # requirement, not a hint; see HOW THE TWO STAGES DIVIDE THE CORPUS in the fixture.
    never_asked = sorted(llm_ids - seen)
    assert not never_asked, (
        f"{len(never_asked)} of the {len(llm_ids)} cases marked `rule_layer: llm` never "
        f"reached the classifier: {never_asked}. Something decided them with a keyword, "
        "which means either the rule layer is guessing on sentences whose ruling turns "
        "on the subject or the discloser, or it is recognising these strings outright."
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
    # runa-okonkwo-f15 MUST STAY `excluded: false`. It is the only fact in the frozen
    # fixtures that is displayable on every gate except the source-kind whitelist, so it
    # is the only proof that the whitelist bites INDEPENDENTLY of the taste filter and
    # of the confidence floor. Flip it to excluded and the assertion below still passes
    # -- for the wrong reason -- and R12's third gate stops being measured by anything.
    # (A separate frozen-review finding notes that its TEXT is substantively political
    # and a correct taste filter would exclude it; that is a defect in the dossier
    # fixture's wording, owned by another lane, and the fix is to change the text, never
    # the flag.)

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


def _run_variant_family(cases: list[dict], family: str):
    """apply_taste over one rewritten family, scripted on the REWRITTEN sentences."""
    variants = _variant_cases(cases, family)
    llm = _ScriptedTasteLLM(_script_for(variants))
    result = _by_id(_run_apply_taste(_facts_from_cases(variants), llm))
    return variants, llm, result


def test_rulings_survive_entity_renaming_and_reframing(frozen_fixtures):
    """SPEC S3: the ruling belongs to the SENTENCE, not to the string in the fixture.

    Every case is rewritten at run time -- invented companies, streets, people and years
    renamed to other invented ones, the sentence re-framed -- and must come out with the
    same verdict and the same reason. None of these ~150 sentences exists on disk.

    This grades the OUTCOME, with the same cooperative double the rest of the module
    uses. On its own it is not the anti-memorisation defence and must not be mistaken
    for one: that double answers correctly for anything handed to it, so an
    implementation that hands it everything looks perfect here. The two tests that
    follow are where that shows up.
    """
    cases = _load_cases(frozen_fixtures)
    checked = 0
    wrong: list[str] = []
    for family in VARIANT_FAMILIES:
        variants, _llm, result = _run_variant_family(cases, family)
        for variant in variants:
            checked += 1
            got = result.get(variant["id"])
            if got is None:
                wrong.append(f"{variant['id']}: apply_taste dropped the fact entirely")
                continue
            expected_excluded = variant["expect"] == "exclude"
            if got.excluded != expected_excluded or got.exclusion_reason != variant["reason"]:
                wrong.append(
                    f"{variant['id']}: corpus says excluded={expected_excluded} "
                    f"reason={variant['reason']!r}; got excluded={got.excluded} "
                    f"reason={got.exclusion_reason!r} on {variant['text']!r}"
                )
    assert checked >= 120, (
        f"the run-time rewriter produced only {checked} sentences; it is meant to cover "
        "every case in four families and something has stopped applying"
    )
    assert not wrong, (
        f"{len(wrong)} of {checked} rewritten sentences were ruled differently from the "
        f"corpus case they were derived from -- the filter is keyed on the string, not "
        f"on the sentence: {wrong[:10]}"
    )


def test_the_rule_layer_decides_rewritten_sentences_without_an_llm_call(frozen_fixtures):
    """DESIGN Decision 6 + S3: renaming an invented company is not new information.

    THIS IS THE ANTI-MEMORISATION ASSERTION, and it is a STAGING one on purpose.

    A `dict[normalised text] -> (excluded, exclusion_reason)` read straight out of
    `taste_cases_frozen.yaml` -- which T-4's own verify command points the worker at --
    is pure, deterministic, settles all 42 deterministic cases with zero LLM calls, and
    contains no rule. Measured: it scores 10/13 on this module with only the staging
    tests against it, because every outcome test scripts a double that answers correctly
    for whatever it is handed, so "mark everything unseen unsure and let the classifier
    do it" looks flawless from outside.

    It cannot look flawless here. A rule layer that settles "Quarrystone Labs raised a
    Series B" but not "Fenwright Labs raised a Series B" is recognising a string, and
    every one of the ~110 rewritten deterministic sentences below leaves the cheap stage.
    """
    from arrival.taste import apply_taste_rules

    cases = _load_cases(frozen_fixtures)
    deterministic = [c for c in cases if c["rule_layer"] == "deterministic"]
    assert deterministic, "corpus carries no deterministic cases"

    checked = 0
    diverged: list[str] = []
    deferred: list[str] = []
    for family in VARIANT_FAMILIES:
        variants = _variant_cases(deterministic, family)
        checked += len(variants)

        ruled = _by_id(apply_taste_rules(_facts_from_cases(variants)))
        for variant in variants:
            got = ruled.get(variant["id"])
            if got is None:
                diverged.append(f"{variant['id']}: apply_taste_rules dropped the fact")
                continue
            expected_excluded = variant["expect"] == "exclude"
            if got.excluded != expected_excluded or (
                expected_excluded and got.exclusion_reason != variant["reason"]
            ):
                diverged.append(
                    f"{variant['id']}: expected excluded={expected_excluded} "
                    f"reason={variant['reason']!r}, got excluded={got.excluded} "
                    f"reason={got.exclusion_reason!r} on {variant['text']!r}"
                )

        llm = _ScriptedTasteLLM(_script_for(variants))
        _run_apply_taste(_facts_from_cases(variants), llm)
        deferred.extend(sorted(llm.ids_seen()))

    assert checked >= 100, (
        f"only {checked} rewritten deterministic sentences were graded; the rewriter or "
        "the corpus has drifted and this test has stopped covering anything"
    )
    assert not diverged, (
        f"{len(diverged)}/{checked} sentences the rule layer settles in their committed "
        f"wording are decided differently once rewritten: {diverged[:10]}"
    )
    assert not deferred, (
        f"{len(deferred)}/{checked} rewritten sentences were handed to the classifier "
        f"even though the rule layer settles the originals alone: {deferred[:15]}. A "
        "rule that stops applying when an invented company is renamed is not a rule."
    )


def test_a_hostile_classifier_cannot_move_what_the_rules_must_settle(frozen_fixtures):
    """DESIGN Decision 6 from the other side: an `unsure`-to-everything classifier.

    The outcome-level twin of the staging test above, and the reason both exist. The
    cooperative double returns the corpus' own answer for anything it is handed, so
    deferring the whole corpus to it scores full marks on every outcome assertion. Here
    the classifier answers `unsure` to everything, so anything the rule layer failed to
    settle comes back withheld as `low_confidence` -- and gets named.

    The fail-closed cases are graded in the same run and in the opposite direction: they
    are the ones that MUST reach this classifier, and must be withheld when it shrugs.
    """
    cases = _load_cases(frozen_fixtures)
    checked = 0
    withheld: list[str] = []
    misruled: list[str] = []
    never_asked: list[str] = []
    not_failed_closed: list[str] = []
    for family in VARIANT_FAMILIES:
        variants = _variant_cases(cases, family)
        hostile = {v["text"]: (v["id"], "unsure") for v in variants}
        llm = _ScriptedTasteLLM(hostile, default="unsure")
        result = _by_id(_run_apply_taste(_facts_from_cases(variants), llm))
        seen = llm.ids_seen()
        for variant in variants:
            got = result.get(variant["id"])
            if variant.get("fail_closed"):
                checked += 1
                if variant["id"] not in seen:
                    never_asked.append(f"{variant['id']} -> {variant['text']!r}")
                elif got is None or not got.excluded or got.exclusion_reason != "low_confidence":
                    not_failed_closed.append(
                        f"{variant['id']}: excluded={got and got.excluded} "
                        f"reason={got and got.exclusion_reason!r}"
                    )
                continue
            if variant["rule_layer"] != "deterministic":
                continue
            checked += 1
            if got is None:
                misruled.append(f"{variant['id']}: apply_taste dropped the fact")
            elif variant["expect"] == "keep":
                if got.excluded:
                    withheld.append(
                        f"{variant['id']} ({got.exclusion_reason}) on {variant['text']!r}"
                    )
            elif not got.excluded or got.exclusion_reason != variant["reason"]:
                misruled.append(
                    f"{variant['id']}: expected reason={variant['reason']!r}, got "
                    f"excluded={got.excluded} reason={got.exclusion_reason!r}"
                )
    assert checked >= 100, f"only {checked} sentences were graded against a hostile classifier"
    assert not withheld, (
        f"{len(withheld)} professional facts the rule layer must keep on its own were "
        f"withheld the moment the classifier stopped cooperating: {withheld[:10]}. Those "
        "were never decided by a rule; they were decided by the double."
    )
    assert not misruled, (
        f"{len(misruled)} facts the rule layer must exclude with a NAMED category came "
        f"back with something else -- `low_confidence` here means they were deferred, "
        f"not ruled: {misruled[:10]}"
    )
    assert not never_asked, (
        f"{len(never_asked)} rewritten fail-closed sentences never reached the "
        f"classifier, so the rule layer is recognising the committed strings rather than "
        f"the cue in them: {never_asked}"
    )
    assert not not_failed_closed, (
        "the classifier answered `unsure` and these were not withheld with reason "
        f"low_confidence: {not_failed_closed}"
    )


def _held_back_members(pairs: list[dict]) -> list[dict]:
    members: list[dict] = []
    for pair in pairs:
        rulings = {side: _recover(pair[side]) for side in ("a", "b")}
        # Corpus sanity, not a graded property: a "pair" ruled the same way both ways
        # discriminates nothing.
        assert rulings["a"] != rulings["b"], (
            f"held-back pair {pair['pair']!r} rules both members the same way"
        )
        for side in ("a", "b"):
            member = dict(pair[side])
            member["verdict"] = rulings[side]
            member["pair"] = pair["pair"]
            member["axis"] = pair["axis"]
            member["sibling"] = pair["b" if side == "a" else "a"]["text"]
            members.append(member)
    return members


def _synthetic_case(member: dict, llm_returns: str) -> dict:
    return {
        "id": member["id"],
        "text": member["text"],
        "expect": "keep" if member["verdict"] == "keep" else "exclude",
        "reason": None if member["verdict"] == "keep" else member["verdict"],
        "rule_layer": "llm",
        "llm_returns": llm_returns,
    }


def test_held_back_minimal_pairs_are_ruled_apart(frozen_fixtures):
    """SPEC S3 generalisation: two sentences one edit apart must be ruled differently.

    Six pairs, twelve sentences, none of them under `cases:` and none of their rulings
    written in plaintext (`held_back.minimal_pairs[].verdict_sha256`). Each pair turns on
    one of the corpus' two stated principles -- whose money, court record or house it is,
    and who disclosed it -- so a filter that reads the sentence gets both members and
    every shortcut gets one of them wrong:

      * an exact table keyed on the committed text has no entry for either member;
      * a nearest-neighbour or whitespace-normalised lookup collapses both members onto
        ONE corpus case and answers them identically, which the last assertion names;
      * "fail closed on anything unseen" withholds the keep member;
      * "keep anything unseen" shows the exclude member.
    """
    pairs = _load_held_back(frozen_fixtures)
    members = _held_back_members(pairs)

    synthetic = [
        _synthetic_case(m, "unsure" if m["verdict"] == "low_confidence" else m["verdict"])
        for m in members
    ]
    llm = _ScriptedTasteLLM(_script_for(synthetic))
    result = _by_id(_run_apply_taste(_facts_from_cases(synthetic), llm))

    wrong: list[str] = []
    for member in members:
        got = result.get(member["id"])
        if got is None:
            wrong.append(f"{member['id']}: apply_taste dropped the fact entirely")
            continue
        if _observed_verdict(got) == member["verdict"]:
            continue
        # The expected ruling is deliberately NOT printed. What is printed is the
        # distinction that was missed, which is the useful half and the half a lookup
        # table cannot act on.
        wrong.append(
            f"[{member['pair']}, axis={member['axis']}] {member['id']} came back "
            f"{_observed_verdict(got)!r} on {member['text']!r} -- that is not the "
            f"held-back ruling. Its pair partner reads {member['sibling']!r}; the two "
            "differ on that axis and must be ruled apart."
        )
    assert not wrong, f"{len(wrong)}/{len(members)} held-back sentences misruled: {wrong}"

    collapsed = [
        pair["pair"]
        for pair in pairs
        if _observed_verdict(result[pair["a"]["id"]]) == _observed_verdict(result[pair["b"]["id"]])
    ]
    assert not collapsed, (
        "both members of these pairs came back with the SAME verdict, which is what a "
        "nearest-neighbour lookup over the committed corpus does, and what a filter that "
        f"answers everything unseen the same way does: {collapsed}"
    )


def test_held_back_pairs_survive_a_classifier_that_keeps_everything(frozen_fixtures):
    """The held-back pairs again, with a classifier that answers `keep` to everything.

    Without this, the pairs above are satisfied by deferring all twelve sentences to a
    double that hands back the right answer. The corpus states the contract this grades:
    in every pair except the fail-closed one, one member carries an unambiguous R11
    surface marker whose subject can only be the person, so the RULE LAYER alone has to
    find it. Which member, and which category, are not written down anywhere.

    The fail-closed pair is graded in the opposite direction, against a classifier that
    answers `unsure` to everything: there, one member must reach the classifier and be
    withheld as low_confidence, and its near-identical sibling must be kept by the rules
    alone with no help from a classifier that is no help.
    """
    pairs = _load_held_back(frozen_fixtures)
    members = _held_back_members(pairs)
    by_pair: dict[str, list[dict]] = {}
    for member in members:
        by_pair.setdefault(member["pair"], []).append(member)

    settled = [m for m in members if m["verdict"] not in ("keep", "low_confidence")]
    deferral_pairs = {m["pair"] for m in members if m["verdict"] == "low_confidence"}
    assert settled, "no held-back member carries a nameable exclusion category"
    assert deferral_pairs, "no held-back pair probes the fail-closed route"

    # --- run 1: the classifier is useless in the permissive direction ----------------
    permissive = [
        _synthetic_case(m, "keep") for m in members if m["pair"] not in deferral_pairs
    ]
    llm = _ScriptedTasteLLM({c["text"]: (c["id"], "keep") for c in permissive}, default="keep")
    result = _by_id(_run_apply_taste(_facts_from_cases(permissive), llm))

    shown: list[str] = []
    for member in members:
        if member["pair"] in deferral_pairs:
            continue
        got = result.get(member["id"])
        if got is None:
            shown.append(f"{member['id']}: apply_taste dropped the fact")
        elif member["verdict"] == "keep":
            if got.excluded:
                shown.append(
                    f"[{member['pair']}] {member['id']} was withheld "
                    f"({got.exclusion_reason}) even though the classifier said keep"
                )
        elif _observed_verdict(got) != member["verdict"]:
            shown.append(
                f"[{member['pair']}, axis={member['axis']}] {member['id']} came back "
                f"{_observed_verdict(got)!r} on {member['text']!r} once the classifier "
                "stopped supplying the answer. This sentence carries an unambiguous "
                "marker whose subject can only be the person; the rule layer has to see "
                f"it. Its sibling reads {member['sibling']!r}."
            )
    assert not shown, f"{len(shown)} held-back sentences depended on the classifier: {shown}"

    # --- run 2: the classifier is useless in the cautious direction ------------------
    cautious = [_synthetic_case(m, "unsure") for m in members if m["pair"] in deferral_pairs]
    llm2 = _ScriptedTasteLLM({c["text"]: (c["id"], "unsure") for c in cautious}, default="unsure")
    result2 = _by_id(_run_apply_taste(_facts_from_cases(cautious), llm2))
    asked = llm2.ids_seen()

    problems: list[str] = []
    for member in members:
        if member["pair"] not in deferral_pairs:
            continue
        got = result2.get(member["id"])
        if got is None:
            problems.append(f"{member['id']}: apply_taste dropped the fact")
        elif member["verdict"] == "low_confidence":
            if member["id"] not in asked:
                problems.append(
                    f"{member['id']} never reached the classifier; the sentence says "
                    "outright that its source does not resolve the question, which is "
                    "the corpus' stated cue to defer"
                )
            elif not got.excluded or got.exclusion_reason != "low_confidence":
                problems.append(
                    f"{member['id']}: excluded={got.excluded} "
                    f"reason={got.exclusion_reason!r}, expected a fail-closed withhold"
                )
        elif got.excluded:
            problems.append(
                f"{member['id']} was withheld ({got.exclusion_reason}) on "
                f"{member['text']!r}. It shares its first fifty-two characters with its "
                "sibling and differs only in the half that resolves it; withholding both "
                "is failing closed on everything, not reading the sentence."
            )
    assert not problems, f"the fail-closed pair was not handled: {problems}"


def test_is_displayable_pins_the_r12_confidence_boundary(frozen_fixtures):
    """R12 is `confidence >= 0.7`, so 0.70 displays and anything below it does not.

    The dossier fixture carries only 0.55 and >= 0.90, so before this test EVERY floor in
    (0.55, 0.86] passed -- including 0.6, which shows facts R12 withholds, and a `> 0.7`
    comparison, which withholds a fact R12 shows. The boundary is the whole content of
    the requirement, so it is probed at the boundary.
    """
    from arrival.taste import is_displayable

    assert is_displayable(_probe_fact("r12-at-floor", 0.7)) is True, (
        "R12 reads `confidence >= 0.7`: a whitelisted, non-excluded fact at EXACTLY 0.7 "
        "is displayable, so the comparison is >= and not >"
    )
    assert is_displayable(_probe_fact("r12-over", 0.71)) is True, (
        "0.71 clears the floor on a whitelisted source and must be displayable"
    )
    assert is_displayable(_probe_fact("r12-just-under", 0.6999)) is False, (
        "0.6999 is below the 0.7 floor; a floor that rounds to two decimals, or compares "
        "against 0.65, shows a fact R12 withholds"
    )
    assert is_displayable(_probe_fact("r12-under", 0.69)) is False, (
        "0.69 is below the 0.7 floor and must never be displayed"
    )
    assert is_displayable(_probe_fact("r12-excluded-at-floor", 0.7, excluded=True)) is False, (
        "clearing the confidence floor does not un-exclude a fact: R11 and the floor are "
        "separate gates and both must hold"
    )
