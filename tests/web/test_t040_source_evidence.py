"""T-040: every claim's own quote, under the document it came from.

`Provenance` is PER FACT — it carries that fact's `quote` and that fact's `confidence` —
while `Digest.sources` holds ONE entry per `doc_id`. On the frozen `runa-okonkwo` corpus a
single document (`35b4e2600c8a6ea6`) backs four separately-shown claims with four different
quotes, so a "Why we know this" list printing one `source.quote` per `<li>` showed three of
those four an excerpt that does not support them. Meet rows are the worst case: the page
renders no inline quote for them at all, so the source entry is the only evidence a host has.

Everything here grades against material this ticket cannot write:

* the frozen corpus at `.swarm-loop/acceptance/fixtures/dossiers/` (orchestrator-owned, and
  the same corpus the acceptance suite grades on — `tests/test_t0b_fixture_conventions.py`
  and `tests/extract/test_t3_frozen_corpus_guard.py` read it for the same reason);
* `arrival.digest` and `arrival.graph`, which decide what is shown and what a Meet row is
  made of, and `arrival.taste.is_displayable`, which decides what may reach a screen;
* `arrival.contracts`, for the shape of a `Hub`'s `evidence_fact_ids`.

No assertion in this module compares against `render.py` or `digest.html`.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from arrival.contracts import Digest, Dossier, Fact, Hub, HubContribution, Match, PersonRef
from arrival.digest import OPENER_TEMPLATE, make_digest, opener_hook_candidates, who_line_for
from arrival.graph import build_graph
from arrival.graph import match as match_present
from arrival.taste import is_displayable
from arrival.web.render import digest_view, render

pytestmark = pytest.mark.ticket("T-8")

FROZEN_DOSSIERS = (
    Path(__file__).resolve().parents[2] / ".swarm-loop" / "acceptance" / "fixtures" / "dossiers"
)
ARRIVING_ID = "runa-okonkwo"

#: Verbatim from `runa-okonkwo.json`, fact `runa-okonkwo-f10`, whose document is also the Who
#: line's. It is the ONLY sentence in the corpus that supports the Meet row "Both backed by
#: Foundry Seed 2019", and before this ticket the page put `f01`'s quote there instead.
FOUNDRY_QUOTE = "Quarrystone Labs raised its first outside money from Foundry Seed in 2019"
WHO_QUOTE = "I co-founded Quarrystone Labs in 2016 and I run the platform team there"


class _FailingLLM:
    """Forces `make_digest` down its documented fallback, so the opener QUOTES a fact.

    A model-written opener is a paraphrase and cites nothing (`digest._say_out_loud`), so the
    "Say out loud" claim only has evidence on this path — and on the frozen corpus that path
    puts a document into `Digest.sources` that nothing else on the page leans on.
    """

    async def structured(self, **_kwargs):
        raise RuntimeError("offline")


class _ScriptedLLM:
    """A model-written opener: valid under R14, quoting nothing."""

    def __init__(self, line="Ask what pulled them into developer tooling."):
        self.line = line

    async def structured(self, *, schema, **_kwargs):
        return schema(line=self.line)


def _corpus() -> dict[str, Dossier]:
    dossiers = [
        Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(FROZEN_DOSSIERS.glob("*.json"))
    ]
    assert dossiers, f"the frozen corpus at {FROZEN_DOSSIERS} is empty"
    return {d.person.person_id: d for d in dossiers}


async def _build(person_id: str = ARRIVING_ID, llm=None) -> tuple[Dossier, Digest]:
    corpus = _corpus()
    dossier = corpus[person_id]
    graph = build_graph(corpus.values())
    matches = match_present(graph, person_id, sorted(corpus))
    digest = await make_digest(dossier, matches, llm or _FailingLLM())
    return dossier, digest


def _row_evidence_facts(dossier: Dossier, row: Match) -> list[Fact]:
    """A Meet row's displayable evidence, derived from the CONTRACT rather than from render.py.

    `HubContribution.hub` is the arriving person's Hub, so `evidence_fact_ids` resolve in this
    dossier; `is_displayable` is T-4's gate and `graph.py` deliberately does not apply it.
    """
    by_id = {f.fact_id: f for f in dossier.facts}
    return [
        by_id[fact_id]
        for contribution in row.contributions
        for fact_id in contribution.hub.evidence_fact_ids
        if fact_id in by_id and is_displayable(by_id[fact_id])
    ]


def _opener_fact(dossier: Dossier, digest: Digest) -> Fact | None:
    _line, who_facts = who_line_for(dossier)
    for candidate in opener_hook_candidates(dossier, exclude=who_facts):
        if OPENER_TEMPLATE.format(text=candidate.text.strip()) == digest.say_out_loud:
            return candidate
    return None


def _shown_facts(dossier: Dossier, digest: Digest) -> list[tuple[str, Fact]]:
    """`(where it is shown, fact)` for everything the page leans on, from T-7's own rules."""
    _line, who_facts = who_line_for(dossier)
    shown: list[tuple[str, Fact]] = [("who", f) for f in who_facts]
    for row in digest.meet:
        shown += [(f"meet:{row.other.name}", f) for f in _row_evidence_facts(dossier, row)]
    shown += [("lately", f) for f in digest.lately]
    if digest.non_obvious is not None:
        shown.append(("not-on-the-first-page", digest.non_obvious))
    quoted = _opener_fact(dossier, digest)
    if quoted is not None:
        shown.append(("say-out-loud", quoted))
    return shown


def _evidence(view: dict) -> dict[int, list[dict]]:
    """The per-document quote list. `.get` so a view model without one fails on the
    REQUIREMENT below rather than on a KeyError that says nothing about the page."""
    return view.get("source_evidence", {})


def _quotes(view: dict, number: int) -> list[str]:
    return [entry["quote"] for entry in _evidence(view).get(number, [])]


# --------------------------------------------------------------- the defect, reproduced


async def test_every_shown_fact_has_its_own_quote_under_its_source_entry():
    """The reproduction.

    Four facts on `runa-okonkwo` come out of document `35b4e2600c8a6ea6`. One `<li>` holding
    one `source.quote` can show at most one of them, so three claims cite an entry whose
    excerpt does not support them.
    """
    dossier, digest = await _build()
    numbers = {p.doc_id: n for n, p in enumerate(digest.sources, start=1)}
    view = digest_view(digest, dossier)

    # Positive control: the corpus really does make several facts share one document, so a
    # green here is not a page that happens to have one fact per source.
    shown = _shown_facts(dossier, digest)
    per_doc: dict[str, set[str]] = {}
    for _where, fact in shown:
        per_doc.setdefault(fact.provenance.doc_id, set()).add(fact.fact_id)
    crowded = [doc_id for doc_id, ids in per_doc.items() if len(ids) > 1]
    assert crowded, "no document backs more than one shown fact; this test proves nothing"

    missing = []
    for where, fact in shown:
        number = numbers.get(fact.provenance.doc_id)
        if number is None or fact.provenance.quote in _quotes(view, number):
            continue
        missing.append(
            f"{where} / {fact.fact_id}: {fact.provenance.quote!r} is not under "
            f"source [{number}], which shows {_quotes(view, number)!r}"
        )
    assert not missing, (
        "a claim on the page cites a source entry that does not carry the quote supporting "
        "it:\n  " + "\n  ".join(missing)
    )


async def test_the_foundry_seed_meet_row_is_backed_by_the_foundry_seed_quote():
    """The concrete case named in T-040, end to end.

    "Both backed by Foundry Seed 2019" cites [1], and [1]'s document also carries the Who
    line. Before this ticket [1] displayed the Who line's quote, which never mentions Foundry
    Seed at all.
    """
    dossier, digest = await _build()
    view = digest_view(digest, dossier)

    row = next(r for r in view["meet_rows"] if r["match"].other.name == "Sil Vantorre")
    assert "Foundry Seed" in row["match"].why, (
        "the fixture no longer produces the Foundry Seed row this test is about"
    )
    assert row["citations"], "the Foundry Seed row cites nothing at all"

    quotes = [q for n in row["citations"] for q in _quotes(view, n)]
    assert FOUNDRY_QUOTE in quotes, (
        f"the Foundry Seed row cites {row['citations']}, whose entries show {quotes!r}. "
        "None of them is the sentence that mentions Foundry Seed."
    )

    # ...and the reader can tell WHICH quote is theirs.
    attributed = [
        entry
        for n in row["citations"]
        for entry in _evidence(view).get(n, [])
        if entry["quote"] == FOUNDRY_QUOTE
    ]
    assert attributed and any("Sil Vantorre" in label for label in attributed[0]["labels"]), (
        "the Foundry Seed quote is rendered but names no claim, so a host reading the Meet "
        f"row cannot tell it apart from the other quotes under that document: {attributed!r}"
    )
    assert "Sil Vantorre" in attributed[0]["backs"], (
        "the attribution line a host actually reads does not name the Meet row: "
        f"{attributed[0]['backs']!r}"
    )


async def test_the_rendered_page_puts_the_foundry_seed_quote_in_the_source_list():
    """Same defect, graded on the HTML a host actually reads."""
    dossier, digest = await _build()
    html = render("digest.html", **digest_view(digest, dossier))

    sources = html[html.index('id="why-we-know-this"') :]
    assert FOUNDRY_QUOTE in sources, (
        "'Why we know this' never shows the sentence behind the Foundry Seed Meet row"
    )
    assert WHO_QUOTE in sources, (
        "the Who line's quote vanished from the source list; the fix must ADD evidence, "
        "not swap which single quote is shown"
    )
    entry = sources[sources.index(FOUNDRY_QUOTE) : sources.index(FOUNDRY_QUOTE) + 400]
    assert "Sil Vantorre" in entry, (
        f"the Foundry Seed quote is rendered unattributed:\n{entry}"
    )


async def test_confidence_is_rendered_beside_a_quote_and_not_beside_a_document():
    """`Provenance.confidence` describes one extraction, never the document as a whole."""
    dossier, digest = await _build()
    view = digest_view(digest, dossier)

    crowded = next(
        n for n, entries in _evidence(view).items() if len(entries) > 1
    )
    confidences = {entry["confidence"] for entry in _evidence(view)[crowded]}
    assert len(confidences) > 1, (
        f"source [{crowded}] carries several quotes but one confidence; the numbers are "
        "per fact and must differ here"
    )

    html = render("digest.html", **digest_view(digest, dossier))
    sources = html[html.index('id="why-we-know-this"') :]
    for value in confidences:
        assert str(value) in sources, f"confidence {value} is not rendered anywhere"


# --------------------------------------------------------------- what must NOT appear


async def test_a_taste_excluded_fact_behind_a_shared_hub_is_never_quoted():
    """R11/R12, at the last gate before a host-facing page.

    `graph.py` does not filter hubs — matching is not display — so a hub whose evidence was
    taste-excluded can legitimately score a match. Its sentence must still never be rendered.
    """
    dossier, digest = await _build()
    withheld = [f for f in dossier.facts if not is_displayable(f)]
    assert withheld, "the fixture has no withheld facts, so this test proves nothing"

    # Force each withheld fact into the evidence path by making it a shared hub's evidence,
    # and give its document a source slot so `numbers.get` cannot be what saves us.
    victim = withheld[0]
    poisoned = Match(
        other=PersonRef(person_id="sil-vantorre", name="Sil Vantorre"),
        score=100.0,
        contributions=[
            HubContribution(
                hub=Hub(
                    hub_id="investor:foundry-seed-2019",
                    label="Foundry Seed 2019",
                    type="investor",
                    evidence_fact_ids=[victim.fact_id],
                ),
                idf_weight=0.5,
                recency=1.0,
                type_boost=1.5,
                contribution=0.75,
            )
        ],
        path=["person:runa-okonkwo", "hub:investor:foundry-seed-2019", "person:sil-vantorre"],
        why="Both backed by Foundry Seed 2019.",
    )
    leaky = digest.model_copy(
        update={"meet": [poisoned], "sources": [*digest.sources, victim.provenance]}
    )

    view = digest_view(leaky, dossier)
    rendered = [e["quote"] for entries in _evidence(view).values() for e in entries]
    why_hidden = victim.exclusion_reason or "below the display gate"
    assert victim.provenance.quote not in rendered, (
        f"the withheld fact {victim.fact_id} ({why_hidden}) is quoted in the evidence list"
    )
    html = render("digest.html", **view)
    assert victim.provenance.quote not in html, (
        f"the withheld fact {victim.fact_id}'s sentence reached the host-facing page"
    )
    # Positive control: the same path DOES render evidence when the fact is displayable.
    assert rendered, "nothing was rendered at all, so the absence above proves nothing"


async def test_a_document_outside_digest_sources_is_never_quoted():
    """T-7 decides what `sources` holds; this layer may not open a slot for anything else."""
    dossier, digest = await _build()
    trimmed = digest.model_copy(update={"sources": digest.sources[:1]})
    view = digest_view(trimmed, dossier)

    assert set(_evidence(view)) <= {1}, (
        f"evidence was attached to source numbers that do not exist: {sorted(_evidence(view))}"
    )
    kept = trimmed.sources[0].doc_id
    for entry in _evidence(view).get(1, []):
        fact = next(f for f in dossier.facts if f.fact_id == entry["fact_id"])
        assert fact.provenance.doc_id == kept, (
            f"{entry['fact_id']} is quoted under source [1], whose document is {kept}"
        )


# --------------------------------------------------------------- completeness / structure


@pytest.mark.parametrize("person_id", sorted(_corpus()))
async def test_no_source_entry_loses_the_quote_digest_py_chose_for_it(person_id):
    """Every numbered slot still carries the strongest provenance T-7 picked for that document.

    `digest._sources` puts the highest-confidence provenance in a document's slot. That quote
    is the page's best single answer to "why believe this document is about this person", and
    a rewrite of the list that dropped it would be a regression however complete it otherwise
    looked. Run over the whole corpus, because four of the five people never reach the
    multi-quote case at all.
    """
    dossier, digest = await _build(person_id)
    view = digest_view(digest, dossier)
    assert digest.sources, f"{person_id} produced no sources; the check below is vacuous"
    for n, provenance in enumerate(digest.sources, start=1):
        assert provenance.quote in _quotes(view, n), (
            f"{person_id} source [{n}] ({provenance.doc_id}) no longer shows the quote "
            f"digest.py chose for it: {provenance.quote!r}, shown {_quotes(view, n)!r}"
        )


@pytest.mark.parametrize("person_id", sorted(_corpus()))
async def test_every_numbered_source_carries_at_least_one_quote(person_id):
    """No numbered slot may render empty — including the one only the opener leans on.

    On all five frozen people the templated opener contributes a document that nothing else
    on the page cites. A re-derivation that covered Who, Meet, Lately and the non-obvious find
    but not the spoken line would leave that entry with a URL and no evidence.
    """
    dossier, digest = await _build(person_id)
    html = render("digest.html", **digest_view(digest, dossier))
    sources = html[html.index('id="why-we-know-this"') :]
    for n in range(1, len(digest.sources) + 1):
        entry_start = sources.index(f'id="source-{n}"')
        entry = sources[entry_start : sources.index("</li>", entry_start)]
        assert "&ldquo;" in entry, f"{person_id} source [{n}] renders no quote at all:\n{entry}"


async def test_the_spoken_line_is_the_claim_credited_with_the_openers_quote():
    """The fifth category, named. It is the one `Digest` does not record."""
    dossier, digest = await _build()
    quoted = _opener_fact(dossier, digest)
    assert quoted is not None, "the fallback opener quoted nothing; this test proves nothing"

    view = digest_view(digest, dossier)
    numbers = {p.doc_id: n for n, p in enumerate(digest.sources, start=1)}
    entries = _evidence(view).get(numbers[quoted.provenance.doc_id], [])
    mine = [e for e in entries if e["fact_id"] == quoted.fact_id]
    assert mine, f"{quoted.fact_id} is quoted aloud but carries no evidence entry"
    assert "say-out-loud" in mine[0]["sections"], (
        f"the opener's quote is not credited to the spoken line: {mine[0]!r}"
    )


async def test_a_model_written_opener_credits_no_claim_to_the_spoken_line():
    """The other half of the same rule: a paraphrase cites nothing, so nothing is attributed."""
    dossier, digest = await _build(llm=_ScriptedLLM())
    assert digest.say_out_loud == "Ask what pulled them into developer tooling.", (
        f"the scripted opener was rejected: {digest.say_out_loud!r}"
    )
    view = digest_view(digest, dossier)
    sections = {
        s for entries in _evidence(view).values() for e in entries for s in e["sections"]
    }
    assert "say-out-loud" not in sections, (
        "a model-written opener quotes no fact, yet the evidence list credits one to it"
    )
    assert sections, "no evidence at all was attributed; the absence above proves nothing"


async def test_the_source_list_is_still_one_numbered_entry_per_document():
    """The frozen dedupe and the citation-indexing contract both survive the extra quotes."""
    dossier, digest = await _build()
    html = render("digest.html", **digest_view(digest, dossier))
    sources = html[html.index('id="why-we-know-this"') :]

    doc_ids = [p.doc_id for p in digest.sources]
    assert len(doc_ids) == len(set(doc_ids)), f"sources are no longer deduped: {doc_ids}"
    anchors = re.findall(r'id="source-(\d+)"', sources)
    assert anchors == [str(n) for n in range(1, len(digest.sources) + 1)], (
        f"the numbered anchors are {anchors}, not one per document in order"
    )
    assert "<ol" in sources.lower(), "the source list is no longer an ordered list"


async def test_digest_view_still_exposes_sources_as_numbered_provenance_pairs():
    """The view-model shape `digest.html` and `test_t8_render.py` both read."""
    dossier, digest = await _build()
    view = digest_view(digest, dossier)
    assert [n for n, _ in view["sources"]] == list(range(1, len(digest.sources) + 1))
    assert [p.doc_id for _n, p in view["sources"]] == [p.doc_id for p in digest.sources]


def test_a_digest_with_no_dossier_still_renders_its_sources():
    """`digest_view(digest, None)` is a real call site; it must not lose the evidence list."""
    retrieved = dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.UTC)
    fact = Fact(
        fact_id="f1",
        text="Runs the platform team.",
        category="recent_activity",
        provenance={
            "doc_id": "doc-a",
            "url": "https://example.com/a",
            "source_kind": "self_page",
            "quote": "Runs the platform team",
            "published_at": dt.date(2026, 1, 5),
            "retrieved_at": retrieved,
            "confidence": 0.9,
        },
    )
    digest = Digest(
        digest_id="0123456789abcdef",
        person=PersonRef(person_id="ada-lark", name="Ada Lark"),
        who_line="Ada Lark.",
        meet=[],
        lately=[fact],
        non_obvious=None,
        say_out_loud="Ask what they are working on right now.",
        sources=[fact.provenance],
        exclusion_policy="policy",
        created_at=retrieved,
    )
    html = render("digest.html", **digest_view(digest, None))
    assert "Runs the platform team" in html
    assert 'id="source-1"' in html


# ------------------------------------------- the last gate before HTML, exercised for real


def _withheld(dossier: Dossier) -> Fact:
    victim = next((f for f in dossier.facts if f.excluded), None)
    assert victim is not None, "the fixture has no R11-excluded fact; this test proves nothing"
    return victim


async def test_a_withheld_fact_smuggled_into_lately_never_reaches_the_page():
    """`is_displayable` at the render layer, on the one path nothing upstream re-filters.

    `Digest.lately` is "displayable only" by contract, and a Meet row's evidence is filtered
    by `_hub_evidence_facts` before `_source_evidence` ever sees it — so a sabotage that
    removes the gate inside `_source_evidence` is invisible through the Meet path. This is the
    path that actually reaches it: a `Digest` whose `lately` carries a taste-excluded fact.
    Whatever produced such a digest is broken, but `render.py` is the LAST code before HTML
    and must not be the thing that publishes it.
    """
    dossier, digest = await _build()
    victim = _withheld(dossier)
    leaky = digest.model_copy(
        update={
            "lately": [victim, *digest.lately],
            "sources": [victim.provenance, *digest.sources],
        }
    )

    view = digest_view(leaky, dossier)
    quotes = [e["quote"] for entries in _evidence(view).values() for e in entries]
    assert quotes, "no evidence rendered at all, so the absences below prove nothing"
    assert victim.provenance.quote not in quotes, (
        f"{victim.fact_id} ({victim.exclusion_reason}) is quoted in the evidence list"
    )

    html = render("digest.html", **view)
    assert victim.provenance.quote not in html, (
        f"{victim.fact_id}'s source excerpt reached the host-facing page"
    )
    assert victim.text not in html, f"{victim.fact_id}'s own sentence reached the page"
    # Positive control: the displayable Lately bullets are still there.
    assert digest.lately[0].text in html, "the real Lately bullets vanished with the withheld one"


async def test_a_withheld_fact_smuggled_into_not_on_the_first_page_never_reaches_the_page():
    """The other un-refiltered slot. `pick_non_obvious` applies R12; this re-applies it."""
    dossier, digest = await _build()
    victim = _withheld(dossier)
    leaky = digest.model_copy(
        update={"non_obvious": victim, "sources": [victim.provenance, *digest.sources]}
    )

    html = render("digest.html", **digest_view(leaky, dossier))
    assert victim.text not in html, f"{victim.fact_id}'s own sentence reached the page"
    assert victim.provenance.quote not in html, (
        f"{victim.fact_id}'s source excerpt reached the host-facing page"
    )
    assert "Nothing here a first page" in html, (
        "the withheld find was suppressed but the section states no absence in its place"
    )


async def test_a_low_confidence_fact_is_gated_the_same_way_as_an_excluded_one():
    """R12's three clauses are independent, so the gate cannot be an `excluded` check."""
    dossier, digest = await _build()
    quiet = next(
        f
        for f in dossier.facts
        if not f.excluded and not is_displayable(f)
    )
    leaky = digest.model_copy(
        update={"lately": [quiet, *digest.lately], "sources": [quiet.provenance, *digest.sources]}
    )
    html = render("digest.html", **digest_view(leaky, dossier))
    assert quiet.text not in html, f"{quiet.fact_id} is below the display gate yet is rendered"
    assert quiet.provenance.quote not in html
