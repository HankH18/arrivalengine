"""DESIGN Decision 4 and 5, rule by rule, on documents built in the test.

The case corpus (`test_t2_resolve_cases.py`) grades whole outcomes; this module isolates
one rule at a time, so a failure names the rule rather than the case.
"""

from __future__ import annotations

import datetime as dt

import pytest
from doubles import LLMDouble, assert_conforms

from arrival.contracts import LLMClient, LLMError, PersonRef, RawDoc, Verdict
from arrival.resolve import (
    DocVerdict,
    cites_document,
    negative_evidence_veto,
    resolve,
    strong_keys_for,
    verdict_prompt,
)
from arrival.util import doc_id as make_doc_id

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 2, 18, 9, 30, tzinfo=dt.timezone.utc)

PERSON = PersonRef(
    person_id="brannoc-uleyfield",
    name="Brannoc Uleyfield",
    details=["Head of survey at Tenterhook Geodesy", "Aberdeen"],
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


async def run(docs, llm, person: PersonRef = PERSON):
    return await resolve(person, docs, llm)


# Deliberately on a third-party host: a document sitting on the employer's OWN domain is
# a `company_domain` strong key, which would resolve half the cases below on the strength
# of a url and stop them measuring the verdict rules at all.
EMPLOYER_DOC = doc(
    "self_page",
    "https://firm-directory.example/tenterhook-geodesy/team",
    "Brannoc Uleyfield is head of survey at Tenterhook Geodesy and has run the levelling "
    "crew since 2019.",
)
CITY_DOC = doc(
    "search",
    "https://survey-notes.example/2025/levelling",
    "Brannoc Uleyfield has worked out of Aberdeen since 2014 and says the weather is a "
    "measurement problem.",
)


# ---------------------------------------------------------------- the two arms


async def test_two_yes_verdicts_on_different_disambiguators_resolve():
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.7, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Aberdeen since 2014", "city")),
    )
    resolution = await run([EMPLOYER_DOC, CITY_DOC], llm)
    assert resolution.status == "resolved"
    assert resolution.accepted_doc_ids == [EMPLOYER_DOC.doc_id, CITY_DOC.doc_id]
    assert resolution.confidence == pytest.approx(0.7), (
        "the overall confidence is the best single verdict, never a pooled or averaged one"
    )


async def test_two_yes_verdicts_on_the_same_disambiguator_do_not_resolve():
    other = doc(
        "search",
        "https://survey-notes.example/2024/crews",
        "Brannoc Uleyfield, head of survey at Tenterhook Geodesy, ran the 2024 crew.",
    )
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.9, "head of survey at Tenterhook Geodesy", "employer")),
        (other, verdict(other, "yes", 0.88, "head of survey at Tenterhook Geodesy", "employer")),
    )
    resolution = await run([EMPLOYER_DOC, other], llm)
    assert resolution.status == "unresolved"
    assert resolution.accepted_doc_ids == []
    assert resolution.confidence == 0.0


async def test_one_yes_verdict_without_a_strong_key_does_not_resolve():
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.99, "head of survey at Tenterhook Geodesy", "employer")),
    )
    resolution = await run([EMPLOYER_DOC], llm)
    assert resolution.status == "unresolved", "R2: one attribute is not an identification"
    assert resolution.accepted_doc_ids == []


async def test_an_unsure_verdict_is_never_promoted_by_its_confidence():
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.5, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "unsure", 0.99, "has worked out of Aberdeen since 2014", "city")),
    )
    resolution = await run([EMPLOYER_DOC, CITY_DOC], llm)
    assert resolution.status == "unresolved", (
        "an `unsure` at 0.99 is still an `unsure`; promoting it lowers the bar by one notch"
    )


async def test_every_document_is_put_to_the_model():
    docs = [EMPLOYER_DOC, CITY_DOC]
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.7, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "no", 0.8, "has worked out of Aberdeen since 2014", "role")),
    )
    await run(docs, llm)
    assert llm.call_count == 2
    for doc_ in docs:
        assert any(doc_.doc_id in call.user and doc_.url in call.user for call in llm.calls), (
            "the prompt must name the document, or a verdict can be filed against the wrong one"
        )
    call = llm.calls[0]
    assert call.schema_name == "DocVerdict"
    assert call.cache_prefix is True, "the system prefix is constant and must be cached"
    assert call.system and PERSON.name not in call.system, (
        "per-person text in the SYSTEM prompt throws the cache away on every person"
    )
    assert PERSON.name in call.user


async def test_duplicate_documents_are_judged_once():
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.7, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.6, "has worked out of Aberdeen since 2014", "city")),
    )
    resolution = await run([EMPLOYER_DOC, CITY_DOC, EMPLOYER_DOC], llm)
    assert llm.call_count == 2
    assert resolution.accepted_doc_ids == [EMPLOYER_DOC.doc_id, CITY_DOC.doc_id]


async def test_a_failing_llm_call_yields_unsure_rather_than_a_guess():
    llm = LLMDouble()  # every call is unscripted, so every call raises LLMError
    resolution = await run([EMPLOYER_DOC, CITY_DOC], llm)
    assert resolution.status == "unresolved"
    assert [v.match for v in resolution.rejected] == ["unsure", "unsure"]


async def test_no_documents_at_all_is_unresolved_not_a_crash():
    resolution = await run([], LLMDouble())
    assert resolution.status == "unresolved"
    assert resolution.accepted_doc_ids == []
    assert resolution.confidence == 0.0


# ------------------------------------------------------- negative evidence (3)


def test_negative_evidence_vetoes():
    """T-2 acceptance 3: a `no` asserting a conflicting employer or city is a hard reject."""
    conflicting_employer = Verdict(
        doc_id="a" * 16,
        match="no",
        confidence=0.96,
        evidence="Employer: Orrell Conservatory. Work location: Trieste.",
        disambiguator="employer",
    )
    assert negative_evidence_veto(PERSON, conflicting_employer) is True

    conflicting_city = Verdict(
        doc_id="b" * 16,
        match="no",
        confidence=0.9,
        evidence="He has never lived outside Nova Scotia.",
        disambiguator="city",
    )
    assert negative_evidence_veto(PERSON, conflicting_city) is True

    # A `no` that quotes the person's OWN employer is not asserting a conflicting one.
    same_employer = Verdict(
        doc_id="c" * 16,
        match="no",
        confidence=0.5,
        evidence="a different Brannoc at Tenterhook Geodesy entirely",
        disambiguator="employer",
    )
    assert negative_evidence_veto(PERSON, same_employer) is False

    # Polarity matters: the frozen suite proves a `yes` on the same span must be accepted.
    positive = Verdict(
        doc_id="d" * 16,
        match="yes",
        confidence=0.96,
        evidence="Employer: Orrell Conservatory. Work location: Trieste.",
        disambiguator="employer",
    )
    assert negative_evidence_veto(PERSON, positive) is False


async def test_a_name_matching_document_is_rejected_on_conflicting_evidence():
    """"Even if the name matches" — the name is in every one of these documents."""
    decoy = doc(
        "wikipedia",
        "https://encyclopedia.example/wiki/Brannoc_Uleyfield_organist",
        "Brannoc Uleyfield was organist at the Hallowmere Chapel in Kirkwall for forty "
        "years and died there in 2011.",
    )
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.6, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.55, "has worked out of Aberdeen since 2014", "city")),
        (decoy, verdict(decoy, "no", 0.97, "organist at the Hallowmere Chapel in Kirkwall", "employer")),
    )
    resolution = await run([EMPLOYER_DOC, CITY_DOC, decoy], llm)
    assert resolution.status == "resolved"
    assert decoy.doc_id not in resolution.accepted_doc_ids
    assert decoy.doc_id in {v.doc_id for v in resolution.rejected}
    assert resolution.confidence == pytest.approx(0.6), (
        "the decoy's 0.97 must not touch the resolution's confidence"
    )


# ------------------------------------------------------- the citation check (5)


def test_cites_document_normalises_whitespace_and_case():
    text = "Brannoc  Uleyfield\nis head\tof survey."
    assert cites_document("brannoc uleyfield is head of survey", text) is True
    assert cites_document("", text) is False
    assert cites_document("head of procurement", text) is False


async def test_uncited_evidence_is_downgraded_to_unsure_and_the_rest_survive():
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "yes", 0.7, "head of survey at Tenterhook Geodesy", "employer")),
        (CITY_DOC, verdict(CITY_DOC, "yes", 0.95, "Brannoc Uleyfield has run the Aberdeen office since 2011.", "city")),
    )
    resolution = await run([EMPLOYER_DOC, CITY_DOC], llm)
    assert resolution.status == "unresolved"
    downgraded = next(v for v in resolution.rejected if v.doc_id == CITY_DOC.doc_id)
    assert downgraded.match == "unsure"
    assert downgraded.evidence, "the uncited span is kept for /debug rather than erased"
    survivor = next(v for v in resolution.rejected if v.doc_id == EMPLOYER_DOC.doc_id)
    assert survivor.match == "yes", "only the uncited verdict is downgraded"


async def test_an_uncited_no_cannot_veto():
    """A hallucinated contradiction is a hallucination first and a contradiction second."""
    llm = scripted(
        (EMPLOYER_DOC, verdict(EMPLOYER_DOC, "no", 0.99, "He was dismissed from Tenterhook in 2019.", "employer")),
    )
    resolution = await run([EMPLOYER_DOC], llm)
    assert [v.match for v in resolution.rejected] == ["unsure"]


async def test_the_returned_doc_id_is_ours_not_the_models():
    wrong = DocVerdict(
        doc_id="0000000000000000",
        match="yes",
        confidence=0.7,
        evidence="head of survey at Tenterhook Geodesy",
        disambiguator="employer",
    )
    llm = scripted((EMPLOYER_DOC, wrong))
    resolution = await run([EMPLOYER_DOC], llm)
    assert [v.doc_id for v in resolution.rejected] == [EMPLOYER_DOC.doc_id]


# ------------------------------------------------------------- strong keys (2)


WIKIDATA_MATCH = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000901",
    "Brannoc Uleyfield (Q900000901)\n\nItem mirror. Instance of: human. Employer: "
    "Tenterhook Geodesy. Work location: Aberdeen.",
    title="Brannoc Uleyfield (Q900000901) - item mirror",
)
WIKIDATA_NAME_ONLY = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000902",
    "Brannoc Uleyfield (Q900000902)\n\nItem mirror. Instance of: human. Occupation: "
    "surveyor. This item carries no employer statement and no work location statement.",
    title="Brannoc Uleyfield (Q900000902) - item mirror",
)


def test_a_qid_matched_on_name_and_detail_is_a_strong_key():
    assert strong_keys_for(PERSON, [WIKIDATA_MATCH]) == {"wikidata_qid": "Q900000901"}


def test_a_qid_matched_on_the_name_alone_is_not():
    assert strong_keys_for(PERSON, [WIKIDATA_NAME_ONLY]) == {}


def test_a_github_profile_needs_both_the_name_and_the_company():
    confirmed = doc(
        "github",
        "https://code-host.example/users/buleyfield",
        "Name: Brannoc Uleyfield\nCompany: Tenterhook Geodesy\nLocation: Aberdeen",
    )
    unset_company = doc(
        "github",
        "https://code-host.example/users/buley",
        "Name: Brannoc Uleyfield\nCompany: not set\nLocation: Aberdeen",
    )
    other_company = doc(
        "github",
        "https://code-host.example/users/bu",
        "Name: Brannoc Uleyfield\nCompany: Hallowmere Chapel\nLocation: Aberdeen",
    )
    assert strong_keys_for(PERSON, [confirmed]) == {"github": "buleyfield"}
    assert strong_keys_for(PERSON, [unset_company]) == {}
    assert strong_keys_for(PERSON, [other_company]) == {}


def test_a_cik_needs_the_persons_own_company_not_the_issuer():
    own = doc(
        "edgar",
        "https://filings.example/ownership/0009000901",
        "Reporting person: ULEYFIELD BRANNOC. CIK of reporting person: 0009000901.\n"
        "Issuer: Tenterhook Geodesy. Relationship: Officer.",
    )
    issuer_only = doc(
        "edgar",
        "https://filings.example/ownership/0009000903",
        "Reporting person: ULEYFIELD BRANNOC. CIK of reporting person: 0009000903.\n"
        "Issuer: Kestrel Basin Mining. Relationship: Director. Not an officer. This filing "
        "names no employer for the reporting person.",
    )
    assert strong_keys_for(PERSON, [own]) == {"sec_cik": "0009000901"}
    assert strong_keys_for(PERSON, [issuer_only]) == {}


def test_a_company_domain_comes_from_the_host_not_the_path():
    on_domain = doc(
        "self_page",
        "https://tenterhookgeodesy.example/team/survey",
        "Brannoc Uleyfield is head of survey.",
    )
    path_only = doc(
        "self_page",
        "https://aggregator.example/tenterhookgeodesy/team",
        "Brannoc Uleyfield is head of survey at Tenterhook Geodesy.",
    )
    assert strong_keys_for(PERSON, [on_domain]) == {"company_domain": "tenterhookgeodesy.example"}
    assert strong_keys_for(PERSON, [path_only]) == {}, (
        "a page ABOUT the company on somebody else's host is not the company's domain"
    )


async def test_a_strong_key_resolves_on_one_yes_verdict_alone():
    hedged = doc(
        "search",
        "https://survey-notes.example/2023/aberdeen",
        "A talk was given by a Uleyfield whose employer was not on the slide.",
    )
    llm = scripted(
        (
            WIKIDATA_MATCH,
            verdict(WIKIDATA_MATCH, "yes", 0.93, "Employer: Tenterhook Geodesy. Work location: Aberdeen.", "employer"),
        ),
        (hedged, verdict(hedged, "unsure", 0.4, "whose employer was not on the slide", "role")),
    )
    resolution = await run([WIKIDATA_MATCH, hedged], llm)
    assert resolution.status == "resolved"
    assert resolution.strong_keys == {"wikidata_qid": "Q900000901"}
    assert resolution.accepted_doc_ids == [WIKIDATA_MATCH.doc_id], (
        "the `unsure` document is not accepted just because the person resolved"
    )


async def test_a_strong_key_is_never_taken_from_a_document_that_was_not_accepted():
    """The frozen decoy is this shape: the only Wikidata item belongs to the other person."""
    llm = scripted(
        (
            WIKIDATA_MATCH,
            verdict(WIKIDATA_MATCH, "no", 0.96, "Employer: Tenterhook Geodesy. Work location: Aberdeen.", "role"),
        ),
    )
    resolution = await run([WIKIDATA_MATCH], llm)
    assert resolution.strong_keys == {}
    assert resolution.status == "unresolved"


# ------------------------------------------------------------------- the prompt


def test_the_verdict_prompt_carries_the_person_the_document_and_its_id():
    prompt = verdict_prompt(PERSON, EMPLOYER_DOC)
    assert PERSON.name in prompt
    assert "Head of survey at Tenterhook Geodesy" in prompt
    assert EMPLOYER_DOC.doc_id in prompt
    assert EMPLOYER_DOC.url in prompt
    assert EMPLOYER_DOC.text[:60] in prompt


def test_the_double_and_the_protocol_agree():
    assert_conforms(LLMDouble(), LLMClient)


async def test_llm_error_is_the_contract_for_an_unscripted_call():
    llm = LLMDouble()
    with pytest.raises(LLMError):
        await llm.structured(system="s", user="u", schema=DocVerdict)
