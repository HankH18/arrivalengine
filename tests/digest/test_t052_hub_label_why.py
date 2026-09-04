"""T-052: a hub label is a noun, so naming one must not blank a Meet row's reasoning.

**The defect.** ``graph._why`` builds a Meet row's ``why`` by interpolating a hub LABEL into
a phrase template, and eight of its ten templates -- plus its fallback -- end on a bare token
of ``digest.NOUN_PHRASE_OPENERS``: "both connected to {label}", "both building on {label}",
"both rooted in {label}". (The other two end on "behind", a preposition that list does not
carry, and on the verb "know", so those two hub types never had the defect.) That is where
``digest._splices_a_clause`` looks for a sentence spliced into a noun slot, and its verb
detector reads any capitalised, un-hyphenated, non-ALL-CAPS word of four or more characters
ending in "s", "ed" or "ing" as a verb. So::

    graph emits 'Both connected to Databricks.'  ->  digest ships WHY_OF_LAST_RESORT
    graph emits 'Both connected to Reuters.'     ->  BLANKED
    graph emits 'Both building on Kubernetes.'   ->  BLANKED
    graph emits 'Both deep in Sailing.'          ->  BLANKED

Databricks, Reuters and Kubernetes are ordinary hub labels for this product's population.
R10 says the matcher's reasoning is exposed; the fallback here is not another candidate but
an admitted blank, so refusing the line deletes the reasoning outright.

**What every assertion here grades against.** This ticket owns ``src/arrival/digest.py`` and
``tests/digest/**``, so nothing below compares against either:

* the expected ``why`` is whatever ``arrival.graph.match`` produced for the same input --
  the digest passing its input through unchanged is the property, and ``graph.py`` belongs
  to T-5;
* the corpus family reads ``.swarm-loop/acceptance/fixtures/dossiers``, which is frozen and
  hash-locked, and ``tests/fixtures/dossiers``, which belongs to T-0;
* everything else is a string literal spelled out in this file.

No assertion reads a constant out of ``arrival.digest`` -- in particular not
``WHY_OF_LAST_RESORT``, which is the value under test. "The row was blanked" is spelled as
"what shipped is not what the matcher said", which stays true whatever that constant says.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from t7_digest_helpers import load

from arrival.contracts import (
    Dossier,
    Hub,
    HubContribution,
    Match,
    PersonRef,
    Resolution,
)
from arrival.digest import _speakable_match, is_speakable, make_digest
from arrival.graph import build_graph, match
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")

_REPO = Path(__file__).resolve().parents[2]

#: Both dossier corpora in the repo. Neither is writable by this ticket.
CORPUS_DIRS = (
    _REPO / "tests" / "fixtures" / "dossiers",
    _REPO / ".swarm-loop" / "acceptance" / "fixtures" / "dossiers",
)

#: Every ``HubType`` in ``contracts.py``, spelled out rather than imported so that widening
#: the Literal cannot silently shrink this sweep.
HUB_TYPES = (
    "company",
    "investor",
    "school",
    "board",
    "topic",
    "city",
    "technology",
    "event",
    "cause",
    "person",
)

#: The four labels the defect was reported on, plus the shapes that generalise it. Every one
#: is an ordinary name for this product's population: real companies, a real technology, a
#: real school, two real cities, an ordinary interest.
NOUN_LABELS = (
    "Databricks",
    "Reuters",
    "Kubernetes",
    "Sailing",
    "Boeing",
    "Airbus",
    "Rutgers",
    "Athens",
    "Reading",
    "Redis",
)

#: Multi-word names whose HEAD reads as a verb. Exemption 1 (a word that closes its phrase
#: is not a clause) cannot reach these -- the label continues past the verb-looking word --
#: so they are what proves the second, provenance-based exemption is doing work.
MULTI_WORD_NOUN_LABELS = (
    "Reuters Media Group",
    "Building Futures Fund",
    "Co-founded Partners",
)


# --------------------------------------------------------------------------- construction


def _dossier(person_id: str, name: str, hubs: list[Hub]) -> Dossier:
    """A minimal valid dossier carrying exactly ``hubs`` and no facts.

    Facts are empty because matching never reads them: the ``why`` under test is built from
    hubs alone.
    """
    return Dossier(
        person=PersonRef(person_id=person_id, name=name),
        resolution=Resolution(
            person_id=person_id,
            status="resolved",
            accepted_doc_ids=[],
            rejected=[],
            confidence=1.0,
        ),
        facts=[],
        hubs=hubs,
        built_at=dt.datetime(2026, 9, 4, tzinfo=dt.UTC),
    )


def _matched_on(label: str, hub_type: str) -> Match:
    """The ``Match`` a pair sharing exactly one hub of this ``(label, type)`` receives.

    Built through ``build_graph``/``match`` rather than by hand, so the ``why`` under test is
    the sentence the product actually emits and the contributions are the ones T-5 attaches.
    Four hubless fillers set N so the shared hub clears the IDF clamp.
    """
    hub = Hub(hub_id=f"{hub_type}:only", label=label, type=hub_type, evidence_fact_ids=[])
    people = [_dossier("a", "A", [hub]), _dossier("b", "B", [hub])]
    people += [_dossier(f"f{n}", f"Filler {n}", []) for n in range(4)]
    matches = match(build_graph(people), "a", ["b"])
    assert matches and matches[0].contributions, (
        f"positive control: {label!r}/{hub_type} scored nothing, so no why names it"
    )
    return matches[0]


def _every_why(directory: Path) -> list[tuple[str, str, Match]]:
    """``(arriving, other, match)`` for every ordered pair a corpus can produce."""
    paths = sorted(directory.glob("*.json"))
    assert paths, f"corpus is empty or missing: {directory}"
    dossiers = [
        Dossier.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in paths
    ]
    graph = build_graph(dossiers)
    ids = sorted(d.person.person_id for d in dossiers)
    return [
        (a, m.other.person_id, m)
        for a in ids
        for m in match(graph, a, [p for p in ids if p != a])
    ]


# ------------------------------------------------- the defect, on the four reported labels


@pytest.mark.parametrize(
    ("label", "hub_type", "expected"),
    [
        ("Databricks", "company", "Both connected to Databricks."),
        ("Reuters", "company", "Both connected to Reuters."),
        ("Kubernetes", "technology", "Both building on Kubernetes."),
        ("Sailing", "topic", "Both deep in Sailing."),
    ],
)
def test_the_four_reported_labels_reach_the_page_instead_of_being_blanked(
    label, hub_type, expected
):
    """The exact before/after this ticket exists for, through the real path.

    ``expected`` is a literal, so this grades the SENTENCE and not merely "the digest agreed
    with itself": it fails both if the digest blanks the row and if ``graph._why`` stops
    producing the line the defect was reported on.
    """
    produced = _matched_on(label, hub_type)
    assert produced.why == expected, (
        f"graph no longer emits the reported line for {label!r}: {produced.why!r}"
    )

    shipped = _speakable_match(produced).why

    assert shipped == expected, (
        f"the Meet row's reasoning was replaced by a fallback: {expected!r} -> {shipped!r}"
    )
    assert label in shipped, f"the hub label did not survive to the page: {shipped!r}"


@pytest.mark.parametrize("label", NOUN_LABELS + MULTI_WORD_NOUN_LABELS)
@pytest.mark.parametrize("hub_type", HUB_TYPES)
def test_a_noun_label_survives_every_hub_type(label, hub_type):
    """Every hub type, every label shape: what the matcher said is what the host reads.

    The answer key is ``graph.match``'s own output, which this ticket does not own. Ten
    types times thirteen labels is the sweep that showed 118 of 330 lines blanked before
    this change and none after.
    """
    produced = _matched_on(label, hub_type)

    shipped = _speakable_match(produced).why

    assert shipped == produced.why, (
        f"[{hub_type}] the digest refused the matcher's own sentence and blanked the row: "
        f"{produced.why!r} -> {shipped!r}"
    )


@pytest.mark.parametrize("label", NOUN_LABELS)
def test_a_single_word_noun_label_is_speakable_without_any_declaration(label):
    """Exemption 1, which needs no ``Match``: a word that closes its phrase is not a clause.

    This is the half that also protects the callers with nothing to declare -- the LLM
    opener, and ``tests/graph``'s own tripwire, which asks ``is_speakable(why)`` bare.
    """
    line = f"Both connected to {label}."

    assert is_speakable(line), f"a one-word name at the close of the phrase was refused: {line!r}"


def test_a_label_that_needed_mechanical_repair_is_still_not_blanked():
    """The declaration has to reach the SECOND judgement too, the one on the repaired line.

    ``_speakable_match`` repairs the five mechanical clauses and then re-checks; a label
    carrying a parenthetical goes down that branch. If only the first check knows the label,
    the row still blanks -- with the parenthesis gone and the name intact, which is the
    version of this defect a reader would never think to look for.
    """
    produced = _matched_on("Reuters (Media) Group", "company")
    assert "(" in produced.why, f"positive control: nothing to repair in {produced.why!r}"

    shipped = _speakable_match(produced).why

    assert shipped == "Both connected to Reuters Group.", (
        f"the repaired why was blanked instead of shown: {shipped!r}"
    )


# ------------------------------------------------------- the corpora, end to end (R10)


@pytest.mark.parametrize("directory", CORPUS_DIRS, ids=lambda p: p.parent.parent.name)
def test_no_why_in_either_corpus_is_blanked_on_its_way_to_the_page(directory):
    """The product property, on the two corpora this repo actually grades against.

    Not a snapshot: the expected value is the matcher's own sentence for that same pair, so
    this stays honest if the corpora or the phrasing change.
    """
    for arriving, other, produced in _every_why(directory):
        shipped = _speakable_match(produced).why
        assert shipped == produced.why, (
            f"{arriving} -> {other}: the digest replaced the matcher's reasoning with a "
            f"fallback: {produced.why!r} -> {shipped!r}"
        )


# --------------------------------------------------- the other direction: a splice stays out


#: Lines a host cannot read aloud because a CLAUSE was put where a noun phrase belongs. Each
#: verb is followed by the rest of its own clause, which is what makes it a clause and not a
#: name. The first is T-029's reproduction, verbatim.
SPLICED_LINES = (
    "Ask about Argues that developer-tools pricing should be published in full on a public page.",
    "Ask about Lives in Austin and leads an operations team.",
    "Ask about Led the Foundry Seed 2019 fund.",
    "Ask about Studied at Bellhaven Polytechnic and returned to teach.",
    "Ask about Co-founded Quarrystone Labs in 2016.",
    "Curious about Maintains the Quarrystone command line tool.",
    "Ask about Has lived in Austin since 2014.",
    "Ask about Publishes the scoring rules it uses to assign lanes.",
    "Ask about Building a status page for the platform team.",
)


@pytest.mark.parametrize("line", SPLICED_LINES)
def test_a_spliced_clause_is_still_refused_with_nothing_declared(line):
    """T-029's property, unchanged: the opener path declares no nouns, so nothing is exempt."""
    assert not is_speakable(line), f"a host cannot read this aloud as written: {line!r}"


@pytest.mark.parametrize("line", SPLICED_LINES)
def test_a_spliced_clause_is_refused_even_when_a_hub_label_is_declared(line):
    """The exemption is a SPAN, not a licence: declaring one name does not admit a sentence.

    A ``Match`` that legitimately names "Databricks" gets no cover for a clause spliced
    elsewhere in the same line, because the declared phrase does not occur there.
    """
    assert not is_speakable(line, noun_phrases=["Databricks", "Reuters Media Group"]), (
        f"declaring an unrelated hub label admitted a spliced clause: {line!r}"
    )


def test_a_word_the_label_happens_to_contain_does_not_exempt_it_elsewhere():
    """The declaration covers a SEQUENCE, not a vocabulary.

    "Building Futures Fund" is a legitimate hub label, and "Building" is also a real verb.
    Exempting the label must not exempt the second "Building", which opens a clause of its
    own after a bare "about". A rule that exempted any word appearing anywhere in a declared
    label would admit this line, and every test above would stay green -- found by
    sabotage, not by reading.
    """
    line = "Both connected to Building Futures Fund and about Building a status page."

    assert not is_speakable(line, noun_phrases=["Building Futures Fund"]), (
        f"a word borrowed from the label exempted a clause it does not cover: {line!r}"
    )
    assert is_speakable("Both connected to Building Futures Fund.", noun_phrases=[
        "Building Futures Fund"
    ]), "positive control: the declared label itself must still be exempt"


def test_only_the_declared_span_is_exempt_inside_a_real_why():
    """A why that names a legitimate hub AND splices a clause is still refused.

    The Match declares "Databricks", the sentence is otherwise a splice, and the row must
    still be blanked -- the assertion is spelled as "what shipped is not what came in", so it
    does not read the fallback constant out of the module under test.
    """
    hub = Hub(hub_id="company:databricks", label="Databricks", type="company")
    spliced = (
        "Both connected to Databricks and about Argues that pricing must be published."
    )
    produced = Match(
        other=PersonRef(person_id="b", name="B"),
        score=50.0,
        contributions=[
            HubContribution(
                hub=hub, idf_weight=0.51, recency=1.0, type_boost=1.0, contribution=0.51
            )
        ],
        path=["person:a", "hub:company:databricks", "person:b"],
        why=spliced,
    )

    shipped = _speakable_match(produced).why

    assert shipped != spliced, (
        "a clause spliced outside the declared label span was passed through to the host: "
        f"{shipped!r}"
    )
    assert "Argues that pricing" not in shipped, (
        f"the spliced clause reached the page anyway: {shipped!r}"
    )


async def test_the_opener_path_still_refuses_a_model_line_that_splices_a_sentence():
    """The model's own line gets no exemption: ``_validate_opener`` declares no nouns.

    Exercised through ``make_digest`` so it is the real path, not the predicate alone.
    """
    spliced = "Ask about Argues that pricing should be published in full on a public page."
    llm = LLMDouble()
    llm.queue({"line": spliced})

    digest = await make_digest(_arriving_with_one_hook(), [], llm)

    assert digest.say_out_loud != spliced, (
        f"a spliced model line reached the host: {digest.say_out_loud!r}"
    )


async def test_a_model_line_naming_only_a_noun_is_no_longer_thrown_away():
    """The same defect on the opener path: "Ask about Databricks." was refused as a splice.

    R14 asks for an invitation and this is one; the old rule read the name as a verb and
    discarded the model's line for the template fallback.
    """
    good = "Ask about Databricks."
    llm = LLMDouble()
    llm.queue({"line": good})

    digest = await make_digest(_arriving_with_one_hook(), [], llm)

    assert digest.say_out_loud == good, (
        f"a valid one-name invitation was discarded: {digest.say_out_loud!r}"
    )


def _arriving_with_one_hook() -> Dossier:
    """T-0's ``alpha`` fixture: a dossier with a displayable, speakable opener hook.

    Loaded rather than hand-built so the opener tests run against a record this ticket
    cannot write. ``tests/fixtures/dossiers`` belongs to T-0.
    """
    return load("alpha")
