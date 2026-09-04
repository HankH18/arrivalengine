"""T-029 and T-033: the opener is English, and nothing on the page depends on list order.

Two defects, both reproduced end-to-end before a line was changed, both invisible to the
suite that was already green.

**T-029 — the spoken line was ungrammatical on the demo's default path.**
``OPENER_TEMPLATE`` spliced a fact sentence into the object slot of "about", so the fallback
opener read "Ask about Argues that developer-tools pricing should be published in full on a
public page." That is the path taken on every timeout, every transport error, every rejected
model line, and on any deploy with no API key — which is the default. R18 exists to stop the
host stumbling and ``is_speakable`` graded five mechanical hazards that this line does not
trip, so it passed validation. Both halves are tested here: the template, and the predicate
that has to fail the old template if anyone restores it.

**T-033 — two order dependencies.**
``_capped_meet`` sorted on ``Match.score`` with no tiebreaker while ``score`` is
``round(100 * raw / ref)``, so ties are the normal case; it was safe only because
``graph.match``'s own sort happens to be total, which is a promise T-5 never made to T-7 and
the opposite of this function's own docstring ("in any order"). ``_sources`` deduped
``Provenance`` — a PER-FACT object carrying that fact's ``quote`` and ``confidence`` — by
``doc_id``, so which quote a document's single citation displayed was decided by which
SECTION of the page reached the document first.

Every expected value here is a literal written in this file or a field read off a fixture
under ``tests/fixtures/``, which this ticket does not own. Nothing is compared against a
constant in ``arrival.digest``: a test that reads its answer out of the module under test
would pass for any template, grammatical or not.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
from t7_digest_helpers import fact_of, hub_of, load, make_match, replacing, variant

from arrival.contracts import HubContribution, Match, PersonRef
from arrival.digest import (
    MEET_CAP,
    WHY_OF_LAST_RESORT,
    is_speakable,
    make_digest,
    pick_lately,
    pick_opener_hook,
)
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")


@pytest.fixture
def alpha():
    return load("alpha")


def _dead_llm() -> LLMDouble:
    """Unscripted: ``LLMDouble`` raises ``LLMError``, which is the fallback path."""
    return LLMDouble()


# --------------------------------------------------------------------------- T-029, template


#: The shape the extractor actually writes: "one sentence about the person", in practice a
#: predicate with the subject elided. Every one of these is a verbatim ``Fact.text`` from a
#: dossier in this repo, so none of them is a strawman invented to fail.
SPLICING_FACTS = [
    "Argues that developer-tools pricing should be published in full on a public page.",
    "Replaced the Tallow Harbor trial with a non-expiring usage allowance.",
    "Lives in Austin and leads an operations team that has worked remotely since 2020.",
    "Led the Foundry Seed 2019 fund and sits on four of its boards.",
    "Studied at Bellhaven Polytechnic and returned to teach there.",
]


@pytest.mark.parametrize("text", SPLICING_FACTS)
async def test_the_fallback_opener_never_splices_a_fact_after_the_preposition(alpha, text):
    """R14 + R18: "Ask about" takes a noun phrase, and a fact is a sentence.

    The assertion is on the SHAPE and not merely on speakability, because
    ``is_speakable`` lives in the module under test: comparing against it here would let a
    future edit move both together and stay green. ``"Ask about " + text`` is a literal.
    """
    hook = variant(fact_of(alpha, "alpha-hook"), text=text)
    dossier = replacing(alpha, {"alpha-hook": hook})

    digest = await make_digest(dossier, [], _dead_llm())

    assert digest.say_out_loud != f"Ask about {text}", (
        "the fact sentence was pasted straight into the object slot of 'about'; a host "
        f"reading this aloud stumbles on the first verb: {digest.say_out_loud!r}"
    )
    assert digest.say_out_loud == f"Ask about this: {text}", (
        f"the fallback opener is not the documented invitation: {digest.say_out_loud!r}"
    )
    assert text in digest.say_out_loud, "the fact was edited rather than carried verbatim"


async def test_the_fallback_opener_still_quotes_the_chosen_hook_verbatim(alpha):
    """The grammar fix must not become a licence to reword a fact (R9 leans on verbatim)."""
    digest = await make_digest(alpha, [], _dead_llm())

    hook = pick_opener_hook(alpha)
    assert hook is not None, "positive control: the fixture has no opener hook"
    assert hook.text in digest.say_out_loud, (
        f"the opener no longer carries {hook.fact_id}'s own sentence: {digest.say_out_loud!r}"
    )
    assert digest.say_out_loud.startswith("Ask about this: ")


# --------------------------------------------------------------------------- T-029, predicate


#: Lines a host cannot read aloud because a clause was put where a noun phrase belongs.
#: The first is the reproduction, verbatim, from a live boot with no API key.
SPLICED_LINES = [
    "Ask about Argues that developer-tools pricing should be published in full on a public page.",
    "Ask about Lives in Austin and leads an operations team.",
    "Ask about Led the Foundry Seed 2019 fund.",
    "Ask about Studied at Bellhaven Polytechnic.",
    "Ask about Co-founded Quarrystone Labs in 2016.",
    "Curious about Maintains the Quarrystone command line tool.",
    "Ask about Has lived in Austin since 2014.",
    "Ask about Took four months away from work.",
    "Ask about the Replaced trial and what it bought.",
]


@pytest.mark.parametrize("line", SPLICED_LINES)
def test_is_speakable_rejects_a_clause_spliced_into_a_noun_phrase_slot(line):
    """R18's sixth clause — the property the other five were blind to.

    Without this the template fix regresses silently the moment someone edits
    ``OPENER_TEMPLATE`` back, because nothing downstream would notice.
    """
    assert not is_speakable(line), f"a host cannot read this aloud as written: {line!r}"


#: Lines that must keep passing. Several are verbatim output of this project's own code, and
#: a rule that reads a capitalised hub label as a verb blanks a Meet row's exposed reasoning
#: (R10).
#:
#: T-052 correction, no assertion removed. The comment here used to say `graph._why` emits
#: "Both deep in Developer-tools go-to-market."; T-041 lower-cases a CATEGORY hub's label, so
#: what that function emits for the corpus pair is now the lower-cased line. The capitalised
#: string is KEPT because it is the stricter input and the mitigation it exercises is still
#: load-bearing — every hub type but topic/technology/cause keeps the label's stored
#: capitalisation, so a company called "Meridian-Ops Systems" still arrives capitalised,
#: hyphenated and ending in "-s" directly after a bare "to". Both of those, and the real
#: current corpus line, are now listed.
SPEAKABLE_LINES = [
    "Ask about this: Argues that developer-tools pricing should be published on a public page.",
    "Ask about the nine months of rubric work before the first line of code.",
    "Ask about Quarrystone Labs and the public status page it shipped in 2017.",
    "Both deep in Developer-tools go-to-market.",
    "Both deep in developer-tools go-to-market.",
    "Both connected to Meridian-Ops Systems.",
    "Both connected to Databricks.",
    "Both building on Kubernetes.",
    "Both backed by Foundry Seed 2019.",
    "Runa Okonkwo. Co-founded Quarrystone Labs in 2016 and runs its platform team.",
    "Lives in Austin and leads an operations team that has worked remotely since 2020.",
    "Curious about Austin and what keeps the team remote.",
    "Ask about Bellhaven Polytechnic and the instrumentation years.",
    "Worth a hello; nothing quotable on the record yet.",
]


@pytest.mark.parametrize("line", SPEAKABLE_LINES)
def test_is_speakable_still_accepts_the_lines_the_page_actually_speaks(line):
    """The negative control. A grammar rule that fires too eagerly deletes real signal."""
    assert is_speakable(line), f"a legitimate spoken line was refused: {line!r}"


def test_a_fact_sentence_alone_is_still_speakable(alpha):
    """A subject-elided predicate is fine on its own: it only breaks after a preposition.

    ``who_line_for`` renders "Name. <fact>", so the new clause must not start refusing the
    facts the Who line is built from.
    """
    for fact in alpha.facts:
        assert is_speakable(fact.text) or "(" in fact.text or "http" in fact.text, (
            f"the new clause refused a fixture fact outright: {fact.fact_id} {fact.text!r}"
        )


async def test_a_why_that_cannot_be_repaired_states_the_absence_instead(alpha):
    """R18 binds on a Meet row, and ``speakable`` repairs only the mechanical clauses.

    A spliced ``why`` cannot be repaired without rewriting what the matcher claimed, so the
    row states an absence rather than putting an unreadable sentence in the host's mouth.
    """
    rough = "Both came up through Foundry Seed and about Argues that pricing should be public."
    match = make_match(alpha, load("bravo"), score=100.0, why=rough)

    digest = await make_digest(alpha, [match], _dead_llm())

    assert digest.meet, "the Meet row was dropped instead of repaired"
    assert digest.meet[0].why != rough, f"an unreadable why reached the page: {rough!r}"
    assert digest.meet[0].why == WHY_OF_LAST_RESORT
    assert match.why == rough, "the incoming Match was mutated in place"


# --------------------------------------------------------------------------- T-033, Meet order


def _peer(person_id: str) -> PersonRef:
    return PersonRef(person_id=person_id, name=person_id.title(), details=[])


def _match(alpha, person_id: str, *, score: float, contribution: float | None = None) -> Match:
    """A ``Match`` with a controlled score AND a controlled raw contribution sum."""
    contributions: list[HubContribution] = []
    if contribution is not None:
        contributions = [
            HubContribution(
                hub=hub_of(alpha, "city:austin"),
                idf_weight=contribution,
                recency=1.0,
                type_boost=1.0,
                contribution=contribution,
            )
        ]
    return Match(
        other=_peer(person_id),
        score=score,
        contributions=contributions,
        path=[f"person:{alpha.person.person_id}", f"person:{person_id}"],
        why="Both work in Austin.",
    )


async def _meet_ids(alpha, matches):
    digest = await make_digest(alpha, list(matches), _dead_llm())
    return [row.other.person_id for row in digest.meet]


async def test_meet_is_the_same_whatever_order_the_matches_arrive_in(alpha):
    """T-033: ``make_digest`` documents "in any order" and now actually means it.

    Four peers, two of them tied on 0.0 — the corpus's own shape, where two of four present
    people share nothing with the arriving member. Before the tiebreaker existed, 12 of these
    24 permutations produced a Meet ending in one of the tied peers and 12 in the other, so
    which name a host read off the third row was decided by the order T-5 happened to return
    its list in.
    """
    matches = [
        _match(alpha, "sil-vantorre", score=100.0, contribution=0.77),
        _match(alpha, "jem-arrowood", score=67.0, contribution=0.51),
        _match(alpha, "mira-hollowell", score=0.0),
        _match(alpha, "theo-baptiste", score=0.0),
    ]

    outcomes = {tuple(await _meet_ids(alpha, order)) for order in itertools.permutations(matches)}

    assert len(outcomes) == 1, f"Meet depends on the order its input arrived in: {outcomes}"
    assert outcomes == {("sil-vantorre", "jem-arrowood", "mira-hollowell")}
    assert len(next(iter(outcomes))) == MEET_CAP


async def test_a_rounded_score_tie_is_broken_by_the_raw_contribution_it_lost(alpha):
    """``score`` is ``round(100 * raw / ref)``, so a tie is a rounding artefact, not a draw.

    Both peers show 42; one of them shares strictly more. The stronger pair must take the
    row whichever way round the list arrives, and the weaker one must not.
    """
    stronger = _match(alpha, "aaa-weakest-id", score=42.0, contribution=0.60)
    weaker = _match(alpha, "zzz-strongest-id", score=42.0, contribution=0.30)
    filler = [
        _match(alpha, "peer-one", score=90.0, contribution=0.9),
        _match(alpha, "peer-two", score=80.0, contribution=0.8),
    ]

    forward = await _meet_ids(alpha, [*filler, stronger, weaker])
    backward = await _meet_ids(alpha, [weaker, stronger, *reversed(filler)])

    assert forward == backward, f"the cap reshuffled on input order: {forward} vs {backward}"
    assert forward[-1] == "aaa-weakest-id", (
        f"the rounded tie was not broken on the raw contribution: {forward}"
    )
    assert "zzz-strongest-id" not in forward


async def test_an_exact_tie_is_broken_deterministically_by_person_id(alpha):
    """Equal score AND equal contribution still has to resolve the same way every time."""
    tied = [
        _match(alpha, "beta-peer", score=0.0, contribution=0.0),
        _match(alpha, "alpha-peer", score=0.0, contribution=0.0),
        _match(alpha, "gamma-peer", score=0.0, contribution=0.0),
        _match(alpha, "delta-peer", score=0.0, contribution=0.0),
    ]

    outcomes = {tuple(await _meet_ids(alpha, order)) for order in itertools.permutations(tied)}

    assert outcomes == {("alpha-peer", "beta-peer", "delta-peer")}, (
        f"a four-way tie resolved differently depending on input order: {outcomes}"
    )


# --------------------------------------------------------------------------- T-033, citations


async def test_a_documents_citation_is_its_strongest_evidence_not_the_first_section_to_reach_it(
    alpha,
):
    """T-033: ``Provenance`` is per-fact; ``sources`` holds one per document.

    ``alpha-recent`` and ``alpha-hook`` are extracted from the same document and carry
    different quotes. The dates below make ``alpha-recent`` the fresher of the two, so Lately
    reaches the document through IT first while the stronger evidence — ``alpha-hook``,
    further down the same list — arrives second. Under a first-wins dedupe the document's one
    citation then displayed a 0.75 quote while the page held a 0.92 one from the same source.

    The dates are set explicitly rather than left to the fixture because ``pick_lately``
    orders on ``(published_at, confidence, fact_id)``: raising a fact's confidence alone
    ALSO promotes it up the bullet list, which would move the very arrival order this test
    needs to hold still. Separating the two knobs is what makes the assertion about which
    provenance survives rather than about which fact happened to be read first.
    """
    weak_recent = variant(
        fact_of(alpha, "alpha-recent"), confidence=0.75, published_at=dt.date(2026, 8, 15)
    )
    strong_hook = variant(
        fact_of(alpha, "alpha-hook"), confidence=0.92, published_at=dt.date(2026, 6, 1)
    )
    dossier = replacing(alpha, {"alpha-recent": weak_recent, "alpha-hook": strong_hook})
    doc_id = weak_recent.provenance.doc_id
    assert strong_hook.provenance.doc_id == doc_id, "fixture changed: the two facts split docs"
    assert weak_recent.provenance.quote != strong_hook.provenance.quote

    order = [f.fact_id for f in pick_lately(dossier, exclude=[fact_of(alpha, "alpha-work")])]
    assert order.index("alpha-recent") < order.index("alpha-hook"), (
        f"positive control: the weaker fact no longer reaches the document first ({order})"
    )

    digest = await make_digest(dossier, [], _dead_llm())

    entries = [p for p in digest.sources if p.doc_id == doc_id]
    assert len(entries) == 1, f"sources are not deduped by doc_id: {entries}"
    assert entries[0].quote == strong_hook.provenance.quote, (
        "the citation for this document shows the quote of whichever section reached it "
        f"first rather than the strongest evidence behind it: {entries[0].quote!r}"
    )
    assert entries[0].confidence == 0.92, (
        "the confidence rendered beside the citation belongs to a different fact"
    )


async def test_the_citation_list_keeps_first_use_order_while_choosing_its_entries(alpha):
    """The fix is about WHICH provenance survives, never about where its document sits.

    T-8 numbers citations by position in ``Digest.sources``, so first-use order is pinned.
    """
    weak_recent = variant(
        fact_of(alpha, "alpha-recent"), confidence=0.75, published_at=dt.date(2026, 8, 15)
    )
    strong_hook = variant(
        fact_of(alpha, "alpha-hook"), confidence=0.92, published_at=dt.date(2026, 6, 1)
    )
    dossier = replacing(alpha, {"alpha-recent": weak_recent, "alpha-hook": strong_hook})

    digest = await make_digest(dossier, [], _dead_llm())

    doc_ids = [p.doc_id for p in digest.sources]
    assert doc_ids[0] == fact_of(alpha, "alpha-work").provenance.doc_id, (
        f"the Who line's document is no longer cited first: {doc_ids}"
    )
    lately_docs: list[str] = []
    for fact in digest.lately:
        if fact.provenance.doc_id not in lately_docs:
            lately_docs.append(fact.provenance.doc_id)
    assert lately_docs, "positive control: no Lately bullet, so there is no order to check"
    positions = [doc_ids.index(doc_id) for doc_id in lately_docs]
    assert positions == sorted(positions), (
        f"sources left first-use order: Lately cites {lately_docs} at positions {positions}"
    )
    assert weak_recent.provenance.doc_id in lately_docs, (
        "positive control: the shared document is not the one Lately reaches first"
    )
    assert len(doc_ids) == len(set(doc_ids))
