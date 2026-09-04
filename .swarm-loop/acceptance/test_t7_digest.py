"""FROZEN acceptance tests for ticket T-7 — the digest builder.

Graded requirements: R7 (sections + hard caps), R8 (empty Meet is stated, not padded),
R9 (every shown fact is cited), R13 (exclusion policy on the page), R14 + R18 (the three
spoken lines are speakable as written), S6 (sources cover exactly what is shown).

Rules this module obeys (see the frozen harness brief):

* Product imports are LAZY — inside the test bodies. At cycle 0 `arrival` does not exist;
  a module-scope import would turn an unbuilt feature into a COLLECTION error and take the
  whole file out of both the numerator and the denominator.
* The inputs come from the ORCHESTRATOR-OWNED corpus under `.swarm-loop/acceptance/fixtures`
  via the session `frozen_fixtures` fixture, never from `tests/fixtures` — a gradee that can
  write the answer key is not being graded.
* `Match` objects are constructed DIRECTLY from `arrival.contracts` rather than by calling
  `arrival.graph.match`, so a T-5 regression cannot mark T-7 red.
* Async is driven with `asyncio.run(_inner())`; the frozen suite runs with `-o addopts=` and
  its own rootdir, so the project's `asyncio_mode = auto` is deliberately not relied upon.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import typing

import pytest

# Two markers, deliberately. `t7` is the marker NAME this suite's own
# conftest mandates for selection (`pytest -m t7`), and every scored metric
# selects on it. `ticket("T-7")` is the ARGUMENT form the freeze-time coverage
# and read-edge gates parse out of the AST; without it those gates see a suite
# with zero attributed tests and freeze refuses every ticket. Additive on
# purpose: neither dialect replaces the other.
pytestmark = [pytest.mark.t7, pytest.mark.ticket("T-7")]

try:  # 3.10+ writes `X | None` as types.UnionType, 3.9 as typing.Union
    from types import UnionType as _UnionType
except ImportError:  # pragma: no cover - Python < 3.10
    _UnionType = None


ARRIVING = "runa-okonkwo"
PRESENT = ["sil-vantorre", "jem-arrowood", "mira-hollowell", "theo-baptiste"]

# ---------------------------------------------------------------------------
# Frozen corpus facts (FROZEN-SPEC section 1b). These ids and strings are pinned by
# the orchestrator-owned fixtures; they are the answer key this ticket is graded against.
# ---------------------------------------------------------------------------

# runa's material that must NEVER reach a host-facing surface, and why:
#   f12  excluded         -> exclusion_reason "family"
#   f13  excluded         -> exclusion_reason "home_or_property"
#   f14  confidence 0.55  -> below the R12 display floor, NOT excluded
#   f15  source_kind fec  -> off the R12 display whitelist, NOT excluded
#
# f15's `excluded: false` is LOAD-BEARING CORPUS, not an oversight, and must not be "tidied"
# to true by anyone editing these fixtures. test_t4_taste.py takes it as `bad_kind` and
# asserts `bad_kind.excluded is False` while `is_displayable(bad_kind) is False`: it is the
# only proof in the suite that the source-kind display gate (R12) is a SEPARATE gate from the
# taste filter (R11), rather than the taste filter wearing a second name. Marking it excluded
# would make that test pass for the wrong reason and lose the discrimination entirely.
WITHHELD_FACT_IDS = {
    "runa-okonkwo-f12",
    "runa-okonkwo-f13",
    "runa-okonkwo-f14",
    "runa-okonkwo-f15",
}
# Each of those four facts is the ONLY fact citing its document, so a citation list that
# names one of these documents has leaked withheld material into "Why we know this".
WITHHELD_DOC_IDS = {
    "de86db5a839147e2",  # f12, family
    "babaa3f0a06e9dfe",  # f13, home_or_property
    "cf0c86082dfcc081",  # f14, low confidence
    "31173fc736e73821",  # f15, fec
}
WITHHELD_STRINGS = [
    "their spouse Delia Moreno-Vance",
    "1442 Quarrystone Lane",
    "a low-confidence claim about ferry schedules",
    "a contribution recorded in a filing",
]

# The positive controls. If these are absent the digest is empty, and an empty digest must
# never be allowed to pass a "nothing forbidden appears" test.
NON_OBVIOUS_FACT_ID = "runa-okonkwo-f09"
NON_OBVIOUS_STRING = "Quarrystone Labs shipped a public status page in 2017"
TOP_HOOK_FACT_ID = "runa-okonkwo-f03"  # hook, podcast, confidence 0.91  <- highest
OTHER_HOOK_FACT_ID = "runa-okonkwo-f04"  # hook, hn, confidence 0.78
TOP_HOOK_PHRASE = "developer-tools pricing should be published in full on a public page"
OTHER_HOOK_PHRASE = "worst error messages"

# recent_activity facts, most recent first: f05 2026-02-11, f06 2025-11-04,
# f07 2025-08-19, f08 2025-05-27. Four candidates against a cap of three, so f08 is the
# one the cap must cut under any defensible reading of "most recent professional activity".
OLDEST_ACTIVITY_FACT_ID = "runa-okonkwo-f08"

# The newest displayable date in the dossier is shared by TWO facts, and the tie cannot be
# broken in the corpus: f05 (recent_activity) and f11 (affiliation) are both extracted from
# RawDoc 92b1d32390d8795f, and `provenance.published_at` is its document's publication date
# for all 35 facts here (measured in CORPUS-PROOF check 7, section D). Re-dating one of them
# would put the fixture in contradiction with its own source document.
#
# The tie is nonetheless NOT a grading ambiguity, which is worth stating because it looks
# like one. With three slots to fill, f05 survives every reading of the candidate set that
# the binding documents support:
#   * narrowest (`recent_activity` only, 4 candidates): f05 is the newest of the four;
#   * widest (TASKS T-7 acceptance 1 -- every `is_displayable` fact, `published_at` desc,
#     13 candidates): f05 and f11 are the two newest, so BOTH land in a three-slot list
#     however the tie is broken;
#   * anything in between (e.g. recent_activity + affiliation): same, for the same reason.
# The one shape that could show f11 without f05 is a Lately deduplicated by `doc_id`, and
# nothing asks for that: R9/S6 dedupe the SOURCE list by doc_id, never the bullets.
#
# It is also pinned elsewhere in the frozen suite and must stay consistent with it:
# `test_t8_web.py` DISPLAYED[1] is f05's text verbatim and is asserted to appear in the
# rendered digest, and Lately is the only section f05 can reach (who_line is built from
# `current_work`, non_obvious is f09, say_out_loud from a `hook`). Relaxing this assertion
# to "either tied fact will do" would leave T-7 and T-8 grading the same digest differently.
NEWEST_DISPLAYABLE_DATE = dt.date(2026, 2, 11)
MOST_RECENT_ACTIVITY_FACT_ID = "runa-okonkwo-f05"
TIED_NEWEST_FACT_IDS = {"runa-okonkwo-f05", "runa-okonkwo-f11"}

# DESIGN Decision 3 smooths the denominator: idf = max(0, ln(N / (1 + n_people_on_hub))).
# Both hubs used below are held by exactly TWO of the five people, so both weigh
# max(0, ln(5 / (1 + 2))) = ln(5/3). The shorthand "ln(5/3)" is the REDUCED form of the
# smoothed expression, not an unsmoothed ln(N/n) -- reading it as "three people on the
# hub" is the mistake to avoid.
IDF_RARE_HUB = 0.5108256237659907  # max(0, ln(5 / (1 + 2))) = ln(5/3)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load(frozen_fixtures, person_id):
    from arrival.contracts import Dossier

    path = frozen_fixtures / "dossiers" / f"{person_id}.json"
    return Dossier.model_validate_json(path.read_text(encoding="utf-8"))


def _fact(dossier, fact_id):
    for f in dossier.facts:
        if f.fact_id == fact_id:
            return f
    raise AssertionError(
        f"frozen corpus changed: {fact_id} missing from {dossier.person.person_id}"
    )


def _hub(dossier, hub_id):
    for h in dossier.hubs:
        if h.hub_id == hub_id:
            return h
    raise AssertionError(f"frozen corpus changed: hub {hub_id} missing")


def _match(runa, other, hub_id, score, type_boost, why):
    """A Match exactly as DESIGN specifies T-5 would emit it.

    Built by hand from `arrival.contracts` so this module grades the digest builder and
    nothing else. Per DESIGN, `HubContribution.hub` is the ARRIVING person's Hub object.
    """
    from arrival.contracts import HubContribution, Match

    contributions = []
    path = [f"person:{runa.person.person_id}", f"person:{other.person.person_id}"]
    if hub_id is not None:
        contributions = [
            HubContribution(
                hub=_hub(runa, hub_id),
                idf_weight=IDF_RARE_HUB,
                recency=1.0,
                type_boost=type_boost,
                contribution=IDF_RARE_HUB * 1.0 * type_boost,
            )
        ]
        path = [
            f"person:{runa.person.person_id}",
            f"hub:{hub_id}",
            f"person:{other.person.person_id}",
        ]
    return Match(
        other=other.person,
        score=score,
        contributions=contributions,
        path=path,
        why=why,
    )


def _four_matches(frozen_fixtures):
    """runa's four present peers, ordered as `graph.match` returns them: 100, 67, 0, 0."""
    runa = _load(frozen_fixtures, ARRIVING)
    sil = _load(frozen_fixtures, "sil-vantorre")
    jem = _load(frozen_fixtures, "jem-arrowood")
    mira = _load(frozen_fixtures, "mira-hollowell")
    theo = _load(frozen_fixtures, "theo-baptiste")
    return runa, [
        _match(runa, sil, "investor:foundry-seed-2019", 100.0, 1.5,
               "Both came up through the Foundry Seed 2019 fund."),
        _match(runa, jem, "topic:developer-tools-go-to-market", 67.0, 1.0,
               "Both have spent years on developer-tools go-to-market."),
        _match(runa, mira, None, 0.0, 0.0, "Both work in Austin, which everyone here shares."),
        _match(runa, theo, None, 0.0, 0.0, "Both work in Austin, which everyone here shares."),
    ]


def _is_union(origin):
    return origin is typing.Union or (_UnionType is not None and origin is _UnionType)


def _fill(schema, line):
    """Instantiate an arbitrary pydantic model, putting `line` in every string field.

    `make_digest`'s say-out-loud schema is T-7's own internal type, so this double cannot
    name it. It fills the shape generically instead. If a required field cannot be filled,
    construction raises and the digest builder must take its documented fallback path —
    which is itself a correct outcome, never a false green.
    """
    from pydantic import BaseModel

    kwargs = {}
    for name, field in schema.model_fields.items():
        ann = field.annotation
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        if origin is typing.Literal:
            kwargs[name] = args[0]
            continue
        if _is_union(origin):
            non_none = [a for a in args if a is not type(None)]  # noqa: E721
            if not non_none:
                kwargs[name] = None
                continue
            ann = non_none[0]
            origin = typing.get_origin(ann)
            args = typing.get_args(ann)
            if origin is typing.Literal:
                kwargs[name] = args[0]
                continue
        if origin in (list, set, tuple, frozenset):
            kwargs[name] = []
            continue
        if origin is dict:
            kwargs[name] = {}
            continue
        if isinstance(ann, type):
            if issubclass(ann, BaseModel):
                kwargs[name] = _fill(ann, line)
                continue
            if issubclass(ann, bool):
                kwargs[name] = True
                continue
            if issubclass(ann, str):
                kwargs[name] = line
                continue
            if issubclass(ann, float):
                kwargs[name] = 0.9
                continue
            if issubclass(ann, int):
                kwargs[name] = 1
                continue
            if issubclass(ann, dt.datetime):
                kwargs[name] = dt.datetime(2026, 2, 20, 14, 0, tzinfo=dt.timezone.utc)
                continue
            if issubclass(ann, dt.date):
                kwargs[name] = dt.date(2026, 1, 5)
                continue
        if not field.is_required():
            kwargs.pop(name, None)
    return schema(**kwargs)


class _LLMStub:
    """Minimal `LLMClient` double for the one say-out-loud call (DESIGN Decision 12).

    Written locally on purpose: `tests/doubles.py` is inside a graded ticket's scope.
    """

    def __init__(self, line="Ask about publishing developer-tools pricing on a public page.",
                 delay=0.0):
        self.line = line
        self.delay = delay
        self.calls = []

    async def structured(self, *, system, user, schema, max_tokens=2000, cache_prefix=True):
        self.calls.append({"schema": getattr(schema, "__name__", str(schema)), "user": user})
        if self.delay:
            await asyncio.sleep(self.delay)
        return _fill(schema, self.line)


def _digest(dossier, matches, llm, timeout=10.0):
    from arrival.digest import make_digest

    async def _inner():
        # The outer wait_for is a harness guard, never the thing under test: if the digest
        # builder implements no timeout of its own the test fails here instead of hanging.
        return await asyncio.wait_for(make_digest(dossier, matches, llm), timeout=timeout)

    return asyncio.run(_inner())


def _published(fact):
    return fact.provenance.published_at


def _displayable(fact):
    """R12's display gate, spelled out here rather than imported from `taste`.

    This module grades the DIGEST builder. A corpus self-check that asked T-4's predicate
    whether the corpus is still what T-7 assumes would go quiet the moment T-4 broke, which
    is the one moment it needs to speak.
    """
    return (
        not fact.excluded
        and fact.provenance.confidence >= 0.7
        and fact.provenance.source_kind not in ("fec", "courtlistener")
    )


def _spoken_lines(digest):
    return [digest.who_line] + [m.why for m in digest.meet] + [digest.say_out_loud]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_meet_is_capped_at_three_when_four_people_are_present(frozen_fixtures):
    """R7: the Meet section holds at most three present people, highest score first."""
    runa, matches = _four_matches(frozen_fixtures)
    assert len(matches) == 4, "harness input changed: this test needs four present peers"

    digest = _digest(runa, matches, _LLMStub())

    assert len(digest.meet) <= 3
    assert len(digest.meet) == 3, "four peers were offered and the cap is three"
    scores = [m.score for m in digest.meet]
    assert scores == sorted(scores, reverse=True), f"Meet is not ordered by score: {scores}"
    kept = {m.other.person_id for m in digest.meet}
    assert "sil-vantorre" in kept, "the 100-scoring peer must survive the cap"
    assert "jem-arrowood" in kept, "the 67-scoring peer must survive the cap"


def test_lately_is_capped_at_three_and_ordered_most_recent_first(frozen_fixtures):
    """R7: Lately holds three bullets - the cap, filled - most recent activity first."""
    runa, matches = _four_matches(frozen_fixtures)

    # Corpus self-check, run BEFORE the digest so a fixture change is reported as a fixture
    # change rather than as a digest bug. It pins the shape the f05 assertion below depends
    # on: f05 carries the newest displayable date, and shares it with exactly one other fact.
    newest = max(_published(f) for f in runa.facts if _displayable(f))
    assert newest == NEWEST_DISPLAYABLE_DATE, (
        f"frozen corpus changed: the newest displayable fact is now dated {newest}"
    )
    tied = {f.fact_id for f in runa.facts if _displayable(f) and _published(f) == newest}
    assert tied == TIED_NEWEST_FACT_IDS, (
        f"frozen corpus changed: the newest-displayable tie is now {sorted(tied)}"
    )

    digest = _digest(runa, matches, _LLMStub())

    assert len(digest.lately) <= 3
    # The cap is a FLOOR here as well as a ceiling. Every reading of the candidate set that
    # the binding documents support leaves at least four eligible facts -- four displayable
    # `recent_activity` facts on the narrowest, thirteen displayable facts on the widest --
    # so all three slots are fillable, and a digest that showed one bullet would otherwise
    # have scored this criterion. (No document defines a recency window for "lately";
    # RESEARCH.md lists that as an open user-research question, so no window is graded.)
    assert len(digest.lately) == 3, (
        f"Lately has {len(digest.lately)} bullets; at least four facts are eligible under "
        "every reading the binding documents support, so all three slots are fillable"
    )
    dates = [_published(f) for f in digest.lately]
    assert all(d is not None for d in dates), f"Lately must be datable to be ordered: {dates}"
    assert dates == sorted(dates, reverse=True), f"Lately is not most-recent-first: {dates}"

    ids = [f.fact_id for f in digest.lately]
    assert len(ids) == len(set(ids)), f"Lately repeats a fact: {ids}"
    # The corpus offers four recent_activity facts with distinct dates, so the cap has to
    # cut exactly one, and the oldest is the only one it can defensibly cut.
    assert OLDEST_ACTIVITY_FACT_ID not in ids, (
        "the oldest recent-activity fact survived a cap that had three slots and four "
        "more-recent candidates"
    )
    # f05 is required, not merely "one of the two facts dated 2026-02-11" -- see the note on
    # NEWEST_DISPLAYABLE_DATE. It survives the cap under every reading of the candidate set
    # the binding documents support, and test_t8_web.py independently requires its text on
    # the rendered page, which Lately is the only route to.
    assert MOST_RECENT_ACTIVITY_FACT_ID in ids, (
        "the most recent professional activity is missing from Lately "
        f"({MOST_RECENT_ACTIVITY_FACT_ID}, dated {NEWEST_DISPLAYABLE_DATE}); got {ids}"
    )


def _promote_withheld(dossier):
    """The four withheld facts, made the freshest professional activity in the dossier.

    In the corpus as committed they are `interest` facts with old dates, so they never
    compete for a Lately slot and a digest builder that forgot R11/R12 entirely would still
    look clean. Promoting them puts them at the front of the queue. Nothing that makes them
    withheld is touched: `excluded`, `exclusion_reason`, `confidence` and `source_kind` are
    exactly as frozen. Only the section they compete for changes.
    """
    promoted = []
    for i, fact in enumerate(dossier.facts):
        if fact.fact_id in WITHHELD_FACT_IDS:
            fresh = dt.date(2026, 3, 1) + dt.timedelta(days=i)
            fact = fact.model_copy(update={
                "category": "recent_activity",
                "provenance": fact.provenance.model_copy(update={"published_at": fresh}),
            })
        promoted.append(fact)
    return dossier.model_copy(update={"facts": promoted})


def test_withheld_facts_never_appear_while_displayable_material_does(frozen_fixtures):
    """R11 + R12: excluded, low-confidence and non-whitelisted facts are never shown.

    The positive controls in the second half are load-bearing: a digest that showed
    nothing at all would satisfy every negative assertion above them.
    """
    runa, matches = _four_matches(frozen_fixtures)
    runa = _promote_withheld(runa)

    digest = _digest(runa, matches, _LLMStub())

    shown_facts = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    shown_ids = {f.fact_id for f in shown_facts}
    leaked = shown_ids & WITHHELD_FACT_IDS
    assert not leaked, f"withheld facts reached the digest: {sorted(leaked)}"

    # R12 stated positively, so the guard does not depend on the four sentinel facts
    # happening to survive whatever ordering the builder applies. Spelled out here rather
    # than delegated to `taste.is_displayable`: a filter that agrees with a broken
    # predicate would otherwise be graded green by that same broken predicate.
    for fact in shown_facts:
        assert not fact.excluded, f"{fact.fact_id} is shown despite excluded=True"
        assert fact.provenance.confidence >= 0.7, (
            f"{fact.fact_id} is shown at confidence {fact.provenance.confidence}, "
            "below the R12 display floor of 0.7"
        )
        assert fact.provenance.source_kind not in ("fec", "courtlistener"), (
            f"{fact.fact_id} is shown from {fact.provenance.source_kind}, which R12 and "
            "the DESIGN whitelist never display"
        )

    surface = " ".join([digest.who_line, digest.say_out_loud]
                       + [f.text for f in shown_facts]
                       + [m.why for m in digest.meet])
    for needle in WITHHELD_STRINGS:
        assert needle.casefold() not in surface.casefold(), (
            f"withheld string rendered into a host-facing field: {needle!r}"
        )

    # --- positive controls: the surface examined above is not empty ---
    assert digest.lately, (
        "positive control failed: Lately is empty, so 'no withheld fact is in Lately' "
        "was asserted about nothing"
    )
    assert digest.non_obvious is not None, "positive control failed: nothing was shown"
    assert NON_OBVIOUS_STRING.casefold() in surface.casefold(), (
        "positive control failed: a known displayable fact is missing, so the negative "
        "assertions above were made against an empty page"
    )
    assert "quarrystone" in digest.who_line.casefold(), (
        "positive control failed: who_line carries none of the current_work material"
    )


def test_non_obvious_is_the_single_eligible_wayback_fact(frozen_fixtures):
    """R7: 'Not on the first page' is exactly one fact from an archival/non-search source."""
    runa, matches = _four_matches(frozen_fixtures)

    digest = _digest(runa, matches, _LLMStub())

    assert digest.non_obvious is not None, (
        "the corpus contains an eligible wayback non_obvious fact, so the slot must be filled"
    )
    assert digest.non_obvious.fact_id == NON_OBVIOUS_FACT_ID
    assert digest.non_obvious.category == "non_obvious"
    assert digest.non_obvious.provenance.source_kind == "wayback"
    assert digest.non_obvious not in digest.lately, (
        "the non-obvious find must occupy its own section, not double as a Lately bullet"
    )


def test_meet_is_empty_and_unpadded_when_nobody_else_is_present(frozen_fixtures):
    """R8: with nobody else in the building the Meet section is empty, never padded."""
    runa = _load(frozen_fixtures, ARRIVING)

    digest = _digest(runa, [], _LLMStub())

    assert digest.meet == [], f"Meet was padded with {[m.other.person_id for m in digest.meet]}"
    # ... and the rest of the digest is still a digest, so "empty Meet" is a stated
    # absence rather than a collapsed build.
    assert digest.who_line.strip(), "who_line went empty when nobody else was present"
    assert digest.say_out_loud.strip(), "say_out_loud went empty when nobody else was present"
    assert digest.person.person_id == ARRIVING


def test_sources_cite_every_shown_fact_and_nothing_withheld(frozen_fixtures):
    """S6 + R9: the numbered source list covers what is shown, deduped by doc_id, in order."""
    runa, matches = _four_matches(frozen_fixtures)

    digest = _digest(runa, matches, _LLMStub())

    source_ids = [p.doc_id for p in digest.sources]
    assert source_ids, "no sources at all: nothing shown can be checked back to a document"
    assert len(source_ids) == len(set(source_ids)), f"sources not deduped by doc_id: {source_ids}"

    # Direction 1 — every fact shown is citable. This is the direction R9 exists for.
    shown = list(digest.lately) + ([digest.non_obvious] if digest.non_obvious else [])
    assert shown, "positive control failed: nothing was shown, so coverage is vacuous"
    for fact in shown:
        assert fact.provenance.doc_id in set(source_ids), (
            f"{fact.fact_id} is shown with no entry in 'Why we know this'"
        )

    # Direction 2 — nothing is cited that is not behind something shown, and in particular
    # no document whose only fact is withheld may appear in the citation list.
    corpus_doc_ids = {f.provenance.doc_id for f in runa.facts}
    for doc_id in source_ids:
        assert doc_id in corpus_doc_ids, (
            f"sources cite {doc_id}, which backs no fact in the arriving person's dossier"
        )
    leaked = set(source_ids) & WITHHELD_DOC_IDS
    assert not leaked, f"citation list names documents behind withheld facts: {sorted(leaked)}"

    # First-use order: the documents behind Lately appear in sources in Lately's own order.
    lately_docs = []
    for fact in digest.lately:
        if fact.provenance.doc_id not in lately_docs:
            lately_docs.append(fact.provenance.doc_id)
    positions = [source_ids.index(d) for d in lately_docs]
    assert positions == sorted(positions), (
        f"sources are not in first-use order: Lately cites {lately_docs} but they appear "
        f"at positions {positions} in {source_ids}"
    )


def test_spoken_lines_are_speakable_as_written(frozen_fixtures):
    """R18: who_line, every Meet why and say_out_loud read aloud cleanly, ≤ 30 words."""
    runa, matches = _four_matches(frozen_fixtures)

    digest = _digest(runa, matches, _LLMStub())

    lines = _spoken_lines(digest)
    assert len(lines) >= 3, "positive control failed: there is nothing to read aloud"
    for line in lines:
        assert line.strip(), "a spoken line is empty"
        assert "http" not in line.casefold(), f"URL in a spoken line: {line!r}"
        assert not re.search(r"\[\s*\d+\s*\]", line), f"citation marker in a spoken line: {line!r}"
        assert "(" not in line and ")" not in line, f"parenthetical in a spoken line: {line!r}"
        assert len(line.split()) <= 30, f"{len(line.split())} words, cap is 30: {line!r}"


def test_say_out_loud_is_an_invitation_not_a_disclosure(frozen_fixtures):
    """R14: the opener invites ('Ask about…'), never reveals what the system knows.

    The model is scripted to return exactly the phrasing R14 forbids. T-7 acceptance 3
    makes the digest builder, not the model, responsible for that line, so the surveillance
    wording must be rejected and the invitation template used instead.
    """
    runa, matches = _four_matches(frozen_fixtures)
    rogue = _LLMStub(line="I saw that you have been writing about pricing lately.")

    digest = _digest(runa, matches, rogue)

    assert rogue.calls, "the digest builder never asked the model for an opener"
    line = digest.say_out_loud.strip()
    assert line, "positive control failed: there is no opener to check"
    assert line.startswith(("Ask", "Curious")), (
        f"opener must be phrased as an invitation, got {line!r}"
    )
    lowered = line.casefold()
    for phrase in ("i saw", "we noticed", "our records"):
        assert phrase not in lowered, f"surveillance phrasing {phrase!r} in opener: {line!r}"


def test_say_out_loud_falls_back_to_the_highest_confidence_hook_on_timeout(frozen_fixtures):
    """DESIGN Decision 12: a slow LLM yields the templated highest-confidence hook opener.

    The delay is REAL (`asyncio.sleep`), not injected: `make_digest(dossier, matches, llm)`
    exposes no timeout parameter, so there is nothing to inject into. The stub sleeps far
    longer than the budget and the implementation's own 2.5 s timeout cancels it, so the
    test costs about 2.5 s rather than the full sleep. The outer `wait_for` in `_digest`
    fails the test instead of hanging if no timeout is implemented at all.
    """
    runa, matches = _four_matches(frozen_fixtures)
    top_hook = _fact(runa, TOP_HOOK_FACT_ID)
    other_hook = _fact(runa, OTHER_HOOK_FACT_ID)
    assert top_hook.provenance.confidence > other_hook.provenance.confidence

    digest = _digest(runa, matches, _LLMStub(delay=30.0), timeout=10.0)

    line = digest.say_out_loud.casefold()
    assert line.startswith("ask about"), f"fallback must use the documented template: {line!r}"
    assert TOP_HOOK_PHRASE.casefold() in line, (
        "the fallback opener does not name the highest-confidence displayable hook fact "
        f"({TOP_HOOK_FACT_ID}); got {digest.say_out_loud!r}"
    )
    assert OTHER_HOOK_PHRASE.casefold() not in line, (
        "the fallback opener used the lower-confidence hook fact "
        f"({OTHER_HOOK_FACT_ID}); got {digest.say_out_loud!r}"
    )


def test_exclusion_policy_is_the_taste_module_constant(frozen_fixtures):
    """R13: the digest carries the one exclusion policy paragraph, defined once in taste."""
    from arrival import taste

    runa, matches = _four_matches(frozen_fixtures)

    digest = _digest(runa, matches, _LLMStub())

    assert digest.exclusion_policy == taste.EXCLUSION_POLICY
    assert digest.exclusion_policy.strip(), "the exclusion policy paragraph is empty"
