"""Two properties nothing else in the suite asserts, both about things nobody chose.

1. **A strong key is a durable identifier, so it must not be decided by arrival order.**
   `research._interleave` hands `resolve` a document list whose order is a function of how
   many results each remote API happened to return and in what ranking, so "the first
   accepted document that matches" makes `Resolution.strong_keys` a function of the
   internet's mood. Every assertion here is a PERMUTATION property — the same documents in
   a different order must produce the same key — which is why it cannot be satisfied by
   writing an answer key into a fixture: there is no expected value to write, only an
   agreement between two runs of the gradee.

2. **`Verdict.disambiguator` is free text the model chose, so it must not be the thing that
   decides whether a person exists.** Decision 4's second arm counts INDEPENDENT
   attributes; the attribute a verdict corroborates is a property of its evidence measured
   against the person's own details, and only the leftovers fall back to the label.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from arrival.contracts import PersonRef, RawDoc
from arrival.resolve import (
    DocVerdict,
    attribute_family,
    resolve,
    strong_keys_for,
    verdict_attribute,
)
from arrival.util import doc_id as make_doc_id
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-2")

FETCHED = dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC)

PERSON = PersonRef(
    person_id="oriane-quarrystone",
    name="Oriane Quarrystone",
    details=["Head of research at Quarrystone Labs", "Trondheim"],
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


def scripted(*triples) -> LLMDouble:
    """`(doc, match, evidence, disambiguator)` triples, one scripted verdict each."""
    llm = LLMDouble()
    for doc_, match, evidence, disambiguator in triples:
        llm.when(
            "DocVerdict",
            doc_.doc_id,
            DocVerdict(
                doc_id=doc_.doc_id,
                match=match,
                confidence=0.8,
                evidence=evidence,
                disambiguator=disambiguator,
            ),
        )
    return llm


# ------------------------------------------------------------------ half one: keys


APEX = doc(
    "self_page",
    "https://quarrystonelabs.example/team/research",
    "Oriane Quarrystone leads research at Quarrystone Labs from Trondheim.",
)
BLOG = doc(
    "search",
    "https://blog.quarrystonelabs.example/2025/field-notes",
    "A field note by Oriane Quarrystone, head of research at Quarrystone Labs.",
)


def test_a_company_domain_is_the_same_key_whichever_document_arrived_first():
    """`blog.<company>` and `<company>` are one company, and one durable identifier."""
    forwards = strong_keys_for(PERSON, [APEX, BLOG])
    backwards = strong_keys_for(PERSON, [BLOG, APEX])
    assert forwards == backwards, (
        "the same accepted documents in a different order produced a different "
        f"company_domain: {forwards} vs {backwards}"
    )
    assert forwards == {"company_domain": "quarrystonelabs.example"}, (
        "a subdomain of the employer's domain must canonicalise to the registrable domain, "
        "not become a second identifier for the same company"
    )


WIKIDATA_BOTH_DETAILS = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000701",
    "Oriane Quarrystone (Q900000701)\n\nItem mirror. Instance of: human. Employer: "
    "Quarrystone Labs. Work location: Trondheim.",
    title="Oriane Quarrystone (Q900000701) - item mirror",
)
WIKIDATA_ONE_DETAIL = doc(
    "wikidata",
    "https://wikidata.example/entity/Q900000702",
    "Oriane Quarrystone (Q900000702)\n\nItem mirror. Instance of: human. Occupation: "
    "researcher. Work location: Trondheim. This item carries no employer statement.",
    title="Oriane Quarrystone (Q900000702) - item mirror",
)


def test_the_wikidata_item_matching_more_details_wins_whichever_arrived_first():
    """Evidence decides which QID is the person's, never `_interleave`'s ordering."""
    forwards = strong_keys_for(PERSON, [WIKIDATA_BOTH_DETAILS, WIKIDATA_ONE_DETAIL])
    backwards = strong_keys_for(PERSON, [WIKIDATA_ONE_DETAIL, WIKIDATA_BOTH_DETAILS])
    assert forwards == backwards, (
        f"arrival order changed the QID strong key: {forwards} vs {backwards}"
    )
    assert forwards == {"wikidata_qid": "Q900000701"}, (
        "the item matching name AND both details must beat the item matching one of them"
    )


def test_two_equally_matched_wikidata_items_mint_no_qid_at_all():
    """R2 in the strong-key arm: an identifier we cannot separate on evidence is a guess."""
    twin = doc(
        "wikidata",
        "https://wikidata.example/entity/Q900000703",
        "Oriane Quarrystone (Q900000703)\n\nItem mirror. Instance of: human. Employer: "
        "Quarrystone Labs. Work location: Trondheim.",
        title="Oriane Quarrystone (Q900000703) - item mirror",
    )
    forwards = strong_keys_for(PERSON, [WIKIDATA_BOTH_DETAILS, twin])
    backwards = strong_keys_for(PERSON, [twin, WIKIDATA_BOTH_DETAILS])
    assert forwards == backwards
    assert "wikidata_qid" not in forwards, (
        "two items matching the person equally well are two candidate humans; picking "
        "either one is arrival order wearing the costume of evidence"
    )


GITHUB_A = doc(
    "github",
    "https://code-host.example/users/oquarrystone",
    "Name: Oriane Quarrystone\nCompany: Quarrystone Labs\nLocation: Trondheim",
)
GITHUB_B = doc(
    "github",
    "https://code-host.example/users/orianeq",
    "Name: Oriane Quarrystone\nCompany: Quarrystone Labs\nLocation: Oslo",
)
EDGAR_A = doc(
    "edgar",
    "https://filings.example/ownership/0009000701",
    "Reporting person: QUARRYSTONE ORIANE. CIK of reporting person: 0009000701.\n"
    "Issuer: Quarrystone Labs. Relationship: Officer. Address: Trondheim.",
)
EDGAR_B = doc(
    "edgar",
    "https://filings.example/ownership/0009000702",
    "Reporting person: QUARRYSTONE ORIANE. CIK of reporting person: 0009000702.\n"
    "Issuer: Quarrystone Labs. Relationship: Officer.",
)


def test_a_github_handle_is_decided_by_the_profile_not_by_arrival_order():
    forwards = strong_keys_for(PERSON, [GITHUB_A, GITHUB_B])
    backwards = strong_keys_for(PERSON, [GITHUB_B, GITHUB_A])
    assert forwards == backwards, (
        f"arrival order changed the github strong key: {forwards} vs {backwards}"
    )
    assert forwards == {"github": "oquarrystone"}, (
        "the profile confirming the city as well as the company is the better-evidenced one"
    )


def test_a_sec_cik_is_decided_by_the_filing_not_by_arrival_order():
    forwards = strong_keys_for(PERSON, [EDGAR_A, EDGAR_B])
    backwards = strong_keys_for(PERSON, [EDGAR_B, EDGAR_A])
    assert forwards == backwards, (
        f"arrival order changed the sec_cik strong key: {forwards} vs {backwards}"
    )
    assert forwards == {"sec_cik": "0009000701"}, (
        "the filing matching name, company AND city outranks the one matching two of them"
    )


def test_every_arrival_order_of_a_mixed_corpus_yields_the_same_strong_keys():
    """The property in general: `strong_keys_for` is invariant under permutation."""
    docs = [APEX, BLOG, WIKIDATA_BOTH_DETAILS, WIKIDATA_ONE_DETAIL, GITHUB_A, EDGAR_A]
    baseline = strong_keys_for(PERSON, docs)
    assert baseline, "pre-condition: this corpus must earn at least one key"
    for permutation in itertools.permutations(docs):
        assert strong_keys_for(PERSON, list(permutation)) == baseline, (
            f"order {[d.doc_id for d in permutation]} gave "
            f"{strong_keys_for(PERSON, list(permutation))}, not {baseline}"
        )


def test_strong_keys_come_back_in_the_documented_priority_order():
    """T-2 acceptance 2 lists the keys in an order; the mapping must preserve it."""
    docs = [EDGAR_A, GITHUB_A, APEX, WIKIDATA_BOTH_DETAILS]
    keys = list(strong_keys_for(PERSON, docs))
    assert keys == ["wikidata_qid", "company_domain", "github", "sec_cik"], (
        "the acceptance criterion names QID, then company domain, then GitHub, then CIK"
    )


# ------------------------------------------------- half two: the model's word choice


PLAIN_A = doc(
    "search",
    "https://survey-notes.example/2025/closing",
    "The closing talk was given by Oriane Quarrystone, who ran over by ten minutes.",
)
PLAIN_B = doc(
    "search",
    "https://survey-notes.example/2024/panel",
    "Oriane Quarrystone chaired the 2024 panel and wrote up the notes afterwards.",
)
EMPLOYER_A = doc(
    "search",
    "https://industry-weekly.example/2025/labs",
    "A statement from Quarrystone Labs was read out by Oriane Quarrystone.",
)
EMPLOYER_B = doc(
    "search",
    "https://industry-weekly.example/2024/labs",
    "Oriane Quarrystone signed the Quarrystone Labs submission.",
)
CITY_DOC = doc(
    "podcast",
    "https://audio.example/episodes/12",
    "Oriane Quarrystone has worked out of Trondheim since 2018.",
)


def test_two_spellings_of_one_attribute_are_one_attribute_beyond_employer_and_city():
    """`attribute_family` canonicalised only the two families the veto needed."""
    assert attribute_family("role") == attribute_family("job title") == "role"
    assert attribute_family("Position") == attribute_family("occupation") == "role"
    assert attribute_family("handle") == attribute_family("username") == "handle"
    assert attribute_family("school") == attribute_family("university") == "school"
    assert attribute_family("coauthor") == attribute_family("collaborator") == "coauthor"
    # The six families the system prompt itself enumerates stay six distinct families.
    families = {
        attribute_family(label)
        for label in ("employer", "city", "role", "handle", "school", "coauthor")
    }
    assert len(families) == 6


async def test_whether_a_person_exists_does_not_turn_on_the_models_word_choice():
    """Two verdicts corroborating nothing beyond the name are one attribute, spelled twice."""
    same_word = scripted(
        (PLAIN_A, "yes", "The closing talk was given by Oriane Quarrystone", "role"),
        (PLAIN_B, "yes", "Oriane Quarrystone chaired the 2024 panel", "role"),
    )
    two_words = scripted(
        (PLAIN_A, "yes", "The closing talk was given by Oriane Quarrystone", "role"),
        (PLAIN_B, "yes", "Oriane Quarrystone chaired the 2024 panel", "job title"),
    )
    repeated = await resolve(PERSON, [PLAIN_A, PLAIN_B], same_word)
    respelled = await resolve(PERSON, [PLAIN_A, PLAIN_B], two_words)
    assert repeated.status == respelled.status, (
        "the identical documents and the identical evidence resolved differently because "
        f"the model wrote 'job title' instead of 'role': {repeated.status} vs "
        f"{respelled.status}"
    )
    assert respelled.status == "unresolved", (
        "neither verdict corroborates an identifying detail, so there is one attribute "
        "here at most and R2 says refuse to guess"
    )


async def test_the_attribute_counted_is_the_one_the_evidence_corroborates():
    """A mislabelled verdict corroborating the employer is still an employer verdict."""
    llm = scripted(
        (EMPLOYER_A, "yes", "A statement from Quarrystone Labs was read out", "employer"),
        (EMPLOYER_B, "yes", "Oriane Quarrystone signed the Quarrystone Labs submission", "handle"),
    )
    resolution = await resolve(PERSON, [EMPLOYER_A, EMPLOYER_B], llm)
    assert resolution.status == "unresolved", (
        "both spans corroborate the employer and nothing else; calling the second one "
        "`handle` manufactures independence out of a word"
    )
    assert resolution.accepted_doc_ids == []

    # Sabotage companion: the same shape with a span that really does corroborate the
    # second detail. Without this half a resolver that never resolves anything passes.
    control = scripted(
        (EMPLOYER_A, "yes", "A statement from Quarrystone Labs was read out", "employer"),
        (CITY_DOC, "yes", "has worked out of Trondheim since 2018", "handle"),
    )
    resolved = await resolve(PERSON, [EMPLOYER_A, CITY_DOC], control)
    assert resolved.status == "resolved", (
        "employer plus city is two independent attributes however the model spelled them"
    )
    assert resolved.accepted_doc_ids == [EMPLOYER_A.doc_id, CITY_DOC.doc_id]


def test_verdict_attribute_prefers_the_label_when_the_evidence_agrees_with_it():
    """A span naming both details is not silently re-filed under the other one."""
    from arrival.contracts import Verdict

    both = Verdict(
        doc_id="a" * 16,
        match="yes",
        confidence=0.9,
        evidence="Oriane Quarrystone has led Quarrystone Labs out of Trondheim since 2018",
        disambiguator="city",
    )
    assert verdict_attribute(PERSON, both) == "city"
    assert verdict_attribute(PERSON, both.model_copy(update={"disambiguator": "employer"})) == (
        "employer"
    )
    # An off-contract label over evidence that corroborates nothing names no attribute we
    # can tell apart from any other off-contract label.
    invented = Verdict(
        doc_id="b" * 16,
        match="yes",
        confidence=0.5,
        evidence="Oriane Quarrystone chaired the 2024 panel",
        disambiguator="vibes",
    )
    other = invented.model_copy(update={"disambiguator": "aura"})
    assert verdict_attribute(PERSON, invented) == verdict_attribute(PERSON, other)
