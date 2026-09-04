"""T-077: the extractor must emit hubs two DIFFERENT people can actually land on.

Measured before this ticket, on the live ten-person corpus in `data/dossiers/`: 68 hubs
across 7 resolved people, every one of them carried by exactly ONE person, and 1 of the 21
pairs sharing anything. Every hub was a proper noun of a specific entity — Coinbase, Twitch,
IMVU, Etsy — and nobody shares a portfolio company by accident. Five of the ten members are
based in San Francisco and the graph knew none of it; five are venture capitalists and the
graph knew none of that either.

Two additions close that, and each carries its own refusal, because a fabricated connection
is worse than none (R2, applied to hubs):

* a FIELD hub — an abstraction over entities — drawn from a closed vocabulary, so that two
  people whose documents say "seed-stage venture capital firm" and "worked in venture
  capital" reach ONE node rather than two;
* a CITY hub built from the club's own roster detail and corroborated by a document, so that
  a place is never inferred from a sentence about somebody else's office.

Every expectation here is a literal written in this file or a name from `arrival.contracts`.
Nothing is compared against a constant in `arrival.extract`, which this ticket owns — a
vocabulary test that read the vocabulary would measure nothing.
"""

from __future__ import annotations

import pytest
import t077_corpus as corpus

from arrival.contracts import Hub, HubType
from arrival.extract import (
    CandidateFact,
    CandidateHub,
    ExtractionResult,
    ExtractionStats,
    extract,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-3")

#: DESIGN Decision 3, quoted rather than imported: this is the list a field vocabulary may
#: never re-admit, and reading it out of `arrival.extract` would let a change to the module
#: silently change the requirement. Identical to the set pinned by
#: `tests/extract/test_t3_hubs.py::test_the_stop_list_is_the_one_design_decision_3_names`.
DESIGN_STOP_HUBS = frozenset(
    {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}
)


def _fact(doc, text, quote, fact_id):
    return CandidateFact(doc_id=doc.doc_id, text=text, quote=quote, fact_id=fact_id)


async def _run(person, docs, result, stats=None):
    llm = LLMDouble()
    llm.queue(result)
    return await extract(
        person, corpus.resolution_for(person, *docs), list(docs), llm, stats=stats
    )


def _ids(hubs: list[Hub]) -> set[str]:
    return {hub.hub_id for hub in hubs}


# --------------------------------------------------------------------------
# the field hub: an abstraction two people can share
# --------------------------------------------------------------------------


async def test_two_people_describing_one_field_differently_reach_the_same_hub():
    """The headline defect. Neither document uses the other's words for the same field.

    This is the assertion that has to hold for person-to-person matching to exist at all:
    the hub id must be a function of the FIELD, not of the sentence one document happened
    to use. Deliberately makes no claim about WHICH id — naming it would grade the
    vocabulary against itself.
    """
    harlow_doc, bridges_doc = corpus.harlow_doc(), corpus.bridges_doc()
    _facts, harlow_hubs = await _run(
        corpus.HARLOW,
        [harlow_doc],
        ExtractionResult(
            facts=[_fact(harlow_doc, "Ada Harlow is a partner at Quillmark Capital.",
                         corpus.HARLOW_SPAN, "a")],
            hubs=[CandidateHub(label="seed-stage venture capital", type="topic",
                               evidence_fact_ids=["a"], field=True)],
        ),
    )
    _facts, bridges_hubs = await _run(
        corpus.BRIDGES,
        [bridges_doc],
        ExtractionResult(
            facts=[_fact(bridges_doc, "Ines Bridges founded Larkfield Group.",
                         corpus.BRIDGES_SPAN, "b")],
            hubs=[CandidateHub(label="venture capital", type="topic",
                               evidence_fact_ids=["b"], field=True)],
        ),
    )

    shared = _ids(harlow_hubs) & _ids(bridges_hubs)
    assert shared, (
        "two people whose documents describe one field in different words must reach one "
        f"hub: {sorted(_ids(harlow_hubs))} vs {sorted(_ids(bridges_hubs))}"
    )
    for hubs in (harlow_hubs, bridges_hubs):
        held = {hub.hub_id: hub for hub in hubs}
        for hub_id in shared:
            assert held[hub_id].type in set(HubType.__args__), "a field must be a real HubType"
            assert held[hub_id].evidence_fact_ids, "a field hub must cite something"


async def test_a_field_the_documents_do_not_name_is_refused():
    """The mechanical half of the guard. The model may not attach a field to any fact.

    A named entity can be slightly misspelled; a field is a CLAIM about what somebody does,
    and a model that has read one funding round can make it about anybody. So the phrase
    must be in the document the cited fact came from.
    """
    doc = corpus.norell_doc()
    stats = ExtractionStats()
    _facts, hubs = await _run(
        corpus.NORELL,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Tomas Norell writes essays and reviews.",
                         corpus.NORELL_SPAN, "a")],
            hubs=[CandidateHub(label="venture capital", type="topic",
                               evidence_fact_ids=["a"], field=True)],
        ),
        stats=stats,
    )
    assert not hubs, f"a field no document states must not become a node: {_ids(hubs)}"
    assert stats.hubs_kept == 0


async def test_a_one_word_field_is_refused_however_the_model_types_it():
    """Every DESIGN Decision 3 stop hub is a bare single word, and that is not a coincidence.

    A field sayable in one word is the shape the decision banned, whether or not this
    particular word is on its list.
    """
    doc = corpus.harlow_doc()
    _facts, hubs = await _run(
        corpus.HARLOW,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Ada Harlow is a partner at Quillmark Capital.",
                         corpus.HARLOW_SPAN, "a")],
            hubs=[CandidateHub(label="Boards", type="topic",
                               evidence_fact_ids=["a"], field=True)],
        ),
    )
    assert not hubs, f"a one-word field must never be a node: {_ids(hubs)}"


async def test_no_field_hub_can_carry_a_design_decision_3_stop_label():
    """The abstraction layer may not re-admit through the front door what the stop list bans.

    Asserted through `extract`, over every label the decision names, so it holds whatever
    the vocabulary contains — including a vocabulary someone widens later.
    """
    doc = corpus.harlow_doc()
    for banned in sorted(DESIGN_STOP_HUBS):
        _facts, hubs = await _run(
            corpus.HARLOW,
            [doc],
            ExtractionResult(
                facts=[_fact(doc, "Ada Harlow is a partner at Quillmark Capital.",
                             corpus.HARLOW_SPAN, "a")],
                hubs=[CandidateHub(label=banned, type="topic",
                                   evidence_fact_ids=["a"], field=True)],
            ),
        )
        assert not hubs, f"the stop label {banned!r} came back as a field hub: {_ids(hubs)}"


async def test_a_person_carries_at_most_two_fields_however_many_are_proposed():
    """A named entity is self-limiting; a field is generated, and would not be.

    Nobody has forty employers, but a model asked what somebody works on will produce eight
    overlapping paraphrases of one career, each of which joins its owner to somebody.
    """
    doc = corpus.harlow_doc()
    proposed = [
        "seed-stage venture capital",
        "developer tools",
        "private equity",
        "enterprise software",
        "open source",
    ]
    _facts, hubs = await _run(
        corpus.HARLOW,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Ada Harlow is a partner at Quillmark Capital.",
                         corpus.HARLOW_SPAN, "a")],
            hubs=[
                CandidateHub(label=label, type="topic", evidence_fact_ids=["a"], field=True)
                for label in proposed
            ],
        ),
    )
    assert len(hubs) <= 2, f"a person's abstraction budget was exceeded: {_ids(hubs)}"


async def test_the_field_cap_does_not_depend_on_the_order_the_model_listed_them():
    """Whatever survives the cap, it may not be chosen by the model's output ordering."""
    doc = corpus.harlow_doc()
    proposed = ["developer tools", "private equity", "enterprise software", "open source"]

    def result(order):
        return ExtractionResult(
            facts=[_fact(doc, "Ada Harlow is a partner at Quillmark Capital.",
                         corpus.HARLOW_SPAN, "a")],
            hubs=[
                CandidateHub(label=label, type="topic", evidence_fact_ids=["a"], field=True)
                for label in order
            ],
        )

    _f, forwards = await _run(corpus.HARLOW, [doc], result(proposed))
    _f, backwards = await _run(corpus.HARLOW, [doc], result(list(reversed(proposed))))
    assert _ids(forwards) == _ids(backwards), "the cap let output order pick the survivors"


async def test_a_named_entity_is_untouched_by_the_field_rules():
    """The guards apply to `field`, never to a name. A one-word company is still a company."""
    doc = corpus.harlow_doc()
    _facts, hubs = await _run(
        corpus.HARLOW,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Ada Harlow is a partner at Quillmark Capital.",
                         corpus.HARLOW_SPAN, "a")],
            hubs=[CandidateHub(label="Quillmark", type="company", evidence_fact_ids=["a"])],
        ),
    )
    assert [hub.label for hub in hubs] == ["Quillmark"]
    assert [hub.type for hub in hubs] == ["company"]


# --------------------------------------------------------------------------
# the city hub: the one thing the club has first-party knowledge of
# --------------------------------------------------------------------------


async def test_the_members_own_city_becomes_a_hub_when_a_document_names_it():
    """Cause 2. The roster states the place; the document corroborates it; both are needed."""
    doc = corpus.harlow_doc()
    _facts, hubs = await _run(
        corpus.HARLOW,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Ada Harlow has backed developer tools companies.",
                         corpus.HARLOW_CITY_SPAN, "a")],
            hubs=[],
            based_in="Porthaven",
        ),
    )
    cities = [hub for hub in hubs if hub.type == "city"]
    assert len(cities) == 1, f"expected the roster's own place as one hub, got {_ids(hubs)}"
    assert cities[0].label == "Porthaven"
    assert cities[0].evidence_fact_ids, "a city hub must cite the document that named it"


async def test_two_members_of_one_city_reach_one_node_though_the_roster_spells_it_differently():
    """"Porthaven" and "Porthaven, East Riding" are one place and must be one node.

    The live roster spells four of its ten entries "City, Region" and the rest bare, so a
    join that depended on the roster being consistent would find nothing.
    """
    harlow_doc, bridges_doc = corpus.harlow_doc(), corpus.bridges_doc()
    _f, harlow_hubs = await _run(
        corpus.HARLOW,
        [harlow_doc],
        ExtractionResult(
            facts=[_fact(harlow_doc, "Ada Harlow has backed developer tools companies.",
                         corpus.HARLOW_CITY_SPAN, "a")],
            based_in="Porthaven",
        ),
    )
    _f, bridges_hubs = await _run(
        corpus.BRIDGES,
        [bridges_doc],
        ExtractionResult(
            facts=[_fact(bridges_doc, "Larkfield keeps its only office in Porthaven.",
                         corpus.BRIDGES_CITY_SPAN, "b")],
            based_in="Porthaven, East Riding",
        ),
    )
    shared = _ids(harlow_hubs) & _ids(bridges_hubs)
    assert shared, (
        "two members of one city must share a node whatever the roster's spelling: "
        f"{sorted(_ids(harlow_hubs))} vs {sorted(_ids(bridges_hubs))}"
    )


async def test_a_place_the_roster_does_not_state_is_discarded():
    """`based_in` is confirmed against the details, never adopted from the model.

    Confirm-or-refuse, the discipline `_hub_qid` already applies to a QID, applied to
    somewhere a host will say out loud.
    """
    doc = corpus.norell_doc()
    _facts, hubs = await _run(
        corpus.NORELL,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Tomas Norell is a fellow of the Marrowfield Institute.",
                         corpus.NORELL_CITY_SPAN, "a")],
            hubs=[],
            based_in="Porthaven",
        ),
    )
    assert not [hub for hub in hubs if hub.type == "city"], (
        f"a place the roster never states must not become a hub: {_ids(hubs)}"
    )


async def test_a_city_the_roster_contradicts_is_replaced_by_the_one_it_states():
    """The live false-positive shape: the city belongs to an institution, not the member.

    "He is a fellow of the Marrowfield Institute, a Porthaven-based research body" places
    the INSTITUTE, and a member the roster puts elsewhere must not inherit it.
    """
    doc = corpus.bridges_doc()
    _facts, hubs = await _run(
        corpus.BRIDGES,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Larkfield keeps its only office in Porthaven.",
                         corpus.BRIDGES_CITY_SPAN, "b")],
            hubs=[CandidateHub(label="Calderstane", type="city", evidence_fact_ids=["b"])],
            based_in="Porthaven, East Riding",
        ),
    )
    labels = {hub.label for hub in hubs if hub.type == "city"}
    assert "Calderstane" not in labels, f"a roster-contradicted city survived: {labels}"


async def test_a_member_with_no_roster_place_keeps_the_city_their_documents_support():
    """Silence in the roster is not a claim that the member is nowhere.

    Measured on the live corpus: the one member whose details give no city has a
    `city:new-york-city` hub read out of Wikidata's structured "work location" field. A veto
    that fired on roster silence would delete it.
    """
    doc = corpus.norell_doc()
    _facts, hubs = await _run(
        corpus.NORELL,
        [doc],
        ExtractionResult(
            facts=[_fact(doc, "Tomas Norell is a fellow of the Marrowfield Institute.",
                         corpus.NORELL_CITY_SPAN, "a")],
            hubs=[CandidateHub(label="Porthaven", type="city", evidence_fact_ids=["a"])],
        ),
    )
    assert "Porthaven" in {hub.label for hub in hubs if hub.type == "city"}, (
        f"roster silence deleted a documented city: {_ids(hubs)}"
    )
