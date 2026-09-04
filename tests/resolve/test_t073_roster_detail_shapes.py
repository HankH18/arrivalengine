"""T-073: the resolver has to read the roster shapes a roster actually writes.

WHAT WAS WRONG, MEASURED ON THE FIRST LIVE RUN (2026-09-04).  Three of ten people came
back `unresolved` with confidence 0.00 and no facts.  Decision 4's second arm counts
`verdict_attributes`, which reads each verbatim evidence span against the person's OWN
details — so what those details PARSE TO decides whether anybody can be identified at all.
`_organisation_part` split a detail on the first separator it found, and `", "` is the
separator a roster uses for a job title AND for a city:

    "Boulder, Colorado"   ->  organisation "Colorado"
    "Sydney, Australia"   ->  organisation "Australia"

so the city detail was consumed as an employer and `_city` fell through to whatever came
next in the list.  For Brad Feld that was `"feld.com"`, and the resolver spent the whole
live run asking whether an evidence span mentioned `feld` and `com` when it meant to ask
whether it mentioned Boulder.  For Melanie Perkins there was nothing after it, so she had
no city at all.  A second shape did the matching damage on the employer side:

    "co-founder, Foundry Group; co-founder, Techstars"
        ->  ["foundry", "group", "founder", "techstars"]

and `_mentions` requires EVERY token, so no real document could corroborate that employer.

THE TWO SHAPES ARE TESTED TOGETHER AND SO IS THE OLD ONE.  The fix is a strict pass (the
head has to name a role) with the original separator-only rule kept as a per-person
fallback, because the frozen corpus is full of `"Head of platform at Tinbridge Analytics"`
— a head that is not a role phrase and an employer that must still be found.  A change
that fixed the city and lost those would trade three people for eight.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from arrival.contracts import PersonRef, RawDoc, Verdict
from arrival.resolve import DocVerdict, resolve, verdict_attributes
from arrival.util import doc_id as make_doc_id
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC)

#: Brad Feld's roster shape with synthetic words: a two-company employer detail joined by
#: `;`, a `"<City>, <State>"` detail, and a bare personal domain after it.
CITY_AFTER_ORG = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=[
        "co-founder, Harrowgate Systems; co-founder, Tinbridge Labs",
        "Boulder, Colorado",
        "whitfield.example.com",
    ],
)

#: Melanie Perkins's shape: the city detail is LAST, so a rule that eats it leaves the
#: person with no city at all rather than with the wrong one.
CITY_LAST = PersonRef(
    person_id="pell-marrowby",
    name="Pell Marrowby",
    details=["co-founder and CEO, Pelmyre Works", "Sydney, Australia"],
)

#: The shape the frozen corpus is written in — the head is NOT a role phrase, and the
#: employer has to be found anyway.
NOT_ROLE_HEADED = PersonRef(
    person_id="oriane-quarrystone",
    name="Oriane Quarrystone",
    details=["Head of platform at Quarrystone Labs", "Trondheim"],
)


def doc(text: str, url: str) -> RawDoc:
    """A document on a host that can never earn a strong key: the second arm alone."""
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


def resolve_with(person: PersonRef, pairs: list[tuple[RawDoc, str, str]]):
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
    return asyncio.run(resolve(person, [d for d, _, _ in pairs], llm))


# --- a city detail is a city -------------------------------------------------------


def test_a_city_and_state_detail_is_not_an_employer():
    span = doc("x", "https://encyclopedia.example/1")
    evidence = "Dara Whitfield has lived in Boulder, Colorado since 2014"

    assert verdict_attributes(CITY_AFTER_ORG, verdict(span, evidence, "role")) == {"city"}, (
        "'Boulder, Colorado' is where she lives. Read as `<role>, <Organisation>` it "
        "becomes the organisation 'Colorado', the city detail is spent, and `_city` falls "
        "through to the next detail in the list -- which on the live roster was a blog's "
        "domain name"
    )


def test_the_personal_domain_after_the_city_is_never_read_as_the_city():
    span = doc("x", "https://encyclopedia.example/2")
    # The exact failure shape: a span quoting the blog's address and nothing else.
    evidence = "Dara Whitfield writes at whitfield.example.com about scheduling"

    assert "city" not in verdict_attributes(CITY_AFTER_ORG, verdict(span, evidence, "role")), (
        "a span that names the member's WEBSITE corroborated her CITY, because the city "
        "detail had already been eaten as an organisation"
    )


def test_a_city_detail_listed_last_is_still_found():
    span = doc("x", "https://encyclopedia.example/3")
    evidence = "Pell Marrowby founded the company in Sydney, Australia"

    assert verdict_attributes(CITY_LAST, verdict(span, evidence, "employer")) == {"city"}, (
        "with the city detail listed last, misreading it as an organisation leaves the "
        "person with NO city, so nothing can ever corroborate one"
    )


# --- an employer detail is one employer --------------------------------------------


def test_a_second_semicolon_clause_is_a_second_affiliation_not_a_longer_name():
    span = doc("x", "https://encyclopedia.example/4")
    evidence = "Dara Whitfield co-founded Harrowgate Systems in 2011"

    assert "employer" in verdict_attributes(CITY_AFTER_ORG, verdict(span, evidence, "role")), (
        "`_mentions` requires EVERY token, so reading "
        "'Harrowgate Systems; co-founder, Tinbridge Labs' as one employer demands four "
        "words no document about her puts in one span, and her employer becomes "
        "permanently uncorroborable"
    )


def test_an_employer_detail_whose_head_is_not_a_role_phrase_is_still_an_employer():
    span = doc("x", "https://encyclopedia.example/5")
    evidence = "Oriane Quarrystone has led Quarrystone Labs since 2018"

    assert verdict_attributes(NOT_ROLE_HEADED, verdict(span, evidence, "role")) == {"employer"}, (
        "'Head of platform at Quarrystone Labs' is the shape most of the frozen corpus is "
        "written in; the strict role-headed rule must keep the separator-only fallback for "
        "it or eight pinned people lose their employer to fix three"
    )
    city = doc("y", "https://encyclopedia.example/6")
    assert verdict_attributes(
        NOT_ROLE_HEADED, verdict(city, "Oriane Quarrystone moved to Trondheim in 2016", "role")
    ) == {"city"}


# --- and the whole thing, through `resolve` ----------------------------------------


def test_the_employer_and_the_city_of_a_real_roster_shape_resolve_the_person():
    employer_span = "Dara Whitfield co-founded Harrowgate Systems in 2011"
    city_span = "Dara Whitfield has lived in Boulder, Colorado since 2014"
    # Decision 5: a verdict's evidence must be verbatim IN its document, so the corpus
    # carries the spans rather than a placeholder.
    employer_doc = doc(employer_span, "https://encyclopedia.example/7")
    city_doc = doc(city_span, "https://encyclopedia.example/8")

    resolution = resolve_with(
        CITY_AFTER_ORG,
        [(employer_doc, employer_span, "employer"), (city_doc, city_span, "city")],
    )

    assert resolution.strong_keys == {}, (
        "pre-condition: no strong key is earnable here, or the second arm is not what is "
        f"under test (got {dict(resolution.strong_keys)})"
    )
    assert resolution.status == "resolved", (
        "one document ties her name to the employer and another to the city -- two "
        "independent attributes, which is Decision 4's second arm exactly"
    )
    assert sorted(resolution.accepted_doc_ids) == sorted(
        [employer_doc.doc_id, city_doc.doc_id]
    )


def test_two_documents_corroborating_only_the_employer_still_refuse():
    # The doctrine is untouched by the parsing fix: this is the live shape of Josh
    # Kopelman and Sarah Tavel, and it must still refuse.
    span_a = "Dara Whitfield co-founded Harrowgate Systems in 2011"
    span_b = "a partner at Harrowgate Systems, Dara Whitfield"
    first = doc(span_a, "https://encyclopedia.example/9")
    second = doc(span_b, "https://encyclopedia.example/10")

    resolution = resolve_with(
        CITY_AFTER_ORG, [(first, span_a, "employer"), (second, span_b, "handle")]
    )

    assert resolution.status == "unresolved", (
        "two spans quoting one employer are one fact corroborated twice, however the "
        "model spelled the second label"
    )
    assert list(resolution.accepted_doc_ids) == [], "R2: an unresolved person stores nothing"
