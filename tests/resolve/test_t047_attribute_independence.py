"""T-047: what the evidence-first attribute rule costs, and what it must never cost.

`verdict_attributes` reads the verdict's EVIDENCE against the person's own details and
consults the model's `disambiguator` only when the span corroborates no detail at all.
That ordering is not free — a span labelled `role` or `handle` that also quotes the
employer contributes `employer` alone, and two such spans no longer resolve a person on
Decision 4's second arm. This module pins BOTH halves of that trade, because a rule whose
cost is undocumented is a rule the next reader will "fix":

* the cost is real and is asserted here, not hidden;
* the thing the cost buys — a model cannot manufacture independence by relabelling one of
  two employer-quoting spans — is asserted here too, and it is the reason the cost stands.

It also pins the half that was pure loss and has been repaired: a span naming BOTH the
employer and the city corroborates both, and "one document must not resolve a person by
itself" is enforced by counting accepted VERDICTS rather than by throwing a corroboration
away.

ANSWER KEYS. Nothing here compares against `arrival.resolve` or `arrival.research`, which
this lane owns. Every expectation is a property of an input written in this file — the
person's own `details`, and whether a span quotes them — or an agreement between two runs
of the gradee (the permutation properties). There is no recorded output to paste.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from arrival.contracts import PersonRef, RawDoc, Verdict
from arrival.resolve import DocVerdict, resolve, verdict_attribute, verdict_attributes
from arrival.util import doc_id as make_doc_id
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC)

PERSON = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["CFO at Harrowgate Systems", "Austin"],
)
EMPLOYER = "Harrowgate Systems"
CITY = "Austin"


def doc(text: str, url: str) -> RawDoc:
    """A document on a host that can never earn a strong key.

    `encyclopedia.example` carries no label matching the employer, and `wikipedia` is
    neither `wikidata`, `github` nor `edgar`, so every assertion below is about the second
    arm alone. A strong key leaking in would make an `unresolved` expectation unfalsifiable.
    """
    return RawDoc(
        doc_id=make_doc_id(url),
        source_kind="wikipedia",
        url=url,
        title="",
        text=text,
        fetched_at=FETCHED,
    )


def verdict(doc_: RawDoc, evidence: str, disambiguator: str) -> Verdict:
    return Verdict(
        doc_id=doc_.doc_id,
        match="yes",
        confidence=0.8,
        evidence=evidence,
        disambiguator=disambiguator,
    )


def resolved_status(pairs: list[tuple[RawDoc, str, str]]) -> str:
    """Drive the real `resolve` with one scripted `yes` per document."""
    llm = LLMDouble()
    for doc_, evidence, disambiguator in pairs:
        llm.when(
            "DocVerdict",
            doc_.doc_id,
            DocVerdict(
                doc_id=doc_.doc_id,
                match="yes",
                confidence=0.8,
                evidence=evidence,
                disambiguator=disambiguator,
            ),
        )
    resolution = asyncio.run(resolve(PERSON, [d for d, _, _ in pairs], llm))
    assert resolution.strong_keys == {}, (
        "pre-condition: this corpus must earn no strong key, or the second arm is not "
        f"what is under test here (got {resolution.strong_keys})"
    )
    return resolution.status


# ------------------------------------------------------------------ what it buys

EMPLOYER_SPAN_A = f"Dara Whitfield joined {EMPLOYER} in 2019"
EMPLOYER_SPAN_B = f"github.com/dwhitfield - {EMPLOYER}"
EMPLOYER_SPAN_C = f"Dara Whitfield, Chief Financial Officer at {EMPLOYER}"


@pytest.mark.parametrize(
    ("label_a", "label_b"),
    [
        ("employer", "handle"),
        ("employer", "role"),
        ("handle", "employer"),
        ("role", "job title"),
        ("company", "workplace"),
        ("employer", "sponsorship arrangement"),
    ],
)
def test_two_spans_that_both_quote_the_employer_are_one_fact_however_labelled(
    label_a: str, label_b: str
) -> None:
    """The attack this ordering exists to close, in six spellings.

    Both spans say the same thing — this person works at Harrowgate Systems — and the only
    thing separating them is a word the model chose. If the label could outrank the span,
    every one of these pairs would read as two independent attributes and resolve a person
    on one corroborated fact. `disambiguator` is free to the model; the span is not.
    """
    first = doc(f"An account of her career. {EMPLOYER_SPAN_A}.", "https://encyclopedia.example/1")
    second = doc(f"Her public code. {EMPLOYER_SPAN_B}.", "https://encyclopedia.example/2")

    attributes = verdict_attributes(PERSON, verdict(first, EMPLOYER_SPAN_A, label_a))
    attributes |= verdict_attributes(PERSON, verdict(second, EMPLOYER_SPAN_B, label_b))
    assert attributes == {"employer"}, (
        f"labels {label_a!r} and {label_b!r} turned one corroborated fact into "
        f"{sorted(attributes)}; the model's word choice must not mint an attribute"
    )

    assert (
        resolved_status(
            [(first, EMPLOYER_SPAN_A, label_a), (second, EMPLOYER_SPAN_B, label_b)]
        )
        == "unresolved"
    ), "two documents corroborating one fact are not two independent attributes"


def test_the_attack_stays_closed_when_the_span_really_does_name_a_handle() -> None:
    """The uncomfortable case, stated plainly rather than left for someone to discover.

    `github.com/dwhitfield - Harrowgate Systems` genuinely names a handle. It is still read
    as `employer`, because `PersonRef.details` carries a handle for nobody and the span
    quotes a detail it DOES carry. Reading it as `handle` is indistinguishable from the
    attack above, so this is the deliberate cost, not an oversight.
    """
    span_doc = doc(f"Her public code. {EMPLOYER_SPAN_B}.", "https://encyclopedia.example/2")
    assert verdict_attribute(PERSON, verdict(span_doc, EMPLOYER_SPAN_B, "handle")) == "employer"


# ------------------------------------------------------------------ what it costs


def test_a_role_span_that_also_quotes_the_employer_contributes_the_employer() -> None:
    """The documented cost. Held here so it cannot change silently in either direction."""
    role_doc = doc(f"Leadership. {EMPLOYER_SPAN_C}.", "https://encyclopedia.example/3")
    news = doc(f"News. {EMPLOYER_SPAN_A}.", "https://encyclopedia.example/4")

    assert verdict_attributes(PERSON, verdict(role_doc, EMPLOYER_SPAN_C, "role")) == {"employer"}
    assert (
        resolved_status([(role_doc, EMPLOYER_SPAN_C, "role"), (news, EMPLOYER_SPAN_A, "employer")])
        == "unresolved"
    )


def test_a_role_span_that_quotes_no_detail_still_counts_as_its_own_attribute() -> None:
    """The cost is confined to spans that quote a detail. It is not a ban on `role`."""
    role_span = "Dara Whitfield has been a chief financial officer since 2016"
    role_doc = doc(f"Profile. {role_span}.", "https://encyclopedia.example/5")
    news = doc(f"News. {EMPLOYER_SPAN_A}.", "https://encyclopedia.example/6")

    assert verdict_attributes(PERSON, verdict(role_doc, role_span, "role")) == {"role"}
    assert (
        resolved_status([(role_doc, role_span, "role"), (news, EMPLOYER_SPAN_A, "employer")])
        == "resolved"
    )


# ------------------------------------------------------------------ the repaired half

BOTH_DETAILS_SPAN = f"Dara Whitfield, {EMPLOYER}, {CITY}"


def test_a_span_naming_both_details_corroborates_both_of_them() -> None:
    """A profile quoting the employer AND the city establishes two checkable facts.

    Filing it under `employer` alone discarded a corroboration that came from the person's
    own details rather than from the model's vocabulary — the one kind of evidence this
    module has any reason to trust.
    """
    profile = doc(f"Item mirror. {BOTH_DETAILS_SPAN}.", "https://encyclopedia.example/7")
    assert verdict_attributes(PERSON, verdict(profile, BOTH_DETAILS_SPAN, "handle")) == {
        "employer",
        "city",
    }


def test_one_accepted_document_never_resolves_a_person_by_itself() -> None:
    """Decision 4's second arm asks for two `yes` VERDICTS, and one is not two.

    This is the requirement the old single-attribute-per-verdict collapse was standing in
    for. It is now stated where it can be checked, so the corroboration no longer has to be
    thrown away to enforce it.
    """
    profile = doc(f"Item mirror. {BOTH_DETAILS_SPAN}.", "https://encyclopedia.example/8")
    assert resolved_status([(profile, BOTH_DETAILS_SPAN, "employer")]) == "unresolved"


def test_two_documents_tying_the_name_to_the_employer_and_the_city_resolve() -> None:
    """The resolution the collapse was costing, recovered through evidence, not labels."""
    profile = doc(f"Item mirror. {BOTH_DETAILS_SPAN}.", "https://encyclopedia.example/9")
    news = doc(f"News. {EMPLOYER_SPAN_A}.", "https://encyclopedia.example/10")
    assert (
        resolved_status([(profile, BOTH_DETAILS_SPAN, "employer"), (news, EMPLOYER_SPAN_A, "role")])
        == "resolved"
    )


def test_the_one_word_answer_agrees_with_the_set() -> None:
    """`verdict_attribute` is `/debug`'s summary of `verdict_attributes`, never a second rule."""
    cases = [
        (BOTH_DETAILS_SPAN, "city"),
        (BOTH_DETAILS_SPAN, "handle"),
        (EMPLOYER_SPAN_B, "handle"),
        ("Dara Whitfield co-wrote the 2021 survey with R. Idris", "coauthor"),
        ("Dara Whitfield spoke at length", "vibes"),
        ("Dara Whitfield spoke at length", ""),
    ]
    holder = doc("text", "https://encyclopedia.example/11")
    for evidence, label in cases:
        single = verdict_attribute(PERSON, verdict(holder, evidence, label))
        every = verdict_attributes(PERSON, verdict(holder, evidence, label))
        assert (single in every) if every else (single == ""), (
            f"{label!r}/{evidence!r}: one-word answer {single!r} is not in {sorted(every)}"
        )
