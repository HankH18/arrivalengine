"""A residence sentence must not reach a host-facing page under a WORK category.

Found by a second observer session (BUILD observation X09031947-07) after the T-086/T-087
fixes landed. Its measurement and mine agree on the facts and disagree on the conclusion,
so both are recorded here.

WHAT IS TRUE. `data/dossiers/brad-feld.json` stores fact `ef6d8b928d78d66a-f9` —
"Brad Feld lives in Boulder, Colorado and Homer, Alaska." — with `category: "current_work"`
and `excluded: false`, and it still mints `city:homer-alaska`. That is a residence
statement, R11's first prohibited class, filed under a work category.

WHAT IS NOT TRUE. It does not leak. Measured against the live deploy after the T-086
redeploy: "Homer, Alaska", "homer-alaska" and bare "Homer" are all absent from `/`,
`/building`, `/graph`, `/corpus` and five rendered digests, and the only `data-hub` values
on those pages are `company:a16z` and `school:massachusetts-institute-of-technology`.
T-086's re-check at display time refuses the fact whatever its stored category says, and
T-087's gate in `build_graph` then withholds a label whose every carrier's evidence is
undisplayable.

WHY THIS MODULE EXISTS ANYWAY. The stored category makes the display gate the ONLY thing
standing between that sentence and a page, and the observer confirmed — as did I — that
no test in `tests/` or `.swarm-loop/acceptance/` mentions either hub. So a regression in
`is_displayable` would restore the leak silently, and a future "zero R11 offenders" sweep
over `data/dossiers/` would still report zero, because at the fact layer it is disguised
as `current_work`.

WHY THE FACT IS NOT SIMPLY RECATEGORISED, which is what the observer proposed. TASKS.md
T-9.1: "any wrong or distasteful fact triggers a fix in the pipeline or a fixture addition
to `taste_cases.yaml`, NEVER a hand-edit of the JSON", and T-9's non-goals repeat it. The
corpus is evidence of what the pipeline produced; editing it by hand would make the next
build disagree with the committed file and would hide the extractor defect that assigned
the category. The pipeline fix is the durable one and it is already in place; this module
is the guard that says so out loud.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.ticket("T-9")]

REPO_ROOT = Path(__file__).resolve().parents[1]
DOSSIER_DIR = REPO_ROOT / "data" / "dossiers"

#: The exact fact the observer named. Pinned by id so this cannot drift onto a different
#: sentence that happens to mention a place.
RESIDENCE_FACT_ID = "ef6d8b928d78d66a-f9"
RESIDENCE_PERSON = "brad-feld"


def _dossier(person_id: str):
    from arrival.contracts import Dossier

    path = DOSSIER_DIR / f"{person_id}.json"
    if not path.is_file():
        pytest.skip(f"no committed corpus at {path}; T-9's human gate has not run")
    return Dossier.model_validate_json(path.read_text(encoding="utf-8"))


def test_the_miscategorised_residence_fact_is_refused_at_display_time():
    """The one that matters: whatever the stored category says, it must not be displayable.

    Graded through `taste.is_displayable`, which is the product's own answer and a module
    this test does not own — not against a copy of the rule spelled out here.
    """
    from arrival.taste import is_displayable

    dossier = _dossier(RESIDENCE_PERSON)
    fact = next((f for f in dossier.facts if f.fact_id == RESIDENCE_FACT_ID), None)
    if fact is None:
        pytest.skip(f"{RESIDENCE_FACT_ID} is not in this corpus; it was rebuilt")

    assert "lives in" in fact.text.lower(), (
        "fixture pre-condition: this test is about a residence sentence, and this fact "
        f"no longer reads like one: {fact.text!r}"
    )
    assert not is_displayable(fact), (
        "R11 leak restored: a residence sentence is displayable again. It is stored as "
        f"category={fact.category!r} excluded={fact.excluded}, so the STORED corpus does "
        "not mark it withheld — the display-time re-check is the only thing refusing it, "
        f"and it just stopped. Fact {RESIDENCE_FACT_ID}: {fact.text!r}"
    )


def test_no_residence_sentence_anywhere_in_the_corpus_is_displayable():
    """The class, not the instance — the observer could only name the hubs it checked.

    A sweep of `data/dossiers/` for R11 categories reports zero, and will keep reporting
    zero while a residence sentence sits under a work category. This asks the question the
    category field cannot answer: does the sentence SAY where somebody lives.
    """
    from arrival.taste import is_displayable

    if not DOSSIER_DIR.is_dir():
        pytest.skip("no committed corpus")

    offenders: list[tuple[str, str, str]] = []
    for path in sorted(DOSSIER_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        from arrival.contracts import Dossier

        for fact in Dossier.model_validate(raw).facts:
            lowered = fact.text.lower()
            residence = " lives in " in f" {lowered} " or " owns homes in " in f" {lowered} "
            if residence and is_displayable(fact):
                offenders.append((path.stem, fact.fact_id, fact.text))

    assert not offenders, (
        "a sentence saying where somebody lives is displayable on a host-facing page. "
        "R11's first prohibited class is home and property; a work CATEGORY on the fact "
        f"does not change what the sentence says. Offenders: {offenders}"
    )


def test_the_hub_that_fact_mints_is_withheld_from_the_graph():
    """T-087's gate, exercised on the real corpus rather than on a constructed one.

    `city:homer-alaska` is still STORED — the fixes are display-time by design, so the
    committed JSON is unchanged. What must hold is that `build_graph` refuses to name it.
    """
    from arrival.graph import build_graph

    dossier = _dossier(RESIDENCE_PERSON)
    stored = {hub.hub_id for hub in dossier.hubs}
    if "city:homer-alaska" not in stored:
        pytest.skip("this corpus no longer mints city:homer-alaska; the build changed")

    graph = build_graph([dossier])
    named = {
        str(data.get("label", "")).lower()
        for _, data in graph.nodes(data=True)
        if data.get("label")
    }
    assert not any("homer" in label for label in named), (
        "the hub minted from a residence sentence is named in the graph again. Its label "
        "is the same secret the fact carries, and `graph._why` renders a shared hub's "
        f"label into the sentence a host reads ALOUD. Named hubs: {sorted(named)}"
    )
