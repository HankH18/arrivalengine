"""FROZEN acceptance: contract guards over the ticket T-0 shared surface.

T-0 (`src/arrival/{contracts,util,config}.py`) is the surface that the other nine
tickets import.  It is built BEFORE the freeze, so every test in this module is green
at baseline BY DESIGN and every one of them carries `@pytest.mark.guard` in addition to
`t0`, which keeps them out of the scored per-ticket counts (`run.py --ticket T-0
--count-passing` runs `-m "t0 and not guard"`).

Their value is REGRESSION, not scoring: nine downstream tickets import these models,
these Literal member sets and these three util primitives, and any of those tickets
could drift them.  A model field renamed in cycle 11 to make one ticket's code tidier
silently breaks the four tickets that read the old name; these guards turn that into a
named failure instead of a mystery.

Every product import is INSIDE a test body.  A module-scope `import arrival.contracts`
would turn "T-0 has not landed" into a collection error, which removes this whole file
from both the numerator and the denominator of the pass-rate metric — the rate would
then read green over a suite that never ran.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import typing

import pytest

pytestmark = pytest.mark.t0


# --------------------------------------------------------------------------------------
# The DESIGN "Interfaces" block, transcribed.  This table IS the contract; it is the
# reason these guards exist, and it is deliberately written out here rather than derived
# from the product so that a drift in the product is a disagreement, not a tautology.
# --------------------------------------------------------------------------------------

DESIGN_MODELS = {
    "PersonRef": {"person_id", "name", "details"},
    "RawDoc": {"doc_id", "source_kind", "url", "title", "text", "published_at", "fetched_at"},
    "Verdict": {"doc_id", "match", "confidence", "evidence", "disambiguator"},
    "Resolution": {
        "person_id",
        "status",
        "strong_keys",
        "accepted_doc_ids",
        "rejected",
        "confidence",
    },
    "Provenance": {
        "doc_id",
        "url",
        "source_kind",
        "quote",
        "published_at",
        "retrieved_at",
        "confidence",
    },
    "Fact": {"fact_id", "text", "category", "provenance", "excluded", "exclusion_reason"},
    "Hub": {"hub_id", "label", "type", "recency", "evidence_fact_ids"},
    "Dossier": {"person", "resolution", "facts", "hubs", "built_at", "schema_version"},
    "HubContribution": {"hub", "idf_weight", "recency", "type_boost", "contribution"},
    "Match": {"other", "score", "contributions", "path", "why"},
    "Digest": {
        "digest_id",
        "person",
        "who_line",
        "meet",
        "lately",
        "non_obvious",
        "say_out_loud",
        "sources",
        "exclusion_policy",
        "created_at",
    },
    "Budget": {"docs_per_connector", "max_docs_total", "max_llm_calls"},
    "BuildReport": {"people", "started_at", "finished_at"},
}

DESIGN_PROTOCOLS = ("Connector", "LLMClient")

DESIGN_LITERALS = {
    "SourceKind": {
        "self_page",
        "search",
        "wikidata",
        "wikipedia",
        "github",
        "edgar",
        "uspto",
        "propublica",
        "wayback",
        "hn",
        "openalex",
        "youtube",
        "podcast",
        "fec",
        "courtlistener",
    },
    "FactCategory": {
        "current_work",
        "collaborator",
        "interest",
        "recent_activity",
        "hook",
        "affiliation",
        "non_obvious",
    },
    "ExclusionReason": {
        "home_or_property",
        "family",
        "health",
        "legal",
        "wealth",
        "political",
        "low_confidence",
        "source_kind_not_displayable",
    },
    "HubType": {
        "company",
        "investor",
        "school",
        "board",
        "topic",
        "city",
        "technology",
        "event",
        "cause",
        "person",
    },
}


def _settings_field_names(settings):
    """Declared setting names, however `config.Settings` is implemented.

    Prefers pydantic's `model_fields` (C3 pins Pydantic v2, so `BaseSettings` is the
    expected shape) and falls back to the instance dict, so the guard grades the
    contract — "these keys are readable off Settings" — and not the base class.
    """
    fields = getattr(type(settings), "model_fields", None)
    if fields:
        return set(fields)
    return set(vars(settings))


def _make_settings(monkeypatch):
    """A `Settings` built with every documented env key present.

    `.env.example` (TASKS T-0 acceptance 7) names ANTHROPIC_API_KEY, TAVILY_API_KEY,
    GITHUB_TOKEN, CONTACT_EMAIL, DEBUG_VIEWS and the model ids, so a Settings that
    marks any of them required must still construct here.
    """
    from arrival.config import Settings

    for key, value in (
        ("CONTACT_EMAIL", "harness@example.org"),
        ("ANTHROPIC_API_KEY", "sk-frozen-harness"),
        ("TAVILY_API_KEY", "tvly-frozen-harness"),
        ("GITHUB_TOKEN", "ghp-frozen-harness"),
        ("DEBUG_VIEWS", "0"),
    ):
        monkeypatch.setenv(key, value)
    return Settings()


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


@pytest.mark.guard
def test_contracts_exposes_every_name_in_the_design_interfaces_block():
    """T-0 acceptance 1 / DESIGN §Interfaces: contracts.py is the single shared surface."""
    from arrival import contracts

    expected = sorted(
        list(DESIGN_MODELS) + list(DESIGN_PROTOCOLS) + list(DESIGN_LITERALS)
    )
    missing = [name for name in expected if not hasattr(contracts, name)]
    assert missing == [], (
        "arrival.contracts is missing names pinned by DESIGN §Interfaces: "
        f"{missing}. Downstream tickets import these; a ticket that redefines one "
        "instead has forked the contract."
    )


@pytest.mark.guard
def test_model_field_names_match_the_design_interfaces_block():
    """T-0 acceptance 1 / DESIGN §Interfaces: field names are the cross-ticket contract."""
    from arrival import contracts

    drift = {}
    for model_name, expected_fields in DESIGN_MODELS.items():
        model = getattr(contracts, model_name)
        actual = set(model.model_fields)
        if actual != expected_fields:
            drift[model_name] = {
                "missing": sorted(expected_fields - actual),
                "unexpected": sorted(actual - expected_fields),
            }
    assert drift == {}, f"model fields drifted from DESIGN §Interfaces: {drift}"


@pytest.mark.guard
def test_literal_member_sets_are_exactly_what_design_pins():
    """DESIGN §Interfaces: SourceKind/FactCategory/ExclusionReason/HubType member sets."""
    from arrival import contracts

    drift = {}
    for alias_name, expected_members in DESIGN_LITERALS.items():
        actual = set(typing.get_args(getattr(contracts, alias_name)))
        if actual != expected_members:
            drift[alias_name] = {
                "missing": sorted(expected_members - actual),
                "unexpected": sorted(actual - expected_members),
            }
    assert drift == {}, (
        "Literal member sets drifted from DESIGN §Interfaces: "
        f"{drift}. R11/R12 read these members by name (fec and courtlistener must "
        "remain declared even though they are never displayable)."
    )


@pytest.mark.guard
def test_both_protocols_are_runtime_checkable():
    """T-0 acceptance 5 / DESIGN §Interfaces: `isinstance(x, Connector|LLMClient)` must work."""
    from arrival.contracts import Connector, LLMClient, PersonRef

    class _ConnectorShaped:
        kind = "search"

        async def search(self, person, budget):
            return []

    class _LLMShaped:
        async def structured(
            self, *, system, user, schema, max_tokens=2000, cache_prefix=True
        ):
            return schema

    # isinstance() against a Protocol that is NOT runtime_checkable raises TypeError,
    # so these two assertions are the whole test: they cannot pass without the decorator.
    assert isinstance(_ConnectorShaped(), Connector)
    assert isinstance(_LLMShaped(), LLMClient)

    # Control: the checks must also be capable of saying no, or they measure nothing.
    assert not isinstance(PersonRef(person_id="p", name="P"), Connector)
    assert not isinstance(PersonRef(person_id="p", name="P"), LLMClient)


@pytest.mark.guard
def test_declared_defaults_match_the_design_interfaces_block():
    """DESIGN §Interfaces: every `= default` in the contract, asserted by construction."""
    from arrival.contracts import (
        Budget,
        Dossier,
        Fact,
        Hub,
        PersonRef,
        Provenance,
        RawDoc,
        Resolution,
    )

    when = _dt.datetime(2026, 2, 20, 14, 0, tzinfo=_dt.timezone.utc)

    person = PersonRef(person_id="p", name="P")
    assert person.details == []

    doc = RawDoc(
        doc_id="0123456789abcdef",
        source_kind="search",
        url="https://example.org/a",
        text="body",
        fetched_at=when,
    )
    assert doc.title == ""
    assert doc.published_at is None

    resolution = Resolution(
        person_id="p", status="unresolved", accepted_doc_ids=[], rejected=[], confidence=0.1
    )
    assert resolution.strong_keys == {}

    prov = Provenance(
        doc_id="0123456789abcdef",
        url="https://example.org/a",
        source_kind="search",
        quote="body",
        retrieved_at=when,
        confidence=0.9,
    )
    assert prov.published_at is None

    fact = Fact(fact_id="f1", text="A professional fact.", category="current_work", provenance=prov)
    assert fact.excluded is False
    assert fact.exclusion_reason is None

    hub = Hub(hub_id="topic:remote-work", label="Remote work", type="topic")
    assert hub.recency == 1.0
    assert hub.evidence_fact_ids == []

    dossier = Dossier(
        person=person, resolution=resolution, facts=[], hubs=[], built_at=when
    )
    assert dossier.schema_version == 1

    budget = Budget()
    assert (budget.docs_per_connector, budget.max_docs_total, budget.max_llm_calls) == (8, 40, 80)


@pytest.mark.guard
def test_llm_error_exists_and_is_an_exception_type():
    """DESIGN §Interfaces/LLMClient: `structured` raises LLMError after one retry."""
    err = None
    for module_name in ("arrival.contracts", "arrival.llm", "arrival.llm.client"):
        try:
            module = __import__(module_name, fromlist=["LLMError"])
        except ImportError:
            continue
        err = getattr(module, "LLMError", None)
        if err is not None:
            break
    assert err is not None, (
        "LLMError is not importable from arrival.contracts, arrival.llm or "
        "arrival.llm.client. DESIGN names it as the failure mode of "
        "LLMClient.structured, so every caller that catches it needs one canonical "
        "definition rather than a per-ticket copy."
    )
    assert isinstance(err, type) and issubclass(err, Exception)


@pytest.mark.guard
def test_a_fully_populated_dossier_round_trips_through_json_unchanged(frozen_fixtures):
    """T-0 acceptance 1: Dossier survives model_dump_json/model_validate_json intact."""
    from arrival.contracts import Dossier

    source = (frozen_fixtures / "dossiers" / "runa-okonkwo.json").read_text(encoding="utf-8")
    dossier = Dossier.model_validate_json(source)

    # Control: an empty Dossier round-trips trivially, so assert the subject is the
    # populated one the digest and graph tickets actually read.
    assert len(dossier.facts) >= 10
    assert len(dossier.hubs) >= 4
    assert any(f.excluded for f in dossier.facts)
    assert dossier.resolution.rejected

    again = Dossier.model_validate_json(dossier.model_dump_json())
    assert again == dossier
    assert again.model_dump(mode="json") == dossier.model_dump(mode="json")

    # And the JSON on disk is itself contract-shaped: the frozen corpus was authored
    # against DESIGN, so a round-trip that silently drops a key would show up here.
    assert set(json.loads(source)) == DESIGN_MODELS["Dossier"]


@pytest.mark.guard
def test_slug_pins_the_design_example():
    """T-0 acceptance 2: slug("Jane O'Neil-Ruiz") == "jane-oneil-ruiz"."""
    from arrival.util import slug

    assert slug("Jane O'Neil-Ruiz") == "jane-oneil-ruiz"


@pytest.mark.guard
def test_normalize_ws_pins_the_design_example():
    """T-0 acceptance 2: normalize_ws collapses whitespace and casefolds (Decision 5)."""
    from arrival.util import normalize_ws

    assert normalize_ws("A  b\nC") == "a b c"
    # Decision 5's citation check is `quote in normalize_ws(doc.text)`, so leading and
    # trailing whitespace must not survive either or every quote check gains a false
    # negative at the edges.
    assert normalize_ws("  Padded\t\ttext \n") == "padded text"


@pytest.mark.guard
def test_doc_id_is_the_sha1_prefix_of_the_url(frozen_fixtures):
    """T-0 acceptance 2 / frozen-spec §2: doc_id(url) == sha1(url)[:16], corpus-wide."""
    from arrival.util import doc_id

    docs = sorted((frozen_fixtures / "docs").glob("*.json"))
    assert len(docs) >= 20, "frozen RawDoc corpus is missing; this guard would be vacuous"

    mismatches = []
    for path in docs:
        record = json.loads(path.read_text(encoding="utf-8"))
        url = record["url"]
        expected = hashlib.sha1(url.encode()).hexdigest()[:16]
        if doc_id(url) != expected or record["doc_id"] != expected:
            mismatches.append((url, doc_id(url), record["doc_id"], expected))
    assert mismatches == [], f"doc_id disagreed with sha1(url)[:16]: {mismatches}"


@pytest.mark.guard
def test_settings_exposes_the_env_keys_pinned_by_tasks(monkeypatch):
    """T-0 acceptance 7 / C5 / R15: the env surface every downstream ticket reads."""
    settings = _make_settings(monkeypatch)

    required = ["contact_email", "debug_views", "anthropic_api_key", "tavily_api_key", "github_token"]
    missing = [name for name in required if not hasattr(settings, name)]
    assert missing == [], (
        f"arrival.config.Settings is missing {missing}. These are pinned by "
        ".env.example in TASKS T-0 acceptance 7 and read by T-1 (contact_email for the "
        "C5 User-Agent, tavily_api_key, github_token), T-2 (anthropic_api_key) and T-8 "
        "(debug_views for the R15 gate); a ticket that has to widen Settings to get one "
        "of them is editing another ticket's file."
    )


@pytest.mark.guard
def test_settings_exposes_a_cache_dir_and_two_model_id_settings(monkeypatch):
    """DESIGN §Data models + Decision 9: HTTP cache location and model ids are settings."""
    settings = _make_settings(monkeypatch)
    names = _settings_field_names(settings)

    # DESIGN pins the cache LOCATION (.cache/http/) but not the setting's name, so this
    # guard grades the requirement — T-1 must not have to invent its own — not a spelling.
    cache_settings = sorted(n for n in names if "cache" in n)
    assert cache_settings, (
        "arrival.config.Settings exposes no cache-directory setting. Without one, T-1's "
        f"disk cache location is unreachable from configuration. Declared: {sorted(names)}"
    )

    # Decision 9: "Model IDs are settings, not constants" — one Haiku-class for
    # extraction/taste, one Sonnet-class for resolution and say-out-loud.
    model_settings = sorted(n for n in names if "model" in n)
    assert len(model_settings) >= 2, (
        "DESIGN Decision 9 requires at least two model-id settings (a cheap extraction "
        f"model and a stronger resolution model). Found: {model_settings}"
    )
