"""R11/R12 on `/corpus`: the gate, all three of its independent clauses, and the counts.

`/corpus` is host-facing. `/debug` is the only page in this app allowed to show withheld
material, and `arrival.graph` deliberately does not filter hubs — matching is not display —
so a hub whose evidence was taste-excluded is legitimately on the graph, legitimately drawn,
and its evidence must still never be quoted. `corpus_graph.hub_evidence` is the only thing
standing between the two, and this module is the reason to believe it.

`taste.is_displayable` refuses on three grounds that do not overlap, and each is checked here
against a fixture the test itself asserts is genuinely withheld before it asserts anything
about the page — an "is not in the page" assertion is satisfied by a fixture that was never
withheld, by a page that rendered no evidence, and by no page at all. So every case carries a
positive control that must appear.

**Would these still fail if the change were reverted?** They would fail to import, because
the route would not exist. Against the change as built, each one fails the moment
`is_displayable` is removed from `corpus_graph.hub_evidence` — which is exactly why that gate
is written in this change's own module instead of borrowed from `graph_view`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from arrival.contracts import Dossier, Fact, Hub, PersonRef, Provenance, Resolution
from arrival.graph import WITHHELD_HUB_LABEL
from arrival.taste import CONFIDENCE_FLOOR, NEVER_DISPLAYABLE_KINDS, is_displayable
from arrival.web.app import create_app
from doubles import LLMDouble

pytestmark = pytest.mark.ticket("CORPUSGRAPH")

OPENER = "Ask about the evaluation harness they open-sourced last spring."


def _provenance(doc_id, *, confidence=0.9, source_kind="self_page", quote="a verbatim span"):
    return Provenance(
        doc_id=doc_id,
        url=f"https://example.invalid/{doc_id}",
        source_kind=source_kind,
        quote=quote,
        retrieved_at=dt.datetime(2026, 1, 2, 3, 4, 5),
        confidence=confidence,
    )


def _fact(fact_id, text, **provenance):
    excluded = provenance.pop("excluded", False)
    reason = provenance.pop("exclusion_reason", None)
    return Fact(
        fact_id=fact_id,
        text=text,
        category="affiliation",
        provenance=_provenance(f"doc-{fact_id}", **provenance),
        excluded=excluded,
        exclusion_reason=reason,
    )


def _person(person_id, name, hub_id, label, *, facts, hub_facts=None):
    return Dossier(
        person=PersonRef(person_id=person_id, name=name, details=[]),
        resolution=Resolution(
            person_id=person_id,
            status="resolved",
            strong_keys={},
            accepted_doc_ids=[],
            rejected=[],
            confidence=0.9,
        ),
        facts=facts,
        hubs=[
            Hub(
                hub_id=hub_id,
                label=label,
                type=hub_id.split(":", 1)[0],
                recency=1.0,
                evidence_fact_ids=hub_facts or [fact.fact_id for fact in facts],
            )
        ],
        built_at=dt.datetime(2026, 1, 2, 3, 4, 5),
    )


def _write(directory, people):
    directory.mkdir(parents=True, exist_ok=True)
    for dossier in people:
        (directory / f"{dossier.person.person_id}.json").write_text(
            dossier.model_dump_json(), encoding="utf-8"
        )
    return directory


def _corpus_page(directory):
    llm = LLMDouble().when("SayOutLoud", "Member:", {"line": OPENER})
    with TestClient(create_app(dossier_dir=directory, llm=llm)) as client:
        response = client.get("/corpus")
    assert response.status_code == 200, response.text[:400]
    return response.text


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("taste-excluded", {"excluded": True, "exclusion_reason": "family"}),
        ("below the confidence floor", {"confidence": 0.4}),
        ("a never-displayable source kind", {"source_kind": "fec"}),
    ],
)
def test_a_withheld_fact_behind_a_shared_hub_never_reaches_the_corpus_page(tmp_path, label, kwargs):
    """R11 / R12 on the page that could leak them, all three independent clauses.

    The hub itself is legitimately shared and legitimately drawn — `arrival.graph` does not
    filter hubs. Its EVIDENCE is a different question, and `taste.is_displayable` is the only
    answer to it.

    The positive control is inside the test: the OTHER carrier's fact passes every clause and
    must appear. Without it a page that rendered no evidence at all — or no page at all —
    would satisfy every "not in" assertion here.
    """
    withheld = _fact(
        "hidden", "Kit and their spouse keep a workshop on Mockingbird Terrace.", **kwargs
    )
    shown = _fact("open", "Lee led the Harbor Fund seed round in 2019.")
    people = [
        _person("kit", "Kit Known", "investor:harbor-fund", "Harbor Fund", facts=[withheld]),
        _person("lee", "Lee Known", "investor:harbor-fund", "Harbor Fund", facts=[shown]),
    ]
    directory = _write(tmp_path / f"withheld-{len(label)}", people)

    assert not is_displayable(withheld), f"the {label} fixture is displayable; test is vacuous"
    assert is_displayable(shown), "the positive control is not displayable; test is vacuous"

    page = _corpus_page(directory)

    assert "Harbor Fund" in page, "the hub is shared and must still be drawn"
    assert shown.text in page, "the positive control: displayable evidence is shown"
    assert withheld.text not in page, f"a {label} fact reached a host-facing page"
    assert withheld.provenance.quote not in page
    assert "Nothing behind this we are willing to show." in page


def test_the_three_clauses_are_still_independent_of_each_other():
    """The fixtures above are only meaningful while R12's clauses do not overlap.

    Graded against `taste.py`'s own constants: a confidence-floor fixture must be blocked by
    the floor ALONE (not excluded, and on a displayable kind), and a source-kind fixture must
    be blocked by the kind ALONE (high confidence, not excluded). If a future change collapsed
    two clauses into one, the parametrisation above would silently test one thing three times
    and this test is what notices.
    """
    low = _fact("low", "A perfectly tasteful sentence.", confidence=CONFIDENCE_FLOOR - 0.1)
    assert not low.excluded
    assert low.provenance.source_kind not in NEVER_DISPLAYABLE_KINDS
    assert not is_displayable(low)

    kind = sorted(NEVER_DISPLAYABLE_KINDS)[0]
    barred = _fact("kind", "Another perfectly tasteful sentence.", confidence=0.99,
                   source_kind=kind)
    assert not barred.excluded
    assert barred.provenance.confidence >= CONFIDENCE_FLOOR
    assert not is_displayable(barred)


def test_a_hub_whose_every_carrier_is_withheld_says_so_instead_of_showing_an_empty_table(tmp_path):
    """A carrier is never dropped for having nothing showable — the page says the words.

    Dropping them would make the page show one fewer carrier than the graph has, which is a
    quieter and worse failure than an honest sentence.
    """
    people = [
        _person(
            "kit",
            "Kit Known",
            "investor:harbor-fund",
            "Harbor Fund",
            facts=[_fact("k", "Kit's home is on Mockingbird Terrace.", excluded=True,
                         exclusion_reason="home_or_property")],
        ),
        _person(
            "lee",
            "Lee Known",
            "investor:harbor-fund",
            "Harbor Fund",
            facts=[_fact("l", "Lee's home is on Mockingbird Terrace.", excluded=True,
                         exclusion_reason="home_or_property")],
        ),
    ]
    directory = _write(tmp_path / "all-withheld", people)
    page = _corpus_page(directory)

    # JUSTIFIED TEST EDIT — T-087. This line read `assert "Harbor Fund" in page`.
    #
    # It required the LABEL of a hub whose every carrier's evidence is taste-excluded to be
    # printed on a host-facing page. That is the defect T-087 is about, and it is wrong
    # independently of the fix: a hub label is minted FROM the evidence facts, so when all
    # of that evidence is withheld the label can be the withheld thing itself. Reproduced
    # before the fix, on this very shape: two members carrying a fact excluded
    # `home_or_property` produced the Meet row **"Both rooted in Ravensworth Hill."** — the
    # street they live on, in the sentence R18 says a host reads OUT LOUD to their face.
    # The gate cannot tell that case from this one, where the label happens to be innocuous,
    # so it withholds both; `tests/test_tadv_r11_hub_label_bypass.py` is the module that
    # states the requirement.
    #
    # THE PROPERTY THIS TEST EXISTS FOR IS UNTOUCHED, and it is the one its docstring names:
    # a carrier is never DROPPED for having nothing showable. Both carriers are still
    # asserted present below, the honest sentence is still counted twice, and the hub row
    # itself is still asserted present — under a label that says it is withheld rather than
    # under one that leaks. Nothing here is loosened: an equality on the page's content
    # replaces a weaker containment, and the leak is now pinned closed as well.
    assert WITHHELD_HUB_LABEL in page
    assert "Harbor Fund" not in page
    assert "Mockingbird Terrace" not in page
    assert page.count("Nothing behind this we are willing to show.") == 2
    assert "Kit Known" in page and "Lee Known" in page


def test_the_withheld_tally_reports_the_category_and_never_the_sentence(tmp_path):
    """The selling point, and the line it must not cross: counts and categories, no text."""
    facts = [
        _fact("a", "Kit's net worth is reported at a large number.", excluded=True,
              exclusion_reason="wealth"),
        _fact("b", "Kit's sibling runs a bakery.", excluded=True, exclusion_reason="family"),
        _fact("c", "Kit is on the board of Harbor Fund."),
    ]
    people = [
        _person("kit", "Kit Known", "investor:harbor-fund", "Harbor Fund", facts=facts,
                hub_facts=["c"]),
        _person("lee", "Lee Known", "investor:harbor-fund", "Harbor Fund",
                facts=[_fact("d", "Lee is on the board of Harbor Fund.")]),
    ]
    directory = _write(tmp_path / "tally", people)
    page = _corpus_page(directory)

    assert "wealth" in page and "family" in page
    for hidden in facts[:2]:
        assert hidden.text not in page
        assert hidden.provenance.quote not in page
    assert facts[2].text in page, "the positive control: a displayable fact is shown"
