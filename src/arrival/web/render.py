"""Server-rendered HTML: the Jinja environment, and the view models the templates read.

SPEC's non-goals pin "no design system, no JS framework, no mobile layout. Server-rendered
HTML", so the templates are plain HTML with a few inline rules and the only scripted
affordance on the page is `<details>`, which the browser implements.

**Every arithmetic and selection decision has already been made upstream.** `make_digest`
(T-7) decided what is shown; `graph.match` (T-5) decided the score and its components;
`taste.is_displayable` (T-4) decided what may reach a screen. This module renders those
answers and derives exactly one thing of its own: the mapping from a document to its
number in the "Why we know this" list.

**Source numbering — a decision worth stating.** `Digest.sources` is deduped by `doc_id`
"in first-use order" as T-7 assembles the page's material: Who line, then Lately, then the
non-obvious find, then the evidence behind each Meet row, then a templated opener's quoted
fact. That is NOT R7's page order, which puts Meet second. Two options existed: renumber
the list in page order, or cite by the position `Digest.sources` already assigns. This
module cites by `Digest.sources` position, because the contract says that list is
"NUMBERED IN ORDER" and a rendered `[3]` that does not index `sources[2]` makes the digest
and its own data model disagree. The visible consequence is that a Meet row can carry a
higher citation number than a Lately bullet below it; footnote numbering in a document
whose sections are read out of order does the same thing, and it is the honest half of the
trade.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from arrival.contracts import Digest, Dossier, Fact, Match
from arrival.digest import who_line_for
from arrival.taste import CONFIDENCE_FLOOR, DISPLAYABLE_KINDS, is_displayable

__all__ = [
    "TEMPLATE_DIR",
    "debug_view",
    "digest_view",
    "environment",
    "render",
    "withholding_reason",
]

#: Anchored on this file, never on the working directory — `uvicorn` is started from
#: wherever an operator happens to be standing.
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_ENVIRONMENT: Environment | None = None


def _fmt_number(value: float) -> str:
    """A score component, to four decimals.

    Four rather than two on purpose: R10 asks for the weight to be VISIBLE, and an IDF of
    0.5108 rounded to "0.51" has already lost the digits that distinguish two hubs whose
    membership differs by one person.
    """
    return f"{float(value):.4f}"


def _fmt_score(value: float) -> str:
    """A 0-100 match score. Integral by contract, so it is rendered as one."""
    return f"{float(value):.0f}"


def _fmt_day(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return "undated"
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    return value.isoformat()


def environment() -> Environment:
    """The process-wide Jinja environment, built once.

    Autoescaping is on for every template. It is not decoration: every string on these
    pages is third-party text pulled out of a fetched document, and the digest exists to
    quote it verbatim.
    """
    global _ENVIRONMENT
    if _ENVIRONMENT is None:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(default_for_string=True, default=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["number"] = _fmt_number
        env.filters["score"] = _fmt_score
        env.filters["day"] = _fmt_day
        _ENVIRONMENT = env
    return _ENVIRONMENT


def render(template_name: str, **context: Any) -> str:
    return environment().get_template(template_name).render(**context)


# --------------------------------------------------------------------------- the digest


def _citations(facts: list[Fact], numbers: dict[str, int]) -> list[int]:
    """Source numbers for `facts`, deduped, in order, skipping anything uncited.

    `numbers.get` rather than `numbers[...]` is the load-bearing part: a fact whose document
    is not in `Digest.sources` is a fact T-7 did NOT put on the page, and this module will
    not smuggle a citation to it back on.
    """
    seen: set[int] = set()
    out: list[int] = []
    for fact in facts:
        number = numbers.get(fact.provenance.doc_id)
        if number is None or number in seen:
            continue
        seen.add(number)
        out.append(number)
    return out


def _hub_evidence(dossier: Dossier, row: Match, numbers: dict[str, int]) -> list[int]:
    """Citations for the arriving person's facts behind a Meet row's shared hubs.

    `HubContribution.hub` is the ARRIVING person's Hub by contract, so its
    `evidence_fact_ids` resolve in this dossier. `is_displayable` is applied here for the
    same reason `digest.py` applies it: `graph.py` deliberately does not filter hubs,
    because matching is not display, so a hub whose evidence was taste-excluded can
    legitimately score a match and must still never be cited.
    """
    by_id = {f.fact_id: f for f in dossier.facts}
    evidence = [
        by_id[fact_id]
        for contribution in row.contributions
        for fact_id in contribution.hub.evidence_fact_ids
        if fact_id in by_id and is_displayable(by_id[fact_id])
    ]
    return _citations(evidence, numbers)


def digest_view(digest: Digest, dossier: Dossier | None) -> dict[str, Any]:
    """Everything `digest.html` needs, computed here so the template stays declarative."""
    numbers = {provenance.doc_id: n for n, provenance in enumerate(digest.sources, start=1)}

    who_facts: list[Fact] = []
    if dossier is not None:
        # Re-derived rather than stored: `who_line_for` is pure and deterministic, and
        # `Digest` carries the SENTENCE, not the facts it was built from. Calling T-7's own
        # function is the only way to cite the Who line without re-implementing its
        # selection rule in a second place.
        _line, who_facts = who_line_for(dossier)

    meet_rows = [
        {
            "match": row,
            "citations": _hub_evidence(dossier, row, numbers) if dossier is not None else [],
        }
        for row in digest.meet
    ]

    return {
        "digest": digest,
        "sources": list(enumerate(digest.sources, start=1)),
        "who_citations": _citations(who_facts, numbers),
        "meet_rows": meet_rows,
        "lately_rows": [
            {"fact": fact, "citations": _citations([fact], numbers)} for fact in digest.lately
        ],
        "non_obvious_citations": (
            _citations([digest.non_obvious], numbers) if digest.non_obvious else []
        ),
    }


# --------------------------------------------------------------------------- /debug


def withholding_reason(fact: Fact) -> str | None:
    """Why this fact is not on a host-facing page, or `None` when it is.

    R12's three clauses are independent and the operator view has to say WHICH one bit.
    Reporting "excluded" for every hidden fact would be wrong on two of the frozen corpus's
    own facts: one is kept at confidence 0.55 and blocked only by the display floor, the
    other is kept at confidence 0.92 and blocked only because its source kind is `fec`.
    Those two exist to prove the gates are independent; a debug view that collapsed them
    would be showing the operator a line that is not where the line actually is.
    """
    if fact.excluded:
        return fact.exclusion_reason or "excluded"
    if fact.provenance.confidence < CONFIDENCE_FLOOR:
        return "low_confidence"
    if fact.provenance.source_kind not in DISPLAYABLE_KINDS:
        return "source_kind_not_displayable"
    return None


def debug_view(dossier: Dossier) -> dict[str, Any]:
    """Everything `debug.html` needs: R15's full dossier, withheld material included."""
    rows = [
        {"fact": fact, "reason": withholding_reason(fact), "shown": is_displayable(fact)}
        for fact in dossier.facts
    ]
    return {
        "dossier": dossier,
        "person": dossier.person,
        "resolution": dossier.resolution,
        "fact_rows": rows,
        "withheld_rows": [row for row in rows if not row["shown"]],
        "hubs": dossier.hubs,
        "confidence_floor": CONFIDENCE_FLOOR,
    }
