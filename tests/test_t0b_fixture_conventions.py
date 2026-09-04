"""T-0b regressions for the two fixture-convention traps and the empty /debug fixture.

A fixture is an answer key. When it disagrees with the contract, a worker reads the
fixture, infers the wrong rule, and is then graded on the right one — which is worse than
having no fixture at all, because it costs the worker the time to trust it first.

D6a — `person_id` was the filename stem (`alpha`..`delta`) while `person.name` was an
      unrelated fictional name, so 0 of 4 satisfied the `person_id == slug(name)` rule
      that `contracts.py:59`, `DESIGN.md` §Interfaces and `SPEC.md` Q1 all state. The
      frozen grading corpus obeys it in all five of its dossiers.

D6b — every fixture carried the hub `topic:ai`, whose label is on DESIGN Decision 3's
      stop-hub list, so a conformant extractor could never produce this corpus.

D7  — the dossier T-8's acceptance points `/debug` at had 0 excluded facts, 0 rejected
      verdicts and 0 `non_obvious` facts, so the surface that is *supposed* to show
      withheld material had nothing to show and R7's "Not on the first page" slot was
      empty for the person T-8 arrives.

These tests are about the CONVENTIONS. The designed hub overlaps and the pinned score
arithmetic live in `test_t0_fixtures.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arrival.contracts import Dossier
from arrival.util import normalize_ws, slug

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]
DOSSIER_DIR = Path(__file__).resolve().parent / "fixtures" / "dossiers"

#: The mnemonic ids T-5's and T-8's acceptance criteria name in tickets.json and
#: TASKS.md. Deliberately not slug(person.name) -- see the justify-test-edit block below.
ALPHA, BRAVO, CHARLIE, DELTA = "alpha", "bravo", "charlie", "delta"

#: DESIGN Decision 3, verbatim: "Stop-hubs (never nodes): {texas, startup, founder, ai,
#: technology, business, ceo, investor} after lowercasing."
STOP_HUBS = frozenset(
    {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}
)

#: R7's "Not on the first page" slot (DESIGN §Non-obvious eligibility).
NON_OBVIOUS_KINDS = frozenset(
    {"edgar", "uspto", "propublica", "wayback", "github", "hn", "openalex", "wikidata", "podcast"}
)

#: The person T-8's acceptance arrives and points `/debug` at. Named by NAME, not by id,
#: so this file keeps working whichever id convention the corpus ends up on.
DEBUG_SUBJECT = "Selin Ardahan"

#: The rare hub the whole matching-score design rests on. See the LABELS test below.
RARE_HUB = "investor:foundry-seed-2019"


@pytest.fixture(scope="module")
def dossiers() -> dict[str, Dossier]:
    paths = sorted(DOSSIER_DIR.glob("*.json"))
    assert paths, f"no dossier fixtures found in {DOSSIER_DIR}"
    return {path.stem: Dossier.model_validate_json(path.read_text()) for path in paths}


@pytest.fixture(scope="module")
def debug_subject(dossiers) -> Dossier:
    matches = [d for d in dossiers.values() if d.person.name == DEBUG_SUBJECT]
    assert len(matches) == 1, f"expected exactly one {DEBUG_SUBJECT} fixture, got {len(matches)}"
    return matches[0]


# --------------------------------------------------------------------------
# D6a — person_id == slug(name)
# --------------------------------------------------------------------------


# justify-test-edit -- REPLACED, not deleted, and the reasoning is recorded because an
# unjustified assertion change is indistinguishable from reward hacking after the fact.
#
# WAS: test_every_fixture_person_id_is_the_slug_of_its_name, asserting
#      person_id == slug(name) across THESE fixtures.
# REQUIREMENT IT ENCODED: contracts.py:59, DESIGN Interfaces and SPEC Q1 all state
#      person_id = slug(name). That requirement is REAL and is not being weakened.
# WOULD IT STILL BE WRONG IF THE FIXTURE RENAME WERE REVERTED? YES -- which is why the
#      assertion moved rather than the fixtures staying renamed. The invariant is a
#      product invariant. This file's fixtures are T-0's own unit fixtures, and nothing
#      scored reads them: the frozen acceptance suite never asserts the invariant and
#      never opens tests/ at all (both verified by grep). Meanwhile SEVEN lines of
#      ticket text that T-5 and T-8 are built against name these people by mnemonic --
#      match(g,'charlie',['alpha','bravo','delta']) and GET /debug/charlie among them --
#      so renaming them to slugs broke the ticket text and bought nothing measurable.
#      The old assertion applied a real invariant to the wrong artifact.
# NOW: the invariant is pinned where it actually holds (the frozen grading corpus), and
#      the deliberate deviation here is pinned so it cannot drift back by accident.


def test_the_frozen_grading_corpus_satisfies_person_id_equals_slug_of_name():
    """The product invariant, asserted against the corpus that actually grades the build.

    contracts.py:59, DESIGN Interfaces and SPEC Q1 all state person_id = slug(name), and
    T-8's /arrive takes a NAME whose documented lookup is slug(name). The frozen corpus
    is what every scored metric reads, so it is where this has to hold.
    """
    import json

    frozen = REPO_ROOT / ".swarm-loop" / "acceptance" / "fixtures" / "dossiers"
    files = sorted(frozen.glob("*.json"))
    assert files, f"no frozen dossiers found at {frozen} -- this test grades nothing"
    wrong = {}
    for f in files:
        person = json.loads(f.read_text())["person"]
        if person["person_id"] != slug(person["name"]):
            wrong[f.name] = (person["person_id"], slug(person["name"]))
    assert not wrong, f"frozen corpus violates person_id == slug(name): {wrong}"


def test_these_unit_fixtures_deliberately_use_mnemonic_ids():
    """The deviation above, pinned so it cannot silently drift back.

    These four are named alpha/bravo/charlie/delta because the T-5 and T-8 acceptance
    criteria name them that way in tickets.json and TASKS.md. If someone renames them to
    slug(name) again, this fails and points at the seven ticket lines that would break.
    """
    assert (ALPHA, BRAVO, CHARLIE, DELTA) == ("alpha", "bravo", "charlie", "delta"), (
        "T-0's unit fixtures were renamed away from the mnemonic ids that T-5's "
        "match(g,'charlie',['alpha','bravo','delta']) and T-8's GET /debug/charlie "
        "name in tickets.json and TASKS.md. Update those seven lines first, or revert."
    )


def test_every_fixture_file_is_named_for_its_person_id(dossiers):
    """DESIGN §Data models: a dossier on disk is `{person_id}.json`.

    T-6 writes them under that name and the frozen corpus is stored that way, so the
    fixture directory must be addressable the same way.
    """
    for stem, dossier in dossiers.items():
        assert dossier.person.person_id == stem, (
            f"{stem}.json holds person_id {dossier.person.person_id!r}"
        )


def test_every_resolution_is_for_its_own_person(dossiers):
    for stem, dossier in dossiers.items():
        assert dossier.resolution.person_id == dossier.person.person_id, stem


# --------------------------------------------------------------------------
# D6b — the stop-hub list, and what it is matched against
# --------------------------------------------------------------------------


def test_no_fixture_hub_is_a_stop_hub(dossiers):
    """A fixture the pipeline is FORBIDDEN to produce is a broken answer key.

    T-3 is graded on never emitting a stop-hub, so a corpus containing one can only be
    reached by an extractor that fails its own acceptance.
    """
    offenders = {
        f"{stem}:{hub.hub_id}"
        for stem, dossier in dossiers.items()
        for hub in dossier.hubs
        if hub.label.strip().casefold() in STOP_HUBS
    }
    assert not offenders, f"stop-hub labels in the fixture corpus: {sorted(offenders)}"


def test_the_stop_list_matches_hub_LABELS_and_never_a_type_prefix(dossiers):
    """READ THIS BEFORE IMPLEMENTING THE STOP LIST (T-3, T-5).

    `investor` is on the stop list AND is a `HubType`. Matching the list against
    `hub.type`, or against the `type:` prefix of `hub_id`, deletes
    `investor:foundry-seed-2019` — the one rare hub the entire matching-score design rests
    on (charlie-delta 100, everything else 0; the frozen corpus is built the same way).
    The list is matched against the hub's LABEL, lowercased: "Foundry Seed 2019" is not
    "investor", so the hub survives; "AI" is on the list, so `topic:ai` does not.
    """
    rare = [h for d in dossiers.values() for h in d.hubs if h.hub_id == RARE_HUB]
    assert len(rare) == 2, "the rare hub must be on exactly two fixture people"
    for hub in rare:
        assert hub.type == "investor"
        assert hub.label.strip().casefold() not in STOP_HUBS, (
            "the rare investor hub's LABEL is not a stop word; only a type-prefix match "
            "would drop it, and that would destroy the scoring design"
        )


# --------------------------------------------------------------------------
# D7 — the /debug subject needs material to show
# --------------------------------------------------------------------------


def test_the_debug_subject_has_excluded_facts_with_reasons(debug_subject):
    """R15/T-8: `/debug/{person_id}` shows the withheld facts AND why they were withheld."""
    excluded = [f for f in debug_subject.facts if f.excluded]
    assert excluded, f"{DEBUG_SUBJECT} has no excluded facts for /debug to show"
    for fact in excluded:
        assert fact.exclusion_reason is not None, fact.fact_id


def test_the_debug_subject_has_a_rejected_verdict(debug_subject):
    """R15/T-8: `/debug` also shows the rejected candidate documents."""
    rejected = debug_subject.resolution.rejected
    assert rejected, f"{DEBUG_SUBJECT} has no rejected verdicts for /debug to show"
    for verdict in rejected:
        assert verdict.match in {"no", "unsure"}
        assert verdict.evidence.strip(), verdict.doc_id
        assert verdict.disambiguator.strip(), verdict.doc_id
        assert verdict.doc_id not in debug_subject.resolution.accepted_doc_ids


def test_the_debug_subject_has_a_displayable_non_obvious_fact(debug_subject):
    """R7: the arriving person's digest needs something for "Not on the first page"."""
    candidates = [
        f
        for f in debug_subject.facts
        if f.category == "non_obvious"
        and not f.excluded
        and f.provenance.source_kind in NON_OBVIOUS_KINDS
        and f.provenance.confidence >= 0.7
    ]
    assert candidates, (
        f"{DEBUG_SUBJECT} is the person T-8 arrives, and has no eligible non_obvious fact"
    )


def test_the_debug_subjects_withheld_material_is_really_withheld(debug_subject):
    """The withheld facts must not also be reachable through a fact the digest renders."""
    shown = " ".join(
        f"{f.text} {f.provenance.quote} {f.provenance.url}"
        for f in debug_subject.facts
        if not f.excluded
    )
    shown = normalize_ws(shown)
    for fact in debug_subject.facts:
        if not fact.excluded:
            continue
        assert normalize_ws(fact.text) not in shown, fact.fact_id
        assert normalize_ws(fact.provenance.quote) not in shown, fact.fact_id
