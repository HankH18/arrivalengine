"""T-7 acceptance 2 (R9, S6): "Why we know this" covers exactly what the page shows.

``sources`` must carry every ``Provenance`` behind the facts in ``who_line``, ``lately``
and ``non_obvious``, plus — for each Meet row — the ARRIVING person's facts named in
``contributions[*].hub.evidence_fact_ids``; deduped by ``doc_id``, in first-use order.
"""

from __future__ import annotations

import datetime as dt

import pytest
from t7_digest_helpers import fact_of, load, make_match, replacing, variant

from arrival.contracts import Hub, HubContribution, Match
from arrival.digest import make_digest
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("T-7")


def _llm() -> LLMDouble:
    double = LLMDouble()
    double.queue({"line": "Ask about the evaluation harness."})
    return double


@pytest.fixture
def alpha():
    return load("alpha")


@pytest.fixture
def matches(alpha):
    """Two Meet rows whose hubs name different evidence facts of alpha's."""
    return [
        make_match(alpha, load("bravo"), score=100.0, hub_id="company:northgate-labs",
                   why="Both work on machine learning in Austin."),
        make_match(alpha, load("charlie"), score=40.0, hub_id="technology:evaluation-harnesses",
                   why="Both build evaluation harnesses."),
    ]


async def test_sources_cover_all(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    doc_ids = [p.doc_id for p in digest.sources]
    assert doc_ids, "nothing is citable"
    assert len(doc_ids) == len(set(doc_ids)), f"sources are not deduped by doc_id: {doc_ids}"

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    assert shown, "positive control: nothing shown, so coverage is vacuous"
    for fact in shown:
        assert fact.provenance.doc_id in doc_ids, f"{fact.fact_id} is shown with no citation"

    # who_line is built from current_work, so its document is cited too.
    assert fact_of(alpha, "alpha-work").provenance.doc_id in doc_ids

    # Every Meet row's hub evidence, which is the arriving person's own facts.
    for match in digest.meet:
        for contribution in match.contributions:
            for fact_id in contribution.hub.evidence_fact_ids:
                assert fact_of(alpha, fact_id).provenance.doc_id in doc_ids, (
                    f"the hub behind {match.other.person_id} cites {fact_id}, which is not "
                    "in 'Why we know this'"
                )

    # Nothing is cited that no shown fact stands behind.
    corpus = {f.provenance.doc_id for f in alpha.facts}
    assert set(doc_ids) <= corpus


async def test_sources_are_in_first_use_order(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    doc_ids = [p.doc_id for p in digest.sources]
    first_use = []
    for fact in list(digest.lately) + (
        [digest.non_obvious] if digest.non_obvious else []
    ):
        if fact.provenance.doc_id not in first_use:
            first_use.append(fact.provenance.doc_id)
    positions = [doc_ids.index(d) for d in first_use]
    assert positions == sorted(positions), (
        f"sources are not in first-use order: {first_use} land at {positions} in {doc_ids}"
    )


async def test_a_hub_whose_evidence_was_taste_excluded_is_never_cited(alpha):
    """The digest, not the graph, is where a withheld hub stops being citable.

    ``graph.py`` deliberately does not filter hubs — matching is not display — so a hub can
    legitimately score a match on evidence the host must never see. R12 has to bite here or
    the exclusion leaks into "Why we know this" through the back door.
    """
    tainted = variant(fact_of(alpha, "alpha-work"), excluded=True, exclusion_reason="family")
    dossier = replacing(alpha, {"alpha-work": tainted})
    match = make_match(dossier, load("bravo"), score=100.0, hub_id="company:northgate-labs",
                       why="Both work on machine learning in Austin.")
    assert "alpha-work" in match.contributions[0].hub.evidence_fact_ids

    digest = await make_digest(dossier, [match], _llm())

    assert digest.meet, "positive control: the match was dropped, so nothing was proven"
    assert tainted.provenance.doc_id not in {p.doc_id for p in digest.sources}, (
        "an excluded fact's document was cited because a hub named it as evidence"
    )


async def test_a_document_behind_two_shown_facts_is_cited_once(alpha):
    """S6 dedupes the SOURCE list by doc_id; it never dedupes the bullets."""
    digest = await make_digest(alpha, [], _llm())

    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    shared = [f for f in shown if f.provenance.doc_id == "b1159ac929dac1e6"]
    assert len(shared) >= 2, (
        "fixture changed: this test needs two shown facts extracted from one document"
    )
    doc_ids = [p.doc_id for p in digest.sources]
    assert doc_ids.count("b1159ac929dac1e6") == 1


async def test_sources_are_empty_of_documents_behind_nothing_shown(alpha, matches):
    digest = await make_digest(alpha, matches, _llm())

    cited = {p.doc_id for p in digest.sources}
    shown_docs = {f.provenance.doc_id for f in digest.lately}
    if digest.non_obvious is not None:
        shown_docs.add(digest.non_obvious.provenance.doc_id)
    shown_docs.add(fact_of(alpha, "alpha-work").provenance.doc_id)
    for match in digest.meet:
        for contribution in match.contributions:
            for fact_id in contribution.hub.evidence_fact_ids:
                shown_docs.add(fact_of(alpha, fact_id).provenance.doc_id)

    assert cited <= shown_docs, f"cited with nothing behind it: {sorted(cited - shown_docs)}"


def _dossier_where_every_section_has_its_own_document(alpha):
    """`alpha`, extended so who_line, Lately, the hook and a hub each cite a DIFFERENT doc.

    In the fixture as committed the hub-evidence documents are a SUBSET of the who_line and
    Lately documents, so the two contributions to `sources` mask each other: deleting either
    one from the citation set leaves every test in this suite green. Measured. Giving each
    section a document of its own is what makes the assertions below able to fail.
    """
    from t7_digest_helpers import with_facts

    hub_only = variant(
        fact_of(alpha, "alpha-interest"),
        fact_id="alpha-hub-evidence",
        text="He has served on the Northgate open source steering group since 2019.",
        category="affiliation",
        published_at=dt.date(2019, 1, 7),  # too old to reach Lately
        doc_id="00000000000000e1",
        url="https://example.org/hub-only",
    )
    fillers = [
        variant(
            fact_of(alpha, "alpha-recent"),
            fact_id=f"alpha-filler-{n}",
            text=f"Northgate shipped release {n}.0 of the harness that quarter.",
            category="recent_activity",
            published_at=dt.date(2026, 8, 10 + n),
            doc_id=f"00000000000000f{n}",
            url=f"https://example.org/filler-{n}",
        )
        for n in (1, 2, 3)
    ]
    old_hook = variant(
        fact_of(alpha, "alpha-hook"),
        published_at=dt.date(2018, 3, 3),  # too old to reach Lately, still the best hook
        doc_id="00000000000000a1",
        url="https://example.org/hook-only",
    )
    dossier = replacing(alpha, {"alpha-hook": old_hook})
    dossier = with_facts(dossier, [*dossier.facts, hub_only, *fillers])
    hub = Hub(
        hub_id="topic:steering",
        label="Steering group",
        type="topic",
        evidence_fact_ids=["alpha-hub-evidence"],
    )
    return dossier, hub, hub_only, old_hook


async def test_hub_evidence_is_cited_even_when_no_other_section_names_its_document(alpha):
    """TASKS acceptance 2's Meet-row clause, made able to fail.

    Deleting the hub-evidence contribution to `sources` used to leave the whole suite green
    because those documents also arrived via who_line and Lately.
    """
    dossier, hub, hub_only, _ = _dossier_where_every_section_has_its_own_document(alpha)
    match = Match(
        other=load("bravo").person,
        score=90.0,
        contributions=[
            HubContribution(hub=hub, idf_weight=0.51, recency=1.0, type_boost=1.0,
                            contribution=0.51)
        ],
        path=["person:alpha", "hub:topic:steering", "person:bravo"],
        why="Both sit on the Northgate steering group.",
    )

    digest = await make_digest(dossier, [match], _llm())

    doc_ids = [p.doc_id for p in digest.sources]
    shown_docs = {f.provenance.doc_id for f in digest.lately}
    if digest.non_obvious is not None:
        shown_docs.add(digest.non_obvious.provenance.doc_id)
    assert hub_only.provenance.doc_id not in shown_docs, (
        "the fixture no longer isolates the hub-evidence document; this test is masked again"
    )
    assert hub_only.provenance.doc_id in doc_ids, (
        "the document behind a Meet row's hub evidence is not in 'Why we know this'"
    )


async def test_the_who_line_document_is_cited_even_when_no_other_section_names_it(alpha):
    """The who_line contribution to `sources`, likewise made able to fail on its own."""
    dossier, _, _, _ = _dossier_where_every_section_has_its_own_document(alpha)
    work = fact_of(dossier, "alpha-work")

    digest = await make_digest(dossier, [], _llm())

    assert work.text in digest.who_line
    assert work.provenance.doc_id not in {f.provenance.doc_id for f in digest.lately}
    assert work.provenance.doc_id in {p.doc_id for p in digest.sources}


async def test_the_templated_opener_quotes_a_fact_and_that_fact_is_cited(alpha):
    """R9: the fallback opener shows a fact's own sentence, so it must be checkable.

    The model's opener is a paraphrase and cites nothing; the TEMPLATE's is the fact
    verbatim. On the graded corpus the hook fact reaches no other section, so without this
    the page quotes a sentence that appears in no source entry at all.
    """
    dossier, _, _, old_hook = _dossier_where_every_section_has_its_own_document(alpha)
    llm = LLMDouble()  # unscripted: the template branch is taken

    digest = await make_digest(dossier, [], llm)

    assert old_hook.text in digest.say_out_loud, "the fixture no longer isolates the hook"
    assert old_hook.provenance.doc_id not in {f.provenance.doc_id for f in digest.lately}
    assert old_hook.provenance.doc_id in {p.doc_id for p in digest.sources}, (
        "the opener quotes a fact whose document is in no source entry"
    )


async def test_a_model_written_opener_adds_no_citation(alpha):
    """A paraphrase is not a shown fact, so it must not pad 'Why we know this'."""
    dossier, _, _, old_hook = _dossier_where_every_section_has_its_own_document(alpha)
    llm = LLMDouble()
    llm.queue({"line": "Ask about the rubric work that came before the code."})

    digest = await make_digest(dossier, [], llm)

    assert digest.say_out_loud == "Ask about the rubric work that came before the code."
    assert old_hook.provenance.doc_id not in {p.doc_id for p in digest.sources}
