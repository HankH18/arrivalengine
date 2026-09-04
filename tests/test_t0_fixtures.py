"""T-0 acceptance 6: the four fixture dossiers are valid, cited, and arithmetically pinned.

These four files are the ground truth T-5, T-7 and T-8 are graded against, so this module
checks not only that they parse but that the *design* holds: which hubs overlap, what the
IDF math comes out to, and that every quote really is in the RawDoc it names.

If one of these fails, fix the fixture — do not weaken the assertion.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from arrival.contracts import Dossier, RawDoc
from arrival.util import doc_id as compute_doc_id
from arrival.util import normalize_ws, slug

pytestmark = pytest.mark.ticket("T-0")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DOSSIER_DIR = FIXTURES / "dossiers"
DOC_DIR = FIXTURES / "http"
PEOPLE = ("alpha", "bravo", "charlie", "delta")

# DESIGN Decision 3. Repeated here on purpose: these are the numbers the fixture was
# DESIGNED to produce, and this module is the fixture's spec, not graph.py's.
TYPE_BOOST = {
    "company": 1.5,
    "investor": 1.5,
    "board": 1.5,
    "event": 1.3,
    "cause": 1.3,
    "person": 1.3,
    "technology": 1.0,
    "topic": 1.0,
    "school": 0.8,
    "city": 0.5,
}
DISPLAYABLE_KINDS = frozenset(
    {
        "self_page", "search", "wikidata", "wikipedia", "github", "edgar", "uspto",
        "propublica", "wayback", "hn", "openalex", "youtube", "podcast",
    }
)


def load_dossier(name: str) -> Dossier:
    return Dossier.model_validate_json((DOSSIER_DIR / f"{name}.json").read_text())


def load_docs(name: str) -> dict[str, RawDoc]:
    raw = json.loads((DOC_DIR / f"fixture_dossier_docs_{name}.json").read_text())
    docs = [RawDoc.model_validate(d) for d in raw]
    return {d.doc_id: d for d in docs}


@pytest.fixture(scope="module")
def dossiers() -> dict[str, Dossier]:
    return {name: load_dossier(name) for name in PEOPLE}


@pytest.fixture(scope="module")
def docs() -> dict[str, dict[str, RawDoc]]:
    return {name: load_docs(name) for name in PEOPLE}


# --------------------------------------------------------------------------
# validity and citations
# --------------------------------------------------------------------------


def test_fixture_dossiers_valid(dossiers, docs):
    """All four load, validate, and every fact's quote is really in its RawDoc.

    This is the acceptance-6 test: schema validity plus the citation property that
    DESIGN Decision 5 makes the hallucination guard.
    """
    assert set(dossiers) == set(PEOPLE)
    for name, dossier in dossiers.items():
        assert dossier.schema_version == 1
        assert dossier.person.person_id == name, "filename stem must equal person_id"
        assert dossier.resolution.person_id == name
        assert dossier.facts, f"{name} has no facts"
        assert dossier.hubs, f"{name} has no hubs"

        by_id = docs[name]
        for fact in dossier.facts:
            doc = by_id.get(fact.provenance.doc_id)
            assert doc is not None, f"{name}/{fact.fact_id} cites a doc that is not committed"
            assert normalize_ws(fact.provenance.quote) in normalize_ws(doc.text), (
                f"{name}/{fact.fact_id}: quote is not a normalise-substring of {doc.url}"
            )
            assert fact.provenance.url == doc.url
            assert fact.provenance.source_kind == doc.source_kind
            assert fact.provenance.published_at == doc.published_at


def test_fixture_facts_obey_the_field_contracts(dossiers):
    for name, dossier in dossiers.items():
        seen: set[str] = set()
        for fact in dossier.facts:
            assert fact.fact_id not in seen, f"{name}: duplicate fact_id {fact.fact_id}"
            seen.add(fact.fact_id)
            assert len(fact.text) <= 200, f"{name}/{fact.fact_id} exceeds 200 chars"
            assert 0.0 <= fact.provenance.confidence <= 1.0
            if fact.excluded:
                assert fact.exclusion_reason is not None
            else:
                assert fact.exclusion_reason is None


def test_fixture_hubs_are_canonical_and_resolve_their_evidence(dossiers):
    for name, dossier in dossiers.items():
        fact_ids = {f.fact_id for f in dossier.facts}
        hub_ids = [h.hub_id for h in dossier.hubs]
        assert len(hub_ids) == len(set(hub_ids)), f"{name}: duplicate hub_id"
        for hub in dossier.hubs:
            assert hub.hub_id == f"{hub.type}:{slug(hub.label)}", (
                f"{name}: {hub.hub_id!r} is not the canonical {{type}}:{{slug(label)}} form"
            )
            assert 0.0 <= hub.recency <= 1.0
            assert hub.evidence_fact_ids, f"{name}/{hub.hub_id} has no evidence"
            # T-7 resolves these ids inside the ARRIVING person's dossier.
            assert set(hub.evidence_fact_ids) <= fact_ids


def test_fixture_docs_are_self_consistent(docs):
    for name, by_id in docs.items():
        for did, doc in by_id.items():
            assert did == compute_doc_id(doc.url), f"{name}: {doc.url} has a stale doc_id"
            assert doc.text.strip(), f"{name}: {doc.url} has empty text"
            assert len(doc.text) <= 20_000
            assert ".example" in doc.url or "web.archive.org" in doc.url, (
                "fixture URLs must be unresolvable/synthetic"
            )


def test_accepted_and_rejected_docs_are_committed(dossiers, docs):
    for name, dossier in dossiers.items():
        by_id = docs[name]
        for did in dossier.resolution.accepted_doc_ids:
            assert did in by_id, f"{name}: accepted doc {did} is not committed"
        for verdict in dossier.resolution.rejected:
            doc = by_id.get(verdict.doc_id)
            assert doc is not None, f"{name}: rejected doc {verdict.doc_id} is not committed"
            assert normalize_ws(verdict.evidence) in normalize_ws(doc.text)
            assert verdict.doc_id not in dossier.resolution.accepted_doc_ids


# --------------------------------------------------------------------------
# the designed hub overlaps
# --------------------------------------------------------------------------


def _hub_ids(dossier: Dossier) -> set[str]:
    return {h.hub_id for h in dossier.hubs}


def test_all_four_share_the_two_generic_hubs(dossiers):
    for name, dossier in dossiers.items():
        assert "city:austin" in _hub_ids(dossier), name
        assert "topic:ai" in _hub_ids(dossier), name


def test_alpha_and_bravo_share_nothing_beyond_the_generic_hubs(dossiers):
    shared = _hub_ids(dossiers["alpha"]) & _hub_ids(dossiers["bravo"])
    assert shared == {"city:austin", "topic:ai"}


def test_charlie_and_delta_share_exactly_one_rare_hub(dossiers):
    shared = _hub_ids(dossiers["charlie"]) & _hub_ids(dossiers["delta"])
    assert shared == {"city:austin", "topic:ai", "investor:foundry-seed-2019"}
    rare = next(h for h in dossiers["charlie"].hubs if h.hub_id == "investor:foundry-seed-2019")
    assert rare.type == "investor"
    assert rare.recency == 1.0
    other = next(h for h in dossiers["delta"].hubs if h.hub_id == "investor:foundry-seed-2019")
    assert other.recency == 1.0


def test_the_rare_hub_is_on_exactly_two_people(dossiers):
    holders = [n for n, d in dossiers.items() if "investor:foundry-seed-2019" in _hub_ids(d)]
    assert sorted(holders) == ["charlie", "delta"]


def test_no_other_cross_person_hub_overlap_exists(dossiers):
    """Any extra overlap would move the scores off their pinned values."""
    generic = {"city:austin", "topic:ai"}
    for a in PEOPLE:
        for b in PEOPLE:
            if a >= b:
                continue
            extra = (_hub_ids(dossiers[a]) & _hub_ids(dossiers[b])) - generic
            expected = {"investor:foundry-seed-2019"} if {a, b} == {"charlie", "delta"} else set()
            assert extra == expected, f"unexpected overlap between {a} and {b}: {extra}"


# --------------------------------------------------------------------------
# the pinned arithmetic (DESIGN Decision 3)
# --------------------------------------------------------------------------


def _idf(n_people: int, n_on_hub: int) -> float:
    return max(0.0, math.log(n_people / (1 + n_on_hub)))


def test_generic_hubs_clamp_to_zero(dossiers):
    n = len(dossiers)
    assert n == 4
    assert _idf(n, 4) == 0.0, "a hub everyone shares must contribute nothing"


def test_fixture_hub_math_pins_the_scores(dossiers):
    """charlie-delta must come out at exactly 100 and alpha-bravo at exactly 0."""
    n = len(dossiers)
    holders: dict[str, int] = {}
    for dossier in dossiers.values():
        for hub in dossier.hubs:
            holders[hub.hub_id] = holders.get(hub.hub_id, 0) + 1

    def raw(a: str, b: str) -> float:
        da, db = dossiers[a], dossiers[b]
        by_id_b = {h.hub_id: h for h in db.hubs}
        total = 0.0
        for hub in da.hubs:
            partner = by_id_b.get(hub.hub_id)
            if partner is None:
                continue
            total += (
                _idf(n, holders[hub.hub_id])
                * min(hub.recency, partner.recency)
                * TYPE_BOOST[hub.type]
            )
        return total

    ref = math.log(n / 3) * 1.5

    def score(a: str, b: str) -> int:
        return min(100, round(100 * raw(a, b) / ref))

    assert holders["investor:foundry-seed-2019"] == 2
    assert math.isclose(_idf(n, 2), math.log(4 / 3))
    assert math.isclose(raw("charlie", "delta"), math.log(4 / 3) * 1.5)
    assert math.isclose(ref, math.log(4 / 3) * 1.5)
    assert score("charlie", "delta") == 100
    assert score("alpha", "bravo") == 0
    assert score("alpha", "charlie") == 0
    assert score("bravo", "delta") == 0


# --------------------------------------------------------------------------
# the material later tickets need
# --------------------------------------------------------------------------


def test_alpha_has_the_two_excluded_facts_t8_must_never_render(dossiers):
    alpha = dossiers["alpha"]
    excluded = [f for f in alpha.facts if f.excluded]
    assert len(excluded) >= 2
    reasons = {f.exclusion_reason for f in excluded}
    assert {"family", "home_or_property"} <= reasons

    blob = " ".join(f.text for f in excluded)
    assert "1442 Quarrystone Lane" in blob
    assert "his wife Delia Moreno-Vance" in blob


# Every distinctive token that lives ONLY in alpha's withheld facts. T-8 acceptance 3 says
# "excluded fixture facts (family/address text) do not appear anywhere in the HTML", and the
# natural way to write that test is `assert "quarrystone" not in html.lower()` — a single
# distinctive word, lowercased. So the fixture has to keep these tokens out of every field a
# digest renders, INCLUDING provenance URLs and titles: R7 renders the source URL in "Why we
# know this" and R9 makes the citation visible, so a source URL is displayed text.
WITHHELD_TOKENS = (
    "quarrystone",       # the street in the excluded home_or_property fact
    "1442",              # its house number
    "delia",             # the spouse in the excluded family fact
    "moreno-vance",
    "travisledger",      # the host that carries both excluded facts
)


def _renderable_strings(dossier, docs_for_person) -> list[tuple[str, str]]:
    """(label, text) for everything a digest or its reasoning toggle can put on screen.

    Deliberately WIDER than the digest: hub labels feed R10's "why", provenance urls and
    titles feed R7/R9's citations. /debug is excluded by design — R15 is the surface that
    is *supposed* to show withheld material, behind DEBUG_VIEWS.
    """
    out = [("person.name", dossier.person.name)]
    out += [(f"person.details[{i}]", d) for i, d in enumerate(dossier.person.details)]
    for hub in dossier.hubs:
        out.append((f"hub {hub.hub_id}", f"{hub.label} {hub.hub_id}"))
    for fact in dossier.facts:
        if fact.excluded:
            continue
        prov = fact.provenance
        out.append((f"{fact.fact_id}.text", fact.text))
        out.append((f"{fact.fact_id}.quote", prov.quote))
        out.append((f"{fact.fact_id}.url", prov.url))
        doc = docs_for_person.get(prov.doc_id)
        if doc is not None:
            out.append((f"{fact.fact_id}.doc.title", doc.title))
    return out


def test_the_excluded_strings_appear_nowhere_a_digest_could_reach(dossiers, docs):
    """No withheld token reaches ANY field a digest renders — url and title included.

    The earlier version of this test searched only ``fact.text`` and
    ``provenance.quote``. That is not the whole rendered surface: alpha's kept, displayable
    non_obvious fact was cited to ``http://quarrystone-coop.example/newsletter``, which put
    the deliberately withheld street name into the digest's own visible source list, where a
    correct T-8 implementation would have rendered it and a correct T-8 test would have
    caught it — as a fixture bug wearing the costume of a code bug.
    """
    phrases = ("1442 Quarrystone Lane", "Delia Moreno-Vance", "Quarrystone Lane")
    for name, dossier in dossiers.items():
        for label, text in _renderable_strings(dossier, docs[name]):
            lowered = text.lower()
            for needle in phrases:
                assert needle not in text, f"{name}/{label} leaks {needle!r}: {text!r}"
            for token in WITHHELD_TOKENS:
                assert token not in lowered, f"{name}/{label} leaks {token!r}: {text!r}"


def test_the_withheld_tokens_really_are_distinctive(dossiers):
    """Guards the guard: each token must actually occur in an EXCLUDED fact.

    Without this, deleting alpha's excluded facts would make the leak test vacuously green.
    """
    excluded_blob = " ".join(
        f"{f.text} {f.provenance.quote} {f.provenance.url}"
        for d in dossiers.values()
        for f in d.facts
        if f.excluded
    ).lower()
    for token in WITHHELD_TOKENS:
        assert token in excluded_blob, f"{token!r} is not in any excluded fact any more"


def test_the_non_obvious_fact_is_cited_to_an_unrelated_host(dossiers):
    """R7's "Not on the first page" slot is displayed WITH its citation (R9).

    So the one kept wayback fact's URL is displayed text, and pinning it here stops the
    co-op host drifting back onto the withheld street name.
    """
    alpha = dossiers["alpha"]
    fact = next(f for f in alpha.facts if f.category == "non_obvious" and not f.excluded)
    assert "rillwater-coop.example" in fact.provenance.url
    assert "quarrystone" not in fact.provenance.url.lower()


def test_alpha_has_a_non_obvious_wayback_fact(dossiers):
    """R7's "Not on the first page" slot needs eligible material in the fixture."""
    alpha = dossiers["alpha"]
    candidates = [
        f
        for f in alpha.facts
        if f.category == "non_obvious"
        and f.provenance.source_kind == "wayback"
        and not f.excluded
    ]
    assert candidates, "alpha needs at least one non_obvious fact sourced from wayback"


@pytest.mark.parametrize("name", ["charlie", "delta"])
def test_charlie_and_delta_have_digest_material(dossiers, name):
    """T-7 builds a who_line from current_work and a say-out-loud from a hook fact."""
    dossier = dossiers[name]
    for category in ("current_work", "hook"):
        usable = [
            f
            for f in dossier.facts
            if f.category == category
            and not f.excluded
            and f.provenance.confidence >= 0.7
            and f.provenance.source_kind in DISPLAYABLE_KINDS
        ]
        assert usable, f"{name} needs a displayable {category} fact with confidence >= 0.7"


def test_every_person_has_a_displayable_current_work_fact(dossiers):
    for name, dossier in dossiers.items():
        usable = [
            f
            for f in dossier.facts
            if f.category == "current_work"
            and not f.excluded
            and f.provenance.confidence >= 0.7
            and f.provenance.source_kind in DISPLAYABLE_KINDS
        ]
        assert usable, f"{name} has no displayable current_work fact for the who-line"


def test_no_fixture_fact_uses_a_never_displayable_source(dossiers):
    """fec/courtlistener are never displayable (R12); keep them out of the fixture set."""
    for name, dossier in dossiers.items():
        for fact in dossier.facts:
            assert fact.provenance.source_kind not in {"fec", "courtlistener"}, name
