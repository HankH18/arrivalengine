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

**A citation marker backs the sentence it hangs off — T-055, and the rule that settles it
for both surfaces.** The measured line, on `runa-okonkwo` with all five present: the Mira
Hollowell Meet row scores 0.0, reads "Nothing in common on the record yet.", and carried a
`[1]` marker anyway — because the row still holds `city:austin` and `topic:remote-work`,
whose IDF clamped to zero on a corpus where all five people share them. A host with ninety
seconds reads a sentence that says nothing is shared and a footnote offering to prove it.

The evidence is real, so this is not a correctness leak; it is a question about which
surface owes which answer, and the page has three:

* **the spoken `why` and its `<sup>` markers.** R18 material. `graph._why` already decided
  what this sentence may claim — it names only hubs with `contribution > 0`, "citing a hub
  the clamp zeroed would claim credit for a connection worth nothing" — so the markers
  follow the same predicate (`_counted_hubs`). A sentence that claims no shared hub cites
  no document for one.
* **the R10 reasoning table**, behind `<details>`. It shows the ARITHMETIC, so it keeps
  EVERY shared hub including the zeroes; `digest.html` says so at its own `data-reasoning`
  block and that comment stands unchanged. Hiding a zero row would make the sum stop
  adding up.
* **"Why we know this"**, the numbered evidence list. An audit surface like the table, so
  it also stays complete: `_page_claims` still passes `row.contributions` in full, and the
  Austin quote is still rendered under source [1] reading "supports Meet: Sil Vantorre, Jem
  Arrowood, Mira Hollowell". That is what keeps the table's zero rows CHECKABLE from the
  page — the objection against suppressing anything — and it is why this needed no change
  to `digest.html`.

One rule, three surfaces: **each cites exactly what it claims.** The zero-score row is not
the only thing it fixes. Jem Arrowood's row reads "Both deep in developer-tools
go-to-market." and cited `[3]` AND `[1]`, where [1] is the self-page whose quotes are about
Austin and remote work — the document-level twin of the defect `digest._sources` was written
to kill. It now cites [3] alone, the document that actually carries the sentence's evidence.

**`/debug` asks `resolve` what a verdict turned on; it does not read the label.** The
rejected-candidates table used to print `verdict.disambiguator` straight out of the
contract, which is the model's own free-text word — the word T-031 spent two tickets
refusing to let decide anything, because a label is free to the model while an evidence
span is checked against the document. `_rejected_row` calls `resolve.verdict_attribute`
and `resolve.verdict_attributes` here, in the view model, rather than from the template:
the rule for what a verdict corroborates belongs to the resolver, and a Jinja template
reaching into `arrival.resolve` would put that rule in the layer whose job is to be
declarative. The raw label is still rendered beside the resolved attribute, because on the
one page that exists to explain the system's reasoning, the two disagreeing IS the
reasoning — see `_rejected_row`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from arrival.contracts import (
    Digest,
    Dossier,
    Fact,
    HubContribution,
    Match,
    PersonRef,
    Verdict,
)
from arrival.digest import OPENER_TEMPLATE, opener_hook_candidates, who_line_for
from arrival.resolve import attribute_family, verdict_attribute, verdict_attributes
from arrival.taste import CONFIDENCE_FLOOR, DISPLAYABLE_KINDS, is_displayable

__all__ = [
    "SECTION_LABELS",
    "TEMPLATE_DIR",
    "debug_view",
    "digest_view",
    "environment",
    "render",
    "withholding_reason",
]

#: How each R7 section is named to a host reading the evidence list, keyed by the section's
#: own `id` on the page. A Meet row appends the other person's name to its label, because
#: "Meet" alone does not tell a host WHICH row a quote is under.
SECTION_LABELS: dict[str, str] = {
    "who": "Who",
    "meet": "Meet",
    "lately": "Lately",
    "not-on-the-first-page": "Not on the first page",
    "say-out-loud": "Say out loud",
}

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


def _hub_evidence_facts(
    dossier: Dossier, contributions: Sequence[HubContribution]
) -> list[Fact]:
    """The arriving person's displayable facts behind these shared-hub contributions.

    `HubContribution.hub` is the ARRIVING person's Hub by contract, so its
    `evidence_fact_ids` resolve in this dossier. `is_displayable` is applied here for the
    same reason `digest.py` applies it: `graph.py` deliberately does not filter hubs,
    because matching is not display, so a hub whose evidence was taste-excluded can
    legitimately score a match and must still never be cited — nor, now, quoted.

    Takes the contributions rather than the `Match` because the page asks two different
    questions of them — see `_counted_hubs`.
    """
    by_id = {f.fact_id: f for f in dossier.facts}
    return [
        by_id[fact_id]
        for contribution in contributions
        for fact_id in contribution.hub.evidence_fact_ids
        if fact_id in by_id and is_displayable(by_id[fact_id])
    ]


def _counted_hubs(row: Match) -> list[HubContribution]:
    """The shared hubs the row's `why` sentence was actually built from.

    `graph._why` opens with `[c for c in components if c.contribution > 0]` and returns
    `_WHY_NOTHING_SHARED` — "Nothing in common on the record yet." — when that list is
    empty, on the stated ground that "citing a hub the clamp zeroed would claim credit for
    a connection worth nothing". This is that same predicate, applied to the CITATION
    MARKERS that hang off the sentence, and it is a predicate rather than a restatement of
    the matcher's rule: which of the counted hubs `_why` then names (at most two) stays
    graph.py's business, exactly as `digest._speakable_match` leaves it there.

    See the module docstring for the product judgement this implements.
    """
    return [contribution for contribution in row.contributions if contribution.contribution > 0]


def _hub_evidence(dossier: Dossier, row: Match, numbers: dict[str, int]) -> list[int]:
    """Citations for the facts behind the hubs a Meet row's `why` actually claims."""
    return _citations(_hub_evidence_facts(dossier, _counted_hubs(row)), numbers)


def _opener_quoted_fact(digest: Digest, dossier: Dossier) -> Fact | None:
    """The fact whose own sentence the "Say out loud" line quotes, or `None`.

    `Digest` records the LINE, not the fact behind it, and T-7 appends that fact to the
    material `sources` is built from — so on the frozen corpus the last entry in the list is
    routinely a document nothing else on the page leans on. Without this the evidence under
    that entry would be empty on all five graded people.

    The test is exact rather than fuzzy: `digest._fallback_opener` returns
    `OPENER_TEMPLATE.format(text=hook.text.strip())` verbatim, so a candidate reproducing
    `say_out_loud` character for character IS the quoted fact. A model-written opener is a
    paraphrase and matches nothing, which is the right answer — it cites nothing. The one
    remaining way to match is a model that emitted the template's exact sentence, and in
    that case the fact really is quoted verbatim on the page, so attributing it is still
    correct.
    """
    _line, who_facts = who_line_for(dossier)
    for candidate in opener_hook_candidates(dossier, exclude=who_facts):
        if OPENER_TEMPLATE.format(text=candidate.text.strip()) == digest.say_out_loud:
            return candidate
    return None


def _page_claims(
    digest: Digest, dossier: Dossier | None, who_facts: Sequence[Fact]
) -> list[tuple[str, str, str, list[Fact]]]:
    """`(section id, section label, row detail, backing facts)` for every claim shown.

    In READING order — R7's section order — not `Digest.sources`' first-use order. Numbering
    is one question and the order quotes appear UNDER a document is another: a host who
    followed `[1]` out of the Meet section is scanning that entry for the row they came from,
    and R7's own order is the order they just read.

    A Meet row's `detail` is the other person's name, because "Meet" alone does not say WHICH
    row a quote is under, and Meet rows are the ones with no inline quote of their own.

    The five categories are exactly the five things `make_digest` puts into the material it
    builds `sources` from — Who line, Meet hub evidence, Lately, the non-obvious find, and
    the templated opener's quoted fact. Miss one and a quote silently vanishes from the
    evidence list while its document keeps its number.
    """
    claims: list[tuple[str, str, str, list[Fact]]] = []
    if who_facts:
        claims.append(("who", SECTION_LABELS["who"], "", list(who_facts)))
    if dossier is not None:
        for row in digest.meet:
            claims.append(
                (
                    "meet",
                    SECTION_LABELS["meet"],
                    row.other.name,
                    # EVERY shared hub, zeroes included — the audit surfaces stay complete.
                    # See the module docstring: this is the half of the T-055 rule that is
                    # not the citation marker.
                    _hub_evidence_facts(dossier, row.contributions),
                )
            )
    for fact in digest.lately:
        claims.append(("lately", SECTION_LABELS["lately"], "", [fact]))
    if digest.non_obvious is not None:
        key = "not-on-the-first-page"
        claims.append((key, SECTION_LABELS[key], "", [digest.non_obvious]))
    if dossier is not None:
        quoted = _opener_quoted_fact(digest, dossier)
        if quoted is not None:
            claims.append(("say-out-loud", SECTION_LABELS["say-out-loud"], "", [quoted]))
    return claims


def _backs(claims: Sequence[tuple[str, str]]) -> str:
    """"Meet: Sil Vantorre, Jem Arrowood, Mira Hollowell" — the section named once.

    A shared hub like `city:austin` is evidence for every Meet row that shares it, and
    repeating "Meet:" three times in one line is noise a host has to read past.
    """
    grouped: dict[str, list[str]] = {}
    for section_label, detail in claims:
        details = grouped.setdefault(section_label, [])
        if detail and detail not in details:
            details.append(detail)
    return "; ".join(
        f"{label}: {', '.join(details)}" if details else label
        for label, details in grouped.items()
    )


def _source_evidence(
    claims: Sequence[tuple[str, str, str, list[Fact]]], numbers: dict[str, int]
) -> dict[int, list[dict[str, Any]]]:
    """Per source number, the quotes from that document that back something shown here.

    This is the whole of T-040. `Provenance` carries a FACT's quote and a FACT's confidence,
    while `Digest.sources` holds one entry per `doc_id` — four of the shown facts on
    `runa-okonkwo` come out of document `35b4e2600c8a6ea6` alone. A template printing
    `source.quote` therefore showed three of those four claims an excerpt that does not
    support them, and printed one fact's confidence as though it described the document.

    So the document keeps its single numbered slot (the acceptance suite pins the dedupe, and
    a `[3]` that did not index `sources[2]` would make the page uncheckable) and the QUOTES
    inside it become a list, each next to its own confidence and the claim it supports.

    Two gates, both deliberate:

    * `is_displayable` on every fact, with no exception for the categories T-7 already
      filtered. This is the last code between a taste-excluded sentence and a host-facing
      page, and `graph.py` does not filter hubs — matching is not display — so a withheld
      fact can legitimately reach a Meet row's evidence and must still never be quoted.
    * `numbers.get`, never a dossier lookup. A document T-7 kept off `Digest.sources` gets no
      slot, and this module will not open one for it.
    """
    evidence: dict[int, list[dict[str, Any]]] = {}
    seen: dict[int, dict[str, dict[str, Any]]] = {}
    for section, section_label, detail, facts in claims:
        label = f"{section_label}: {detail}" if detail else section_label
        for fact in facts:
            if not is_displayable(fact):
                continue
            number = numbers.get(fact.provenance.doc_id)
            if number is None:
                continue
            entries = evidence.setdefault(number, [])
            by_fact = seen.setdefault(number, {})
            entry = by_fact.get(fact.fact_id)
            if entry is None:
                entry = {
                    "fact_id": fact.fact_id,
                    "quote": fact.provenance.quote,
                    "confidence": fact.provenance.confidence,
                    "sections": [section],
                    "labels": [label],
                    "claims": [(section_label, detail)],
                }
                by_fact[fact.fact_id] = entry
                entries.append(entry)
                continue
            # One fact can back several claims — `city:austin` is evidence for every Meet
            # row that shares Austin. It stays ONE quote and collects the claims, rather
            # than repeating the same sentence three times under one document.
            if section not in entry["sections"]:
                entry["sections"].append(section)
            if label not in entry["labels"]:
                entry["labels"].append(label)
                entry["claims"].append((section_label, detail))
    for entries in evidence.values():
        for entry in entries:
            entry["backs"] = _backs(entry.pop("claims"))
    return evidence


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

    # R12 re-applied on the two slots nothing between `make_digest` and here re-checks.
    # `Digest.lately` is "displayable only" and `pick_non_obvious` gates its answer, so on
    # every digest this project produces these two filters are no-ops. They are here because
    # this module is the LAST code before HTML: a digest that violated the contract upstream
    # would otherwise have `render.py` publish an R11 sentence and its source excerpt
    # verbatim, and that is the one failure this product cannot absorb. Measured while
    # sabotage-testing T-040: with a taste-excluded fact placed in `lately`, the page
    # rendered both its text and its quote.
    lately = [fact for fact in digest.lately if is_displayable(fact)]
    non_obvious = (
        digest.non_obvious
        if digest.non_obvious is not None and is_displayable(digest.non_obvious)
        else None
    )

    return {
        "digest": digest,
        "sources": list(enumerate(digest.sources, start=1)),
        "source_evidence": _source_evidence(_page_claims(digest, dossier, who_facts), numbers),
        "who_citations": _citations(who_facts, numbers),
        "meet_rows": meet_rows,
        "non_obvious": non_obvious,
        "lately_rows": [
            {"fact": fact, "citations": _citations([fact], numbers)} for fact in lately
        ],
        "non_obvious_citations": (
            _citations([non_obvious], numbers) if non_obvious is not None else []
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


def _rejected_row(person: PersonRef, verdict: Verdict) -> dict[str, Any]:
    """One row of `/debug`'s rejected-candidates table: what counted, and what was claimed.

    `resolve.verdict_attribute` is the headline because the column asks a singular question
    ("decided by") and that function is the resolver's own one-word answer to it: it asks
    the EVIDENCE SPAN which of the person's own details it names, and consults
    `verdict.disambiguator` only when the span names none. `verdict_attributes` supplies
    `also`, the attributes the one-word summary has to drop — a span quoting the employer
    AND the city corroborates two, and two is the number Decision 4's second arm counts, so
    on an unresolved person (where `Resolution.rejected` holds EVERY verdict) the set is the
    arithmetic the operator is actually auditing.

    `disputed` compares the resolved attribute against `attribute_family(disambiguator)`
    rather than against the raw label, so pure spelling — `company` for `employer` — is not
    reported as a disagreement. What is left is the case worth a marker: the resolver did
    not take the model's word, either because the span named a different detail or because
    the label was off-contract and got bucketed.
    """
    attributes = sorted(verdict_attributes(person, verdict))
    attribute = verdict_attribute(person, verdict)
    return {
        "verdict": verdict,
        "attribute": attribute,
        "attributes": attributes,
        "also": [name for name in attributes if name != attribute],
        "disputed": attribute_family(verdict.disambiguator) != attribute,
    }


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
        "rejected_rows": [
            _rejected_row(dossier.person, verdict) for verdict in dossier.resolution.rejected
        ],
        "hubs": dossier.hubs,
        "confidence_floor": CONFIDENCE_FLOOR,
    }
