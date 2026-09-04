"""FROZEN acceptance tests for ticket T-2 — entity resolution and the production LLM client.

Graded requirements: SPEC R2 (refuse to guess), SPEC S4 (same-name decoy), SPEC C6/C7,
DESIGN Decision 4 (strong key OR two independent attributes; negative evidence hard-rejects;
confidences are never averaged) and DESIGN Decision 5 (evidence must be a verbatim span).

Everything is driven from the orchestrator-owned corpus in `fixtures/resolve_cases/`, which
no worker may write. Two tests build *variants* of a frozen case in memory — each variant
changes exactly one dimension of the committed case so the assertion isolates one rule.

Three of the frozen cases (`strong-key-refused-*`) exist to make the strong-key arm of
Decision 4 *discriminating*: each holds a strong-key-CAPABLE document (wikidata, github,
edgar) that carries a `yes` verdict, is accepted, and must still earn NO key, because the
QID matches on name only, the GitHub profile's Company is unset, or the CIK is matched on
a different company. Without them, `kind in {wikidata, github, edgar} and verdict == "yes"
-> take the key` passes the whole corpus while implementing none of Decision 4.

Product imports are deliberately inside function bodies: at cycle 0 `arrival` does not
exist, and a module-scope import would turn an unbuilt feature into a collection error,
which silently removes these tests from both sides of the pass-rate fraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import pytest

# Two markers, deliberately. `t2` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t2`), and every scored metric
# selects on it. `ticket("T-2")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t2, pytest.mark.ticket("T-2")]

_ACCEPTANCE_DIR = Path(__file__).resolve().parent
_RESOLVE_CASE_DIR = _ACCEPTANCE_DIR / "fixtures" / "resolve_cases"

# --------------------------------------------------------------------------------------
# WIKIDATA QIDs IN THIS CORPUS ARE DELIBERATELY OUT OF RANGE. Do not "fix" them back.
#
# The corpus attaches invented biographies to Wikidata items, and a QID is a durable
# real-world identifier no matter how fictional the prose around it is: an item id inside
# the allocated range names a real entity, plausibly a real person, which FROZEN-SPEC §5
# and T-2's non-goals ("fictional people only, everywhere, with no exceptions") forbid.
# So every QID here is nine digits in the Q9000004xx block -- roughly an order of
# magnitude beyond Wikidata's highest allocated item -- which cannot collide with a real
# item and is the same convention the frozen dossier corpus already uses (Q900000317).
# They look implausibly long BECAUSE that is what makes them safe.
#
# The QID also sits in each such document's url, and `doc_id == sha1(url)[:16]`, so
# renumbering one means recomputing its doc_id and every `scripted_verdicts` and
# `expect.accepted_doc_ids` reference to it. `test_the_frozen_resolve_corpus_loaded`
# re-checks that relation for every document in the corpus.
# --------------------------------------------------------------------------------------

# The corpus AS FROZEN. Pinned here as well as globbed off disk because the glob alone is
# silently lossy: pytest builds the denominator of every T-2 metric out of these ids, so a
# corpus that only half-loads -- files deleted, the directory moved or renamed, a
# permission error that `Path.glob` swallows and reports as "no matches" -- quietly shrinks
# the scored count while every surviving case stays green. A smaller green suite is
# indistinguishable from a passing one unless something knows how big the suite was meant
# to be; this set is that something.
_FROZEN_RESOLVE_CASE_IDS = frozenset(
    {
        "decoy-deceased-namesake",
        "evidence-not-in-doc",
        "must-be-unresolved",
        "strong-key-refused-edgar-name-not-company",
        "strong-key-refused-github-unconfirmed",
        "strong-key-refused-wikidata-name-only",
        "strong-key-wikidata",
        "two-independent-attributes",
    }
)

# The parametrisation id that means "the corpus did not load". A sentinel rather than an
# exception, because raising at module scope aborts COLLECTION of this module and takes
# every T-2 criterion out of both sides of the pass-rate fraction at once -- which reads
# as "could not measure", not as a failure. The sentinel keeps the module collectable and
# the test body turns it into one loud, named failure instead.
_CORPUS_DID_NOT_LOAD = "__frozen_resolve_corpus_did_not_load__"


def _discover_resolve_case_ids() -> list[str]:
    """Parametrisation ids, read from disk at collection time. Standard library only.

    Parametrisation ids must exist at collection time, so this cannot move into a fixture;
    the test bodies still load each case through the `frozen_fixtures` session fixture.

    `Path.glob` answers "directory is empty", "directory does not exist" and (on 3.11+)
    "directory could not be read" with the same empty iterator, so none of the three can
    be told apart here. All of them collapse to the sentinel, and the tests below are
    where an empty or unreadable corpus becomes a hard failure rather than a short run.
    """
    try:
        found = sorted(p.stem for p in _RESOLVE_CASE_DIR.glob("*.json"))
    except OSError:  # pragma: no cover - unreadable corpus directory
        found = []
    return found or [_CORPUS_DID_NOT_LOAD]


_RESOLVE_CASE_IDS = _discover_resolve_case_ids()

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
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # unreadable file, or not valid JSON
        raise AssertionError(
            f"frozen resolver case {path} exists but could not be loaded ({exc}). "
            "A case that cannot be read is a broken measuring stick, not a passing case."
        ) from exc
    for key in ("person", "docs", "scripted_verdicts", "expect"):
        assert key in case, f"frozen resolver case {path} is missing the {key!r} key"
    return case


def _run_resolve_capturing(case: dict, verdicts=None, doc_ids=None):
    """`_run_resolve`, additionally returning the scripted LLM and the documents it saw.

    The stub has to be built inside the coroutine (constructing `RawDoc` needs the product
    import, which must stay lazy), so it is handed back out through `captured` rather than
    created by the caller.
    """
    scripted = case["scripted_verdicts"] if verdicts is None else verdicts
    raw = case["docs"] if doc_ids is None else [d for d in case["docs"] if d["doc_id"] in set(doc_ids)]
    assert {v["doc_id"] for v in scripted} == {d["doc_id"] for d in raw}, (
        "every document handed to the resolver must carry exactly one scripted verdict"
    )
    captured: dict = {}

    async def _inner():
        from arrival.contracts import PersonRef, RawDoc
        from arrival.resolve import resolve

        person = PersonRef.model_validate(case["person"])
        docs = [RawDoc.model_validate(d) for d in raw]
        llm = _ScriptedVerdictLLM(docs, scripted)
        captured["llm"] = llm
        captured["docs"] = docs
        return await resolve(person, docs, llm)

    resolution = asyncio.run(_inner())
    return resolution, captured["llm"], captured["docs"]


def _run_resolve(case: dict, verdicts=None, doc_ids=None):
    """Run `resolve` over a frozen case, optionally over a variant verdict set / doc subset."""
    return _run_resolve_capturing(case, verdicts=verdicts, doc_ids=doc_ids)[0]


def _docs_the_resolver_asked_about(llm, docs) -> set[str]:
    """doc_ids the resolver actually put to the model, by the stub's own recognition rule.

    Deliberately the SAME `_docs_named_in` the stub uses to answer, so this measures
    exactly "could this document have received a verdict from the model", and adds no
    failure mode the scripted stub did not already have. Works for a per-document prompt
    loop and for one batched prompt naming every document alike.
    """
    seen: set[str] = set()
    for call in llm.calls:
        for doc in _docs_named_in(docs, call["user"]):
            seen.add(doc.doc_id)
    return seen


def _quoted_verdicts(case: dict) -> list[dict]:
    """The case's verdicts whose evidence really is a verbatim span of its own document."""
    texts = {d["doc_id"]: d["text"] for d in case["docs"]}
    return [v for v in case["scripted_verdicts"] if _norm(v["evidence"]) in _norm(texts[v["doc_id"]])]


# --------------------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------------------
@pytest.mark.guard
def test_the_frozen_resolve_corpus_loaded(frozen_fixtures):
    """Harness self-check: T-2's denominator is the whole frozen corpus, not the survivors.

    `guard`, and therefore excluded from every scored count, because it exercises no
    product code and is green at baseline by design. What it buys is that a corpus which
    fails to load can no longer be mistaken for a corpus that passes: an empty, shrunken,
    moved or unreadable `resolve_cases/` fails HERE, by name, instead of quietly removing
    parametrised criteria from both sides of the pass-rate fraction.
    """
    directory = frozen_fixtures / "resolve_cases"
    assert directory.is_dir(), (
        f"the frozen resolve-case corpus directory is missing or is not a directory: "
        f"{directory}. Every T-2 per-case criterion is parametrised out of it."
    )

    assert _CORPUS_DID_NOT_LOAD not in _RESOLVE_CASE_IDS, (
        f"collection found ZERO cases in {_RESOLVE_CASE_DIR}, so "
        "test_resolver_reproduces_the_frozen_case_outcome is parametrised over a "
        "placeholder and grades nothing."
    )
    on_disk = sorted(p.stem for p in directory.glob("*.json"))
    assert sorted(_RESOLVE_CASE_IDS) == on_disk, (
        "the ids pytest parametrised at collection time disagree with the corpus on disk: "
        f"collected {sorted(_RESOLVE_CASE_IDS)} vs on disk {on_disk}"
    )
    missing = sorted(_FROZEN_RESOLVE_CASE_IDS - set(on_disk))
    assert not missing, (
        f"frozen resolve cases have gone missing from the corpus: {missing}. Their "
        "criteria are not failing, they have silently left the denominator."
    )

    cases = {}
    for case_id in on_disk:
        path = directory / f"{case_id}.json"
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AssertionError(f"{case_id}: corpus file could not be loaded ({exc})") from exc
        cases[case_id] = case

        for key in ("case_id", "person", "docs", "scripted_verdicts", "expect"):
            assert key in case, f"{case_id}: corpus file is missing the {key!r} key"
        assert case["case_id"] == case_id, f"{case_id}: case_id disagrees with its filename"

        doc_ids = [d["doc_id"] for d in case["docs"]]
        assert doc_ids, f"{case_id}: a case with no documents grades nothing"
        assert len(set(doc_ids)) == len(doc_ids), f"{case_id}: duplicate doc_id"
        for doc in case["docs"]:
            expected_id = hashlib.sha1(doc["url"].encode()).hexdigest()[:16]
            assert doc["doc_id"] == expected_id, (
                f"{case_id}: doc_id {doc['doc_id']} != sha1({doc['url']})[:16] = {expected_id}"
            )
            assert doc["text"].strip(), f"{case_id}: {doc['doc_id']} has empty text"
        assert {v["doc_id"] for v in case["scripted_verdicts"]} == set(doc_ids), (
            f"{case_id}: scripted verdicts and documents do not correspond one-to-one"
        )

        expect = case["expect"]
        assert expect["status"] in {"resolved", "unresolved"}, f"{case_id}: bad status"
        assert set(expect["accepted_doc_ids"]) <= set(doc_ids), f"{case_id}: unknown accepted id"
        if expect["status"] == "unresolved":
            assert expect["accepted_doc_ids"] == [], f"{case_id}: unresolved stores no docs (R2)"
        assert isinstance(expect["strong_keys_present"], list), f"{case_id}: bad strong_keys"
        assert expect["note"].strip(), f"{case_id}: a case with no rationale cannot be reviewed"

    # The corpus must keep BREAKING the strong-key shortcut. Every source kind that can
    # carry a strong key needs at least one case where such a document has a `yes` verdict
    # and STILL earns no key; without one of these, `kind in {wikidata, github, edgar} and
    # verdict == "yes" -> take the key` passes the whole corpus while implementing neither
    # priority order, nor name+detail matching, nor confirmation.
    strong_key_kinds = {"wikidata", "github", "edgar"}
    refused = set()
    for case in cases.values():
        if case["expect"]["strong_keys_present"]:
            continue
        verdict_by_doc = {v["doc_id"]: v for v in case["scripted_verdicts"]}
        for doc in case["docs"]:
            if doc["source_kind"] in strong_key_kinds:
                if verdict_by_doc[doc["doc_id"]]["match"] == "yes":
                    refused.add(doc["source_kind"])
    assert refused == strong_key_kinds, (
        "the corpus no longer refuses a strong key to every strong-key-capable source "
        f"kind that carries a `yes` verdict: covered {sorted(refused)}, need "
        f"{sorted(strong_key_kinds)}. A kind missing from that set is a kind an "
        "implementation may key off document type alone and still score full marks."
    )


@pytest.mark.parametrize("case_id", _RESOLVE_CASE_IDS)
def test_resolver_reproduces_the_frozen_case_outcome(frozen_fixtures, case_id):
    """R2 / S4 / T-2 acceptance 2+4: status, accepted docs and strong keys per frozen case."""
    if case_id == _CORPUS_DID_NOT_LOAD:
        pytest.fail(
            f"the frozen resolve-case corpus at {_RESOLVE_CASE_DIR} yielded ZERO cases at "
            "collection time, so this parametrisation is a placeholder and T-2 has no "
            "per-case criteria at all. Restore the corpus before reading any T-2 number: "
            "this is a broken measuring stick, not a product failure."
        )
    case = _load_case(frozen_fixtures, case_id)
    expect = case["expect"]
    resolution, llm, docs = _run_resolve_capturing(case)

    # DESIGN Decision 4 is 'LLM verdict per doc', asserted FIRST and on its own. Two of the
    # frozen cases expect `unresolved` with no accepted documents, so without this a
    # resolver that returns exactly that, unconditionally -- reading no document, asking
    # the model nothing -- collected those two criteria for free.
    unasked = sorted({d.doc_id for d in docs} - _docs_the_resolver_asked_about(llm, docs))
    assert not unasked, (
        f"{case_id}: the resolver produced an answer over {len(docs)} document(s) having "
        f"made {len(llm.calls)} structured call(s), and never put {unasked} to the model. "
        "DESIGN Decision 4 decides this per document, on one LLM verdict each: a document "
        "the model never saw was not judged, it was assumed."
    )
    assert resolution.person_id == case["person"]["person_id"], (
        f"{case_id}: Resolution.person_id is {resolution.person_id!r}, expected "
        f"{case['person']['person_id']!r}"
    )

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

    # Sabotage companion: identical DOCUMENTS, identical confidences, identical source
    # kinds — only the verdict changes, and it changes on the DISAMBIGUATOR dimension
    # rather than on `match`. Each decoy verdict becomes a `yes` on `role`, carrying a
    # different verbatim span from the very same document, chosen so that it asserts no
    # employer and no work location at all. If those documents are now accepted, the sole
    # cause of their rejection above was the negative evidence and nothing else about them.
    #
    # AMENDED 2026-09-03 on the goal owner's decision (ESC-005), from a companion that
    # flipped `no` -> `yes` while KEEPING the contradicting evidence. That form required a
    # document explicitly naming a different employer AND a different city to be ACCEPTED,
    # purely because the model attached `yes` to it — which contradicts DESIGN's "negative
    # evidence hard-rejects... a single contradiction must veto", a statement about the
    # EVIDENCE rather than about which token the model emitted. It also could not tell
    # "polarity caused the rejection" from "the contradiction caused it", because it moved
    # the one dimension that carried both. Flipping the disambiguator keeps every bit of
    # the sabotage value and drops that entanglement.
    _NON_CONFLICTING = {
        "a8c5850fdf7e9766": "Instance of: human. Occupation: marine archaeologist, author, university teacher.",
        "e8c81fcabba7b0f3": "best known for The Cold Cargo, a 1996 history of container freight written for a general readership",
        "66024d822958d78d": "Readers still write to us about the chapter on freight scheduling in The Cold Cargo",
    }
    flipped = [
        dict(v, match="yes", disambiguator="role", evidence=_NON_CONFLICTING[v["doc_id"]])
        if v["match"] == "no"
        else dict(v)
        for v in case["scripted_verdicts"]
    ]
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
