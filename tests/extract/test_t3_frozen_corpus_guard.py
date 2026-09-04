"""The citation guard, graded against documents this ticket CANNOT write.

Every other module in `tests/extract/` quotes `t3_corpus.py`, which this ticket owns. That
is fine for INPUT and useless as an ANSWER KEY: a guard graded only against prose its own
author chose can be green while measuring nothing. So this module reads the
orchestrator-owned corpus under `.swarm-loop/acceptance/fixtures/docs/` — 23 `RawDoc`s
that are frozen, hash-locked and outside every ticket's write scope — and derives every
expectation from THOSE documents at runtime. Nothing here is a value that was typed in and
could have been pasted from an output:

* the good span is sliced out of the document's own text;
* the bad span is that slice with one word replaced;
* the typographic variant is that slice with `'` and `-` swapped for their Unicode
  cousins, and the expected repair is the document's original characters.

`t3_corpus.py`'s docstring argues the other way — that reading the frozen corpus breaks
when the orchestrator re-cuts it. That is true and it is the smaller risk: a re-cut turns
these into a loud red or a stated skip, whereas a self-graded guard fails silently and
forever. Both modules exist on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import t3_corpus as corpus

from arrival.contracts import RawDoc
from arrival.extract import (
    MIN_QUOTE_WORDS,
    CandidateFact,
    ExtractionResult,
    ExtractionStats,
    cited_span,
    extract,
    is_cited,
)
from arrival.util import normalize_ws
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")

_FROZEN_DOCS = Path(__file__).resolve().parents[2] / ".swarm-loop/acceptance/fixtures/docs"


def _frozen_docs() -> list[RawDoc]:
    if not _FROZEN_DOCS.is_dir():
        pytest.skip(f"the orchestrator-owned corpus is not in this tree: {_FROZEN_DOCS}")
    paths = sorted(_FROZEN_DOCS.glob("*.json"))
    # A present-but-empty corpus is a RED, never a skip: that is exactly the shape a
    # vanished answer key takes, and skipping on it is how a gate stops measuring.
    assert paths, f"the corpus directory exists but holds no documents: {_FROZEN_DOCS}"
    return [RawDoc.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in paths]


@pytest.fixture(scope="module")
def frozen():
    return _frozen_docs()


def _longest_sentence(doc: RawDoc) -> str | None:
    """A real span of this document, chosen by the document rather than by the author."""
    best: str | None = None
    for chunk in doc.text.replace("\n", " ").split(". "):
        candidate = " ".join(chunk.split()).strip()
        if len(candidate.split()) < MIN_QUOTE_WORDS + 2:
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


def _spans(docs: list[RawDoc]) -> list[tuple[RawDoc, str]]:
    out = [(doc, span) for doc in docs if (span := _longest_sentence(doc))]
    assert len(out) >= 10, f"the corpus yielded only {len(out)} usable spans"
    return out


def test_every_frozen_document_cites_its_own_longest_sentence(frozen):
    for doc, span in _spans(frozen):
        assert is_cited(span, doc), f"{doc.doc_id}: its own sentence is not cited: {span!r}"
        recovered = cited_span(span, doc)
        assert recovered is not None
        assert " ".join(recovered.split()) == span, (
            f"{doc.doc_id}: cited_span returned {recovered!r}, not the document's own span"
        )
        assert recovered in doc.text, "what comes back must be a literal slice of the source"


def test_one_word_changed_is_refused_by_every_frozen_document(frozen):
    """The half that proves the guard is not simply saying yes."""
    checked = 0
    for doc, span in _spans(frozen):
        words = span.split()
        for position in (0, len(words) // 2, len(words) - 1):
            altered = list(words)
            altered[position] = "trebuchet"
            forgery = " ".join(altered)
            if normalize_ws(forgery) in normalize_ws(doc.text):  # pragma: no cover
                continue  # the corpus really contains it; not a forgery after all
            assert not is_cited(forgery, doc), f"{doc.doc_id}: accepted a one-word forgery"
            assert cited_span(forgery, doc) is None
            checked += 1
    assert checked >= 30, f"only {checked} forgeries were exercised"


def test_a_typographic_retyping_of_a_frozen_span_is_the_same_span(frozen):
    """T-015 hole 3, graded on prose the ticket cannot edit to make it pass."""
    exercised = 0
    for doc, span in _spans(frozen):
        retyped = span.replace("'", "’").replace("-", "–")
        if retyped == span:
            continue
        exercised += 1
        assert is_cited(retyped, doc), (
            f"{doc.doc_id}: a curly apostrophe or en dash dropped a real span: {retyped!r}"
        )
        recovered = cited_span(retyped, doc)
        assert recovered is not None
        assert "’" not in recovered and "–" not in recovered, (
            "the DOCUMENT's characters come back, not the model's typography"
        )
        assert " ".join(recovered.split()) == span
    assert exercised >= 1, (
        "no frozen document's longest sentence carried an apostrophe or a hyphen, so this "
        "test measured nothing; the corpus changed shape"
    )


def test_a_fragment_of_a_frozen_document_is_below_the_evidence_floor(frozen):
    """T-015 hole 1: real words of the document, too few of them to identify anything."""
    checked = 0
    for doc, span in _spans(frozen):
        words = span.split()
        for size in (1, 2):
            fragment = " ".join(words[:size])
            assert not is_cited(fragment, doc), (
                f"{doc.doc_id}: a {size}-word fragment {fragment!r} was accepted as evidence"
            )
            checked += 1
        # And a fragment cut mid-word, which is not a word of the document at all.
        head = span[: max(6, len(span) // 2)]
        if head and head[-1].isalnum() and span[len(head) : len(head) + 1].isalnum():
            assert not is_cited(head, doc), f"{doc.doc_id}: accepted a span cut mid-word"
            checked += 1
    assert checked >= 40, f"only {checked} fragments were exercised"


def test_a_span_of_one_frozen_document_does_not_cite_the_others(frozen):
    """The guard certifies THIS document, not "somewhere in the corpus"."""
    spans = _spans(frozen)
    crossed = 0
    for doc, span in spans:
        for other, _ in spans:
            if other.doc_id == doc.doc_id:
                continue
            if normalize_ws(span) in normalize_ws(other.text):
                continue  # the corpus genuinely repeats it; not a cross-document claim
            assert not is_cited(span, other), (
                f"{doc.doc_id}'s span was accepted as a citation of {other.doc_id}"
            )
            crossed += 1
    assert crossed >= 100, f"only {crossed} cross-document pairs were exercised"


async def test_extract_keeps_the_frozen_span_and_drops_the_forgery_beside_it(frozen):
    """End to end, with both halves, on documents this ticket cannot write."""
    doc, span = _spans(frozen)[0]
    forgery = "Runa Okonkwo was appointed chief executive of Quarrystone Labs in 2021."
    assert normalize_ws(forgery) not in normalize_ws(doc.text)

    llm = LLMDouble()
    llm.queue(
        ExtractionResult(
            facts=[
                CandidateFact(doc_id=doc.doc_id, text="A genuinely cited claim.", quote=span),
                CandidateFact(doc_id=doc.doc_id, text="An invented claim.", quote=forgery),
            ]
        )
    )
    stats = ExtractionStats()
    facts, _hubs = await extract(
        corpus.PERSON, corpus.resolution_for(doc), [doc], llm, stats=stats
    )

    assert [f.text for f in facts] == ["A genuinely cited claim."]
    assert stats.dropped_uncited == 1
    assert facts[0].provenance.doc_id == doc.doc_id
    assert facts[0].provenance.url == doc.url
    assert facts[0].provenance.source_kind == doc.source_kind
    assert facts[0].provenance.published_at == doc.published_at


async def test_every_quote_extract_emits_is_verbatim_in_its_frozen_source(frozen):
    """`contracts.Provenance`'s pinned invariant, over the whole orchestrator corpus.

    Each document is offered its own span retyped with typographic punctuation, so the
    tolerant path is the one under test — and the stored quote must STILL satisfy
    `normalize_ws(quote) in normalize_ws(doc.text)`.
    """
    checked = 0
    for doc, span in _spans(frozen):
        retyped = span.replace("'", "’").replace("-", "–")
        llm = LLMDouble()
        llm.queue(
            ExtractionResult(
                facts=[CandidateFact(doc_id=doc.doc_id, text="A claim.", quote=retyped)]
            )
        )
        facts, _hubs = await extract(
            corpus.PERSON, corpus.resolution_for(doc), [doc], llm
        )
        assert len(facts) == 1, f"{doc.doc_id}: a retyped span of the document was dropped"
        quote = facts[0].provenance.quote
        assert normalize_ws(quote) in normalize_ws(doc.text), (
            f"{doc.doc_id}: stored a quote its source does not contain: {quote!r}"
        )
        checked += 1
    assert checked >= 10


async def test_the_prompt_carries_the_frozen_documents_own_text(frozen):
    """T-012, graded on prose this ticket cannot write.

    `test_t3_prompt_and_counters.py` asserts the same thing against documents built by
    `t3_corpus.py`, which this ticket owns. The property is an identity between the objects
    handed IN and the prompt handed OUT, so the answer key is the input itself either way —
    but running it over the orchestrator's corpus removes the last thing an author could
    have shaped. `_document_block` -> `return ""` fails here as well.
    """
    batch = frozen[:3]
    llm = LLMDouble()
    llm.queue(ExtractionResult(facts=[]))
    await extract(corpus.PERSON, corpus.resolution_for(*batch), list(batch), llm)

    assert llm.call_count == 1
    prompt = llm.calls[0].user
    for doc in batch:
        assert doc.text in prompt, f"the model was never shown {doc.doc_id}'s frozen text"
        assert doc.doc_id in prompt and doc.url in prompt
    # And a document NOT in this batch is not smuggled in.
    for doc in frozen[3:]:
        assert doc.text not in prompt
