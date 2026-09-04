"""T-055: a citation marker backs the sentence it hangs off, and the audit surfaces stay whole.

The measured line, on the frozen corpus with all five people present: `runa-okonkwo`'s Meet
row for Mira Hollowell scored 0.0, read "Nothing in common on the record yet.", and carried a
`[1]` marker anyway — because the row still holds `city:austin` and `topic:remote-work`, whose
IDF clamps to zero on a corpus where all five share them. A host with ninety seconds reads a
sentence saying nothing is shared and a footnote offering to prove it.

**The product judgement this module pins, stated once.** The page has three surfaces and each
cites exactly what it CLAIMS:

* the spoken `why` and its `<sup>` markers — `graph._why` already decided this sentence names
  only hubs whose contribution survived the clamp, so the markers follow it;
* the R10 reasoning table — arithmetic, so every shared hub including the zeroes;
* "Why we know this" — an audit surface, so also complete, which is what keeps the table's
  zero rows checkable from the page.

**What each assertion grades against, and why none of it is an answer key T-055 can write.**
This ticket owns `src/arrival/web/render.py` and new modules under `tests/web/`. So:

* the corpus is `.swarm-loop/acceptance/fixtures/dossiers/` — orchestrator-owned, hash-locked,
  unwritable by any worker, and the same corpus the frozen acceptance suite grades on;
* what a Meet row is made of comes from `arrival.graph` (`Match.contributions`, and the `why`
  sentence itself), what is shown comes from `arrival.digest`, and what may reach a screen
  from `arrival.taste.is_displayable`. None of the three is owned here;
* the "does the sentence name this hub" test reads `graph`'s own emitted sentence, never a
  table of expected phrasings in this file;
* the two literals below are quoted verbatim out of the frozen corpus JSON.

No assertion here compares against `render.py`, against `digest.html`, or against a snapshot
regenerated from either.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from arrival.contracts import Digest, Dossier, Fact, HubContribution, Match
from arrival.digest import make_digest
from arrival.graph import build_graph
from arrival.graph import match as match_present
from arrival.taste import is_displayable
from arrival.web.render import digest_view, render

pytestmark = pytest.mark.ticket("T-8")

FROZEN_DOSSIERS = (
    Path(__file__).resolve().parents[2] / ".swarm-loop" / "acceptance" / "fixtures" / "dossiers"
)
ARRIVING_ID = "runa-okonkwo"

#: The row the ticket is about: on the full corpus this pair shares only clamped hubs.
ZERO_SCORING_NAME = "Mira Hollowell"

#: Verbatim from `runa-okonkwo.json`, facts `f16` and `f17` — the evidence behind the two
#: hubs whose IDF clamps to zero. Both are genuinely shared, which is the whole difficulty:
#: suppressing their MARKER must not suppress them from the page.
AUSTIN_QUOTE = "I have lived in Austin since 2014"
REMOTE_QUOTE = "The company has been remote-first from its first week"

_CITE = re.compile(r'<sup class="cite">.*?</sup>', re.DOTALL)


class _FailingLLM:
    """Forces `make_digest` down its documented fallback. No network, no scripting."""

    async def structured(self, **_kwargs):
        raise RuntimeError("offline")


def _corpus() -> dict[str, Dossier]:
    dossiers = [
        Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(FROZEN_DOSSIERS.glob("*.json"))
    ]
    assert dossiers, f"the frozen corpus at {FROZEN_DOSSIERS} is empty"
    return {d.person.person_id: d for d in dossiers}


async def _build(person_id: str = ARRIVING_ID) -> tuple[Dossier, Digest]:
    """Everyone present — the presence count that produces the zero-scoring row."""
    corpus = _corpus()
    graph = build_graph(corpus.values())
    matches = match_present(graph, person_id, sorted(corpus))
    return corpus[person_id], await make_digest(corpus[person_id], matches, _FailingLLM())


def _named_hubs(row: Match) -> list[HubContribution]:
    """The contributions whose label the row's own sentence NAMES.

    Derived from `graph`'s emitted `why`, not from a rule restated here: `graph._why`
    interpolates a hub label into a phrase template, and `graph._spoken_label` may lower-case
    a category label's leading character, so the comparison is case-insensitive and nothing
    else about the phrasing is assumed.
    """
    return [c for c in row.contributions if c.hub.label.casefold() in row.why.casefold()]


def _evidence_docs(dossier: Dossier, contributions: list[HubContribution]) -> set[str]:
    """The documents behind these hubs' displayable evidence, from the CONTRACT.

    `HubContribution.hub` is the arriving person's Hub (DESIGN §Interfaces), so its
    `evidence_fact_ids` resolve in this dossier; `is_displayable` is T-4's gate.
    """
    by_id: dict[str, Fact] = {f.fact_id: f for f in dossier.facts}
    return {
        by_id[fact_id].provenance.doc_id
        for contribution in contributions
        for fact_id in contribution.hub.evidence_fact_ids
        if fact_id in by_id and is_displayable(by_id[fact_id])
    }


def _rows(view: dict) -> list[dict]:
    return view["meet_rows"]


# ------------------------------------------------------------------- the reproduction


async def test_the_zero_scoring_row_offers_no_proof_of_a_connection_it_denies():
    """The measured line. Score 0, "nothing in common", and a citation marker anyway."""
    dossier, digest = await _build()
    view = digest_view(digest, dossier)

    row = next(r for r in _rows(view) if r["match"].other.name == ZERO_SCORING_NAME)
    match = row["match"]

    # Positive controls: the row really is the hard case — it scores nothing, its sentence
    # names no hub, and it nonetheless carries shared hubs whose evidence is displayable.
    assert match.score == 0, f"{ZERO_SCORING_NAME} no longer scores zero here: {match.score}"
    assert not _named_hubs(match), f"the sentence now names a hub: {match.why!r}"
    assert match.contributions, "the row shares no hub at all, so this test proves nothing"
    assert _evidence_docs(dossier, match.contributions), (
        "the row's clamped hubs have no displayable evidence, so there was never a marker "
        "to suppress and this test proves nothing"
    )

    assert row["citations"] == [], (
        f"the row reads {match.why!r} and still offers {row['citations']} as proof of a "
        "connection the same sentence denies"
    )


async def test_the_rows_that_do_claim_something_still_cite_it():
    """The other half: suppression must not have been achieved by citing nothing anywhere."""
    dossier, digest = await _build()
    view = digest_view(digest, dossier)

    claiming = [r for r in _rows(view) if _named_hubs(r["match"])]
    assert claiming, "no Meet row names a hub at all; the suppression above proves nothing"
    for row in claiming:
        assert row["citations"], (
            f"{row['match'].other.name}'s row says {row['match'].why!r} and cites nothing"
        )


# ------------------------------------------------- what a marker is allowed to point at


@pytest.mark.parametrize("person_id", sorted(_corpus()))
async def test_every_meet_marker_points_at_a_document_backing_that_rows_sentence(person_id):
    """A `<sup>` on a spoken line indexes a document that carries evidence for what it says.

    Run over the whole corpus, because `runa-okonkwo` is only one of the five arrivals and
    the defect is a property of the rule, not of that page. This is also the document-level
    twin of the defect `digest._sources` was written to kill: before this ticket the Jem
    Arrowood row read "Both deep in developer-tools go-to-market." and cited BOTH [3], the
    trade-press piece that carries that sentence's evidence, and [1], a self-page whose
    quotes are about Austin and remote work.
    """
    dossier, digest = await _build(person_id)
    view = digest_view(digest, dossier)
    numbers = {provenance.doc_id: n for n, provenance in enumerate(digest.sources, start=1)}

    cited_anything = False
    for row in _rows(view):
        match = row["match"]
        allowed = {numbers[doc] for doc in _evidence_docs(dossier, _named_hubs(match))}
        allowed.discard(None)
        for number in row["citations"]:
            cited_anything = True
            assert number in allowed, (
                f"{person_id} -> {match.other.name}: the row says {match.why!r} and points "
                f"at source [{number}] ({digest.sources[number - 1].doc_id}), which carries "
                f"no evidence for any hub that sentence names. Allowed: {sorted(allowed)}"
            )
    if person_id == ARRIVING_ID:
        assert cited_anything, "no Meet row cited anything; the check above is vacuous"


# ------------------------------------------------- the two surfaces, as a host reads them


async def test_the_reasoning_table_still_shows_the_clamped_hubs_the_marker_no_longer_offers():
    """R10's arithmetic is untouched: hiding a zero row would stop the sum adding up.

    This is the half of the rule `digest.html`'s own `data-reasoning` comment argues for, and
    it must survive the marker's suppression or the two surfaces have not been made to agree
    — they have merely both been emptied.
    """
    dossier, digest = await _build()
    html = render("digest.html", **digest_view(digest, dossier))

    meet = html[html.index('id="meet"') : html.index('id="lately"')]
    start = meet.index(ZERO_SCORING_NAME)
    row_html = meet[start : meet.index("</li>", start)]

    opens = row_html.index('class="why"')
    why = row_html[opens : row_html.index("</p>", opens)]
    assert not _CITE.search(why), f"the zero-scoring row still renders a citation marker: {why!r}"

    assert "data-reasoning" in row_html, "the row lost its R10 reasoning affordance entirely"
    for label in ("Austin", "Remote work"):
        assert label in row_html, (
            f"{label!r} is a hub this pair really does share, and the reasoning table no "
            f"longer lists it — the arithmetic on the page now has an unexplained zero"
        )
    assert row_html.count("0.0000") >= 2, (
        "the clamped weights are no longer printed, so a host auditing the score cannot see "
        f"WHY the row is worth nothing:\n{row_html}"
    )

    # Companion control: a row that does claim something still renders its marker, so the
    # absence above is this row's answer and not a broken selector.
    claiming = meet[meet.index("Sil Vantorre") : meet.index("</li>", meet.index("Sil Vantorre"))]
    assert _CITE.search(claiming), "no Meet row renders a marker at all; the absence proves nothing"


async def test_the_shared_hubs_are_still_checkable_from_the_evidence_list():
    """The objection against suppressing anything, answered on the rendered page.

    Hiding the marker would make the reasoning table's own contents unverifiable IF the page
    lost the quotes with it. It does not: "Why we know this" is an audit surface like the
    table, so the Austin and remote-first quotes are still rendered and still name the very
    Meet row whose marker went away.
    """
    dossier, digest = await _build()
    html = render("digest.html", **digest_view(digest, dossier))
    sources = html[html.index('id="why-we-know-this"') :]

    # JUSTIFIED TEST EDIT — T-086. This loop ran over `(AUSTIN_QUOTE, REMOTE_QUOTE)`.
    # AUSTIN_QUOTE is the citation for `runa-okonkwo-f16`, "Has lived in Austin since 2014."
    # — a statement of where a member lives, which SPEC R11 names outright ("their home
    # address, property records or where they live") as something the digest never surfaces.
    # The rule layer's home cues were anchored on `they|he|she`, so a fact writing its
    # subject any other way was affirmatively KEPT; T-086 anchors them on the predicate
    # instead, and the fact is now withheld. Requiring its quote to be RENDERED is therefore
    # requiring an R11 violation, which is wrong independently of any implementation.
    #
    # The property this test exists for is untouched and still has a witness: a zero-weight
    # hub's evidence stays on the audit surface, which REMOTE_QUOTE (`f17`, professional and
    # displayable) demonstrates exactly as before. Nothing is loosened — the Austin case
    # moves from "must be present" to the STRICTER "must be absent", so the pair still
    # covers both hubs and the module now also pins the R11 outcome for one of them.
    for quote in (REMOTE_QUOTE,):
        assert quote in sources, (
            f"{quote!r} backs a hub the reasoning table still lists, and it is no longer "
            "anywhere in the evidence list — the table's zero rows are now unverifiable"
        )
        entry = sources[sources.index(quote) : sources.index("</li>", sources.index(quote))]
        assert ZERO_SCORING_NAME in entry, (
            f"{quote!r} is rendered but no longer names {ZERO_SCORING_NAME}'s row, so a host "
            f"cannot tell which claim it supports:\n{entry}"
        )

    assert AUSTIN_QUOTE not in sources, (
        "R11: the Austin quote cites a fact stating where a member lives, so it is withheld "
        "and must not appear on an audit surface a host reads"
    )
