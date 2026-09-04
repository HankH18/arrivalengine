"""A corpus builder shared by the TESTBROWSER regression modules.

Imported explicitly by `tests/web/test_tbrowser_*.py`; it defines no fixtures and no hooks,
so nothing here reaches any other lane's tests. `tests/` is not a package and pytest's
default `prepend` import mode puts `tests/web/` on `sys.path`, which is what makes the
plain `from tbrowser_corpus import ...` in those modules resolve.

**Why the dossiers are built here rather than copied from a fixture directory.** The
assertions in these modules are about arithmetic and about grammar, and both need a corpus
whose hub arithmetic is known EXACTLY -- "present people share exactly one hub" is the
input that makes the singular/plural claim testable at all, and no committed fixture
directory has that shape for a chosen pair. Every value below is a plain literal, and the
things being graded (`graph_view.graph_summary`, `digest`'s citation numbering, `taste`'s
display gate) are computed by modules this lane does not own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Marks a fact the taste filter excluded. Chosen to be a string that could not plausibly
#: occur anywhere else in the codebase, so a privacy sweep for it has no false positives.
WITHHELD_SENTINEL = "Zephyrine Quillfeather-Okonjo"

#: The street address in the same excluded fact. A second, differently-shaped sentinel:
#: a leak could plausibly carry the sentence's subject without its number, or vice versa.
WITHHELD_ADDRESS = "8827 Marchpane Row"

#: A fact that is NOT excluded but is undisplayable under R12's source-kind whitelist.
#: `fec` is in `SourceKind` and outside `taste.DISPLAYABLE_KINDS`.
UNDISPLAYABLE_SENTINEL = "Thornbury Civic Alliance"

#: A fact that is not excluded and comes from a displayable kind, but sits under R12's
#: confidence floor of 0.7.
LOW_CONFIDENCE_SENTINEL = "gantry crane timetable"


def _provenance(
    doc_id: str,
    *,
    kind: str = "self_page",
    quote: str,
    confidence: float = 0.9,
    published: str = "2026-05-01",
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "url": f"https://example.com/{doc_id}",
        "source_kind": kind,
        "quote": quote,
        "published_at": published,
        "retrieved_at": "2026-06-01T12:00:00+00:00",
        "confidence": confidence,
    }


def _fact(
    fact_id: str,
    text: str,
    *,
    category: str = "current_work",
    doc_id: str,
    kind: str = "self_page",
    quote: str | None = None,
    confidence: float = 0.9,
    published: str = "2026-05-01",
    excluded: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "text": text,
        "category": category,
        "provenance": _provenance(
            doc_id, kind=kind, quote=quote or text, confidence=confidence, published=published
        ),
        "excluded": excluded,
        "exclusion_reason": reason,
    }


def _hub(hub_id: str, label: str, hub_type: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "hub_id": hub_id,
        "label": label,
        "type": hub_type,
        "recency": 1.0,
        "evidence_fact_ids": evidence,
    }


def _dossier(
    person_id: str,
    name: str,
    details: list[str],
    facts: list[dict[str, Any]],
    hubs: list[dict[str, Any]],
    *,
    status: str = "resolved",
    confidence: float = 0.93,
) -> dict[str, Any]:
    return {
        "person": {"person_id": person_id, "name": name, "details": details},
        "resolution": {
            "person_id": person_id,
            "status": status,
            "strong_keys": {"github": person_id} if status == "resolved" else {},
            "accepted_doc_ids": sorted({f["provenance"]["doc_id"] for f in facts}),
            "rejected": [],
            "confidence": confidence,
        },
        "facts": facts,
        "hubs": hubs,
        "built_at": "2026-06-01T12:00:00+00:00",
        "schema_version": 1,
    }


def build_corpus(destination: Path) -> Path:
    """Write a five-person corpus with a deliberately known hub topology.

    Shared-hub map (a hub is "shared" only when two or more of the people PRESENT carry it):

        harlow-vane   : company:pellmell-works, topic:queue-theory
        indigo-marsh   : company:pellmell-works
        juniper-crane  : topic:queue-theory
        kestrel-dow    : company:orrery-freight            (shares nothing with anyone)
        lumen-tack     : (unresolved, no facts, no hubs)

    So the pair (harlow, indigo) shares EXACTLY ONE hub, and the trio
    (harlow, indigo, juniper) shares exactly TWO. That is the whole point: the singular
    and plural cases are both reachable with a one-line change to the presence set.
    """
    destination.mkdir(parents=True, exist_ok=True)

    harlow = _dossier(
        "harlow-vane",
        "Harlow Vane",
        ["chief engineer, Pellmell Works", "Ashgrove"],
        [
            _fact(
                "harlow-f1",
                "Chief engineer at Pellmell Works, where they run the scheduling group.",
                doc_id="doc-harlow-1",
                quote="I am the chief engineer at Pellmell Works and I run the scheduling group",
            ),
            _fact(
                "harlow-f2",
                "Published a primer on queue theory for people who schedule freight.",
                category="recent_activity",
                doc_id="doc-harlow-2",
                kind="hn",
                quote="I published a primer on queue theory for people who schedule freight",
            ),
            # --- R11: excluded. Must never reach a host-facing page. ---
            #
            # Every sentinel below is deliberately the MOST ATTRACTIVE candidate the digest
            # has: `recent_activity` (the primary Lately pool), the freshest publication
            # dates on the person, and -- for two of them -- a `NON_OBVIOUS_KINDS` source so
            # they compete for "Not on the first page" as well. The first version of this
            # fixture used `interest`/`affiliation` and older dates, and a sabotage run that
            # disabled `taste.is_displayable` outright moved only ONE assertion: R7's caps
            # were keeping the sentinels off the page, not the display gate. A privacy test
            # whose sentinel could never have been selected anyway proves nothing, so the
            # gate is now the ONLY thing standing between each of these and the page.
            _fact(
                "harlow-f4",
                f"Vane and their spouse {WITHHELD_SENTINEL} restore canal boats together.",
                category="recent_activity",
                doc_id="doc-harlow-4",
                kind="wayback",
                published="2026-05-28",
                quote=(
                    f"Vane and their spouse {WITHHELD_SENTINEL} spend weekends "
                    "restoring canal boats"
                ),
                excluded=True,
                reason="family",
            ),
            _fact(
                "harlow-f5",
                f"A building permit records a home address at {WITHHELD_ADDRESS}.",
                category="recent_activity",
                doc_id="doc-harlow-5",
                kind="hn",
                published="2026-05-27",
                quote=f"A permit for a side extension was issued for {WITHHELD_ADDRESS}",
                excluded=True,
                reason="home_or_property",
            ),
            # --- R12: not excluded, displayable kind, but UNDER the 0.7 floor ---
            _fact(
                "harlow-f7",
                f"A forum thread makes a weak claim about the {LOW_CONFIDENCE_SENTINEL}.",
                category="recent_activity",
                doc_id="doc-harlow-7",
                kind="github",
                published="2026-05-29",
                quote=f"marked low confidence: a claim about the {LOW_CONFIDENCE_SENTINEL}",
                confidence=0.55,
            ),
        ],
        [
            _hub("company:pellmell-works", "Pellmell Works", "company", ["harlow-f1"]),
            _hub("topic:queue-theory", "Queue theory", "topic", ["harlow-f2"]),
        ],
    )

    indigo = _dossier(
        "indigo-marsh",
        "Indigo Marsh",
        ["operations lead, Pellmell Works", "Ashgrove"],
        [
            _fact(
                "indigo-f1",
                "Operations lead at Pellmell Works since the depot opened.",
                doc_id="doc-indigo-1",
                quote="I have been the operations lead at Pellmell Works since the depot opened",
            ),
            _fact(
                "indigo-f2",
                "Opened the Pellmell Works dispatch rules to the drivers who work under them.",
                category="recent_activity",
                doc_id="doc-indigo-2",
                kind="search",
                quote="Pellmell Works opened its dispatch rules to its own drivers",
            ),
            # --- R12: not excluded, but undisplayable by SOURCE KIND ---
            # On Indigo rather than Harlow: Harlow's three sentinels already fill Lately's
            # cap of three, and a sabotage run showed this one being crowded out by them
            # rather than by the display gate. Split across two people, each sentinel is
            # the freshest thing competing for its own page.
            _fact(
                "indigo-f3",
                f"Named in a filing lodged by the {UNDISPLAYABLE_SENTINEL} treasurer.",
                category="recent_activity",
                doc_id="doc-indigo-3",
                kind="fec",
                published="2026-05-30",
                quote=f"a filing lodged by the treasurer of the {UNDISPLAYABLE_SENTINEL}",
                confidence=0.95,
            ),
        ],
        [_hub("company:pellmell-works", "Pellmell Works", "company", ["indigo-f1"])],
    )

    juniper = _dossier(
        "juniper-crane",
        "Juniper Crane",
        ["lecturer in operations research, Ashgrove Institute"],
        [
            _fact(
                "juniper-f1",
                "Lectures in operations research at the Ashgrove Institute.",
                doc_id="doc-juniper-1",
                quote="I lecture in operations research at the Ashgrove Institute",
            ),
            _fact(
                "juniper-f2",
                "Taught a short course on queue theory to freight schedulers.",
                category="recent_activity",
                doc_id="doc-juniper-2",
                kind="openalex",
                quote="a short course on queue theory for freight schedulers",
            ),
        ],
        [_hub("topic:queue-theory", "Queue theory", "topic", ["juniper-f2"])],
    )

    kestrel = _dossier(
        "kestrel-dow",
        "Kestrel Dow",
        ["yard manager, Orrery Freight"],
        [
            _fact(
                "kestrel-f1",
                "Yard manager at Orrery Freight, running the night shift.",
                doc_id="doc-kestrel-1",
                quote="I manage the yard at Orrery Freight and I run the night shift",
            )
        ],
        [_hub("company:orrery-freight", "Orrery Freight", "company", ["kestrel-f1"])],
    )

    lumen = _dossier(
        "lumen-tack",
        "Lumen Tack",
        ["stonemason or systems engineer, unclear"],
        [],
        [],
        status="unresolved",
        confidence=0.24,
    )

    for dossier in (harlow, indigo, juniper, kestrel, lumen):
        path = destination / f"{dossier['person']['person_id']}.json"
        path.write_text(json.dumps(dossier, indent=1), encoding="utf-8")
    return destination


#: Everything a host-facing page must never print, keyed by the rule that withholds it.
#: The value is the exact substring a sweep searches for.
WITHHELD_STRINGS: dict[str, str] = {
    "R11 family (excluded fact text and quote)": WITHHELD_SENTINEL,
    "R11 home_or_property (excluded fact text and quote)": WITHHELD_ADDRESS,
    "R12 source kind not in DISPLAYABLE_KINDS": UNDISPLAYABLE_SENTINEL,
    "R12 confidence below CONFIDENCE_FLOOR": LOW_CONFIDENCE_SENTINEL,
}
