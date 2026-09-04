"""FROZEN acceptance tests for ticket T-3 — fact/hub extraction and the citation guard.

Graded requirements: SPEC R9 (every displayed fact carries a verbatim quote), SPEC C8
(a fact with no verbatim quote in its source is dropped, not shown), SPEC S6, and
DESIGN Decision 5 (the citation check is mechanical) and Decision 3 (stop-hubs, hub
canonicalisation, recency).

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

pytestmark = pytest.mark.t3

# Frozen documents used as source text. Both are committed RawDoc dumps.
_ABOUT_DOC = "35b4e2600c8a6ea6.json"  # self_page — Runa Okonkwo's own about page
_ROADMAP_DOC = "92b1d32390d8795f.json"  # search — trade-press piece on the same company

# Verbatim spans of those documents. Every test asserts the pre-condition before using them.
_ABOUT_SPAN = "I co-founded Quarrystone Labs in 2016 and I run the platform team there"
_ROADMAP_SPAN = "Quarrystone Labs opened its platform team roadmap to customers this month"
_WIKIDATA_SPAN = "Employer: Belmarch Optics. Work location: Rotterdam."

# DESIGN Decision 3, verbatim: never nodes, after lowercasing.
_STOP_HUBS = {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}


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


def _build_extraction(schema, fact_payloads, hub_payloads):
    """Build whatever extraction schema the implementation asked for."""
    fields = getattr(schema, "model_fields", None)
    if fields is None:
        raise AssertionError(f"scripted LLM was handed a non-pydantic schema: {schema!r}")
    list_fields = {n: _list_item_model(f.annotation) for n, f in fields.items()}
    if not any(list_fields.values()):
        if fact_payloads:
            return _fill(schema, fact_payloads[0], _FACT_ALIASES)
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
                kwargs[name] = [_fill(item_model, p, _FACT_ALIASES) for p in fact_payloads]
            elif role == "hub":
                kwargs[name] = [_fill(item_model, p, _HUB_ALIASES) for p in hub_payloads]
            else:
                kwargs[name] = []
        elif field.is_required():
            annotation = _unwrap_optional(field.annotation)
            if hasattr(annotation, "model_fields") and fact_payloads:
                kwargs[name] = _fill(annotation, fact_payloads[0], _FACT_ALIASES)
            else:
                kwargs[name] = _default_for(annotation)
    return schema(**kwargs)


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

    def __init__(self, docs, facts_by_doc, hubs_by_doc):
        self.docs = list(docs)
        self.facts_by_doc = dict(facts_by_doc)
        self.hubs_by_doc = dict(hubs_by_doc)
        self.calls: list[dict] = []

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
        return _build_extraction(schema, fact_payloads, hub_payloads)


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


def _run_extract(docs, accepted_doc_ids, facts_by_doc, hubs_by_doc):
    """Run `extract` with a scripted LLM and return `(facts, hubs)`."""

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
        llm = _ScriptedExtractionLLM(docs, facts_by_doc, hubs_by_doc)
        return await extract(person, resolution, docs, llm)

    return asyncio.run(_inner())


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
    """DESIGN Interfaces/Fact: `text` is at most 200 chars — the extractor must enforce it."""
    doc = _frozen_doc(frozen_fixtures, _ABOUT_DOC)
    assert _is_quoted_in(_ABOUT_SPAN, doc.text)
    short_text = "Runa Okonkwo co-founded Quarrystone Labs and runs its platform team."
    long_text = (
        "Runa Okonkwo argues at considerable length that a pricing page is a moral document, "
        "that documentation is part of the product, that support is part of the product, and "
        "that a company selling to engineers is selling to people who read whatever it ships."
    )
    assert len(long_text) > 200, "fixture pre-condition: the long fact must breach the cap"

    facts, _hubs = _run_extract(
        [doc],
        [doc.doc_id],
        {
            doc.doc_id: [
                {"text": short_text, "quote": _ABOUT_SPAN, "category": "current_work"},
                {"text": long_text, "quote": _ABOUT_SPAN, "category": "interest"},
            ]
        },
        {},
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
