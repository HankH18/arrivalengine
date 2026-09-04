"""FROZEN acceptance tests for ticket T-3 — fact/hub extraction and the citation guard.

Graded requirements: SPEC R9 (every displayed fact carries a verbatim quote), SPEC C8
(a fact with no verbatim quote in its source is dropped, not shown), SPEC S6, SPEC R7
(the "Not on the first page" slot, whose `non_obvious` label the extractor assigns —
DESIGN §Data models, non-obvious eligibility), and DESIGN Decision 5 (the citation check
is mechanical) and Decision 3 (stop-hubs, hub canonicalisation, recency).

Source documents come from the orchestrator-owned corpus (`fixtures/docs/` and the
wikidata document inside `fixtures/resolve_cases/strong-key-wikidata.json`), so the
quotes under test are spans of real committed text that no worker may edit. Where a test
needs a document age or a hub label the corpus does not carry, the document is *derived*
from a frozen one so its prose — and therefore every quote — stays corpus-owned.

Product imports are deliberately inside function bodies: at cycle 0 `arrival` does not
exist, and a module-scope import would turn an unbuilt feature into a collection error,
which silently removes these tests from both sides of the pass-rate fraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

import pytest

# Two markers, deliberately. `t3` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t3`), and every scored metric
# selects on it. `ticket("T-3")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t3, pytest.mark.ticket("T-3")]

# Frozen documents used as source text. All are committed RawDoc dumps.
_ABOUT_DOC = "35b4e2600c8a6ea6.json"  # self_page — Runa Okonkwo's own about page
_ROADMAP_DOC = "92b1d32390d8795f.json"  # search — trade-press piece on the same company
_STATUS_DOC = "d9902fb9cd225788.json"  # wayback — archived 2017 status page for the same company

# Verbatim spans of those documents. Every test asserts the pre-condition before using them.
_ABOUT_SPAN = "I co-founded Quarrystone Labs in 2016 and I run the platform team there"
_FOUNDRY_SPAN = "Quarrystone Labs raised its first outside money from Foundry Seed in 2019"
_ROADMAP_SPAN = "Quarrystone Labs opened its platform team roadmap to customers this month"
_WIKIDATA_SPAN = "Employer: Belmarch Optics. Work location: Rotterdam."
_STATUS_SPAN = (
    "Quarrystone Labs shipped a public status page in 2017, which at the time was unusual "
    "for a company of eleven people"
)
_STATUS_SPAN_2 = "We will keep this page up even on the days it makes us look bad."

# DESIGN Decision 3, verbatim: never nodes, after lowercasing.
#
# These are hub LABELS, never hub_id type PREFIXES. `investor` and `technology` are each
# simultaneously a stop-list label AND a `HubType`, so an implementation that matches the
# stop list against `hub_id` (or against `Hub.type`) silently deletes every investor and
# every technology hub — including `investor:foundry-seed-2019`, the rare high-signal hub
# T-5's whole matching score is built on. `test_stop_hub_matching_is_on_labels_not_hub_id_
# type_prefixes` below is the assertion that holds that line.
_STOP_HUBS = {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}

# DESIGN §Data models, non-obvious eligibility (R7), verbatim. Copied rather than imported:
# the frozen suite never measures the gradee with the gradee's own ruler. `self_page` and
# `search` are deliberately absent — a subject's own about page IS the first page.
_NON_OBVIOUS_ELIGIBLE_SOURCE_KINDS = {
    "edgar",
    "uspto",
    "propublica",
    "wayback",
    "github",
    "hn",
    "openalex",
    "wikidata",
    "podcast",
}


# --------------------------------------------------------------------------------------
# normalisation, mirroring `util.normalize_ws` WITHOUT importing it
#
# The frozen suite must not measure the gradee with the gradee's own ruler: if
# `normalize_ws` were broken, importing it here would make every fixture pre-condition
# agree with the bug instead of exposing it.
# --------------------------------------------------------------------------------------
def _norm(text: object) -> str:
    return " ".join(str(text).split()).casefold()


def _is_quoted_in(quote: str, doc_text: str) -> bool:
    return _norm(quote) in _norm(doc_text)


# --------------------------------------------------------------------------------------
# a local LLMClient stub (tests/doubles.py is inside ticket T-0's scope; the frozen suite
# must not depend on a tree the graded workers can edit)
# --------------------------------------------------------------------------------------
_FACT_ALIASES = {
    "id": "fact_id",
    "fact": "text",
    "sentence": "text",
    "statement": "text",
    "summary": "text",
    "evidence": "quote",
    "supporting_quote": "quote",
    "span": "quote",
    "fact_category": "category",
    "source_doc_id": "doc_id",
    "document_id": "doc_id",
    "source": "source_kind",
    "kind": "source_kind",
    "score": "confidence",
    "date": "published_at",
    "published": "published_at",
    "fetched_at": "retrieved_at",
    "retrieved": "retrieved_at",
}

_HUB_ALIASES = {
    "id": "hub_id",
    "name": "label",
    "title": "label",
    "hub_type": "type",
    "kind": "type",
    "fact_ids": "evidence_fact_ids",
    "evidence": "evidence_fact_ids",
    "supported_by": "evidence_fact_ids",
    "wikidata_qid": "qid",
    "wikidata_id": "qid",
    "wikidata": "qid",
}

# Every model field name that can carry a fact's sentence: `text` itself plus every alias
# key that maps onto it. Used only to prove an over-length probe really reached the
# extractor rather than being truncated on the way in.
_FACT_TEXT_FIELDS = {"text"} | {k for k, v in _FACT_ALIASES.items() if v == "text"}


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


def _instantiate(model, kwargs, lenient):
    """`model(**kwargs)`, falling back to an unvalidated build when `lenient`.

    WHY the fallback exists: a probe that feeds the extractor a deliberately
    contract-breaching candidate (an over-length fact) must reach the extractor
    whatever the implementation's OWN internal extraction schema says. If that
    schema pins `max_length=200`, strict construction raises here and the probe
    dies on the way in — grading a correct implementation as a failure and
    grading a permissive one not at all. `model_construct` keeps the delivery
    unconditional so the CONTRACT is graded where it belongs: on the facts the
    extractor returns. Never used by the ordinary tests, which stay strict.
    """
    if not lenient:
        return model(**kwargs)
    try:
        return model(**kwargs)
    except Exception:
        return model.model_construct(**kwargs)


def _fill(model, payload, aliases, lenient=False):
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
            kwargs[name] = _fill(annotation, payload, aliases, lenient)
        elif field.is_required():
            kwargs[name] = _default_for(annotation)
    return _instantiate(model, kwargs, lenient)


def _carried_fact_texts(obj):
    """Every fact sentence the built extraction object actually carries.

    Walks the object the scripted LLM is about to hand back, so a test can assert its
    own pre-condition — that the candidate it scripted survived construction intact
    — instead of assuming it did.
    """
    found: list[str] = []
    fields = getattr(obj, "model_fields", None)
    if fields is None:
        return found
    for name in fields:
        value = getattr(obj, name, None)
        if name in _FACT_TEXT_FIELDS and isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                found.extend(_carried_fact_texts(item))
        elif hasattr(value, "model_fields"):
            found.extend(_carried_fact_texts(value))
    return found


def _list_role(field_name, item_model):
    lowered = field_name.lower()
    if "fact" in lowered:
        return "fact"
    if "hub" in lowered or "entit" in lowered:
        return "hub"
    item_fields = set(getattr(item_model, "model_fields", {}))
    if "label" in item_fields and "text" not in item_fields:
        return "hub"
    if item_fields & {"text", "quote", "provenance", "category"}:
        return "fact"
    return None


def _build_extraction(schema, fact_payloads, hub_payloads, lenient=False):
    """Build whatever extraction schema the implementation asked for."""
    fields = getattr(schema, "model_fields", None)
    if fields is None:
        raise AssertionError(f"scripted LLM was handed a non-pydantic schema: {schema!r}")
    list_fields = {n: _list_item_model(f.annotation) for n, f in fields.items()}
    if not any(list_fields.values()):
        if fact_payloads:
            return _fill(schema, fact_payloads[0], _FACT_ALIASES, lenient)
        raise AssertionError(
            f"extraction schema {getattr(schema, '__name__', schema)!r} carries no list of "
            "models and there is no scripted fact to fill it with"
        )
    kwargs = {}
    for name, field in fields.items():
        item_model = list_fields.get(name)
        if item_model is not None:
            role = _list_role(name, item_model)
            if role == "fact":
                kwargs[name] = [
                    _fill(item_model, p, _FACT_ALIASES, lenient) for p in fact_payloads
                ]
            elif role == "hub":
                kwargs[name] = [_fill(item_model, p, _HUB_ALIASES, lenient) for p in hub_payloads]
            else:
                kwargs[name] = []
        elif field.is_required():
            annotation = _unwrap_optional(field.annotation)
            if hasattr(annotation, "model_fields") and fact_payloads:
                kwargs[name] = _fill(annotation, fact_payloads[0], _FACT_ALIASES, lenient)
            else:
                kwargs[name] = _default_for(annotation)
    return _instantiate(schema, kwargs, lenient)


def _text_probe(doc) -> str:
    return _norm(doc.text)[:70]


def _docs_named_in(docs, prompt):
    prompt_n = _norm(prompt)
    named = [d for d in docs if _norm(d.doc_id) in prompt_n or _norm(d.url) in prompt_n]
    if named:
        return named
    return [d for d in docs if _text_probe(d) and _text_probe(d) in prompt_n]


def _fact_payload(doc, index, spec):
    return {
        "fact_id": f"{doc.doc_id}-f{index}",
        "text": spec["text"],
        "category": spec.get("category", "current_work"),
        "quote": spec["quote"],
        "doc_id": doc.doc_id,
        "url": doc.url,
        "source_kind": doc.source_kind,
        "published_at": doc.published_at,
        "retrieved_at": doc.fetched_at,
        "confidence": spec.get("confidence", 0.9),
    }


def _hub_payload(doc, spec, fact_ids):
    payload = {
        # Deliberately NOT canonical: canonicalising the id is the extractor's job
        # (DESIGN Interfaces/Hub), so the scripted LLM hands over the raw label.
        "hub_id": spec["label"],
        "label": spec["label"],
        "type": spec["type"],
        "evidence_fact_ids": list(fact_ids),
        "doc_id": doc.doc_id,
        "url": doc.url,
        "source_kind": doc.source_kind,
        "published_at": doc.published_at,
    }
    if spec.get("qid"):
        payload["qid"] = spec["qid"]
    return payload


class _ScriptedExtractionLLM:
    """Satisfies `contracts.LLMClient`: returns the scripted facts/hubs for the prompted docs."""

    def __init__(self, docs, facts_by_doc, hubs_by_doc, lenient=False):
        self.docs = list(docs)
        self.facts_by_doc = dict(facts_by_doc)
        self.hubs_by_doc = dict(hubs_by_doc)
        self.lenient = lenient
        self.calls: list[dict] = []
        # Every fact sentence actually delivered to the extractor, across all calls.
        self.delivered_fact_texts: list[str] = []

    async def structured(
        self, *, system: str, user: str, schema, max_tokens: int = 2000, cache_prefix: bool = True
    ):
        self.calls.append({"schema": schema, "system": system, "user": user})
        named = _docs_named_in(self.docs, user)
        if not named:
            raise AssertionError(
                "the extractor asked the scripted LLM about no recognisable document "
                f"(schema={getattr(schema, '__name__', schema)!r}); prompt began: {user[:220]!r}"
            )
        fact_payloads, hub_payloads = [], []
        for doc in named:
            fact_ids = []
            for index, spec in enumerate(self.facts_by_doc.get(doc.doc_id, [])):
                payload = _fact_payload(doc, index, spec)
                fact_ids.append(payload["fact_id"])
                fact_payloads.append(payload)
            for spec in self.hubs_by_doc.get(doc.doc_id, []):
                hub_payloads.append(_hub_payload(doc, spec, fact_ids))
        result = _build_extraction(schema, fact_payloads, hub_payloads, self.lenient)
        self.delivered_fact_texts.extend(_carried_fact_texts(result))
        return result


# --------------------------------------------------------------------------------------
# corpus helpers
# --------------------------------------------------------------------------------------
def _frozen_doc(frozen_fixtures: Path, filename: str):
    from arrival.contracts import RawDoc

    path = frozen_fixtures / "docs" / filename
    assert path.is_file(), f"frozen RawDoc is missing from the corpus: {path}"
    return RawDoc.model_validate_json(path.read_text(encoding="utf-8"))


def _frozen_wikidata_doc(frozen_fixtures: Path):
    from arrival.contracts import RawDoc

    path = frozen_fixtures / "resolve_cases" / "strong-key-wikidata.json"
    assert path.is_file(), f"frozen resolver case is missing from the corpus: {path}"
    case = json.loads(path.read_text(encoding="utf-8"))
    raw = next(d for d in case["docs"] if d["source_kind"] == "wikidata")
    return RawDoc.model_validate(raw)


def _derived_doc(base, url: str, prefix: str, published_at, fetched_at=None):
    """A frozen document re-dated / re-titled, keeping its corpus-owned prose intact."""
    update = {
        "doc_id": hashlib.sha1(url.encode()).hexdigest()[:16],
        "url": url,
        "text": f"{prefix}\n\n{base.text}",
        "published_at": published_at,
    }
    if fetched_at is not None:
        update["fetched_at"] = fetched_at
    return base.model_copy(update=update)


def _run_extract_capturing(docs, accepted_doc_ids, facts_by_doc, hubs_by_doc, lenient=False):
    """Run `extract` with a scripted LLM and return `(facts, hubs, llm)`.

    The scripted LLM comes back so a test can assert what was actually DELIVERED to the
    extractor, not merely what it intended to deliver.
    """
    llm = _ScriptedExtractionLLM(docs, facts_by_doc, hubs_by_doc, lenient=lenient)

    async def _inner():
        from arrival.contracts import PersonRef, Resolution
        from arrival.extract import extract

        person = PersonRef(
            person_id="runa-okonkwo",
            name="Runa Okonkwo",
            details=["Co-founder of Quarrystone Labs", "Austin"],
        )
        resolution = Resolution(
            person_id=person.person_id,
            status="resolved",
            strong_keys={},
            accepted_doc_ids=list(accepted_doc_ids),
            rejected=[],
            confidence=0.91,
        )
        return await extract(person, resolution, docs, llm)

    facts, hubs = asyncio.run(_inner())
    return facts, hubs, llm


def _run_extract(docs, accepted_doc_ids, facts_by_doc, hubs_by_doc):
    """Run `extract` with a scripted LLM and return `(facts, hubs)`."""
    facts, hubs, _llm = _run_extract_capturing(docs, accepted_doc_ids, facts_by_doc, hubs_by_doc)
    return facts, hubs


# --------------------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------------------
def test_fact_with_a_fabricated_quote_is_dropped_and_the_cited_one_survives(frozen_fixtures):
    """SPEC C8 / R9, DESIGN Decision 5: the citation check is the hallucination guard."""
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    fabricated = "Runa Okonkwo was appointed chief executive of Quarrystone Labs in 2021."
    assert _is_quoted_in(_ABOUT_SPAN, doc.text), "fixture pre-condition: the good span is verbatim"
    assert not _is_quoted_in(fabricated, doc.text), "fixture pre-condition: the bad span is invented"

    cited_text = "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team."
    uncited_text = "Runa Okonkwo has been the chief executive of Quarrystone Labs since 2021."
    facts, _hubs = _run_extract(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {"text": cited_text, "quote": _ABOUT_SPAN, "category": "current_work"},
                {"text": uncited_text, "quote": fabricated, "category": "current_work"},
            ]
        },
        {},
    )

    kept = [f.text for f in facts]
    assert uncited_text not in kept, "a fact whose quote is not in its source must be dropped"
    # The other half: asserting only the drop would pass an extractor that drops everything,
    # which shows nothing and satisfies C8 by emitting no facts at all.
    assert cited_text in kept, "the genuinely cited fact must survive the citation check"


def test_citation_check_ignores_whitespace_runs_and_letter_case(frozen_fixtures):
    """DESIGN Decision 5: the substring test runs after `normalize_ws` (collapse + casefold)."""
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    reflowed = "\n   ".join(_ABOUT_SPAN.upper().split())
    word_changed = _ABOUT_SPAN.replace("platform team", "logistics team")
    assert reflowed != _ABOUT_SPAN and _is_quoted_in(reflowed, doc.text)
    assert not _is_quoted_in(word_changed, doc.text), "fixture pre-condition: one word differs"

    reflowed_text = "Runa Okonkwo runs the platform team at Quarrystone Labs."
    word_changed_text = "Runa Okonkwo runs the logistics team at Quarrystone Labs."
    facts, _hubs = _run_extract(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {"text": reflowed_text, "quote": reflowed, "category": "current_work"},
                {"text": word_changed_text, "quote": word_changed, "category": "current_work"},
            ]
        },
        {},
    )

    kept = [f.text for f in facts]
    assert reflowed_text in kept, (
        "a quote differing from its source only in whitespace and case is still verbatim "
        "under normalize_ws and must survive"
    )
    # The sabotage companion: a quote differing by an actual WORD must still be dropped,
    # otherwise 'whitespace-insensitive' has been implemented as 'no check at all'.
    assert word_changed_text not in kept


def test_wikidata_sourced_hub_is_keyed_by_its_qid(frozen_fixtures):
    """T-3 acceptance 3: `hub_id` is `wd:Q…` from Wikidata, else `{type}:{slug(label)}`."""
    wikidata_doc = _frozen_wikidata_doc(frozen_fixtures)
    search_doc = _frozen_doc(frozen_fixtures, _ROADMAP_DOC)
    assert _is_quoted_in(_WIKIDATA_SPAN, wikidata_doc.text)
    assert _is_quoted_in(_ROADMAP_SPAN, search_doc.text)
    assert "Q7314529" in wikidata_doc.text, "fixture pre-condition: the QID is in the document"

    facts, hubs = _run_extract(
        [wikidata_doc, search_doc],
        [wikidata_doc.doc_id, search_doc.doc_id],
        {
            wikidata_doc.doc_id: [
                {
                    "text": "Ilse Vandermolen is a principal engineer at Belmarch Optics.",
                    "quote": _WIKIDATA_SPAN,
                    "category": "current_work",
                }
            ],
            search_doc.doc_id: [
                {
                    "text": "Quarrystone Labs published its platform roadmap to customers.",
                    "quote": _ROADMAP_SPAN,
                    "category": "recent_activity",
                }
            ],
        },
        {
            wikidata_doc.doc_id: [
                {"label": "Belmarch Optics", "type": "company", "qid": "Q7314529"}
            ],
            search_doc.doc_id: [{"label": "Quarrystone Labs", "type": "company"}],
        },
    )
    assert facts, "hubs are only meaningful alongside the facts that evidence them"

    by_label = {h.label: h for h in hubs}
    assert "Belmarch Optics" in by_label, f"wikidata hub missing, got {sorted(by_label)}"
    assert by_label["Belmarch Optics"].hub_id == "wd:Q7314529"
    # The control half: a hub from a non-wikidata document falls back to the slug form, so
    # a run that emitted no hubs at all, or prefixed everything with `wd:`, cannot pass.
    assert "Quarrystone Labs" in by_label, f"non-wikidata hub missing, got {sorted(by_label)}"
    assert by_label["Quarrystone Labs"].hub_id == "company:quarrystone-labs"


def test_one_label_across_two_docs_becomes_one_hub_evidenced_by_both(frozen_fixtures):
    """T-3 acceptance 3: the same label in two documents yields ONE Hub, evidence merged."""
    about = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    roadmap = _frozen_doc(frozen_fixtures, _ROADMAP_DOC)
    assert about.doc_id != roadmap.doc_id
    assert _is_quoted_in(_ABOUT_SPAN, about.text) and _is_quoted_in(_ROADMAP_SPAN, roadmap.text)

    facts, hubs = _run_extract(
        [about, roadmap],
        [about.doc_id, roadmap.doc_id],
        {
            about.doc_id: [
                {
                    "text": "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team.",
                    "quote": _ABOUT_SPAN,
                    "category": "current_work",
                }
            ],
            roadmap.doc_id: [
                {
                    "text": "Quarrystone Labs opened its platform roadmap to customers.",
                    "quote": _ROADMAP_SPAN,
                    "category": "recent_activity",
                }
            ],
        },
        {
            about.doc_id: [{"label": "Quarrystone Labs", "type": "company"}],
            roadmap.doc_id: [{"label": "Quarrystone Labs", "type": "company"}],
        },
    )

    matching = [h for h in hubs if h.hub_id == "company:quarrystone-labs"]
    assert len(matching) == 1, (
        f"one label across two documents must canonicalise to one Hub, got {[h.hub_id for h in hubs]}"
    )
    facts_by_id = {f.fact_id: f for f in facts}
    evidence = list(matching[0].evidence_fact_ids)
    assert evidence, "the merged Hub must name the facts that evidence it"
    unknown = [fid for fid in evidence if fid not in facts_by_id]
    assert not unknown, f"Hub.evidence_fact_ids must resolve to returned facts; dangling: {unknown}"
    cited_docs = {facts_by_id[fid].provenance.doc_id for fid in evidence}
    assert cited_docs == {about.doc_id, roadmap.doc_id}, (
        f"merged evidence must span both source documents, got {sorted(cited_docs)}"
    )


def test_stop_hub_labels_are_never_emitted_as_hubs(frozen_fixtures):
    """DESIGN Decision 3: `{texas, startup, founder, ai, …}` are never nodes in the graph."""
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, doc.text)

    facts, hubs = _run_extract(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {
                    "text": "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team.",
                    "quote": _ABOUT_SPAN,
                    "category": "current_work",
                }
            ]
        },
        {
            doc.doc_id: [
                {"label": "AI", "type": "topic"},
                {"label": "Startup", "type": "topic"},
                {"label": "Texas", "type": "city"},
                {"label": "founder", "type": "topic"},
                {"label": "Quarrystone Labs", "type": "company"},
            ]
        },
    )
    assert facts, "the fact for this document must survive, or the hubs prove nothing"

    emitted = {h.label.strip().casefold() for h in hubs}
    banned = emitted & _STOP_HUBS
    assert not banned, f"stop-hubs were emitted as graph nodes: {sorted(banned)}"
    assert not {h.hub_id for h in hubs} & {"topic:ai", "topic:startup", "city:texas", "topic:founder"}
    # The positive control, in the same batch: a non-stop label MUST still become a hub, so
    # an extractor that emits no hubs at all cannot pass by doing nothing.
    assert "quarrystone labs" in emitted, f"the non-stop label was dropped too, got {sorted(emitted)}"


def test_stop_hub_matching_is_on_labels_not_hub_id_type_prefixes(frozen_fixtures):
    """DESIGN Decision 3: the stop list is matched against hub LABELS, never against the
    `{type}:` prefix of a canonical `hub_id` and never against `Hub.type`.

    Two of the eight stop words — `investor` and `technology` — are also `HubType` values,
    so `investor:foundry-seed-2019` and `technology:developer-platform` both contain a stop
    word in their canonical id while neither is a stop hub. An implementation that filters
    on the id (or on the type) deletes EVERY investor and EVERY technology hub in the graph.
    `investor:foundry-seed-2019` is exactly the rare, high-signal shared node T-5's matching
    score is designed around — losing it degrades the score to generic overlap while
    `test_stop_hub_labels_are_never_emitted_as_hubs` above stays green, because that test
    happens to use only stop labels whose types (`topic`, `city`) are not stop words.

    Discriminating in both directions in one batch: a prefix/type matcher loses the two
    rare hubs, and an extractor with no stop list at all emits the two stop LABELS.
    """
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, doc.text)
    assert _is_quoted_in(_FOUNDRY_SPAN, doc.text)
    # The premise this test rests on, asserted rather than assumed.
    assert {"investor", "technology"} <= _STOP_HUBS

    facts, hubs = _run_extract(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {
                    "text": "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team.",
                    "quote": _ABOUT_SPAN,
                    "category": "current_work",
                },
                {
                    "text": (
                        "Quarrystone Labs took its first outside money from Foundry Seed in 2019."
                    ),
                    "quote": _FOUNDRY_SPAN,
                    "category": "affiliation",
                },
            ]
        },
        {
            doc.doc_id: [
                # Rare, high-signal hubs whose canonical ids CONTAIN a stop word.
                {"label": "Foundry Seed 2019", "type": "investor"},
                {"label": "Developer platform", "type": "technology"},
                # The same two words as LABELS: these are the real stop hubs and must go.
                {"label": "Investor", "type": "topic"},
                {"label": "Technology", "type": "topic"},
            ]
        },
    )
    assert facts, "the facts for this document must survive, or the hubs prove nothing"

    ids = {h.hub_id for h in hubs}
    labels = {h.label.strip().casefold() for h in hubs}
    assert "investor:foundry-seed-2019" in ids, (
        "the `investor:` type prefix is not a stop hub — `investor` is a stop LABEL. "
        f"Dropping this hub guts T-5's matching score; got {sorted(ids)}"
    )
    assert "technology:developer-platform" in ids, (
        "the `technology:` type prefix is not a stop hub — `technology` is a stop LABEL; "
        f"got {sorted(ids)}"
    )
    # And the converse, so an extractor with no stop list cannot pass this test either.
    banned = labels & _STOP_HUBS
    assert not banned, f"stop-hub LABELS were emitted as graph nodes: {sorted(banned)}"
    assert not ids & {"topic:investor", "topic:technology"}, f"got {sorted(ids)}"


def test_non_obvious_is_assigned_only_to_flagged_facts_from_eligible_sources(frozen_fixtures):
    """T-3 acceptance 5 / SPEC R7: `category='non_obvious'` is the extractor's own label.

    Verbatim from TASKS: "Facts from non-obvious-eligible source kinds are labelled
    category='non_obvious' when the LLM flags them as not-bio-page material, else their
    natural category." Both halves of that sentence are graded here, in one batch:

      A. wayback source (eligible), LLM flags it   -> MUST be `non_obvious`
      B. wayback source (eligible), LLM does not   -> MUST NOT be `non_obvious`
      C. self_page source (NOT eligible), LLM flags it -> MUST NOT be `non_obvious`

    C is the discriminator that matters: a subject's own about page IS the first page, so
    "I co-founded Quarrystone Labs in 2016" is obvious biographical filler no matter what
    the model says about it. DESIGN §Data models puts `self_page` outside the eligible set.

    An extractor that labels everything `non_obvious` fails on B and C; one that labels
    nothing fails on A; one that ignores the LLM's flag and labels by source kind alone
    fails on B. Nothing passes by returning a constant.
    """
    status = _frozen_doc(frozen_fixtures, _STATUS_DOC)
    about = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert status.source_kind in _NON_OBVIOUS_ELIGIBLE_SOURCE_KINDS, (
        f"fixture pre-condition: {_STATUS_DOC} must be a non-obvious-eligible source, "
        f"got {status.source_kind!r}"
    )
    assert about.source_kind not in _NON_OBVIOUS_ELIGIBLE_SOURCE_KINDS, (
        f"fixture pre-condition: {_ABOUT_DOC} must NOT be eligible, got {about.source_kind!r}"
    )
    assert _is_quoted_in(_STATUS_SPAN, status.text)
    assert _is_quoted_in(_STATUS_SPAN_2, status.text)
    assert _is_quoted_in(_ABOUT_SPAN, about.text)

    flagged_eligible = "Quarrystone Labs shipped a public status page in 2017, with eleven people."
    unflagged_eligible = "Quarrystone Labs keeps its incident log public on the bad days too."
    flagged_ineligible = (
        "Runa Okonkwo co-founded Quarrystone Labs in 2016 and runs its platform team."
    )
    for text in (flagged_eligible, unflagged_eligible, flagged_ineligible):
        assert len(text) <= 200, "fixture pre-condition: every candidate is inside the cap"

    facts, _hubs = _run_extract(
        [status, about],
        [status.doc_id, about.doc_id],
        {
            status.doc_id: [
                {"text": flagged_eligible, "quote": _STATUS_SPAN, "category": "non_obvious"},
                {
                    "text": unflagged_eligible,
                    "quote": _STATUS_SPAN_2,
                    "category": "recent_activity",
                },
            ],
            about.doc_id: [
                {"text": flagged_ineligible, "quote": _ABOUT_SPAN, "category": "non_obvious"},
            ],
        },
        {},
    )

    by_text = {f.text: f for f in facts}
    # Every candidate is cited, accepted and inside the cap, so all three must survive —
    # otherwise "not labelled non_obvious" could be satisfied by not emitting the fact.
    candidates = (flagged_eligible, unflagged_eligible, flagged_ineligible)
    missing = [t for t in candidates if t not in by_text]
    assert not missing, f"cited, in-cap facts were dropped: {missing}; got {sorted(by_text)}"

    assert by_text[flagged_eligible].category == "non_obvious", (
        "a flagged fact from a non-obvious-eligible source (wayback) is exactly the "
        "'Not on the first page' material R7 exists for; got "
        f"{by_text[flagged_eligible].category!r}"
    )
    assert by_text[unflagged_eligible].category != "non_obvious", (
        "an eligible SOURCE is not enough — the LLM must have flagged the fact; "
        "labelling by source kind alone makes the whole batch non_obvious"
    )
    assert by_text[flagged_ineligible].category != "non_obvious", (
        "obvious biographical filler from the subject's own about page must fall back to "
        "its natural category — `self_page` is not on the non-obvious eligibility list"
    )
    non_obvious = [f.text for f in facts if f.category == "non_obvious"]
    assert non_obvious == [flagged_eligible], (
        f"exactly one candidate here warrants the label; got {non_obvious}"
    )
    # And the label must not be bought by breaking provenance: it stays on its own source.
    assert by_text[flagged_eligible].provenance.doc_id == status.doc_id
    assert by_text[flagged_eligible].provenance.source_kind == status.source_kind


def test_hub_recency_follows_the_age_of_its_source_document(frozen_fixtures):
    """T-3 acceptance 4: 1.0 within 12 months, 0.6 within 3 years, 0.3 older, 0.5 unknown.

    Ages are computed from the clock at run time and the derived documents are fetched
    "now", so the expected band never rots and holds whether the implementation measures
    age against today or against the document's own `fetched_at`. No wall-clock value is
    asserted — only the band each age must land in.
    """
    base = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, base.text)
    now = datetime.now(timezone.utc)
    today = date.today()
    bands = [
        ("Harbourline Systems", today - timedelta(days=60), 1.0),
        ("Pellworth Optics", today - timedelta(days=700), 0.6),
        ("Kestrel Yards", today - timedelta(days=2200), 0.3),
        ("Norrey Freight", None, 0.5),
    ]

    docs, facts_by_doc, hubs_by_doc, expected = [], {}, {}, {}
    for label, published_at, recency in bands:
        slug = label.lower().replace(" ", "-")
        doc = _derived_doc(
            base,
            f"https://example.org/frozen-acceptance/t3-recency/{slug}",
            f"Dispatch note about {label}.",
            published_at,
            fetched_at=now,
        )
        docs.append(doc)
        facts_by_doc[doc.doc_id] = [
            {
                "text": f"Runa Okonkwo has worked with {label} on platform tooling.",
                "quote": _ABOUT_SPAN,
                "category": "affiliation",
            }
        ]
        hubs_by_doc[doc.doc_id] = [{"label": label, "type": "company"}]
        expected[label] = recency

    _facts, hubs = _run_extract(docs, [d.doc_id for d in docs], facts_by_doc, hubs_by_doc)

    by_label = {h.label: h for h in hubs}
    missing = [label for label in expected if label not in by_label]
    assert not missing, f"hubs missing for {missing}; got {sorted(by_label)}"
    actual = {label: by_label[label].recency for label in expected}
    assert actual == pytest.approx(expected), (
        f"recency must be derived from published_at age bands; expected {expected}, got {actual}"
    )


def test_no_surviving_fact_exceeds_two_hundred_characters(frozen_fixtures):
    """DESIGN Interfaces/Fact: `text` is at most 200 chars — the extractor must enforce it.

    The cap is asserted DIRECTLY on `extract`'s own return value, and the over-length
    candidate is delivered with `lenient=True` so that delivery does not depend on the
    implementation's internal extraction schema being permissive. Grading the contract
    through the gradee's own schema measures the schema, not the extractor: if the
    implementation pins `max_length=200` there, strict construction raises inside the
    scripted LLM and a CORRECT implementation is graded as a failure; if it pins nothing,
    the probe lands. Neither outcome may be left to chance.
    """
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, doc.text)
    short_text = "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team."
    long_text = (
        "Runa Okonkwo argues at considerable length that a pricing page is a moral document, "
        "that documentation is part of the product, that support is part of the product, and "
        "that a company selling to engineers is selling to people who read whatever it ships."
    )
    assert len(long_text) > 200, "fixture pre-condition: the long fact must breach the cap"
    assert len(short_text) <= 200, "fixture pre-condition: the control fact is inside the cap"

    facts, _hubs, llm = _run_extract_capturing(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {"text": short_text, "quote": _ABOUT_SPAN, "category": "current_work"},
                {"text": long_text, "quote": _ABOUT_SPAN, "category": "interest"},
            ]
        },
        {},
        lenient=True,
    )

    # Probe pre-condition, asserted rather than assumed: the extractor really was handed
    # an over-length candidate. Skipped only when the implementation's fact model names
    # its sentence field something no alias covers — in which case `short_text` below
    # fails anyway, so the test still cannot pass by mis-delivering.
    if llm.delivered_fact_texts:
        assert any(len(t) > 200 for t in llm.delivered_fact_texts), (
            "the over-length candidate never reached the extractor — it was truncated or "
            f"dropped on the way in, so this test measured nothing; delivered lengths: "
            f"{sorted(len(t) for t in llm.delivered_fact_texts)}"
        )

    oversized = [f.text for f in facts if len(f.text) > 200]
    assert not oversized, f"facts over the 200-char cap were emitted: {[len(t) for t in oversized]}"
    # The other half: dropping or truncating the long fact is fine, dropping EVERYTHING is not.
    assert short_text in [f.text for f in facts], "the compliant fact must still survive"


def test_every_surviving_fact_cites_an_accepted_document(frozen_fixtures):
    """SPEC R9 / S6 and T-3 acceptance 1: facts come only from resolution-accepted docs."""
    accepted = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    rejected = _frozen_doc(frozen_fixtures, _ROADMAP_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, accepted.text)
    assert _is_quoted_in(_ROADMAP_SPAN, rejected.text)

    from_accepted = "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team."
    from_rejected = "Quarrystone Labs opened its platform roadmap to customers."
    facts, _hubs = _run_extract(
        [accepted, rejected],
        [accepted.doc_id],  # the second document was NOT accepted by the resolver
        {
            accepted.doc_id: [
                {"text": from_accepted, "quote": _ABOUT_SPAN, "category": "current_work"}
            ],
            rejected.doc_id: [
                {"text": from_rejected, "quote": _ROADMAP_SPAN, "category": "recent_activity"}
            ],
        },
        {},
    )

    cited_docs = {f.provenance.doc_id for f in facts}
    assert rejected.doc_id not in cited_docs, (
        "a fact sourced from a document the resolver did not accept must never be emitted"
    )
    assert cited_docs == {accepted.doc_id}, f"unexpected source documents: {sorted(cited_docs)}"
    # The other half: an extractor that returns nothing also has no unaccepted citations.
    assert from_accepted in [f.text for f in facts], "the accepted document's fact must survive"
    for fact in facts:
        assert fact.provenance.url == accepted.url
        assert fact.provenance.source_kind == accepted.source_kind
