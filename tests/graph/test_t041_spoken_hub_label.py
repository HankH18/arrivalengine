"""T-041: the hub label reads as English MID-SENTENCE, and a proper noun still survives.

``graph._why`` interpolates a hub label into a phrase template. Labels are stored
capitalised because they are also headings -- the R10 reasoning table and ``/debug`` print
one as a standalone cell -- so a capitalised common noun landed in the middle of the most
user-visible sentence this product produces. The measured line, from this repo's own frozen
corpus, was::

    Both deep in Developer-tools go-to-market.

R18 is the rule that exists to keep a host from stumbling over exactly that.

**What every assertion here grades against, and why none of it is an answer key I wrote.**
The packet's rule is that a test may not compare against a file its author owns, and this
ticket owns ``src/arrival/graph.py`` and ``tests/graph/**``. So:

* the "proper noun survived" and "still speakable" families read their labels out of the
  fixture dossiers -- ``tests/fixtures/dossiers/`` (T-0's, outside this ticket) and
  ``.swarm-loop/acceptance/fixtures/dossiers/`` (frozen, hash-locked, unwritable by any
  worker) -- and never from a table in this file;
* speakability is judged by ``arrival.digest.is_speakable`` and ``_splices_a_clause``,
  which belong to T-7 and were calibrated BEFORE this change on the very string it alters;
* the phrasing families compare against string literals spelled out in the test.

Nothing here compares against ``graph.py``'s own tables, its source text, or a snapshot
regenerated from its output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from t5_graph_helpers import filler, make_dossier, make_hub

from arrival.contracts import Dossier, Match
from arrival.digest import (
    SPOKEN_WORD_CAP,
    WHY_OF_LAST_RESORT,
    _noun_phrase_spans,
    _speakable_match,
    _splices_a_clause,
    is_speakable,
)
from arrival.graph import build_graph, match

#: T-5 owns `graph.py`; T-041 is the repair ticket. The harness's ticket ids are single
#: digit (`tests/harness.py`), so `--ticket T-5` must keep selecting these.
pytestmark = pytest.mark.ticket("T-5")

# --------------------------------------------------------------------------- the corpora

_REPO = Path(__file__).resolve().parents[2]

#: Both dossier corpora in the repo. The frozen one is orchestrator-owned and hash-locked;
#: the T-0 one belongs to a different ticket. Neither is writable by T-041.
CORPUS_DIRS = (
    _REPO / "tests" / "fixtures" / "dossiers",
    _REPO / ".swarm-loop" / "acceptance" / "fixtures" / "dossiers",
)

#: The hub types whose label is a CATEGORY. Spelled out here rather than imported from
#: `graph`, so that widening `graph._COMMON_NOUN_HUB_TYPES` cannot widen the test with it.
CATEGORY_TYPES = frozenset({"cause", "technology", "topic"})


def _load(directory: Path) -> list[Dossier]:
    paths = sorted(directory.glob("*.json"))
    assert paths, f"corpus is empty or missing: {directory}"
    return [Dossier.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def _every_match(directory: Path) -> list[tuple[str, str, Match]]:
    """``(arriving, other, Match)`` for every ordered pair the corpus can produce."""
    dossiers = _load(directory)
    graph = build_graph(dossiers)
    ids = sorted(d.person.person_id for d in dossiers)
    return [
        (a, m.other.person_id, m)
        for a in ids
        for m in match(graph, a, [p for p in ids if p != a])
    ]


def _every_why(directory: Path) -> list[tuple[str, str, str]]:
    """``(arriving, other, why)`` for every ordered pair, as ``graph`` emits it."""
    return [(a, other, m.why) for a, other, m in _every_match(directory)]


def _corpus_labels() -> list[tuple[str, str, str]]:
    """``(source, label, type)`` for every hub in both corpora, deduplicated."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for directory in CORPUS_DIRS:
        for dossier in _load(directory):
            for hub in dossier.hubs:
                if (hub.label, hub.type) not in seen:
                    seen.add((hub.label, hub.type))
                    out.append((directory.name, hub.label, hub.type))
    return out


def _match_for(label: str, hub_type: str) -> Match:
    """The ``Match`` a pair sharing exactly one hub of this ``(label, type)`` receives.

    Built through ``build_graph``/``match`` rather than by calling the private renderer, so
    what is measured is what the product actually emits — including ``contributions``, which
    is where the matcher records that these words are a hub's LABEL.
    """
    hub = make_hub(f"{hub_type}:only", label, hub_type)
    a = make_dossier("a", "A", [hub])
    b = make_dossier("b", "B", [hub])
    matches = match(build_graph([a, b, *filler(4)]), "a", ["b"])
    assert matches and matches[0].contributions, f"{label!r}/{hub_type} scored nothing"
    return matches[0]


def _why_for(label: str, hub_type: str) -> str:
    """The ``why`` that pair receives, as ``graph`` emits it."""
    return _match_for(label, hub_type).why


# --------------------------------------------------- the defect, on the corpus that had it


def test_the_measured_defect_line_now_reads_as_english():
    """The exact before/after this ticket exists for, on the frozen five-dossier corpus.

    ``jem-arrowood`` and ``runa-okonkwo`` share ``topic:developer-tools-go-to-market`` and
    nothing else that survives the IDF clamp, so this pair is the one line in either corpus
    that carried the defect. The expected sentence is a literal, not a snapshot.
    """
    whys = {
        (a, b): why
        for a, b, why in _every_why(_REPO / ".swarm-loop/acceptance/fixtures/dossiers")
    }
    line = whys[("jem-arrowood", "runa-okonkwo")]

    assert line == "Both deep in developer-tools go-to-market.", line
    assert "Both deep in Developer-tools" not in line, (
        "the capitalised compound is back mid-sentence; R18's whole point is that a host "
        f"reads this aloud: {line!r}"
    )
    assert whys[("runa-okonkwo", "jem-arrowood")] == line, "the pair is not symmetric"


# ------------------------------------------------- nothing became unspeakable (the hazard)


@pytest.mark.parametrize("directory", CORPUS_DIRS, ids=lambda p: p.parent.parent.name)
def test_every_why_in_a_corpus_is_speakable_by_t7s_own_judge(directory):
    """The measured hazard: an unspeakable ``why`` is not shown, it is BLANKED.

    ``digest._speakable_match`` replaces a why it cannot repair with ``WHY_OF_LAST_RESORT``,
    so a graph change that trips T-7's grammar rule deletes a Meet row's reasoning outright
    (R10). The judge is T-7's, calibrated before this change and not owned by this ticket.

    T-064 (found while fixing the sibling tripwire below, and the same defect). Both
    assertions here were written in 2d1ce59 against the BARE judge, and ec9ee37 ("fix(digest):
    a hub label is a noun, so naming one stops blanking the Meet row", T-052) moved the
    product to ``is_speakable(why, noun_phrases=labels)`` and
    ``_splices_a_clause(words, spans)``. So both go RED on a correct product for a real pair
    sharing a multi-word label whose head reads as a verb. Measured at HEAD, with a corpus in
    which two people share only ``company:reuters-media-group``:

        why                    'Both connected to Reuters Media Group.'
        is_speakable(why)      False        <- assertion 1 fires
        _splices_a_clause(...) True         <- assertion 2 fires
        _speakable_match(m)    'Both connected to Reuters Media Group.'   NOT blanked

    They now grade the judgement the product makes, which is what this test's own docstring
    says it is for: the harm is the BLANKING, and the blanking is `_speakable_match`'s call.
    """
    for arriving, other, row in _every_match(directory):
        spoken = _speakable_match(row)
        labels = [c.hub.label for c in row.contributions]
        assert spoken.why != WHY_OF_LAST_RESORT, (
            f"{arriving} -> {other}: T-7 refuses to speak this and blanks the Meet row's "
            f"reasoning to {WHY_OF_LAST_RESORT!r}. graph emitted: {row.why!r}"
        )
        assert is_speakable(spoken.why, noun_phrases=labels), (
            f"{arriving} -> {other}: the product shows {spoken.why!r}, which R18 refuses"
        )
        assert not _splices_a_clause(
            spoken.why.split(), _noun_phrase_spans(spoken.why.split(), labels)
        ), (
            f"{arriving} -> {other}: reads as a clause spliced into a noun slot, in a span "
            f"the matcher did NOT declare to be a hub label: {spoken.why!r}"
        )


@pytest.mark.parametrize(
    ("label", "hub_type"),
    [(label, hub_type) for _, label, hub_type in _corpus_labels()],
    ids=[f"{src}-{hub_type}-{label}" for src, label, hub_type in _corpus_labels()],
)
def test_every_corpus_hub_label_yields_a_speakable_why(label, hub_type):
    """Every label either corpus contains, forced into a why by being the only shared hub.

    The corpora's own pairings leave most labels clamped to zero contribution and therefore
    unnamed; this reaches the ones a real arrival with a different roster would reach.

    T-064 — WHY THIS GRADES THROUGH ``_speakable_match`` AND NOT THROUGH THE BARE JUDGE.
    This assertion was written in 2d1ce59 as ``assert is_speakable(why)``. Thirty-four
    minutes later ec9ee37 ("fix(digest): a hub label is a noun, so naming one stops blanking
    the Meet row", T-052) changed the product path: ``digest._speakable_match`` now calls
    ``is_speakable(why, noun_phrases=labels)``, declaring every label out of
    ``Match.contributions``, because the matcher has already said which words are a name.
    The bare call is therefore no longer the judgement the product makes, and it is wrong in
    the direction that costs a cycle to diagnose — it goes RED on a correct product. Measured
    at HEAD, with no change of mine in the tree: the company label "Reuters Media Group"
    gives ``is_speakable(why) is False`` while ``_speakable_match`` returns "Both connected
    to Reuters Media Group." unblanked. It is a false alarm waiting for either corpus to gain
    such a label.

    The tripwire's VALUE is unchanged and is what is asserted here: an unspeakable why is not
    shown, it is BLANKED to ``WHY_OF_LAST_RESORT``, which deletes a Meet row's exposed
    reasoning outright (R10). That harm is what goes red below. It is not neutered into
    always passing —
    ``test_the_tripwire_still_fires_for_a_label_the_product_genuinely_cannot_speak``
    constructs a label that still trips it.
    """
    row = _match_for(label, hub_type)
    spoken = _speakable_match(row)

    assert spoken.why != WHY_OF_LAST_RESORT, (
        f"{hub_type} {label!r} produces a why the product cannot speak, so T-7 blanks the "
        f"Meet row's reasoning to {WHY_OF_LAST_RESORT!r}. graph emitted: {row.why!r}"
    )
    # ...and it is speakable by the judgement the product actually applies, stated here
    # rather than inferred from `_speakable_match`'s internal structure.
    assert is_speakable(spoken.why, noun_phrases=[c.hub.label for c in row.contributions]), (
        f"{hub_type} {label!r}: the product shows {spoken.why!r}, which R18 refuses"
    )


def test_the_tripwire_still_fires_for_a_label_the_product_genuinely_cannot_speak():
    """T-064's other half: the retargeted tripwire is not a test that cannot fail.

    Retargeting a tripwire at the product path is the move that quietly turns it green
    forever, so the predicate the parametrized test above asserts is exercised here on a
    label constructed to defeat it — and it must go red. It is the same predicate
    ``test_every_why_in_a_corpus_is_speakable_by_t7s_own_judge`` now uses, so this control
    covers both tripwires.

    A label of ``SPOKEN_WORD_CAP + 1`` tokens whose head reads as a verb is the case.
    ``_speakable_match`` declares the label as a noun phrase, but ``speakable`` then
    TRUNCATES the line to the word cap, and ``_noun_phrase_spans`` only covers tokens where
    the whole phrase appears in order — so the declaration stops matching, "Reuters" is left
    capitalised, ending in "-s" and directly after the bare preposition "to" with words still
    following it, and ``_splices_a_clause`` refuses the repair. R18 has no third option, so
    the row's reasoning is blanked.

    Nothing here grades against a table in this file: the cap is ``digest.SPOKEN_WORD_CAP``,
    the verdict is ``digest._speakable_match``'s, and the blank is ``digest.WHY_OF_LAST_RESORT``.
    """
    label = "Reuters " + " ".join(f"w{n}" for n in range(SPOKEN_WORD_CAP))
    row = _match_for(label, "company")

    # The bar the parametrized tripwire applies, applied by hand to a hostile label.
    spoken = _speakable_match(row)
    assert spoken.why == WHY_OF_LAST_RESORT, (
        "a label the product cannot read aloud no longer trips the tripwire, so the "
        f"parametrized test above can no longer fail: {spoken.why!r}"
    )

    # Companion control: the SAME head, inside the cap, is fine — so the red above is the
    # label's unspeakability and not "any label containing 'Reuters'".
    short = _speakable_match(_match_for("Reuters Media Group", "company"))
    assert short.why != WHY_OF_LAST_RESORT, (
        f"a speakable label was blanked too, so the check above proves nothing: {short.why!r}"
    )
    assert "Reuters Media Group" in short.why, short.why


# ------------------------------------------------------------- proper nouns must survive


@pytest.mark.parametrize(
    ("label", "hub_type"),
    [
        (label, hub_type)
        for _, label, hub_type in _corpus_labels()
        if hub_type not in CATEGORY_TYPES
    ],
    ids=[
        f"{hub_type}-{label}"
        for _, label, hub_type in _corpus_labels()
        if hub_type not in CATEGORY_TYPES
    ],
)
def test_a_named_entity_keeps_its_capitalisation_verbatim(label, hub_type):
    """"Both worked at quarrystone labs" is a false claim about somebody's company.

    Every company, investor, school and city label in either corpus must appear in the why
    byte-for-byte as the dossier stores it. The dossiers are the answer key and this ticket
    cannot write them.
    """
    why = _why_for(label, hub_type)
    assert label in why, (
        f"a {hub_type} label was re-cased on its way into the sentence: "
        f"{label!r} is not in {why!r}"
    )


@pytest.mark.parametrize(
    ("label", "hub_type"),
    [
        ("Foundry Seed 2019", "topic"),  # Title Case: a name even under a category type
        ("Quarrystone Labs", "topic"),
        ("Bank of America", "topic"),
        ("AI safety", "topic"),  # acronym head
        ("A/B testing", "topic"),
        ("GitHub actions", "technology"),  # CamelCase head
        ("Kubernetes", "technology"),  # one word: nothing distinguishes it from a category
        ("Austin", "topic"),
        ("Machine Learning", "topic"),  # Title Case common noun: the deliberate miss
        ("Ocean Cleanup", "cause"),
        # A sloppily sentence-cased NAME. Nothing in the orthography saves these -- the
        # hub TYPE is the only thing standing between them and "both worked at northgate
        # labs", which is the failure this ticket must not trade for the one it fixes.
        ("Northgate labs", "company"),
        ("Foundry seed 2019", "investor"),
        ("Bellhaven polytechnic", "school"),
        ("Rio verde college", "school"),
    ],
)
def test_capitalisation_is_left_alone_whenever_the_evidence_is_ambiguous(label, hub_type):
    """The rule errs toward leaving capitalisation alone, and this pins that direction.

    Each of these is a case where lower-casing would either state something false about a
    named entity or mangle an acronym. The deliberate cost is "Machine Learning", a common
    noun the rule refuses because English Title-Cases proper names and it cannot tell.
    """
    assert label in _why_for(label, hub_type)


# -------------------------------------------------------------- category labels read down


@pytest.mark.parametrize(
    ("label", "hub_type", "expected"),
    [
        ("Machine learning", "topic", "Both deep in machine learning."),
        ("Remote work", "topic", "Both deep in remote work."),
        (
            "Developer-tools go-to-market",
            "topic",
            "Both deep in developer-tools go-to-market.",
        ),
        (
            "Evaluation harnesses",
            "technology",
            "Both building on evaluation harnesses.",
        ),
        ("Ocean cleanup", "cause", "Both behind ocean cleanup."),
        ("Climate resilience", "topic", "Both deep in climate resilience."),
    ],
)
def test_a_sentence_cased_category_label_is_lower_cased_in_the_sentence(
    label, hub_type, expected
):
    """The whole sentence, spelled out as a literal -- no snapshot, no round trip."""
    assert _why_for(label, hub_type) == expected


def test_only_the_leading_character_moves():
    """The tail of the label is returned byte-identical, hyphens and inner casing included.

    ``"iOS"`` is the discriminating case: its first character is lower-case, so the
    Title-Case guard lets the label through, and a rule that lower-cased the WHOLE label
    rather than its leading character would say "ios" -- which is a different product.
    """
    why = _why_for("Developer-tools go-to-market", "topic")
    assert "developer-tools go-to-market" in why
    assert "Go-To-Market" not in why and "developer tools" not in why

    assert _why_for("Open-source iOS tooling", "technology") == (
        "Both building on open-source iOS tooling."
    )
    assert _why_for("Container runtimes for macOS", "technology") == (
        "Both building on container runtimes for macOS."
    )


def test_the_sentence_still_opens_on_a_capital_and_ends_on_a_full_stop():
    """R18 mechanics: lower-casing a label must not reach the sentence-initial capital."""
    for label, hub_type in (("Machine learning", "topic"), ("Quarrystone Labs", "company")):
        why = _why_for(label, hub_type)
        assert why[0].isupper(), why
        assert why.endswith("."), why


# ---------------------------------------------------------- the stored Hub is not touched


def test_the_why_does_not_re_case_the_hub_the_reasoning_row_prints():
    """R10 prints ``hub.label`` as a table cell, where the capital is CORRECT.

    This is the test that fails if the lower-casing is moved to the source -- to
    ``extract``, to the elected label, or to the ``Hub`` on the contribution -- instead of
    to the point of use inside the sentence. The stored labels come from the frozen corpus.
    """
    directory = _REPO / ".swarm-loop/acceptance/fixtures/dossiers"
    stored = {
        (hub.hub_id, hub.label) for d in _load(directory) for hub in d.hubs
    }
    by_id = dict(stored)

    dossiers = _load(directory)
    graph = build_graph(dossiers)
    ids = sorted(d.person.person_id for d in dossiers)

    checked = 0
    for a in ids:
        for m in match(graph, a, [p for p in ids if p != a]):
            for contribution in m.contributions:
                hub = contribution.hub
                assert hub.label == by_id[hub.hub_id], (
                    f"the hub the R10 row prints was re-cased: {hub.hub_id} carries "
                    f"{hub.label!r}, the dossier stores {by_id[hub.hub_id]!r}"
                )
                checked += 1
    assert checked, "no contributions were inspected; the corpus stopped matching"

    for node, data in graph.nodes(data=True):
        if data.get("kind") == "hub":
            assert data["label"] == by_id[data["hub_id"]], (
                f"the graph node's own label was re-cased: {node}"
            )


def test_the_why_still_names_the_hub_by_label_and_never_by_id():
    """The frozen T-5 invariant, re-asserted where a re-casing bug would break it."""
    whys = {
        (a, b): why
        for a, b, why in _every_why(_REPO / ".swarm-loop/acceptance/fixtures/dossiers")
    }
    line = whys[("sil-vantorre", "runa-okonkwo")]
    assert "Foundry Seed 2019" in line, line
    assert "investor:foundry-seed-2019" not in line and "hub:" not in line, line
