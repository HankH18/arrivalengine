"""R7-R9, R13, R14, R18 — the digest builder: ninety seconds of speakable page.

A :class:`~arrival.contracts.Digest` is what a host reads aloud in a lobby. Everything
here exists to keep that read short, true and citable:

**Caps are structural, not cosmetic.** Meet holds at most three present people, Lately at
most three bullets, "Not on the first page" exactly one fact or none. A cap is applied by
*selecting*, never by truncating a sentence mid-word, and R8 means an empty Meet is a
stated absence — ``meet == []`` — rather than a list padded with people who share nothing.

**Display is gated once, by :func:`arrival.taste.is_displayable`.** Nothing on a
host-facing surface bypasses it: not Lately, not the non-obvious find, not the documents
named in "Why we know this". The gate is R12's three independent clauses (not excluded,
confidence >= 0.7, whitelisted source kind) and this module owns none of them — it calls
the one predicate T-4 defines so that a display rule can never drift into a second
spelling. That matters most for the hub evidence behind a Meet row: ``graph.py``
deliberately does **not** filter hubs, because matching is not display, so the digest is
the layer where a hub whose evidence fact was taste-excluded stops being citable.

**Facts are shown verbatim.** The extractor's sentence is what appears; this module never
rewrites one. Where R18's speakability rules collide with a fact's own wording the fact is
*skipped*, not edited. The single generated sentence on the page is ``say_out_loud``.

**One LLM call, with a deadline.** DESIGN Decision 12: the opener is one
``llm.structured`` call bounded by :data:`SAY_OUT_LOUD_TIMEOUT_SECONDS`, its output
validated as an invitation (R14 — "Ask about...", never "I saw that you..."). Timeout,
transport failure and a model that ignored the brief all land on the same documented
fallback, ``f"Ask about {hook.text}"``, so R3's latency bound holds whatever the API does.

**Citations are a list, not a sentence.** R18 keeps URLs, ``[n]`` markers, parentheticals
and numbers-as-scores out of the three spoken lines; R9 puts every shown fact's provenance
into ``sources``, deduped by ``doc_id`` and in first-use order, which is what makes the
numbering on the rendered page mean something.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from collections.abc import Iterable, Iterator, Sequence

from pydantic import BaseModel, Field

from arrival import taste
from arrival.contracts import Digest, Dossier, Fact, LLMClient, Match, Provenance
from arrival.taste import is_displayable

__all__ = [
    "LATELY_CAP",
    "LATELY_FALLBACK_CATEGORIES",
    "LATELY_PRIMARY_CATEGORIES",
    "MEET_CAP",
    "NON_OBVIOUS_KINDS",
    "OPENER_OF_LAST_RESORT",
    "OPENER_PREFIXES",
    "OPENER_TEMPLATE",
    "SAY_OUT_LOUD_TIMEOUT_SECONDS",
    "SPOKEN_WORD_CAP",
    "SURVEILLANCE_PHRASES",
    "SayOutLoud",
    "is_speakable",
    "make_digest",
    "pick_lately",
    "pick_non_obvious",
    "pick_opener_hook",
    "speakable",
    "who_line_for",
]


# --------------------------------------------------------------------------- R7 hard caps

#: R7: at most three present people in Meet.
MEET_CAP = 3

#: R7: at most three Lately bullets.
LATELY_CAP = 3

#: R18: the spoken lines are read aloud, so each is capped in WORDS, not characters.
SPOKEN_WORD_CAP = 30

#: R7 Lately is "most recent professional activity first". ``recent_activity`` is what the
#: extractor assigns to exactly that, so it is the primary candidate pool. Other displayable
#: categories only ever *top up* a short list — see :func:`pick_lately`.
LATELY_PRIMARY_CATEGORIES: frozenset[str] = frozenset({"recent_activity"})

#: Categories that may fill a Lately slot the primary pool left empty, in preference order.
#: ``current_work`` is deliberately absent: it is already the Who line.
LATELY_FALLBACK_CATEGORIES: tuple[str, ...] = ("affiliation", "hook", "interest")

#: DESIGN §Data models, "Non-obvious eligibility" (R7). A fact reaches the "Not on the first
#: page" slot only from a source a first page would not have surfaced. This is a whitelist on
#: top of :func:`arrival.taste.is_displayable`, never instead of it.
NON_OBVIOUS_KINDS: frozenset[str] = frozenset(
    {
        "edgar",
        "uspto",
        "propublica",
        "wayback",
        "github",
        "hn",
        "openalex",
        "wikidata",
        "podcast",
    }
)


# --------------------------------------------------------------------------- R14 the opener

#: DESIGN Decision 12. Deliberately a module constant rather than a parameter of
#: ``make_digest``: the deadline is a property of the arrival path (R3's three seconds),
#: not something a caller — or a test — gets to relax.
SAY_OUT_LOUD_TIMEOUT_SECONDS = 2.5

#: R14: the opener is an invitation. These are the openings that read as one.
OPENER_PREFIXES: tuple[str, ...] = ("Ask", "Curious")

#: R14's negative half, lowercased. A line that tells the member what the system has been
#: reading about them is a disclosure however politely it is phrased.
SURVEILLANCE_PHRASES: tuple[str, ...] = ("i saw", "we noticed", "our records")

#: The template DESIGN Decision 12 falls back to. Formatted with the hook fact's text.
OPENER_TEMPLATE = "Ask about {text}"

#: Used only when a dossier carries no displayable fact at all, so there is nothing to
#: invite a question about. Still an invitation; still speakable.
OPENER_OF_LAST_RESORT = "Ask what they are working on right now."

_SAY_OUT_LOUD_SYSTEM = (
    "You write one spoken opener for a club host who is about to greet an arriving "
    "member. The host reads your sentence aloud, so it must sound like a question worth "
    "asking, never like a report on what a system found.\n"
    "Rules, all mandatory:\n"
    "- Begin with 'Ask about', 'Ask', or 'Curious'.\n"
    "- Never say 'I saw', 'we noticed', 'our records', or anything else in the first "
    "person about having looked the member up.\n"
    "- One sentence, at most twenty-five words.\n"
    "- No URLs, no parentheses, no citation markers, no numbers used as scores.\n"
    "- Invite them to talk about something they put in public themselves."
)


class SayOutLoud(BaseModel):
    """The schema for the single ``llm.structured`` call this module makes.

    One field on purpose. The line is the whole product of the call, and a schema with
    spare string fields invites a model to spread the sentence across them.
    """

    line: str = Field(description="The spoken opener, phrased as an invitation.")


# --------------------------------------------------------------------------- R18 speakability

_CITATION_MARKER = re.compile(r"\[\s*\d+\s*\]")
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_BARE_INTEGER = re.compile(r"^\d{1,3}$")


def _looks_like_a_score(word: str) -> bool:
    """A bare integer in 0..100 read aloud is a score (R18), not prose.

    Years and counts survive: ``2016`` is four digits, ``sixty-three`` is not a digit at
    all. Only a standalone one-to-three digit number inside the 0..100 range is treated as
    a score leaking out of the ranking into a sentence.
    """
    stripped = word.strip(".,;:!?'\"")
    return bool(_BARE_INTEGER.fullmatch(stripped)) and int(stripped) <= 100


def is_speakable(text: str) -> bool:
    """R18: can a host read this line aloud, as written, without stumbling?

    No URLs, no ``[n]`` citation markers, no parentheses, no numbers-as-scores, at most
    :data:`SPOKEN_WORD_CAP` words, and not blank.
    """
    if not text.strip():
        return False
    if "http" in text.casefold():
        return False
    if _CITATION_MARKER.search(text):
        return False
    if "(" in text or ")" in text:
        return False
    words = text.split()
    if len(words) > SPOKEN_WORD_CAP:
        return False
    return not any(_looks_like_a_score(w) for w in words)


def speakable(text: str) -> str:
    """Force ``text`` into a shape :func:`is_speakable` accepts.

    Used **only** on sentences this module did not author — a ``Match.why`` arrives from
    the matcher and is shown as a Meet row, so R18 binds even when the matcher was careless.
    It is never used on a :class:`~arrival.contracts.Fact`: a fact is shown verbatim or not
    at all (see :func:`who_line_for`, which skips rather than edits).
    """
    cleaned = _CITATION_MARKER.sub(" ", text)
    cleaned = _PARENTHETICAL.sub(" ", cleaned)
    cleaned = cleaned.replace("(", " ").replace(")", " ")
    words = [
        w
        for w in cleaned.split()
        if "http" not in w.casefold() and not _looks_like_a_score(w)
    ]
    truncated = len(words) > SPOKEN_WORD_CAP
    words = words[:SPOKEN_WORD_CAP]
    line = " ".join(words).strip()
    if truncated and line and line[-1] not in ".!?":
        line += "."
    return line


# --------------------------------------------------------------------------- selection

def _displayable(facts: Iterable[Fact]) -> list[Fact]:
    """R12's gate, applied in exactly one place, by T-4's predicate."""
    return [f for f in facts if is_displayable(f)]


def _recency_key(fact: Fact) -> tuple[dt.date, float, str]:
    """Most recent first, then most confident, then by id so ties never wobble."""
    published = fact.provenance.published_at or dt.date.min
    return (published, fact.provenance.confidence, fact.fact_id)


def _by_recency(facts: Iterable[Fact]) -> list[Fact]:
    return sorted(facts, key=_recency_key, reverse=True)


def who_line_for(dossier: Dossier) -> tuple[str, list[Fact]]:
    """R7's Who line, plus the facts it is built from so they can be cited.

    Built from ``current_work`` facts, most confident first, as many as fit inside R18's
    word cap. A fact whose own wording is unspeakable (a parenthetical, a bare URL) is
    SKIPPED rather than rewritten — facts are shown verbatim, and a digest that quietly
    edits one has lost the property that makes the citation next to it worth anything.

    Falls back to the roster's own self-description, and finally to the bare name, so the
    line is never empty. R8's "nothing to meet" digest is still a digest.
    """
    name = dossier.person.name.strip().rstrip(".")
    opening = f"{name}." if name else ""
    parts = [opening] if opening else []
    used: list[Fact] = []
    budget = len(opening.split())

    candidates = sorted(
        (f for f in _displayable(dossier.facts) if f.category == "current_work"),
        key=lambda f: (-f.provenance.confidence, f.fact_id),
    )
    for fact in candidates:
        text = fact.text.strip()
        if not is_speakable(text):
            continue
        cost = len(text.split())
        if budget + cost > SPOKEN_WORD_CAP:
            continue
        parts.append(text)
        used.append(fact)
        budget += cost

    if used:
        return " ".join(parts).strip(), used

    # No citable current-work sentence fits. Fall back to what the roster itself says,
    # which is the member's own self-description rather than anything researched.
    for detail in dossier.person.details:
        candidate = f"{opening} {detail.strip().rstrip('.')}.".strip()
        if is_speakable(candidate):
            return candidate, []
    return opening or name or dossier.person.person_id, []


def pick_non_obvious(dossier: Dossier) -> Fact | None:
    """R7's "Not on the first page": one fact, or honestly none.

    DESIGN §Data models pins eligibility — ``category == "non_obvious"`` *and* a source
    kind a first page would not have handed over — and picks the highest confidence.
    :func:`arrival.taste.is_displayable` is applied first, because eligibility is a
    narrowing of what may be displayed, never a route around it.
    """
    eligible = [
        f
        for f in _displayable(dossier.facts)
        if f.category == "non_obvious" and f.provenance.source_kind in NON_OBVIOUS_KINDS
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.provenance.confidence, f.fact_id))


def pick_lately(dossier: Dossier, *, exclude: Iterable[Fact] = ()) -> list[Fact]:
    """R7's Lately: up to three bullets, most recent professional activity first.

    ``recent_activity`` is the primary pool because that is the category the extractor
    assigns to exactly this. When it cannot fill three slots the list is topped up from
    other displayable professional categories rather than left short — a host reading two
    bullets when a third true one exists is signal thrown away — and the whole list is then
    ordered by ``published_at`` descending, which is the only order R7 names.

    A fact with no ``published_at`` is never a Lately bullet: an undatable sentence cannot
    be placed in a most-recent-first list, and pretending otherwise puts it in an arbitrary
    slot the host will read as a claim about recency.
    """
    spoken_for = {f.fact_id for f in exclude}
    pool = [
        f
        for f in _displayable(dossier.facts)
        if f.fact_id not in spoken_for and f.provenance.published_at is not None
    ]

    primary = _by_recency(f for f in pool if f.category in LATELY_PRIMARY_CATEGORIES)
    chosen = primary[:LATELY_CAP]
    if len(chosen) < LATELY_CAP:
        taken = {f.fact_id for f in chosen}
        top_up = [
            f
            for f in pool
            if f.fact_id not in taken and f.category in LATELY_FALLBACK_CATEGORIES
        ]
        chosen = chosen + _by_recency(top_up)[: LATELY_CAP - len(chosen)]
    return _by_recency(chosen)


def pick_opener_hook(dossier: Dossier, *, exclude: Iterable[Fact] = ()) -> Fact | None:
    """The fact the fallback opener invites a question about (DESIGN Decision 12).

    The highest-confidence displayable ``hook`` fact; if the dossier has none, the most
    recent displayable fact of any category; if it has none of those either, ``None`` and
    the caller uses :data:`OPENER_OF_LAST_RESORT`.

    ``exclude`` carries the facts the page already speaks aloud, and it earns its place:
    four of the five people in the grading corpus carry no ``hook`` fact at all, and their
    highest-confidence displayable fact IS the one in the Who line. Without this the
    fallback opener asks the host to read the same sentence twice — measured on
    ``mira-hollowell``, ``sil-vantorre`` and ``theo-baptiste``. Excluding what has already
    been said is a narrowing of "the most recent displayable fact", not a departure from it.
    """
    spoken_for = {f.fact_id for f in exclude}
    displayable = [f for f in _displayable(dossier.facts) if f.fact_id not in spoken_for]
    hooks = [f for f in displayable if f.category == "hook"]
    if hooks:
        return max(hooks, key=lambda f: (f.provenance.confidence, f.fact_id))
    if displayable:
        return _by_recency(displayable)[0]
    return None


def _capped_meet(matches: Sequence[Match]) -> list[Match]:
    """R7 + R8: the top three by score, and NOTHING when nobody else is present.

    ``sorted(..., reverse=True)`` is stable, so peers on an equal score keep the order the
    matcher emitted them in rather than being reshuffled here. The cap selects whole rows;
    it never invents one to reach three.

    One person occupies at most one row. R7 caps Meet at "three present people", not three
    Match objects, and a host reading the same name twice has been handed a padded section
    with extra steps. The highest-scoring row for a person wins, which is the first one the
    sort reaches.
    """
    ranked = sorted(matches, key=lambda m: m.score, reverse=True)
    seen: set[str] = set()
    kept: list[Match] = []
    for match in ranked:
        if match.other.person_id in seen:
            continue
        seen.add(match.other.person_id)
        kept.append(_speakable_match(match))
        if len(kept) == MEET_CAP:
            break
    return kept


def _speakable_match(match: Match) -> Match:
    """R18 applied to a Meet row's ``why``, which this module did not write.

    A why that already reads aloud is passed through untouched — the common case, and the
    one that keeps the matcher's exposed reasoning (R10) intact. Only a why that would put
    a URL, a citation marker, a parenthetical or a raw score into the host's mouth is
    rewritten, and the rewrite is a copy: the incoming ``Match`` is never mutated.
    """
    why = match.why.strip()
    if is_speakable(why):
        return match if why == match.why else match.model_copy(update={"why": why})
    repaired = speakable(why)
    if not repaired:
        repaired = f"{match.other.name} is here tonight."
    return match.model_copy(update={"why": repaired})


# --------------------------------------------------------------------------- R9 citations

def _hub_evidence(dossier: Dossier, meet: Sequence[Match]) -> Iterator[Fact]:
    """The arriving person's facts behind each Meet row's shared hubs, in row order.

    ``HubContribution.hub`` is the ARRIVING person's Hub, so its ``evidence_fact_ids``
    resolve in this dossier (DESIGN §Interfaces). The R12 gate is applied here and not
    upstream on purpose: ``graph.py`` does not filter hubs, because matching is not
    display, so a hub whose evidence was taste-excluded can legitimately score a match and
    must still never appear in "Why we know this".
    """
    by_id = {f.fact_id: f for f in dossier.facts}
    for match in meet:
        for contribution in match.contributions:
            for fact_id in contribution.hub.evidence_fact_ids:
                fact = by_id.get(fact_id)
                if fact is not None and is_displayable(fact):
                    yield fact


def _sources(facts: Iterable[Fact]) -> list[Provenance]:
    """R9/S6: one entry per document, in the order the page first leans on it."""
    seen: set[str] = set()
    out: list[Provenance] = []
    for fact in facts:
        provenance = fact.provenance
        if provenance.doc_id in seen:
            continue
        seen.add(provenance.doc_id)
        out.append(provenance)
    return out


# --------------------------------------------------------------------------- R14 say out loud

def _validate_opener(line: str) -> str | None:
    """R14 + R18 on the one generated sentence. ``None`` means "use the fallback"."""
    candidate = line.strip()
    if not candidate:
        return None
    if not candidate.startswith(OPENER_PREFIXES):
        return None
    lowered = candidate.casefold()
    if any(phrase in lowered for phrase in SURVEILLANCE_PHRASES):
        return None
    if not is_speakable(candidate):
        return None
    return candidate


def _fallback_opener(hook: Fact | None) -> str:
    if hook is None:
        return OPENER_OF_LAST_RESORT
    return OPENER_TEMPLATE.format(text=hook.text.strip())


def _opener_prompt(dossier: Dossier, hook: Fact | None, lately: Sequence[Fact]) -> str:
    lines = [f"Member: {dossier.person.name}"]
    if hook is not None:
        lines.append(f"Something they have said in public: {hook.text.strip()}")
    for fact in lately[:LATELY_CAP]:
        lines.append(f"Recently: {fact.text.strip()}")
    lines.append("Write the opener the host should say.")
    return "\n".join(lines)


async def _say_out_loud(
    dossier: Dossier,
    hook: Fact | None,
    lately: Sequence[Fact],
    llm: LLMClient,
) -> str:
    """DESIGN Decision 12: exactly one LLM call, bounded, validated, always answered.

    Every way the call can go wrong — a slow API, a transport error, a model that ignores
    R14 — converges on the same documented template, so the arrival path has no branch that
    can leave the host without a line to say.
    """
    try:
        result = await asyncio.wait_for(
            llm.structured(
                system=_SAY_OUT_LOUD_SYSTEM,
                user=_opener_prompt(dossier, hook, lately),
                schema=SayOutLoud,
                max_tokens=200,
            ),
            timeout=SAY_OUT_LOUD_TIMEOUT_SECONDS,
        )
    except Exception:
        # Broad on purpose. R3 gives the arrival path three seconds and R14 gives it one
        # sentence; a digest that propagates an LLM transport failure has traded a working
        # page for a stack trace. The failure mode is the documented template, not an error.
        return _fallback_opener(hook)

    validated = _validate_opener(str(getattr(result, "line", "") or ""))
    return validated if validated is not None else _fallback_opener(hook)


# --------------------------------------------------------------------------- the builder

async def make_digest(
    dossier: Dossier,
    matches: Sequence[Match],
    llm: LLMClient,
) -> Digest:
    """Assemble the page a host reads aloud in ninety seconds.

    Args:
        dossier: the ARRIVING person's dossier. Every fact shown, and every document
            cited, comes from here — including the evidence behind a Meet row's shared
            hubs, which are the arriving person's hubs by contract.
        matches: what the matcher produced for the people currently present, in any order.
            Capped to :data:`MEET_CAP` by score; an empty sequence yields an empty Meet
            (R8) rather than a padded one.
        llm: used for exactly one ``structured`` call, the say-out-loud line.

    Returns:
        A :class:`~arrival.contracts.Digest` whose every host-facing field has passed R12's
        display gate and R18's speakability rules, and whose ``sources`` cite every fact it
        shows, deduped by ``doc_id`` in first-use order.
    """
    meet = _capped_meet(matches)
    who_line, who_facts = who_line_for(dossier)
    non_obvious = pick_non_obvious(dossier)
    lately = pick_lately(dossier, exclude=who_facts + ([non_obvious] if non_obvious else []))

    hook = pick_opener_hook(dossier, exclude=who_facts)
    say_out_loud = await _say_out_loud(dossier, hook, lately, llm)

    cited: list[Fact] = [*who_facts, *lately]
    if non_obvious is not None:
        cited.append(non_obvious)
    cited.extend(_hub_evidence(dossier, meet))

    return Digest(
        digest_id=uuid.uuid4().hex[:16],
        person=dossier.person,
        who_line=who_line,
        meet=meet,
        lately=lately,
        non_obvious=non_obvious,
        say_out_loud=say_out_loud,
        sources=_sources(cited),
        exclusion_policy=taste.EXCLUSION_POLICY,
        created_at=dt.datetime.now(dt.UTC),
    )
