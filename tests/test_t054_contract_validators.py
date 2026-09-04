"""T-054: the validators `contracts.py` now carries, and the ones it deliberately does not.

**What every assertion here is graded against, and why none of it is `contracts.py`.**
The module under test is in this ticket's write scope, so grading against its source text
would let the answer key be written by the thing it grades. So:

* Constraint behaviour is graded against **pydantic's own** `ValidationError` — a library
  this ticket cannot edit. "Rejects 1.5" is a fact about pydantic, not about my prose.
* "I did not break legitimate data" is graded against the **frozen grading corpus** at
  `.swarm-loop/acceptance/fixtures/dossiers*/`, which is orchestrator-owned and hash-locked.
* The real-world harm each validator prevents is demonstrated against **other modules'**
  behaviour — `arrival.graph`, `arrival.taste`, `arrival.extract`, `arrival.util` — none of
  which this ticket may touch.
* The declines are pinned the same way: a test that the constraint is ABSENT, so a later
  lane that adds one must come here and read why it was refused.

`Hub.model_construct` appears throughout the harm demonstrations. That is deliberate and is
the only honest way to build the illegal value now that the model refuses it: it bypasses
validation, which is exactly what the pre-T-054 constructor did.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from arrival.contracts import (
    BuildReport,
    Digest,
    Dossier,
    Fact,
    Hub,
    HubContribution,
    Match,
    PersonRef,
    Provenance,
    RawDoc,
    Resolution,
    Verdict,
)

pytestmark = pytest.mark.ticket("T-054")

UTC = dt.UTC
NOW = dt.datetime(2026, 2, 20, 14, 0, tzinfo=UTC)

#: The frozen grading corpus. Orchestrator-owned and outside every ticket's write scope,
#: which is what makes it usable as an answer key here.
FROZEN_DOSSIERS = sorted(
    Path(".swarm-loop/acceptance/fixtures/dossiers").glob("*.json")
) + sorted(Path(".swarm-loop/acceptance/fixtures/dossiers_unresolved").glob("*.json"))


def _provenance(**overrides: object) -> Provenance:
    base: dict[str, object] = {
        "doc_id": "0123456789abcdef",
        "url": "https://example.org/a",
        "source_kind": "search",
        "quote": "a verbatim span from the document",
        "retrieved_at": NOW,
        "confidence": 0.9,
    }
    base.update(overrides)
    return Provenance(**base)  # type: ignore[arg-type]


def _fact(fact_id: str = "f1", **overrides: object) -> Fact:
    base: dict[str, object] = {
        "fact_id": fact_id,
        "text": "She runs freight logistics at Lantern.",
        "category": "current_work",
        "provenance": _provenance(),
    }
    base.update(overrides)
    return Fact(**base)  # type: ignore[arg-type]


def _resolution(person_id: str = "p") -> Resolution:
    return Resolution(
        person_id=person_id,
        status="resolved",
        accepted_doc_ids=[],
        rejected=[],
        confidence=0.9,
    )


def _dossier(person_id: str, hubs: list[Hub], facts: list[Fact] | None = None) -> Dossier:
    """Built with `model_construct` for the members, so an ILLEGAL hub can still be placed.

    `Dossier(...)` would re-validate the hubs it is handed and defeat the demonstration.
    """
    return Dossier.model_construct(
        person=PersonRef(person_id=person_id, name=person_id.upper()),
        resolution=_resolution(person_id),
        facts=facts or [],
        hubs=hubs,
        built_at=NOW,
        schema_version=1,
    )


# ==========================================================================
# 0. The control: legitimate data still validates
# ==========================================================================


def test_the_frozen_grading_corpus_still_validates():
    """The answer key for "did a validator reject something real": six frozen dossiers.

    Every constraint added by this ticket is a permanent gate on the corpus this product
    actually ships. If one of them rejects a dossier in the frozen fixtures, the constraint
    is wrong — the fixtures cannot be, they are hash-locked and outside this write scope.
    """
    assert len(FROZEN_DOSSIERS) >= 6, f"frozen corpus not found: {FROZEN_DOSSIERS}"
    for path in FROZEN_DOSSIERS:
        dossier = Dossier.model_validate_json(path.read_text(encoding="utf-8"))
        assert dossier.person.person_id, path


def test_the_committed_unit_fixtures_still_validate():
    """The T-0 fixture dossiers too — a second corpus, on a different id convention."""
    paths = sorted(Path("tests/fixtures/dossiers").glob("*.json"))
    assert len(paths) == 4, paths
    for path in paths:
        assert Dossier.model_validate_json(path.read_text(encoding="utf-8")).hubs


# ==========================================================================
# 1. Probability: every `confidence`, and `recency`
# ==========================================================================

PROBABILITY_FIELDS = [
    pytest.param(
        lambda v: Verdict(doc_id="d", match="yes", confidence=v, evidence="e", disambiguator="x"),
        id="Verdict.confidence",
    ),
    pytest.param(
        lambda v: Resolution(
            person_id="p", status="resolved", accepted_doc_ids=[], rejected=[], confidence=v
        ),
        id="Resolution.confidence",
    ),
    pytest.param(lambda v: _provenance(confidence=v), id="Provenance.confidence"),
    pytest.param(
        lambda v: Hub(hub_id="topic:x", label="X", type="topic", recency=v), id="Hub.recency"
    ),
]


@pytest.mark.parametrize("build", PROBABILITY_FIELDS)
@pytest.mark.parametrize("value", [0.0, 0.28, 0.5, 0.93, 1.0])
def test_a_probability_accepts_every_value_in_range(build, value: float):
    """Both endpoints included: 0.0 is a real confidence and 1.0 is the default recency."""
    assert build(value) is not None


@pytest.mark.parametrize("build", PROBABILITY_FIELDS)
@pytest.mark.parametrize(
    "value",
    [-2.0, -0.0001, 1.0001, 5.0, 100.0, float("nan"), float("inf"), float("-inf")],
    ids=["neg2", "just_under", "just_over", "five", "hundred", "nan", "inf", "neg_inf"],
)
def test_a_probability_rejects_everything_outside_range(build, value: float):
    """NaN and the infinities are in the list on purpose.

    Every comparison against NaN is False, so a NaN slips through any hand-written
    `0 <= x <= 1` guard AND through `taste`'s `confidence < CONFIDENCE_FLOOR`. It does not
    slip through `ge`/`le`, for the same reason: the bound comparison is False, so the
    constraint fails. That is a property of pydantic, which this ticket cannot edit.
    """
    with pytest.raises(ValidationError):
        build(value)


def test_the_bound_binds_on_json_load_not_only_on_construction():
    """The corpus arrives as JSON, so the JSON path is the one that has to hold."""
    good = _dossier("a", [Hub(hub_id="topic:x", label="X", type="topic", recency=0.3)])
    payload = json.loads(good.model_dump_json())
    assert Dossier.model_validate(payload).hubs[0].recency == 0.3

    payload["hubs"][0]["recency"] = 5.0
    with pytest.raises(ValidationError):
        Dossier.model_validate(payload)


# --- the harm an out-of-range recency does, measured in arrival.graph -------


def test_an_out_of_range_recency_silently_reorders_who_you_are_told_to_meet():
    """The shape `Hub.recency: Probability` prevents, demonstrated against `arrival.graph`.

    `recency` is consumed as a MULTIPLIER on a hub's contribution and `graph._normalise`
    clamps only the FINAL score to 0..100, so a corrupt recency does not surface as an
    obviously broken number — it surfaces as a plausible one computed from a corrupt
    contribution, with the clamp hiding the corruption. Here ONE bad value on the arriving
    person's strongest hub demotes the person they should actually meet to the bottom of
    the list, and every score on the resulting page still reads 0..100.

    The corpus is built so the strong hub genuinely wins first: N=5, `topic:rare` on two
    people (idf `ln(5/3)`), `topic:common` on three (idf `ln(5/4)`), one hub type so the
    type boost cancels and only `recency` moves.

    Worth recording, because it bounds the claim: a recency ABOVE 1.0 is largely defanged
    by `graph._contributions` taking `min()` of the two people's edges, so one inflated edge
    is capped by the partner's honest one. The NEGATIVE direction has no such brake — it
    drives the pair's raw score below zero, where `_normalise`'s `raw <= 0` floor turns a
    real connection into a reported 0.

    Graded entirely against `arrival.graph`'s arithmetic, which this ticket cannot touch.
    """
    from arrival.graph import build_graph, match

    def corpus(rare_recency: float) -> list[Dossier]:
        rare = Hub.model_construct(
            hub_id="topic:rare", label="Rare", type="topic",
            recency=rare_recency, evidence_fact_ids=[],
        )
        common = Hub(hub_id="topic:common", label="Common", type="topic")
        return [
            _dossier("a", [rare, common.model_copy()]),
            _dossier("b", [Hub(hub_id="topic:rare", label="Rare", type="topic")]),
            _dossier("c", [common.model_copy()]),
            _dossier("d", [common.model_copy()]),
            _dossier("e", []),
        ]

    honest = {m.other.person_id: m.score for m in match(build_graph(corpus(1.0)), "a", ["b", "c"])}
    assert honest["b"] > honest["c"] > 0.0, (
        f"premise: the rare hub makes `b` the right person to meet. {honest}"
    )

    corrupt = match(build_graph(corpus(-2.0)), "a", ["b", "c"])
    ranked = {m.other.person_id: m.score for m in corrupt}
    assert ranked["c"] > ranked["b"], (
        "premise of this ticket: an out-of-range recency reorders the meet list. "
        f"honest={honest} corrupt={ranked}"
    )
    # ...and every score still LOOKS fine, which is what makes it silent.
    assert all(0.0 <= m.score <= 100.0 for m in corrupt), corrupt

    # The contract now refuses to build that hub at all, on either side of the range.
    for bad in (-2.0, 5.0):
        with pytest.raises(ValidationError):
            Hub(hub_id="topic:rare", label="Rare", type="topic", recency=bad)


def test_graph_still_floors_and_caps_a_score_built_from_a_bad_recency():
    """`graph._normalise` must stay defensive even though the contract now guards the input.

    This is deliberately the same guarantee
    `tests/graph/test_t5_scoring.py::test_score_stays_in_range_when_a_hub_carries_an_out_of_range_recency`
    encodes, re-homed here rather than lost. That test builds its input with `Hub(...)`,
    which this ticket's `Hub.recency` bound now refuses, so it fails at construction; the
    REQUIREMENT it protects — a bad extractor cannot push a score outside 0..100 in either
    direction — is still real, still worth grading, and is graded here through
    `model_construct`, which bypasses validation exactly as the old constructor did.

    The fix for the red test is one line in `tests/graph/t5_graph_helpers.py` (`make_hub`
    building via `Hub.model_construct`), which is outside this ticket's ownership.
    """
    from arrival.graph import build_graph, match

    for bad in (-2.0, 5.0):
        arriving = Hub.model_construct(
            hub_id="company:x", label="X", type="company", recency=bad, evidence_fact_ids=[]
        )
        corpus = [
            _dossier("a", [arriving]),
            _dossier("b", [Hub(hub_id="company:x", label="X", type="company")]),
            _dossier("c", []),
            _dossier("d", []),
            _dossier("e", []),
        ]
        scored = match(build_graph(corpus), "a", ["b"])
        assert scored, bad
        assert 0.0 <= scored[0].score <= 100.0, f"recency {bad} produced {scored[0].score}"


def test_a_nan_confidence_defeats_the_taste_displayability_floor():
    """The shape `Provenance.confidence: Probability` prevents, graded against `arrival.taste`.

    `taste.is_displayable` withholds a fact whose provenance confidence is below
    `CONFIDENCE_FLOOR`. The test is `confidence < CONFIDENCE_FLOOR`, and `NaN < 0.7` is
    False — so a NaN is treated as clearing a floor it cannot possibly clear, and the fact
    is published. `ge`/`le` reject NaN, so the value can no longer be built.
    """
    from arrival.taste import CONFIDENCE_FLOOR, is_displayable

    nan = float("nan")
    assert not (nan < CONFIDENCE_FLOOR), "premise: NaN compares False against the floor"

    smuggled = _fact("nan-fact", provenance=Provenance.model_construct(
        doc_id="0123456789abcdef",
        url="https://example.org/a",
        source_kind="search",
        quote="a verbatim span from the document",
        published_at=None,
        retrieved_at=NOW,
        confidence=nan,
    ))
    assert is_displayable(smuggled), (
        "premise of this validator: a NaN confidence is published as though it cleared the "
        "floor. If this ever fails, taste.py grew its own NaN guard and this test should "
        "become a duplicate, not a deletion."
    )

    with pytest.raises(ValidationError):
        _provenance(confidence=nan)


# ==========================================================================
# 2. NonBlank: RawDoc.text, Provenance.quote, Hub.hub_id
# ==========================================================================

NONBLANK_FIELDS = [
    pytest.param(
        lambda v: RawDoc(
            doc_id="0123456789abcdef",
            source_kind="search",
            url="https://example.org/a",
            text=v,
            fetched_at=NOW,
        ),
        id="RawDoc.text",
    ),
    pytest.param(lambda v: _provenance(quote=v), id="Provenance.quote"),
    pytest.param(lambda v: Hub(hub_id=v, label="X", type="topic"), id="Hub.hub_id"),
]


@pytest.mark.parametrize("build", NONBLANK_FIELDS)
@pytest.mark.parametrize(
    "value",
    ["x", "body", "topic:", " padded ", "数学", "a very ordinary sentence of prose"],
    ids=["one_char", "body", "trailing_colon", "padded", "cjk", "prose"],
)
def test_nonblank_accepts_anything_carrying_a_character(build, value: str):
    """Permissive on shape. Three of these are load-bearing elsewhere in the suite:

    ``"body"`` is the shortest quote in the FROZEN acceptance suite; ``"topic:"`` is a
    hub_id an existing graph test constructs; ``"数学"`` slugs to nothing and must stay legal
    because `graph._identity_key` has a documented fallback for exactly that.
    """
    assert build(value) is not None


@pytest.mark.parametrize("build", NONBLANK_FIELDS)
@pytest.mark.parametrize("value", ["", " ", "   ", "\t", "\n", "\t\n  "], ids=range(6))
def test_nonblank_rejects_the_empty_and_the_all_whitespace(build, value: str):
    """`min_length=1` alone would admit `"   "`, which is blank everywhere it is used."""
    with pytest.raises(ValidationError):
        build(value)


# --- the harm a blank quote does -------------------------------------------


def test_an_empty_quote_is_a_substring_of_every_document_and_cites_nothing():
    """The shape `Provenance.quote: NonBlank` prevents.

    `Provenance.quote`'s contract is "a substring of `RawDoc.text` after
    whitespace-normalisation" — and the empty string satisfies that against ANY document,
    including one it was never taken from. So the stated contract is vacuously true for a
    quote carrying no evidence, and the footnote renders backing nothing.

    Graded against `arrival.util.normalize_ws` and `arrival.extract.is_cited`, neither of
    which this ticket may touch.
    """
    from arrival.extract import is_cited
    from arrival.util import normalize_ws

    doc = RawDoc(
        doc_id="0123456789abcdef",
        source_kind="search",
        url="https://example.org/a",
        text="Nothing in this document mentions the claim at all.",
        fetched_at=NOW,
    )
    unrelated = RawDoc(
        doc_id="fedcba9876543210",
        source_kind="wikipedia",
        url="https://example.org/b",
        text="An entirely different document.",
        fetched_at=NOW,
    )

    for blank in ("", "   "):
        assert normalize_ws(blank) in normalize_ws(doc.text), (
            "premise: the stated substring contract is vacuously satisfied by a blank quote"
        )
        assert normalize_ws(blank) in normalize_ws(unrelated.text)
        # `extract` already refuses it — but only for facts that pass through `extract`.
        assert not is_cited(blank, doc)
        # ...and now so does the model, on every path INCLUDING a hand-written dossier.
        with pytest.raises(ValidationError):
            _provenance(quote=blank)


def test_a_blank_hub_id_would_collapse_every_such_hub_into_one_shared_node():
    """The shape `Hub.hub_id: NonBlank` prevents, graded against `arrival.graph`.

    A hub's id names its node. Two people carrying differently-labelled hubs that both have
    a BLANK id land on the same node and are reported as sharing an interest neither of them
    has — the product's central claim, inverted. Note the id is what collides, not the
    label: `graph._identity_key` groups by `slug(label)` and falls back to `\\0{hub_id}`, so
    a blank id makes that fallback collide too.
    """
    from arrival.graph import build_graph, match

    strangers = [
        _dossier(
            "a", [Hub.model_construct(hub_id="", label="", type="topic", recency=1.0,
                                      evidence_fact_ids=[])]
        ),
        _dossier(
            "b", [Hub.model_construct(hub_id="", label="", type="company", recency=1.0,
                                      evidence_fact_ids=[])]
        ),
        _dossier("c", []),
        _dossier("d", []),
        _dossier("e", []),
    ]
    joined = match(build_graph(strangers), "a", ["b"])
    assert joined and joined[0].score > 0.0, (
        "premise: two blank-id hubs join two strangers. If this stops holding, graph.py "
        f"grew its own guard and this validator became redundant: {joined}"
    )

    with pytest.raises(ValidationError):
        Hub(hub_id="", label="X", type="topic")


def test_an_empty_document_can_support_no_citation():
    """The shape `RawDoc.text: NonBlank` prevents, graded against `arrival.extract`."""
    from arrival.extract import cited_span

    empty = RawDoc.model_construct(
        doc_id="0123456789abcdef",
        source_kind="search",
        url="https://example.org/a",
        title="",
        text="",
        published_at=None,
        fetched_at=NOW,
    )
    assert cited_span("any span at all from somewhere", empty) is None

    with pytest.raises(ValidationError):
        RawDoc(
            doc_id="0123456789abcdef",
            source_kind="search",
            url="https://example.org/a",
            text="",
            fetched_at=NOW,
        )


# ==========================================================================
# 3. The declines. Each one pins the ABSENCE of a constraint.
# ==========================================================================


def test_a_hub_may_still_carry_an_empty_label():
    """DECLINED. `graph._identity_key` accommodates it BY NAME.

    "An empty label leaves nothing to group by, so such a hub falls back to standing alone
    under its own id." That fallback must exist regardless, because `slug("数学")` is also
    `""`, so a legitimate non-Latin label takes the same branch. An unlabelled hub degrades
    visibly (a blank chip, and it joins nobody) rather than corrupting quietly, and
    `tests/graph/test_t053_hub_qid_identity_election.py` pins the behaviour with `label=""`.
    """
    from arrival.util import slug

    assert slug("数学") == "" == slug(""), "premise: a real label can slug to nothing too"
    assert Hub(hub_id="topic:x", label="", type="topic").label == ""
    assert Hub(hub_id="topic:x", label="   ", type="topic").label == "   "


def test_a_dangling_evidence_fact_id_degrades_and_does_not_corrupt():
    """DECLINED: no `Dossier`-level resolvability check. This test is why.

    All three readers of `evidence_fact_ids` resolve with a miss-tolerant lookup and skip
    what is absent, and two of them document that skip as the DESIGNED path for evidence
    that must not be shown. So a dangling id yields a THINNER page, never a wrong one —
    and enforcing it would instead take the whole app offline at import, because
    `web/app.py` ends with `app = create_app()`.

    If this test ever fails, a reader started crashing on a missing id and the decline must
    be revisited. Graded against `arrival.digest` and `arrival.web.render`.
    """
    from arrival.web.render import _hub_evidence_facts

    real = _fact("real-fact")
    hub = Hub(
        hub_id="investor:rare",
        label="Rare",
        type="investor",
        evidence_fact_ids=["real-fact", "this-fact-does-not-exist"],
    )
    dossier = _dossier("a", [hub], facts=[real])
    # The dossier itself is accepted -- that is the decline.
    assert Dossier.model_validate(json.loads(dossier.model_dump_json())).hubs[0].hub_id

    row = Match(
        other=PersonRef(person_id="b", name="B"),
        score=42.0,
        contributions=[
            HubContribution(
                hub=hub, idf_weight=0.5, recency=1.0, type_boost=1.0, contribution=0.5
            )
        ],
        path=["person:a", "hub:investor:rare", "person:b"],
        why="You both know Rare.",
    )
    resolved = _hub_evidence_facts(dossier, row)
    assert [f.fact_id for f in resolved] == ["real-fact"], (
        "a dangling id is skipped, not raised on -- the whole basis of the decline"
    )


def test_a_provenance_quote_is_not_checked_against_any_document():
    """DECLINED, because it CANNOT be done here: the relation has no second side.

    `Provenance` has no `RawDoc`, and neither does `Fact`, and neither does `Dossier` --
    which carries person, resolution, facts and hubs, and no documents at all. A fabricated
    quote therefore still validates, and `extract.cited_span` remains the only enforcement.
    This test states that limitation out loud so nobody reads the NonBlank check as more
    than it is.
    """
    assert "documents" not in Dossier.model_fields
    assert set(Dossier.model_fields) == {
        "person",
        "resolution",
        "facts",
        "hubs",
        "built_at",
        "schema_version",
    }
    fabricated = _provenance(quote="a sentence no document anywhere contains")
    assert fabricated.quote  # accepted: the contract cannot know


def test_build_report_rows_are_still_unvalidated_dicts():
    """DECLINED. `list[dict]` is transcribed from DESIGN and pinned outside this scope.

    Two committed tests also pin the schemalessness on purpose -- a one-key partial row, and
    a row of `None`s asserted to "not kill the report". A schema strict enough to be worth
    having contradicts the second of those directly.
    """
    assert BuildReport.model_fields["people"].annotation == list[dict]
    partial = BuildReport(people=[{"person_id": "charlie"}], started_at=NOW, finished_at=NOW)
    assert partial.people[0]["person_id"] == "charlie"
    nones = BuildReport(
        people=[{"person_id": "x", "status": None, "confidence": None}],
        started_at=NOW,
        finished_at=NOW,
    )
    assert nones.people[0]["confidence"] is None


def test_lengths_and_display_caps_are_not_contract_constraints():
    """DECLINED: `Fact.text <= 200`, `RawDoc.text <= 20k`, `Digest.meet/lately <= 3`.

    Each is a taste or display policy enforced where the policy lives (`extract`'s
    over-length drop, `clip`'s truncation, `digest`'s `MEET_CAP`/`LATELY_CAP`). A too-long
    fact is verbose, not nonsense; a fourth match is a policy slip, not a corrupt corpus.
    Enforcing any of them here would convert those into a hard failure -- at BOOT for the
    dossier ones, and mid-REQUEST for the digest ones.
    """
    long_fact = _fact("long", text="x" * 500)
    assert len(long_fact.text) == 500

    big_doc = RawDoc(
        doc_id="0123456789abcdef",
        source_kind="search",
        url="https://example.org/a",
        text="y" * 25_000,
        fetched_at=NOW,
    )
    assert len(big_doc.text) == 25_000

    row = Match(
        other=PersonRef(person_id="b", name="B"),
        score=1.0,
        contributions=[],
        path=[],
        why="w",
    )
    digest = Digest(
        digest_id="d",
        person=PersonRef(person_id="a", name="A"),
        who_line="A arrived.",
        meet=[row] * 5,
        lately=[_fact("l1"), _fact("l2"), _fact("l3"), _fact("l4")],
        non_obvious=None,
        say_out_loud="s",
        sources=[_provenance(), _provenance()],
        exclusion_policy="policy",
        created_at=NOW,
    )
    assert len(digest.meet) == 5 and len(digest.lately) == 4
    assert len({p.doc_id for p in digest.sources}) == 1, "duplicate doc_ids also still allowed"


def test_identity_fields_and_scores_keep_their_shapes_unconstrained():
    """DECLINED: `doc_id`/`fact_id`/`person_id` shape, `Match.score` range.

    The id comments record how an id is MINTED, not what makes one valid, and the committed
    corpora use three different fact_id conventions. `Match.score` is clamped by its sole
    producer (`graph._normalise`) and a `Digest` is built per REQUEST, so a bound here would
    turn a graph arithmetic bug into a 500 on the page a host is waiting for.
    """
    assert _provenance(doc_id="d1").doc_id == "d1"
    assert _fact("alpha-work").fact_id == "alpha-work"
    assert PersonRef(person_id="", name="").person_id == ""
    assert RawDoc(
        doc_id="../../escaped",
        source_kind="search",
        url="https://example.org/a",
        text="body",
        fetched_at=NOW,
    ).doc_id == "../../escaped"
    over = Match(
        other=PersonRef(person_id="b", name="B"),
        score=150.0,
        contributions=[],
        path=[],
        why="w",
    )
    assert over.score == 150.0


def test_an_excluded_fact_may_still_carry_no_reason():
    """DECLINED, and this one would fail the FROZEN gate.

    `web/render.py` is written for the reason-less shape (`fact.exclusion_reason or
    "excluded"`), a committed test asserts "`exclusion_reason` is optional on the contract",
    and `.swarm-loop/acceptance/test_t4_taste.py` constructs it directly.
    """
    bare = _fact("bare", excluded=True)
    assert bare.excluded is True and bare.exclusion_reason is None


def test_the_per_request_models_carry_no_constraints_at_all():
    """DECLINED for `HubContribution`, `Match` and `Digest`: they are DERIVED, not persisted.

    Every constrained field in this contract belongs to a model that is written to JSON and
    read back, so the check is a gate on the corpus, run once at load, where a failure is a
    named `DossierLoadError`. These three are rebuilt per request from data that has already
    passed those gates — a constraint here can only fire on a `graph`/`digest` arithmetic
    bug, and firing means a 500 on the page a host is waiting for.

    `HubContribution.recency` is the concrete case, and it was measured rather than reasoned:
    it is `min()` of two already-bounded `Hub.recency` values, so on validated data it can
    never fire; with it in place, `graph._contributions` RAISED partway through building the
    meet list for a smuggled-in bad hub, and the reordering demonstration above could not
    run at all until it came off.
    """
    smuggled = HubContribution(
        hub=Hub(hub_id="topic:x", label="X", type="topic"),
        idf_weight=0.5,
        recency=-2.0,
        type_boost=1.0,
        contribution=-1.0,
    )
    assert smuggled.recency == -2.0


def test_a_hub_contribution_need_not_equal_the_product_of_its_parts():
    """DECLINED: it would be a float-equality assertion that can only fire on rounding.

    `tests/test_t0_contracts.py` stores `0.2877 * 1.0 * 1.5` as `0.4315`, which is the
    rounded value and not the product.
    """
    stored = HubContribution(
        hub=Hub(hub_id="topic:x", label="X", type="topic"),
        idf_weight=0.2877,
        recency=1.0,
        type_boost=1.5,
        contribution=0.4315,
    )
    assert stored.contribution != stored.idf_weight * stored.recency * stored.type_boost
    # ...and idf_weight is not a probability either: it grows with the roster.
    assert HubContribution(
        hub=Hub(hub_id="topic:x", label="X", type="topic"),
        idf_weight=math.log(10 / 3),
        recency=1.0,
        type_boost=1.5,
        contribution=1.806,
    ).idf_weight > 1.0


# ==========================================================================
# 4. The technique itself: the frozen contract table must still see bare types
# ==========================================================================


@pytest.mark.parametrize(
    ("model", "field", "expected"),
    [
        (Verdict, "confidence", float),
        (Resolution, "confidence", float),
        (Provenance, "confidence", float),
        (Provenance, "quote", str),
        (Hub, "hub_id", str),
        (Hub, "label", str),
        (Hub, "recency", float),
        (RawDoc, "text", str),
    ],
)
def test_a_constraint_does_not_change_the_declared_annotation(model, field, expected):
    """`Annotated[float, Field(...)]` keeps `FieldInfo.annotation` as bare `float`.

    This is the whole reason the constraints could be added at all: DESIGN's field TYPES are
    the frozen part of this contract and `tests/test_t0_contract_fields.py` grades them. The
    expected values here are Python builtins, not anything this ticket wrote.
    """
    assert model.model_fields[field].annotation is expected


def test_a_defaulted_constrained_field_keeps_its_json_schema_default():
    """`Hub.recency = 1.0` must still emit `"default"` — DESIGN Decision 9 ships the schema."""
    prop = Hub.model_json_schema()["properties"]["recency"]
    assert prop["default"] == 1.0
    assert prop["minimum"] == 0.0 and prop["maximum"] == 1.0
    assert Hub.model_fields["recency"].is_required() is False
