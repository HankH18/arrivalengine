"""T-2b: is DESIGN Decision 4's negative evidence consulted by the PIPELINE at all?

`test_negative_evidence_vetoes` in `test_t2_resolve_rules.py` grades
`negative_evidence_veto` DIRECTLY, so it stays green whether or not `resolve()` ever
reaches the function's positive branch — and for a while it did not. The caller asked
`verdict.match == "yes" and not negative_evidence_veto(...)` while the callee returned
`False` on anything that was not a `no`, two conditions that can never both hold, so the
veto's answer had no effect on any resolution the product ever produced.

This module asks the question from the other end, through `resolve()`:

1. the pipeline really does put `no` verdicts through the veto (`monkeypatch` spy — the
   only handle there is, because a vetoed `no` and an ordinary `no` are both rejected and
   the outcome alone cannot tell them apart), and
2. the half of the veto that IS outcome-bearing works: an accepted document whose own
   evidence names another employer or another work location cannot anchor a strong key.

(2) is the real product hole this ticket found. A resolver that accepts a `yes` on a
Wikidata item asserting `Employer: <somebody else>` and then takes that item's QID has
written a durable, wrong identifier for a different human being with the same name —
SPEC S4's failure arriving through a `yes` instead of through a `no`.
"""

from __future__ import annotations

import datetime as dt

import pytest

import arrival.resolve as resolve_module
from arrival.contracts import PersonRef, RawDoc, Verdict
from arrival.resolve import (
    DocVerdict,
    conflicting_identity_claim,
    negative_evidence_veto,
    resolve,
    strong_keys_for,
)
from arrival.util import doc_id as make_doc_id
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 2, 18, 9, 30, tzinfo=dt.UTC)

PERSON = PersonRef(
    person_id="nessa-ravenhill",
    name="Nessa Ravenhill",
    details=["Principal engineer at Wexmoor Optics", "Trondheim"],
)


def doc(kind: str, url: str, text: str, title: str = "") -> RawDoc:
    return RawDoc(
        doc_id=make_doc_id(url),
        source_kind=kind,
        url=url,
        title=title,
        text=text,
        fetched_at=FETCHED,
    )


def verdict(doc_: RawDoc, match: str, confidence: float, evidence: str, disambiguator: str):
    return DocVerdict(
        doc_id=doc_.doc_id,
        match=match,
        confidence=confidence,
        evidence=evidence,
        disambiguator=disambiguator,
    )


def scripted(*pairs) -> LLMDouble:
    llm = LLMDouble()
    for doc_, answer in pairs:
        llm.when("DocVerdict", doc_.doc_id, answer)
    return llm


# Third-party hosts throughout: a document on the employer's own domain would be a
# `company_domain` strong key and would resolve these cases without any verdict at all.
EMPLOYER_DOC = doc(
    "self_page",
    "https://optics-directory.example/wexmoor-optics/people",
    "Nessa Ravenhill is a principal engineer at Wexmoor Optics and has led the coatings "
    "group since 2019.",
)
CITY_DOC = doc(
    "search",
    "https://coatings-notes.example/2025/anti-reflective",
    "Nessa Ravenhill has worked out of Trondheim since 2014 and says the fjord humidity is "
    "a coating problem.",
)
# The SPEC S4 decoy in the one form the strong-key rule cannot see past: a Wikidata item
# matching the NAME and one DETAIL (her papers are archived in Trondheim) while asserting
# an employer and a work location that belong to somebody else entirely.
FOREIGN_ITEM = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000914",
    "Nessa Ravenhill (Q900000914)\n\nItem mirror. Instance of: human. Employer: Calderhall "
    "Institute. Work location: Perpignan. Her working papers are archived in Trondheim.",
    title="Nessa Ravenhill (Q900000914) - item mirror",
)
FOREIGN_SPAN = "Employer: Calderhall Institute. Work location: Perpignan."

# The same item, same shape, same QID position — asserting HER employer and HER city. The
# control half: without it, a resolver that never takes a strong key passes everything
# below.
OWN_ITEM = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000915",
    "Nessa Ravenhill (Q900000915)\n\nItem mirror. Instance of: human. Employer: Wexmoor "
    "Optics. Work location: Trondheim.",
    title="Nessa Ravenhill (Q900000915) - item mirror",
)
OWN_SPAN = "Employer: Wexmoor Optics. Work location: Trondheim."


# ------------------------------------------------------- the veto really is reached


async def test_resolve_puts_no_verdicts_through_the_veto(monkeypatch):
    """The wiring assertion: `resolve()` reaches the veto's POSITIVE branch.

    There is no outcome that separates a vetoed `no` from an ordinary `no` — both are
    rejected — so this is the only way to observe the wiring, and the wiring is the whole
    defect. With the old guard the spy recorded `yes` verdicts and nothing else.
    """
    real = resolve_module.negative_evidence_veto
    seen: list[tuple[str, bool]] = []

    def spy(person, verdict_):
        answer = real(person, verdict_)
        seen.append((verdict_.match, answer))
        return answer

    monkeypatch.setattr(resolve_module, "negative_evidence_veto", spy)

    llm = scripted(
        (
            EMPLOYER_DOC,
            verdict(
                EMPLOYER_DOC, "yes", 0.7,
                "principal engineer at Wexmoor Optics", "employer",
            ),
        ),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Trondheim since 2014", "city")),
        (FOREIGN_ITEM, verdict(FOREIGN_ITEM, "no", 0.96, FOREIGN_SPAN, "employer")),
    )
    await resolve(PERSON, [EMPLOYER_DOC, CITY_DOC, FOREIGN_ITEM], llm)

    assert ("no", True) in seen, (
        "resolve() never reached the veto's positive branch: it saw "
        f"{seen}. DESIGN Decision 4's hard reject is not consulted by the pipeline, "
        "however green the direct unit test of the function is."
    )


async def test_a_vetoed_document_is_rejected_and_keeps_its_verdict_for_debug():
    llm = scripted(
        (
            EMPLOYER_DOC,
            verdict(
                EMPLOYER_DOC, "yes", 0.7,
                "principal engineer at Wexmoor Optics", "employer",
            ),
        ),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Trondheim since 2014", "city")),
        (FOREIGN_ITEM, verdict(FOREIGN_ITEM, "no", 0.96, FOREIGN_SPAN, "employer")),
    )
    resolution = await resolve(PERSON, [EMPLOYER_DOC, CITY_DOC, FOREIGN_ITEM], llm)

    assert resolution.status == "resolved"
    assert FOREIGN_ITEM.doc_id not in resolution.accepted_doc_ids
    assert FOREIGN_ITEM.doc_id in {v.doc_id for v in resolution.rejected}
    assert dict(resolution.strong_keys) == {}
    assert resolution.confidence == pytest.approx(0.7), (
        "the vetoed document's 0.96 must not touch the resolution's confidence"
    )


# ------------------------------------------ a conflicting claim cannot anchor a key


def test_the_foreign_item_would_key_if_nothing_stopped_it():
    """Fixture pre-condition: this document matches name + city, so the QID rule takes it."""
    assert strong_keys_for(PERSON, [FOREIGN_ITEM]) == {"wikidata_qid": "Q900000914"}


def test_conflicting_identity_claim_reads_the_evidence_not_the_polarity():
    for match in ("yes", "no", "unsure"):
        foreign = Verdict(
            doc_id=FOREIGN_ITEM.doc_id,
            match=match,
            confidence=0.9,
            evidence=FOREIGN_SPAN,
            disambiguator="employer",
        )
        assert conflicting_identity_claim(PERSON, foreign) is True, match
        own = Verdict(
            doc_id=OWN_ITEM.doc_id,
            match=match,
            confidence=0.9,
            evidence=OWN_SPAN,
            disambiguator="employer",
        )
        assert conflicting_identity_claim(PERSON, own) is False, match

    # Silence is not a conflict. `strong-key-sec-cik` in this ticket's own corpus wins on
    # exactly this evidence, and calling it a contradiction throws away an earned CIK.
    silent = Verdict(
        doc_id="e" * 16,
        match="yes",
        confidence=0.8,
        evidence="Relationship of reporting person to issuer: Officer. Title: Chief "
        "Financial Officer.",
        disambiguator="employer",
    )
    assert conflicting_identity_claim(PERSON, silent) is False

    # An explicitly UNSET field asserts nothing either — that is the frozen
    # `strong-key-refused-github-unconfirmed` shape, run-together on one line.
    unset = Verdict(
        doc_id="f" * 16,
        match="yes",
        confidence=0.8,
        evidence="Name: Nessa Ravenhill Company: not set Location: Trondheim",
        disambiguator="city",
    )
    assert conflicting_identity_claim(PERSON, unset) is False


def test_the_veto_and_the_key_rule_ask_different_questions():
    """`negative_evidence_veto` is polarity-gated; the key rule is not. Both are needed."""
    foreign_yes = Verdict(
        doc_id=FOREIGN_ITEM.doc_id,
        match="yes",
        confidence=0.96,
        evidence=FOREIGN_SPAN,
        disambiguator="employer",
    )
    assert negative_evidence_veto(PERSON, foreign_yes) is False
    assert conflicting_identity_claim(PERSON, foreign_yes) is True


async def test_an_accepted_document_asserting_another_employer_earns_no_strong_key():
    """The product hole: a `yes` on somebody else's item must not anchor a durable id."""
    llm = scripted(
        (
            EMPLOYER_DOC,
            verdict(
                EMPLOYER_DOC, "yes", 0.7,
                "principal engineer at Wexmoor Optics", "employer",
            ),
        ),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Trondheim since 2014", "city")),
        (FOREIGN_ITEM, verdict(FOREIGN_ITEM, "yes", 0.96, FOREIGN_SPAN, "employer")),
    )
    resolution = await resolve(PERSON, [EMPLOYER_DOC, CITY_DOC, FOREIGN_ITEM], llm)

    assert "wikidata_qid" not in resolution.strong_keys, (
        "Q900000914 asserts Employer: Calderhall Institute and Work location: Perpignan, "
        "so it identifies a different human being with the same name; a `yes` verdict on "
        "it accepts the document but must never make its QID this person's strong key"
    )
    assert dict(resolution.strong_keys) == {}
    # The document itself IS accepted. Refusing an unusable key is not the same as
    # refusing the document, and the frozen suite grades that distinction directly.
    assert FOREIGN_ITEM.doc_id in resolution.accepted_doc_ids


async def test_the_same_item_asserting_her_own_employer_still_earns_the_key():
    """Sabotage companion: without this half, a resolver that never keys passes above."""
    llm = scripted(
        (
            EMPLOYER_DOC,
            verdict(
                EMPLOYER_DOC, "yes", 0.7,
                "principal engineer at Wexmoor Optics", "employer",
            ),
        ),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Trondheim since 2014", "city")),
        (OWN_ITEM, verdict(OWN_ITEM, "yes", 0.96, OWN_SPAN, "employer")),
    )
    resolution = await resolve(PERSON, [EMPLOYER_DOC, CITY_DOC, OWN_ITEM], llm)

    assert resolution.strong_keys == {"wikidata_qid": "Q900000915"}
    assert OWN_ITEM.doc_id in resolution.accepted_doc_ids
