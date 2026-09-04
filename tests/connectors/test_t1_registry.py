"""TASKS T-1 acceptance 4: `all_connectors(settings)` and what it may and may not contain.

The list is exact in both directions, and the second direction is the one with a product
argument behind it.  SPEC Q4 leaves `fec` (political donations) and `courtlistener` (court
records) unbuilt and R11 forbids ever displaying them, so an eleventh connector is a scope
breach rather than a bonus: a source that can never be shown is a source there was no
reason to have fetched, and the withholding belongs at the fan-out rather than downstream
in the taste filter where the data would already exist.
"""

from __future__ import annotations

import pytest
from t1_recorded import KINDS, settings_for

from arrival.connectors import DISPLAY_PRIORITY, WITHHELD_KINDS, all_connectors
from arrival.contracts import Connector
from doubles import assert_conforms

pytestmark = pytest.mark.ticket("T-1")

#: TASKS T-1 acceptance 2 names these ten by name. Transcribed here independently of
#: `arrival.connectors`, so this test grades the module rather than agreeing with it.
EXPECTED = (
    "self_page",
    "wikipedia",
    "wikidata",
    "github",
    "openalex",
    "edgar",
    "propublica",
    "hn",
    "wayback",
    "search",
)

NEVER = ("fec", "courtlistener", "uspto", "youtube", "podcast")


def test_all_connectors_order(tmp_path):
    """The ten kinds, in display-priority order, with no duplicates and nothing extra."""
    kinds = [c.kind for c in all_connectors(settings_for(tmp_path))]

    missing = [k for k in EXPECTED if k not in kinds]
    extra = [k for k in kinds if k not in EXPECTED]
    assert not missing, f"all_connectors() is missing {missing}; got {kinds}"
    assert not extra, (
        f"all_connectors() returned unexpected kinds {extra}. SPEC Q4 leaves fec, "
        f"courtlistener, uspto, youtube and podcast unbuilt, so the list is exactly "
        f"{list(EXPECTED)}"
    )
    assert len(kinds) == len(set(kinds)), f"duplicate connector kinds: {kinds}"
    assert kinds == list(EXPECTED), (
        f"the order is display priority -- most trustworthy and most attributable first: "
        f"the person's own page outranks an encyclopedia entry about them, which outranks "
        f"a public record naming them, which outranks a search engine's guess. Expected "
        f"{list(EXPECTED)}, got {kinds}"
    )


def test_all_connectors_omits_the_sources_the_product_refuses_to_hold(tmp_path):
    kinds = {c.kind for c in all_connectors(settings_for(tmp_path))}

    for withheld in NEVER:
        assert withheld not in kinds, (
            f"{withheld!r} is in the fan-out. This is a hospitality product; a host who "
            "greets a member with their donation history or their court record has "
            "crossed the line the whole thing is scored on."
        )
    assert {"fec", "courtlistener"} <= set(WITHHELD_KINDS), (
        "the exclusion should be a decision a reader can find, not an omission they must "
        "notice"
    )


def test_every_connector_conforms_to_the_protocol(tmp_path):
    """`assert_conforms`, never `isinstance`.

    `isinstance` against a runtime_checkable Protocol checks only that attributes with the
    right NAMES exist -- a class whose entire `search` returns a string passes it.
    """
    for connector in all_connectors(settings_for(tmp_path)):
        assert_conforms(connector, Connector)


def test_display_priority_is_published_and_matches_the_list(tmp_path):
    assert tuple(DISPLAY_PRIORITY) == EXPECTED
    assert tuple(DISPLAY_PRIORITY) == tuple(c.kind for c in all_connectors(settings_for(tmp_path)))
    assert isinstance(DISPLAY_PRIORITY, tuple), "a caller must not be able to rearrange it"


def test_the_test_helper_and_the_module_agree_on_the_kinds():
    """`KINDS` drives the parametrised fixture tests; a drift there silently skips a source."""
    assert set(KINDS) == set(EXPECTED)


def test_all_connectors_takes_settings_at_call_time_and_returns_fresh_instances(tmp_path):
    """One process must be able to build with two configurations without a global.

    Also the reason `settings` is passed rather than fetched: the frozen acceptance suite
    does not reset `get_settings`'s lru_cache, so a connector that snapshots settings at
    import time passes `pytest --ticket T-1` and fails the frozen gate.
    """
    first = settings_for(tmp_path / "a", contact_email="one@example.org")
    second = settings_for(tmp_path / "b", contact_email="two@example.org")

    a = all_connectors(first)
    b = all_connectors(second)

    assert [c.kind for c in a] == [c.kind for c in b]
    assert all(x is not y for x, y in zip(a, b, strict=True)), (
        "two calls must not hand back the same objects; a connector may legitimately keep "
        "per-instance state and sharing it across configurations would leak it"
    )
    assert a[0].settings.contact_email == "one@example.org"
    assert b[0].settings.contact_email == "two@example.org"


def test_all_connectors_works_with_no_arguments():
    """`None` means "read get_settings() when you need it", which is what the CLI wants."""
    connectors = all_connectors()
    assert [c.kind for c in connectors] == list(EXPECTED)
    assert connectors[0].settings is not None
