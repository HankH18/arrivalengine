"""Corpus loaders and synthetic-dossier builders for the T-5 graph tests.

**Why this is not a ``conftest.py``.** ``tests/`` is not a package, so a second
``conftest.py`` anywhere under it takes the module name ``conftest`` in ``sys.modules`` and
whichever one is imported first wins. ``tests/test_t0_offline.py`` does
``from conftest import NETWORK_DISABLED_MESSAGE, ...``; adding ``tests/graph/conftest.py``
made that import resolve to THIS directory's file and took the whole suite to
``Interrupted: 1 error during collection`` (measured). The duplicate-basename hazard the
codebase map records for ``test_*.py`` modules therefore applies to ``conftest.py`` too --
so every ticket that owns a ``tests/<area>/`` subtree needs a ticket-prefixed helper module
like this one instead of a local conftest.

The four T-0 dossiers at ``tests/fixtures/dossiers/`` are the corpus this ticket's
acceptance criteria name by mnemonic (``match(g, "charlie", [...])``). Their ``person_id``s
are deliberately NOT ``slug(name)`` -- intentional and pinned by
``tests/test_t0b_fixture_conventions.py`` -- so nothing here derives an id from a name.

The synthetic builders exist because the four fixtures cannot exercise everything: they
contain no pair sharing a hub at unequal recency, and no pair sharing three positive hubs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import networkx as nx

from arrival.contracts import Dossier, Hub, PersonRef, Resolution
from arrival.graph import build_graph

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "dossiers"

#: The four T-0 dossiers.
FIXTURE_IDS = ("alpha", "bravo", "charlie", "delta")

#: The one hub only charlie and delta carry -- the whole point of the corpus (SPEC S5).
RARE_HUB_ID = "investor:foundry-seed-2019"
RARE_HUB_LABEL = "Foundry Seed 2019"

#: Carried by all four, so ``ln(4/5) < 0`` clamps them to zero.
CLAMPED_HUB_IDS = ("city:austin", "topic:machine-learning")


def load_raw() -> dict[str, dict]:
    """The four fixture dossiers as raw JSON, keyed by ``person_id``."""
    out: dict[str, dict] = {}
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[raw["person"]["person_id"]] = raw
    assert set(out) == set(FIXTURE_IDS), f"unexpected fixture population: {sorted(out)}"
    return out


def load_dossiers() -> list[Dossier]:
    """The four fixture dossiers, validated, in ``person_id`` order."""
    raw = load_raw()
    return [Dossier.model_validate(raw[person_id]) for person_id in sorted(raw)]


def fixture_graph() -> nx.Graph:
    """The graph over all four fixture dossiers."""
    return build_graph(load_dossiers())


def make_dossier(person_id: str, name: str, hubs: list[Hub]) -> Dossier:
    """A minimal, valid dossier carrying exactly ``hubs`` and no facts.

    Facts are empty on purpose: matching is not display, so the graph must be buildable from
    hubs alone and must never reach into ``facts`` to decide what participates.
    """
    return Dossier(
        person=PersonRef(person_id=person_id, name=name),
        resolution=Resolution(
            person_id=person_id,
            status="resolved",
            accepted_doc_ids=[],
            rejected=[],
            confidence=1.0,
        ),
        facts=[],
        hubs=hubs,
        built_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def make_hub(hub_id: str, label: str, hub_type: str, recency: float = 1.0) -> Hub:
    """Build a Hub, BYPASSING validation, so a test can inject an illegal value.

    `model_construct`, not `Hub(...)`: T-054 put `ge=0, le=1` on `Hub.recency`, and
    `test_score_stays_in_range_when_a_hub_carries_an_out_of_range_recency` exists precisely
    to prove `graph` clamps a recency the contract now refuses. Constructing normally would
    make that test fail at its own setup and prove nothing about the clamp. Every caller
    passing a legal recency is unaffected.
    """
    return Hub.model_construct(hub_id=hub_id, label=label, type=hub_type, recency=recency)


def filler(count: int, *, prefix: str = "f") -> list[Dossier]:
    """Hubless people, present only to set N. They can never match anyone."""
    return [make_dossier(f"{prefix}{i}", f"Filler {i}", []) for i in range(count)]
