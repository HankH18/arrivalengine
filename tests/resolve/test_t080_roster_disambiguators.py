"""T-080: the resolver has to be able to count a disambiguator the roster gave it.

WHAT WAS WRONG, MEASURED LIVE ON 2026-09-04.  `resolve._CORROBORABLE` was
`("employer", "city")`, justified in a code comment as *"the only two the person carries in
`PersonRef.details`"*.  Seven of the ten people on the live roster carry a third detail, so
the premise was false for most of the product.  Sarah Tavel's third detail is
`"formerly Greylock and Pinterest"`; her build produced **ten accepted `yes` verdicts**,
three of whose verbatim spans quote that detail — one of them TechCrunch's *"Tavel joined
Benchmark in 2017 after spending one and a half years as a partner at Greylock and three
years as a product manager at Pinterest."* — and every one of the ten corroborated the
single attribute `employer`.  Ten documents, one countable attribute, `unresolved`.

WHAT THIS CHANGE IS NOT.  It raises no threshold.  `resolve` still demands two accepted
`yes` verdicts; `_verdict_from` still demands a verbatim span; and `verdict_attributes`
still asks the EVIDENCE first and the model's free-text label second, which is what T-031
and T-047 established.  Only the VOCABULARY of countable attributes widened, and only by
the details the roster author already wrote down.

THE PROPERTY THAT KEEPS T-047 CLOSED, AND WHY IT IS TESTED HERE RATHER THAN ASSUMED.  A
phrase-split re-opens manufactured independence one level down if it is allowed to mint two
attributes out of ONE detail: `"formerly Marrow and Quill"` split on `" and "` would let two
documents quoting that single organisation look like two independent facts — T-047's attack
with punctuation standing in for the model's label.  Measured on a monkeypatched copy of
this module before the rule was written: per-phrase attributes gave `['marrow', 'quill']`
and `resolved`.  So the split happens BELOW the attribute — a detail's phrases are
ALTERNATIVES, never separate attributes — and `test_a_conjoined_organisation_is_one_fact`
is that measurement held down.

ANSWER KEYS.  Nothing here compares against `arrival.resolve` or `arrival.connectors`,
which this lane owns.  Every expectation is a property of an input written in this file —
a person's own `details`, and whether a span quotes them — except
`test_the_live_roster_negative_detail_corroborates_nothing`, which grades against
`data/roster.yaml`, a data file outside this lane's ownership.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest
import yaml

from arrival.contracts import PersonRef, RawDoc, Verdict
from arrival.resolve import (
    DocVerdict,
    asserts_negation,
    city_detail,
    resolve,
    verdict_attribute,
    verdict_attributes,
)
from arrival.util import doc_id as make_doc_id
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC)

#: Sarah Tavel's shape with synthetic words: an employer detail, a city detail, and a third
#: detail naming two FORMER employers joined by "and".
THREE_DETAILS = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["CFO at Harrowgate Systems", "Austin", "formerly Pelmyre and Tinbridge"],
)

#: The same shape where the third detail names ONE organisation whose name contains "and".
#: This is the person the conjunction split could have resolved on a single fact.
CONJOINED = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["CFO at Harrowgate Systems", "Austin", "formerly Marrow and Quill"],
)

#: Nabeel Qureshi's shape, punctuation included: a detail that names, on purpose, the
#: person this member is NOT. The `/` matters -- it is a phrase separator, so the split
#: would produce `['apologist', 'dara', 'whitfield', 'who', 'died', '2011']`, a group an
#: obituary of the NAMESAKE quotes word for word.
NEGATIVE_DETAIL = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=[
        "CFO at Harrowgate Systems",
        "Austin",
        "NOT the author/apologist Dara Whitfield who died in 2011",
    ],
)

#: The verbatim obituary sentence of the namesake, which quotes that group in full.
DECOY_SPAN = "the apologist Dara Whitfield who died in 2011 wrote four books"

#: Fred Wilson's and Brad Feld's shape: the third detail is the member's own website.
OWN_WEBSITE = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=["CFO at Harrowgate Systems", "Austin", "writes the DWB blog (dwhitfield.example)"],
)

#: Eric Ries's and Emmett Shear's shape: the third detail carries a parenthetical.
PARENTHESISED = PersonRef(
    person_id="dara-whitfield",
    name="Dara Whitfield",
    details=[
        "CFO at Harrowgate Systems",
        "Austin",
        "founder, Pelmyre Stock Exchange (PLMX)",
    ],
)

EMPLOYER = "Harrowgate Systems"
EMPLOYER_SPAN = f"Dara Whitfield joined {EMPLOYER} in 2019"


def doc(text: str, url: str) -> RawDoc:
    """A document on a host that can never earn a strong key: the second arm alone.

    `encyclopedia.example` carries no label matching the employer, and `wikipedia` is
    neither `wikidata`, `github` nor `edgar`, so a strong key cannot leak in and make an
    `unresolved` expectation unfalsifiable.
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


def resolution_for(person: PersonRef, pairs: list[tuple[RawDoc, str, str]]):
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
    resolution = asyncio.run(resolve(person, [d for d, _, _ in pairs], llm))
    assert resolution.strong_keys == {}, (
        "pre-condition: this corpus must earn no strong key, or the second arm is not what "
        f"is under test here (got {dict(resolution.strong_keys)})"
    )
    return resolution


def attributes_of(person: PersonRef, evidence: str, label: str) -> frozenset[str]:
    holder = doc(f"Text. {evidence}.", "https://encyclopedia.example/holder")
    return verdict_attributes(person, verdict(holder, evidence, label))


# --- the defect: a third detail was structurally uncountable ------------------------


def test_a_third_roster_detail_is_a_corroborable_attribute() -> None:
    """The measured failure, in one assertion.

    The span names neither the employer nor the city; it quotes the third detail and
    nothing else. Before T-080 that produced the model's LABEL, so the roster author's own
    disambiguator contributed nothing the resolver could count.
    """
    span = "Dara Whitfield spent three years at Pelmyre before that"

    assert attributes_of(THREE_DETAILS, span, "employer") != {"employer"}, (
        "a span that never says 'Harrowgate Systems' corroborated the EMPLOYER, which "
        "means the model's label decided it -- the thing T-031 and T-047 took away"
    )
    assert len(attributes_of(THREE_DETAILS, span, "employer")) == 1, (
        "one detail corroborated is one attribute"
    )
    assert attributes_of(THREE_DETAILS, span, "employer") == attributes_of(
        THREE_DETAILS, span, "handle"
    ), "the attribute a span corroborates cannot depend on the word the model chose"


def test_the_tavel_shape_resolves_on_the_employer_and_the_third_detail() -> None:
    """The live shape T-080 was raised for, end to end through `resolve`.

    Sarah Tavel: ten accepted verdicts, all corroborating one employer, plus spans quoting
    the third detail. Two accepted verdicts and two distinct attributes is Decision 4's
    second arm exactly -- the threshold is untouched, the vocabulary is not.
    """
    third_span = "Dara Whitfield was a product manager at Tinbridge"
    employer_doc = doc(f"News. {EMPLOYER_SPAN}.", "https://encyclopedia.example/1")
    third_doc = doc(f"Profile. {third_span}.", "https://encyclopedia.example/2")

    resolution = resolution_for(
        THREE_DETAILS,
        [(employer_doc, EMPLOYER_SPAN, "employer"), (third_doc, third_span, "employer")],
    )
    assert resolution.status == "resolved", (
        "one document ties her name to the employer and another to a DIFFERENT detail the "
        "roster supplied; refusing that is refusing a person the roster disambiguated"
    )
    assert sorted(resolution.accepted_doc_ids) == sorted([employer_doc.doc_id, third_doc.doc_id])


def test_two_details_are_untouched_by_the_widening() -> None:
    """Josh Kopelman's shape: two details, both spent, and no third one to find.

    He must stay `unresolved` -- eight documents all citing the same employer are one fact
    corroborated eight times. A widening that rescued him would have widened too far.
    """
    two_details = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["CFO at Harrowgate Systems", "Austin"],
    )
    span_b = f"a partner at {EMPLOYER}, Dara Whitfield"
    first = doc(f"News. {EMPLOYER_SPAN}.", "https://encyclopedia.example/3")
    second = doc(f"Profile. {span_b}.", "https://encyclopedia.example/4")

    resolution = resolution_for(
        two_details, [(first, EMPLOYER_SPAN, "employer"), (second, span_b, "handle")]
    )
    assert resolution.status == "unresolved", (
        "two spans quoting one employer are one fact corroborated twice, and a person with "
        "no third detail has nothing for the widened vocabulary to read"
    )
    assert list(resolution.accepted_doc_ids) == [], "R2: an unresolved person stores nothing"


# --- what keeps the T-047 attack closed underneath the widening ---------------------


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
def test_the_manufactured_independence_attack_stays_closed_with_a_third_detail(
    label_a: str, label_b: str
) -> None:
    """T-047's attack re-run against a person who HAS the detail T-080 widened for.

    T-047 pins this for a two-detail person, where the widening cannot express itself.
    Both spans still say the same thing -- she works at Harrowgate Systems -- and the only
    thing separating them is a word the model chose.
    """
    span_b = f"github.com/dwhitfield - {EMPLOYER}"
    first = doc(f"Career. {EMPLOYER_SPAN}.", "https://encyclopedia.example/5")
    second = doc(f"Code. {span_b}.", "https://encyclopedia.example/6")

    attributes = verdict_attributes(THREE_DETAILS, verdict(first, EMPLOYER_SPAN, label_a))
    attributes |= verdict_attributes(THREE_DETAILS, verdict(second, span_b, label_b))
    assert attributes == {"employer"}, (
        f"labels {label_a!r} and {label_b!r} turned one corroborated fact into "
        f"{sorted(attributes)}; the model's word choice must not mint an attribute"
    )
    assert (
        resolution_for(
            THREE_DETAILS, [(first, EMPLOYER_SPAN, label_a), (second, span_b, label_b)]
        ).status
        == "unresolved"
    )


def test_a_conjoined_organisation_is_one_fact() -> None:
    """The attack the phrase-split could have opened, and the reason for the split's shape.

    `"formerly Marrow and Quill"` is ONE organisation whose name contains "and". Splitting a
    detail into per-phrase ATTRIBUTES makes two documents quoting it look independent --
    measured on a monkeypatched copy of `resolve`: `['marrow', 'quill']`, `resolved`. The
    phrases are alternatives for one attribute instead, so this refuses.
    """
    span_a = "Dara Whitfield ran operations at Marrow and Quill from 2012"
    span_b = "Before that, Dara Whitfield was at Marrow and Quill"
    first = doc(f"Profile. {span_a}.", "https://encyclopedia.example/7")
    second = doc(f"News. {span_b}.", "https://encyclopedia.example/8")

    attributes = verdict_attributes(CONJOINED, verdict(first, span_a, "employer"))
    attributes |= verdict_attributes(CONJOINED, verdict(second, span_b, "handle"))
    assert len(attributes) == 1, (
        "one organisation quoted by two documents became "
        f"{sorted(attributes)}; a conjunction in an organisation's NAME must not mint an "
        "attribute any more than the model's label may"
    )
    assert (
        resolution_for(
            CONJOINED, [(first, span_a, "employer"), (second, span_b, "handle")]
        ).status
        == "unresolved"
    )


def test_a_detail_can_never_contribute_more_than_one_attribute() -> None:
    """The invariant stated directly: one detail, one attribute, whatever a span quotes.

    A span naming BOTH halves of the third detail is the strongest corroboration of it
    there is, and it is still corroboration of ONE detail.
    """
    both = "Dara Whitfield worked at Pelmyre and later at Tinbridge"
    attributes = attributes_of(THREE_DETAILS, both, "employer")
    assert len(attributes) == 1, (
        f"a span quoting both halves of one detail produced {sorted(attributes)}"
    )
    # And a person can never have more corroborable attributes than the roster gave details.
    every = set()
    for span in (EMPLOYER_SPAN, "She has lived in Austin since 2014", both):
        every |= attributes_of(THREE_DETAILS, span, "employer")
    assert len(every) <= len(THREE_DETAILS.details)


# --- a negative detail is not a disambiguator ---------------------------------------


def test_a_negative_detail_corroborates_nothing() -> None:
    """`"NOT the archaeologist ... who died in 2011"` names who she is NOT.

    Phrase-splitting it would let a document about the WRONG human being corroborate the
    right one -- SPEC S4's failure arriving through the roster instead of through the
    internet. There is no way to tell which half of a negated sentence is negated without
    parsing English, so the whole detail is refused.
    """
    assert asserts_negation("NOT the author/apologist Dara Whitfield who died in 2011")
    assert not asserts_negation("formerly Pelmyre and Tinbridge")

    attributes = attributes_of(NEGATIVE_DETAIL, DECOY_SPAN, "role")
    assert attributes == {"role"}, (
        f"a span about the DEAD namesake corroborated {sorted(attributes)}; the only thing "
        "left to fall back on is the model's label, which is what an uncorroborated span "
        "has always produced"
    )


def test_a_negative_detail_never_resolves_the_decoy() -> None:
    """Through `resolve`, in the shape where the refusal is load-bearing.

    One document ties her name to the real employer; the other is the NAMESAKE's obituary,
    quoting the negative detail word for word and labelled `employer` too. Refused, the
    obituary corroborates nothing and the pair is one attribute. Counted, it would have
    been a second attribute and these two documents would have resolved her -- on a
    sentence the roster wrote down to say the subject is somebody else.
    """
    first = doc(f"News. {EMPLOYER_SPAN}.", "https://encyclopedia.example/9")
    second = doc(f"Obituary. {DECOY_SPAN}.", "https://encyclopedia.example/10")

    assert attributes_of(NEGATIVE_DETAIL, DECOY_SPAN, "employer") == {"employer"}, (
        "the obituary of the namesake corroborated the negative detail, which is the "
        "wrong-person document earning the right person an attribute"
    )
    assert (
        resolution_for(
            NEGATIVE_DETAIL, [(first, EMPLOYER_SPAN, "employer"), (second, DECOY_SPAN, "employer")]
        ).status
        == "unresolved"
    ), "the roster said this is NOT her; a document about him cannot make it her"


def test_a_negative_detail_is_never_read_as_the_employer_or_the_city() -> None:
    """The refusal is applied where the details are READ, not only where they are split."""
    only_negative = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["NOT the archaeologist Dara Whitfield of Trondheim who died in 2011"],
    )
    assert city_detail(only_negative) == "", (
        "a detail saying who she is NOT became her city, so a document about the namesake "
        "could corroborate where she lives"
    )
    assert attributes_of(only_negative, "Dara Whitfield of Trondheim", "city") == {"city"}, (
        "the label fallback is all that should be left; a corroborated attribute here "
        "would mean the negative detail was read as a positive one"
    )


def test_the_live_roster_negative_detail_corroborates_nothing() -> None:
    """The real detail this rule exists for, read from the roster this lane does not own."""
    roster = yaml.safe_load(Path("data/roster.yaml").read_text(encoding="utf-8"))
    negative = [
        detail
        for person in roster["people"]
        for detail in person["details"]
        if detail.lstrip().startswith("NOT ")
    ]
    assert negative, (
        "the roster no longer carries a NEGATIVE detail; this test's subject is gone, and "
        "the rule it pins should be re-argued rather than silently kept green"
    )
    for detail in negative:
        assert asserts_negation(detail), detail


# --- the shapes the rest of the roster is written in --------------------------------


def test_a_detail_that_is_only_a_web_address_corroborates_nothing() -> None:
    """`connectors.base.affiliations` drops the clause carrying an address; so does this.

    What is left of `"writes the DWB blog (dwhitfield.example)"` once the address goes is
    the word "blog", which is worse than nothing as a disambiguator.
    """
    span = "Dara Whitfield writes at dwhitfield.example about scheduling"
    assert attributes_of(OWN_WEBSITE, span, "handle") == {"handle"}, (
        "the member's own domain became a corroborable attribute; every page that links to "
        "her blog would then corroborate her identity"
    )


def test_a_parenthetical_annotation_does_not_have_to_be_quoted() -> None:
    """`"Pelmyre Stock Exchange (PLMX)"` names one organisation, annotated with its ticker.

    `_mentions` requires EVERY token, so demanding the parenthetical makes the detail
    unmatchable by the documents that actually quote it -- Emmett Shear's
    `"briefly interim CEO of OpenAI (Nov 2023)"` would need a span that says "Nov 2023".
    The acronym is read as an alternative SPELLING of the same organisation, so it
    corroborates the same single attribute rather than a second one.
    """
    spelled_out = "Dara Whitfield founded the Pelmyre Stock Exchange in 2019"
    acronym = "Dara Whitfield chairs PLMX"

    assert len(attributes_of(PARENTHESISED, spelled_out, "employer")) == 1
    assert attributes_of(PARENTHESISED, spelled_out, "employer") == attributes_of(
        PARENTHESISED, acronym, "employer"
    ), "the name and its acronym are one organisation, so they are one attribute"


def test_a_three_letter_acronym_falls_below_the_distinctiveness_floor() -> None:
    """The stated boundary of the floor, held down so it is a decision and not a surprise.

    A word has to be four characters to carry a phrase on its own, which is what stops
    `"formerly Ben and Jerry's"` from being corroborated by the word "Ben". A three-letter
    ticker is the cost: it fails CLOSED -- the detail simply is not corroborable by its
    acronym -- which loses a disambiguator rather than handing one to a wrong document.
    """
    short = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["CFO at Harrowgate Systems", "Austin", "founder, Pelmyre Exchange (PMX)"],
    )
    assert attributes_of(short, "Dara Whitfield chairs PMX", "handle") == {"handle"}
    assert len(attributes_of(short, "Dara Whitfield founded Pelmyre Exchange", "handle")) == 1, (
        "the spelled-out name is still four characters and must still corroborate"
    )


def test_a_role_phrase_alone_is_never_a_corroborable_attribute() -> None:
    """A job title matches half the internet, so it cannot identify anybody.

    `"co-founder and partner"` is a detail-shaped phrase that names no organisation. Left
    countable it would corroborate on any document that calls her a partner.
    """
    role_only = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["CFO at Harrowgate Systems", "Austin", "co-founder and partner"],
    )
    span = "Dara Whitfield, a co-founder and partner, spoke at length"
    assert attributes_of(role_only, span, "role") == {"role"}, (
        "a job title became a corroborable attribute of its own; the label fallback is the "
        "only thing a span quoting no organisation should produce"
    )


# --- the one-word summary -----------------------------------------------------------


def test_the_one_word_answer_agrees_with_the_set_and_is_deterministic() -> None:
    """`verdict_attribute` is `/debug`'s summary of `verdict_attributes`, never a second rule.

    It also must not depend on set iteration order: the old fallback was
    `next(iter(frozenset))`, so the same verdict could name a different attribute on
    `/debug` between two runs of the same build.
    """
    cases = [
        (EMPLOYER_SPAN, "employer"),
        ("Dara Whitfield spent three years at Pelmyre before that", "handle"),
        ("Dara Whitfield worked at Pelmyre and later at Tinbridge", "role"),
        ("Dara Whitfield has lived in Austin since 2014", "city"),
        ("Dara Whitfield spoke at length", "vibes"),
        ("Dara Whitfield spoke at length", ""),
    ]
    holder = doc("text", "https://encyclopedia.example/11")
    for evidence, label in cases:
        single = verdict_attribute(THREE_DETAILS, verdict(holder, evidence, label))
        every = verdict_attributes(THREE_DETAILS, verdict(holder, evidence, label))
        assert (single in every) if every else (single == ""), (
            f"{label!r}/{evidence!r}: one-word answer {single!r} is not in {sorted(every)}"
        )
        assert single == verdict_attribute(THREE_DETAILS, verdict(holder, evidence, label))


def test_a_job_title_with_no_organisation_after_it_is_not_a_city() -> None:
    """A detail that is a bare job title has no separator, so it fell through to `_city`.

    Measured on the live roster: Nabeel Qureshi's `"writer and researcher"` became his
    CITY, and every document calling him a writer and researcher then corroborated where
    he lives -- an attribute manufactured out of a detail that is not a place, on the arm
    that decides whether the person exists at all. A wrong city is strictly worse than no
    city, so this fails closed.
    """
    bare_title = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["writer and researcher", "formerly Pelmyre; essays at dwhitfield.example"],
    )
    assert city_detail(bare_title) == "", (
        f"a job title became the city detail: {city_detail(bare_title)!r}"
    )

    span = "Dara Whitfield is a writer and researcher based in Oslo"
    assert "city" not in attributes_of(bare_title, span, "role"), (
        "a span calling her a writer and researcher corroborated her CITY"
    )
    # And a real place is still found, with a role-shaped detail sitting in front of it.
    with_place = PersonRef(
        person_id="dara-whitfield",
        name="Dara Whitfield",
        details=["writer and researcher", "Trondheim"],
    )
    assert city_detail(with_place) == "Trondheim"
