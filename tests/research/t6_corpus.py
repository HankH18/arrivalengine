"""Shared, entirely fictional fixtures for T-6's pipeline tests.

Nobody here is real. The documents are built so that every stage downstream can be driven
honestly rather than stubbed around:

* every document really contains the sentences the scripted verdicts and facts quote, so
  `resolve`'s citation check and `extract`'s citation guard both run for real;
* the disambiguators alternate between an EMPLOYER attribute and a CITY attribute, so
  DESIGN Decision 4's "two independent attributes" arm is reachable without a strong key;
* one document carries a sentence the taste rules exclude DETERMINISTICALLY
  (`home_or_property`), so "at least one excluded fact" can be asserted without the test
  depending on an LLM ruling.

Not a test module: no `test_` prefix, so pytest imports it only when a test asks for it.
The basename is ticket-prefixed like every module under `tests/` (`t3_corpus.py`,
`t4_corpus.py` are the neighbours) because two same-named modules anywhere in this tree are
a hard collection error.
"""

from __future__ import annotations

import datetime as dt

from arrival.contracts import PersonRef, RawDoc, SourceKind
from arrival.extract import CandidateFact, CandidateHub, ExtractionResult
from arrival.resolve import DocVerdict
from arrival.util import doc_id as url_doc_id

__all__ = [
    "CITY",
    "EMPLOYER",
    "FETCHED_AT",
    "OPEN_SOURCE",
    "OTHER",
    "PERSON",
    "PRIVATE",
    "PUBLISHED_AT",
    "docs_for",
    "extraction_result",
    "script_extraction",
    "script_verdicts",
]

PERSON = PersonRef(
    person_id="marisol-trevino",
    name="Marisol Trevino",
    details=["platform lead, Quarrystone Labs", "Austin"],
)
OTHER = PersonRef(
    person_id="anselm-kettleby",
    name="Anselm Kettleby",
    details=["co-founder, Quarrystone Labs", "Austin"],
)

PUBLISHED_AT = dt.date(2026, 1, 5)
FETCHED_AT = dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.UTC)

#: Sentences that appear verbatim in every document `docs_for` builds.
EMPLOYER = "leads the platform team at Quarrystone Labs"
CITY = "has lived in Austin since 2014"
OPEN_SOURCE = "Quarrystone Labs has published its build tooling under an open licence"
#: Excluded by `taste.rule_verdict` as `home_or_property`, deterministically and with no
#: LLM call, which is what makes "the dossier holds an excluded fact" cheap to assert.
PRIVATE = "bought a four-bedroom house on Pecan Street for 2.4 million dollars"


def _text(person: PersonRef, private: bool) -> str:
    body = (
        f"{person.name} {EMPLOYER}. {person.name} {CITY}. {OPEN_SOURCE}, "
        "and the platform team writes a short public note whenever the command line "
        "tool changes."
    )
    if private:
        body = f"{body} {person.name} {PRIVATE}."
    return body


def docs_for(
    kind: SourceKind,
    count: int,
    *,
    person: PersonRef = PERSON,
    private_index: int | None = None,
) -> list[RawDoc]:
    """`count` documents of `kind` about `person`, urls unique per (person, kind, index).

    `private_index` puts the deterministically-excluded sentence in exactly one document.
    """
    docs: list[RawDoc] = []
    for index in range(count):
        url = f"https://example.test/{person.person_id}/{kind}/{index}"
        docs.append(
            RawDoc(
                doc_id=url_doc_id(url),
                source_kind=kind,
                url=url,
                title=f"{person.name} — {kind} record {index}",
                text=_text(person, private=index == private_index),
                published_at=PUBLISHED_AT,
                fetched_at=FETCHED_AT,
            )
        )
    return docs


def script_verdicts(
    llm,
    docs,
    *,
    person: PersonRef = PERSON,
    match: str = "yes",
    confidence: float = 0.9,
):
    """Script one `DocVerdict` per document, alternating the disambiguating ATTRIBUTE.

    Keyed on the document's own id, which `resolve.verdict_prompt` writes into the prompt,
    so the script cannot drift out of step with the order the resolver judges in.
    """
    for index, doc in enumerate(docs):
        employer = index % 2 == 0
        llm.when(
            "DocVerdict",
            doc.doc_id,
            DocVerdict(
                doc_id=doc.doc_id,
                match=match,
                confidence=confidence,
                evidence=f"{person.name} {EMPLOYER}" if employer else f"{person.name} {CITY}",
                disambiguator="employer" if employer else "city",
            ),
        )
    return llm


def extraction_result(docs, *, person: PersonRef = PERSON) -> ExtractionResult:
    """One kept fact per document, one excluded fact where the private sentence is, one hub.

    Every quote is verbatim in the document it names, so nothing here relies on the
    citation guard being lenient.
    """
    facts: list[CandidateFact] = []
    for index, doc in enumerate(docs):
        facts.append(
            CandidateFact(
                doc_id=doc.doc_id,
                fact_id=f"c{index}",
                text=f"{person.name} {EMPLOYER}.",
                quote=f"{person.name} {EMPLOYER}",
                category="current_work",
                natural_category="current_work",
                confidence=0.9,
            )
        )
        if PRIVATE in doc.text:
            facts.append(
                CandidateFact(
                    doc_id=doc.doc_id,
                    fact_id=f"p{index}",
                    text=f"{person.name} {PRIVATE}.",
                    quote=f"{person.name} {PRIVATE}",
                    category="hook",
                    natural_category="hook",
                    confidence=0.9,
                )
            )
    hubs = [
        CandidateHub(
            label="Quarrystone Labs",
            type="company",
            doc_id=docs[0].doc_id if docs else "",
            evidence_fact_ids=["c0"],
        )
    ]
    return ExtractionResult(facts=facts, hubs=hubs)


def script_extraction(llm, docs, *, person: PersonRef = PERSON):
    """Answer every extraction call about `docs` with facts drawn from those documents.

    Registered per document id so a batched extractor (`MAX_DOCS_PER_CALL`) still gets an
    answer whose facts belong to the batch it actually asked about: the first rule whose
    key appears in the prompt wins, and a batch's prompt names only its own documents.
    """
    for doc in docs:
        llm.when("ExtractionResult", doc.doc_id, extraction_result([doc], person=person))
    return llm
