"""T-087: a hub whose every carrier's evidence is withheld loses its NAME, not its edges.

``tests/test_tadv_r11_hub_label_bypass.py`` states the requirement, end to end, over the
four rendered surfaces. This module works one layer down, on ``arrival.graph`` itself, and
pins the two things a page-level test cannot see:

* **the gate is the WEAKEST one that closes the leak.** It withholds a label only when
  every carrier's resolvable evidence is undisplayable, and it leaves the hub's node, edges,
  idf, boost and contribution exactly as they were. "Matching is not display" (T-5
  acceptance 1) survives the fix in full: the pair still scores the same number.
* **the failure modes of judging on silence.** A hub with no evidence ids, or with ids that
  resolve to nothing, must stay nameable. ``extract._roster_city_hub`` builds hubs that way
  and so does most of the test corpus, so a gate that redacted on absence would blank the
  graph — a far larger and quieter failure than the leak it was fixing.

The synthetic dossiers are built through ``tadv_corpus.synthetic_person``, which validates
against ``arrival.contracts`` and belongs to another lane, so a schema drift breaks these
loudly rather than turning them into statements about a shape nothing uses.
"""

from __future__ import annotations

import pytest

from arrival.contracts import Dossier
from arrival.graph import WITHHELD_HUB_LABEL, build_graph, hub_node
from arrival.graph import match as match_present
from tadv_corpus import synthetic_person

pytestmark = pytest.mark.ticket("T-5")

#: A street a member lives on: R11 `home_or_property`, and a label that IS the secret.
SECRET = "Ravensworth Hill"


def _dossier(person_id, name, facts, hubs) -> Dossier:
    return Dossier.model_validate(synthetic_person(person_id, name, facts, hubs))


def _filler(count: int) -> list[Dossier]:
    """People who carry a hub of their own, so N is large enough for a non-zero idf."""
    return [
        _dossier(
            f"filler-{i}",
            f"Filler {i}",
            [(f"Filler {i} works at Northwind.", False, None)],
            [("company:northwind", "company", "Northwind", 0)],
        )
        for i in range(count)
    ]


def _labels(graph):
    return {
        data["hub_id"]: data["label"]
        for _node, data in graph.nodes(data=True)
        if data.get("kind") == "hub"
    }


# ------------------------------------------------------------------- the leak, closed


def test_a_hub_evidenced_only_by_withheld_facts_loses_its_label():
    dossiers = [
        _dossier(
            pid,
            name,
            [(f"{name}'s home is on {SECRET}.", True, "home_or_property")],
            [("city:ravensworth-hill", "city", SECRET, 0)],
        )
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ] + _filler(2)

    labels = _labels(build_graph(dossiers))
    assert labels["city:ravensworth-hill"] == WITHHELD_HUB_LABEL
    assert SECRET not in labels.values()
    # The control: an unrelated, cleanly evidenced hub in the same graph is untouched.
    assert labels["company:northwind"] == "Northwind"


def test_the_withheld_hub_still_scores_exactly_as_it_did():
    """Matching is not display. The fix must cost the product nothing but the name."""
    secret_hub = [("city:ravensworth-hill", "city", SECRET, 0)]
    withheld = [
        _dossier(pid, name, [(f"{name}'s home is on {SECRET}.", True, "home_or_property")],
                 secret_hub)
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ] + _filler(2)
    # The same corpus with the evidence displayable, so only the gate differs.
    shown = [
        _dossier(pid, name, [(f"{name} keeps an office on {SECRET}.", False, None)], secret_hub)
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ] + _filler(2)

    def score(dossiers):
        graph = build_graph(dossiers)
        (match,) = [m for m in match_present(graph, "ann-one", ["bob-two"])]
        return match

    hidden, visible = score(withheld), score(shown)
    assert hidden.score == visible.score > 0
    assert [c.contribution for c in hidden.contributions] == [
        c.contribution for c in visible.contributions
    ]
    # ...and the hub is still a node with edges to both people, so the graph is unchanged.
    graph = build_graph(withheld)
    node = hub_node("city:ravensworth-hill")
    assert graph.has_edge("person:ann-one", node) and graph.has_edge("person:bob-two", node)


def test_the_spoken_why_names_the_next_hub_rather_than_the_withheld_one():
    """R18. A withheld hub is skipped, not spoken and not paraphrased.

    The pair also shares a cleanly evidenced hub, so there is something else to say; the
    line must say that instead of naming the street or announcing the withholding.
    """
    dossiers = [
        _dossier(
            pid,
            name,
            [
                (f"{name}'s home is on {SECRET}.", True, "home_or_property"),
                (f"{name} led the Foundry Seed 2019 fund.", False, None),
            ],
            [
                ("city:ravensworth-hill", "city", SECRET, 0),
                ("investor:foundry-seed-2019", "investor", "Foundry Seed 2019", 1),
            ],
        )
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ] + _filler(3)

    graph = build_graph(dossiers)
    (match,) = match_present(graph, "ann-one", ["bob-two"])

    assert SECRET not in match.why
    assert WITHHELD_HUB_LABEL not in match.why
    assert "Foundry Seed 2019" in match.why, match.why
    # T-016: the path is the picture of the why, so it must not route through the hub the
    # sentence refused to name.
    assert hub_node("city:ravensworth-hill") not in match.path
    assert hub_node("investor:foundry-seed-2019") in match.path


def test_a_pair_sharing_only_a_withheld_hub_says_nothing_rather_than_something_coy():
    dossiers = [
        _dossier(pid, name, [(f"{name}'s home is on {SECRET}.", True, "home_or_property")],
                 [("city:ravensworth-hill", "city", SECRET, 0)])
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ] + _filler(2)

    (match,) = match_present(build_graph(dossiers), "ann-one", ["bob-two"])
    assert match.why == "Nothing in common on the record yet."
    assert match.path == []
    assert match.score > 0, "the hub still scores; only the sentence declines to name it"


# --------------------------------------------------------- the over-redaction failure modes


def test_one_displayable_carrier_anywhere_keeps_the_label_for_everybody():
    """Deliberately the weakest rule that works: the hub is a connection somebody can show."""
    dossiers = [
        _dossier("ann-one", "Ann One", [(f"Ann One's home is on {SECRET}.", True,
                                         "home_or_property")],
                 [("city:ravensworth-hill", "city", SECRET, 0)]),
        _dossier("bob-two", "Bob Two", [(f"Bob Two runs a gallery on {SECRET}.", False, None)],
                 [("city:ravensworth-hill", "city", SECRET, 0)]),
    ] + _filler(2)

    assert _labels(build_graph(dossiers))["city:ravensworth-hill"] == SECRET


def test_a_hub_with_no_evidence_at_all_is_never_judged_on_silence():
    """`extract._roster_city_hub` builds hubs this way; redacting on absence blanks the graph."""
    payloads = [
        synthetic_person(pid, name, [(f"{name}'s home is on a street.", True,
                                      "home_or_property")],
                         [("city:lisbon", "city", "Lisbon", 0)])
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ]
    for payload in payloads:
        payload["hubs"][0]["evidence_fact_ids"] = []
    built = [Dossier.model_validate(p) for p in payloads] + _filler(2)

    assert _labels(build_graph(built))["city:lisbon"] == "Lisbon"


def test_an_evidence_id_that_resolves_to_no_fact_is_left_alone():
    """`research._supported_hubs` states this rule; the display gate follows it exactly."""
    payloads = [
        synthetic_person(pid, name, [(f"{name}'s home is on a street.", True,
                                      "home_or_property")],
                         [("city:lisbon", "city", "Lisbon", 0)])
        for pid, name in [("ann-one", "Ann One"), ("bob-two", "Bob Two")]
    ]
    for payload in payloads:
        payload["hubs"][0]["evidence_fact_ids"] = ["a-fact-that-does-not-exist"]
    built = [Dossier.model_validate(p) for p in payloads] + _filler(2)

    assert _labels(build_graph(built))["city:lisbon"] == "Lisbon"


def test_a_corpus_with_nothing_excluded_is_byte_for_byte_the_graph_it_always_was():
    """The blast-radius check: the gate is inert on a clean corpus."""
    dossiers = _filler(4)
    labels = _labels(build_graph(dossiers))
    assert WITHHELD_HUB_LABEL not in labels.values()
    assert labels == {"company:northwind": "Northwind"}
