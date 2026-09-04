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
fallback, :data:`OPENER_TEMPLATE`, so R3's latency bound holds whatever the API does. That
template carries the fact through a COLON rather than splicing it into the object slot of
"about", because a fact sentence is a clause and "Ask about <clause>" is not English —
see :data:`OPENER_TEMPLATE` and :func:`is_speakable`'s sixth clause.

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
    "IRREGULAR_VERB_FORMS",
    "LATELY_CAP",
    "LATELY_FALLBACK_CATEGORIES",
    "LATELY_PRIMARY_CATEGORIES",
    "MEET_CAP",
    "NON_OBVIOUS_KINDS",
    "NOUN_PHRASE_OPENERS",
    "OPENER_OF_LAST_RESORT",
    "OPENER_PREFIXES",
    "OPENER_TEMPLATE",
    "SAY_OUT_LOUD_TIMEOUT_SECONDS",
    "SCORE_WORDS",
    "SPOKEN_WORD_CAP",
    "SURVEILLANCE_PHRASES",
    "WHO_OF_LAST_RESORT",
    "WHY_OF_LAST_RESORT",
    "SayOutLoud",
    "is_speakable",
    "make_digest",
    "opener_hook_candidates",
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
#:
#: The colon is the whole point, and it is not decoration. ``Fact.text`` is a SENTENCE — the
#: extractor writes "one sentence about the person", and in practice a subject-elided
#: predicate: "Argues that developer-tools pricing should be published in full on a public
#: page." A preposition opens a NOUN-PHRASE slot, so the earlier ``"Ask about {text}"``
#: produced "Ask about Argues that developer-tools pricing should be published in full on a
#: public page." — ungrammatical, and R18's whole job is to keep the host from stumbling.
#: This is the fallback path, so that line was what the product SAID on every timeout, every
#: transport error, every rejected model line, and on any deploy with no API key at all.
#:
#: Three fixes were on the table and only one keeps this module's other invariant. Selecting
#: a noun phrase out of the sentence, and lower-casing it to embed it, both REWRITE a fact —
#: and "facts are shown verbatim, skipped rather than edited" is the rule the citation beside
#: them depends on. A per-:class:`~arrival.contracts.FactCategory` template does not help
#: either: the shapes vary WITHIN a category ("Argues that…" and "Quarrystone Labs took…" are
#: both single-sentence facts about the same person). Giving "about" its own object and
#: letting the colon introduce the fact as an elaboration is grammatical for an ARBITRARY
#: string, including a fragment, and touches not one character of the fact.
OPENER_TEMPLATE = "Ask about this: {text}"

#: Used only when a dossier carries no displayable fact at all — or none whose own wording
#: survives R18 — so there is nothing to invite a question about. Still an invitation.
OPENER_OF_LAST_RESORT = "Ask what they are working on right now."

#: Shown when a Meet row's ``why`` repairs to nothing. It states an absence rather than
#: inventing a shared thing the matcher never claimed.
WHY_OF_LAST_RESORT = "Worth a hello; nothing quotable on the record yet."

#: Shown when a dossier has no name, no roster detail and no person_id to build a Who line
#: from. R8's "still a digest" property has to survive even an empty record.
WHO_OF_LAST_RESORT = "A member has arrived."

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
_SCORE_FRACTION = re.compile(r"^\d{1,3}\s*(?:/\s*100|%)$")
_ORPHANED_PUNCTUATION = re.compile(r"\s+([.,;:!?])")

#: R18 bans a number used **as a score**, which is narrower than "a number". A count is
#: not a score, and an earlier version of this module treated every bare integer in 0..100
#: as one — which silently deleted content from a matcher's own reasoning: "Both shipped 12
#: open-source developer tools together" became "Both shipped open-source developer tools
#: together", and "Both deep in Web 2 standards work" lost the 2. So a digit only reads as
#: a score when the word in front of it says it is one.
SCORE_WORDS: frozenset[str] = frozenset(
    {
        "score",
        "scored",
        "scores",
        "scoring",
        "rank",
        "ranked",
        "ranks",
        "ranking",
        "rated",
        "rating",
        "points",
        "weight",
        "weighted",
    }
)


#: R18's sixth clause. A preposition or an article opens a NOUN-PHRASE slot: what follows it
#: is a name or a thing, never a clause. These are the ones a spliced sentence lands after.
#: Conjunctions are deliberately absent — "and" joins like with like, so a capitalised word
#: after it says nothing about whether a clause was spliced, and including it only cost
#: precision when this was measured.
NOUN_PHRASE_OPENERS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "after",
        "against",
        "an",
        "at",
        "before",
        "by",
        "during",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "onto",
        "over",
        "the",
        "through",
        "to",
        "toward",
        "towards",
        "under",
        "with",
        "within",
        "without",
    }
)

#: Finite verb forms no suffix rule reaches. Kept small on purpose: every entry earns its
#: place by appearing sentence-initially in a real extracted fact ("Led the Foundry Seed 2019
#: fund…", "Took four months away…", "Has lived in Austin since 2014.", "Spent six years…").
IRREGULAR_VERB_FORMS: frozenset[str] = frozenset(
    {
        "am",
        "are",
        "became",
        "began",
        "bought",
        "broke",
        "brought",
        "built",
        "chose",
        "drew",
        "drove",
        "fell",
        "felt",
        "found",
        "gave",
        "got",
        "grew",
        "had",
        "has",
        "have",
        "held",
        "is",
        "kept",
        "knew",
        "left",
        "led",
        "lost",
        "made",
        "met",
        "paid",
        "put",
        "ran",
        "read",
        "said",
        "sat",
        "saw",
        "sent",
        "set",
        "sold",
        "spent",
        "spoke",
        "stood",
        "taught",
        "thought",
        "told",
        "took",
        "was",
        "went",
        "were",
        "won",
        "wore",
        "wrote",
    }
)


#: The punctuation :func:`_bare` strips, named so :func:`_closes_its_phrase` can ask the
#: same question about the other end of a token.
_BARE_PUNCTUATION = ".,;:!?'\""


def _bare(word: str) -> str:
    return word.strip(_BARE_PUNCTUATION)


def _reads_as_a_verb(word: str) -> bool:
    """Does this token read as a finite verb rather than as a name?

    Morphology plus a short irregular list, and no dictionary — which is the honest
    accuracy available without a tagger. Two calibrations were measured against this
    project's own corpus rather than guessed:

    * A capitalised HYPHENATED compound is a product or a hub label far more often than a
      verb. A bare "ends in -s" rule reads "Developer-tools" as a verb and blanks the Meet
      row's reasoning, so a hyphenated word only counts in its participial form — which
      still catches "Co-founded…" and "Co-authored…", the two hyphenated sentence-openers
      the corpus actually has. The example this rule was calibrated on, ``graph._why``
      emitting "Both deep in Developer-tools go-to-market.", is no longer what that function
      emits: T-041 lower-cases a CATEGORY hub's label, so the corpus line now reads "Both
      deep in developer-tools go-to-market." The mitigation is not dead with it — every
      other hub type keeps the label's stored capitalisation, so a company called
      "Meridian-Ops Systems" still arrives capitalised, hyphenated and ending in "-s"
      directly after a bare "to".
    * An ALL-CAPS token is an acronym ("AI", "CEO"), never an inflected verb.

    Measured on the 35 facts of the frozen corpus (``.swarm-loop/acceptance/fixtures/
    dossiers``): this flags 28 of 28 subject-elided predicates and none of the 7 sentences
    that open with a real subject. T-029's own docstring recorded "21 of 21" and "six";
    those numbers do not reproduce on that corpus, which has not changed since — 28 and 7
    are what the loop above and the frozen JSON actually yield.
    """
    lowered = word.casefold()
    if lowered in IRREGULAR_VERB_FORMS:
        return True
    if "-" in lowered:
        return lowered.endswith(("ed", "ing"))
    return len(lowered) >= 4 and lowered.endswith(("s", "ed", "ing"))


def _closes_its_phrase(words: Sequence[str], index: int) -> bool:
    """Does the token at ``index`` end the phrase it sits in?

    True when it is the last token of the line, or when it carries its own trailing
    punctuation — which closes the noun-phrase slot the same way punctuation BEFORE a token
    opens a new clause. The punctuation set is :func:`_bare`'s, so the two tests agree.
    """
    if index == len(words) - 1:
        return True
    word = words[index]
    return word.rstrip(_BARE_PUNCTUATION) != word


def _noun_phrase_spans(words: Sequence[str], phrases: Iterable[str]) -> frozenset[int]:
    """Token indexes covered by a verbatim occurrence of one of ``phrases`` in ``words``.

    Comparison is on the BARE, case-folded token, so it survives the sentence's own
    punctuation ("2019." matches "2019") and ``graph._spoken_label``'s lower-casing of a
    category label's leading character. A phrase only covers tokens where its whole word
    sequence appears in order, so a single shared word never exempts anything.
    """
    covered: set[int] = set()
    for phrase in phrases:
        parts = [bare for word in phrase.split() if (bare := _bare(word).casefold())]
        if not parts:
            continue
        for start in range(len(words) - len(parts) + 1):
            if all(_bare(words[start + n]).casefold() == part for n, part in enumerate(parts)):
                covered.update(range(start, start + len(parts)))
    return frozenset(covered)


def _splices_a_clause(
    words: Sequence[str], noun_spans: frozenset[int] = frozenset()
) -> bool:
    """R18: was a sentence pasted into a slot that wanted a noun phrase?

    This is the property that "Ask about Argues that developer-tools pricing should be
    published in full on a public page." fails and the five older clauses miss: it carries no
    URL, no citation marker, no parenthesis, no score and only sixteen words, so it read as
    speakable while being unreadable aloud.

    A capitalised word is a splice when it stands directly after a bare
    :data:`NOUN_PHRASE_OPENERS` token — bare meaning no trailing punctuation, so a colon,
    dash, comma or full stop before it is a boundary and licenses the new clause — and reads
    as a verb. That is why :data:`OPENER_TEMPLATE`'s "this:" is accepted while the old
    template is not: the preposition has its object, and the colon introduces the rest.

    **Two exemptions, and the difference between them is the difference between a name and
    a sentence.** The morphology cannot tell "Reuters" from "Argues" — both are capitalised
    and both end in "-s" — so neither exemption tries to. They ask something else:

    1. *A clause is longer than one word.* A verb-looking token that CLOSES its phrase
       (:func:`_closes_its_phrase` — the last token, or one carrying its own punctuation)
       has nothing after it to be the rest of a clause, so it is the noun the slot wanted.
       "Both connected to Databricks." is exempt; "Ask about Argues that developer-tools
       pricing…" is not, because "Argues" is followed by the clause it governs. This
       exemption cannot admit a spliced sentence: a sentence has a predicate, and a token
       at the close of the phrase has none.
    2. *A hub label is a noun by construction.* ``noun_spans`` names the tokens covered by
       a phrase the CALLER declared to be a noun phrase, and only
       :func:`_speakable_match` declares any — from ``Match.contributions``, where the
       matcher has already said "these words are a hub's label". That reaches the labels
       exemption 1 cannot, the multi-word ones whose head reads as a verb ("Reuters Media
       Group", "Building Futures Fund"). It is provenance, not grammar: nothing the caller
       did not declare is exempt, and the opener path declares nothing, so
       "Ask about Argues that…" is refused there exactly as before.

    What remains refused, deliberately: a capitalised verb-looking word MID-phrase that no
    caller declared — "…at Reuters on the data team" in a fact. An unspeakable fact is
    SKIPPED and the next candidate tried, so rejection costs a fact. That is not true of a
    why, whose only fallback is :data:`WHY_OF_LAST_RESORT`, an admitted blank — which is
    why the why path is the one that declares its nouns.
    """
    for index in range(1, len(words)):
        previous = words[index - 1]
        if previous != _bare(previous):
            continue  # punctuation ends the phrase, so what follows may start a clause
        if previous.casefold() not in NOUN_PHRASE_OPENERS:
            continue
        word = _bare(words[index])
        if not word or not word[0].isupper() or word.isupper():
            continue
        if index in noun_spans:
            continue  # the caller declared these tokens a noun phrase
        if _closes_its_phrase(words, index):
            continue  # nothing follows it, so there is no clause to have been spliced
        if _reads_as_a_verb(word):
            return True
    return False


def _score_positions(words: Sequence[str]) -> set[int]:
    """Indexes of tokens in ``words`` that read aloud as a score (R18).

    Two shapes qualify: a bare integer 0..100 immediately after a scoring word
    ("scored 67", "weight 3"), and a self-declaring fraction ("67/100", "67%"). Everything
    else — years, counts, version numbers, product names — is prose and survives.
    """
    found: set[int] = set()
    for index, word in enumerate(words):
        stripped = _bare(word)
        if _SCORE_FRACTION.fullmatch(stripped):
            found.add(index)
            continue
        if not _BARE_INTEGER.fullmatch(stripped) or int(stripped) > 100:
            continue
        previous = _bare(words[index - 1]).casefold() if index else ""
        if previous in SCORE_WORDS:
            found.add(index)
    return found


def is_speakable(text: str, *, noun_phrases: Iterable[str] = ()) -> bool:
    """R18: can a host read this line aloud, as written, without stumbling?

    No URLs, no ``[n]`` citation markers, no parentheses, no numbers-as-scores, at most
    :data:`SPOKEN_WORD_CAP` words, not blank — and no sentence spliced into a slot that
    wanted a noun phrase (:func:`_splices_a_clause`).

    That last clause is here because the first five graded only the MECHANICAL hazards and
    left the grammatical one unguarded, which let the fallback opener ship "Ask about Argues
    that…" on the path every failure mode takes. A rule that lives only in the template
    regresses the moment someone edits the template; a rule that lives here fails the line
    instead, and the caller falls through to the next candidate.

    ``noun_phrases`` is how a caller that KNOWS part of this line is a name says so — a
    ``Match``'s hub labels, and nothing else in this codebase. Default empty, so every
    existing call site judges exactly what it judged before; see :func:`_splices_a_clause`
    for why the declaration belongs to the caller and not to the morphology.
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
    if _splices_a_clause(words, _noun_phrase_spans(words, noun_phrases)):
        return False
    return not _score_positions(words)


def speakable(text: str) -> str:
    """Force ``text`` into a shape :func:`is_speakable` accepts.

    Used **only** on sentences this module did not author — a ``Match.why`` arrives from
    the matcher and is shown as a Meet row, so R18 binds even when the matcher was careless.
    It is never used on a :class:`~arrival.contracts.Fact`: a fact is shown verbatim or not
    at all (see :func:`who_line_for`, which skips rather than edits).

    It repairs the five MECHANICAL clauses of :func:`is_speakable` and deliberately not the
    sixth: a spliced clause is a grammar problem, and the only "repair" available — deleting
    the verb, or lower-casing it — changes what the sentence says. So the result of this
    function is not guaranteed speakable, and its one caller checks (see
    :func:`_speakable_match`).
    """
    cleaned = _CITATION_MARKER.sub(" ", text)
    cleaned = _PARENTHETICAL.sub(" ", cleaned)
    cleaned = cleaned.replace("(", " ").replace(")", " ")
    words = [w for w in cleaned.split() if "http" not in w.casefold()]
    scores = _score_positions(words)
    words = [w for index, w in enumerate(words) if index not in scores]
    truncated = len(words) > SPOKEN_WORD_CAP
    words = words[:SPOKEN_WORD_CAP]
    # Removing a token can strand the punctuation that followed it ("Y Combinator ."),
    # which a host reads as a stumble even though every rule above is satisfied.
    line = _ORPHANED_PUNCTUATION.sub(r"\1", " ".join(words).strip())
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
    # which is the member's own self-description rather than anything researched. The
    # detail is sentence-cased on the way in: a roster reads "co-founder, Quarrystone
    # Labs", and "Runa Okonkwo. co-founder, ..." is a stumble on the page.
    for detail in dossier.person.details:
        trimmed = detail.strip().rstrip(".")
        if not trimmed:
            continue
        candidate = f"{opening} {trimmed[0].upper()}{trimmed[1:]}.".strip()
        if is_speakable(candidate):
            return candidate, []
    return opening or name or dossier.person.person_id or WHO_OF_LAST_RESORT, []


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


def opener_hook_candidates(dossier: Dossier, *, exclude: Iterable[Fact] = ()) -> list[Fact]:
    """Facts the fallback opener may invite a question about, best first.

    DESIGN Decision 12's order: displayable ``hook`` facts by confidence descending, then
    every other displayable fact most-recent-first. A LIST rather than a single fact
    because the template it feeds is itself subject to R18 — if the best hook's own wording
    cannot be read aloud, the opener moves to the next one instead of shipping a line the
    host stumbles over (see :func:`_fallback_opener`).

    ``exclude`` carries the facts the page already speaks aloud, and it earns its place:
    four of the five people in the grading corpus carry no ``hook`` fact at all, and their
    highest-confidence displayable fact IS the one in the Who line. Without this the
    fallback opener asks the host to read the same sentence twice — measured on
    ``mira-hollowell``, ``sil-vantorre`` and ``theo-baptiste``. Excluding what has already
    been said is a narrowing of "the most recent displayable fact", not a departure from it.
    """
    spoken_for = {f.fact_id for f in exclude}
    displayable = [f for f in _displayable(dossier.facts) if f.fact_id not in spoken_for]
    hooks = sorted(
        (f for f in displayable if f.category == "hook"),
        key=lambda f: (f.provenance.confidence, f.fact_id),
        reverse=True,
    )
    others = [f for f in _by_recency(displayable) if f.category != "hook"]
    return hooks + others


def pick_opener_hook(dossier: Dossier, *, exclude: Iterable[Fact] = ()) -> Fact | None:
    """The single best opener hook, or ``None`` when the dossier shows nothing at all."""
    candidates = opener_hook_candidates(dossier, exclude=exclude)
    return candidates[0] if candidates else None


def _raw_contribution(match: Match) -> float:
    """The unrounded score behind ``Match.score``, recovered from its own components.

    ``graph.match`` computes ``score = min(100, round(100 * raw / ref))`` from
    ``raw = sum(c.contribution for c in components)`` and hands both back on the Match, so
    this is a reconstruction of its input rather than a second opinion about it.
    """
    return sum(contribution.contribution for contribution in match.contributions)


def _capped_meet(matches: Sequence[Match]) -> list[Match]:
    """R7 + R8: the top three by score, and NOTHING when nobody else is present.

    The sort key is TOTAL, and that is the fix for a real defect rather than tidiness.
    ``Match.score`` is ``round(100 * raw / ref)``, so ties are the normal case and not the
    edge one: on the grading corpus two of the four present peers score exactly 0.0. This
    function's own contract says matches arrive "in any order", and until the key became
    total that promise was false — a plain ``sorted(key=score, reverse=True)`` is stable, so
    which of the two zero-scorers took the third Meet row was decided by the order
    ``graph.match`` happened to return. Measured before the fix: over the 24 permutations of
    the corpus's four matches, 12 produced a Meet ending in ``mira-hollowell`` and 12 one
    ending in ``theo-baptiste``. T-7 was reading a guarantee out of T-5's list order that
    T-5 never made to it, and T-8 renders whichever row this picks.

    So ties fall back to the raw contribution sum the rounding threw away, and then to
    ``person_id``, which is total. Both recover what ``graph.match``'s own ordering means
    (``-raw``, then ``person_id``) without DEPENDING on it: the same input set now yields the
    same Meet whatever order it arrives in. The cap selects whole rows; it never invents one
    to reach three.

    One person occupies at most one row. R7 caps Meet at "three present people", not three
    Match objects, and a host reading the same name twice has been handed a padded section
    with extra steps. The highest-scoring row for a person wins, which is the first one the
    sort reaches.
    """
    ranked = sorted(
        matches, key=lambda m: (-m.score, -_raw_contribution(m), m.other.person_id)
    )
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

    **The why declares its own nouns, and this is the only place in the codebase that
    does.** ``graph._why`` builds the sentence by interpolating a hub LABEL into a phrase
    template, and nine of the ten templates end on a bare preposition — "both connected to
    {label}", "both building on {label}". So the label lands exactly where
    :func:`_splices_a_clause` looks for a spliced verb, and a label like "Databricks",
    "Reuters" or "Kubernetes" is capitalised, un-hyphenated and ends in "-s": the
    morphology reads it as a verb and the row's whole reasoning is replaced by
    :data:`WHY_OF_LAST_RESORT`.

    Rejection is only the safe direction when there is something to fall back TO. For a
    fact there is — the next candidate. For a why there is not: the fallback is an admitted
    blank, so refusing a good line costs R10's exposed reasoning outright. What this
    function has that no morphology does is ``match.contributions``, in which the matcher
    has already said which words are a hub's label. Passing those labels as
    ``noun_phrases`` tells the verb detector what it cannot infer, and nothing else in the
    line is exempted — a clause spliced anywhere the labels do not cover is refused here
    exactly as it is on the opener path.

    Every contribution's label is passed, not only the ones ``graph._why`` chose to name,
    so this does not have to restate that function's selection rule. It is not a widening:
    the only free text in a why IS a named label, so an unnamed label's words can only
    match tokens a named label already covers.

    Each label is declared TWICE — as stored, and as :func:`speakable` renders it — because
    the second judgement below reads the REPAIRED line, in which the stored label no longer
    occurs. A label like "Reuters (Media) Group" loses its parenthetical on the way through
    the repair, so the stored spelling covers nothing and the row blanks with the
    parenthesis gone and the name intact: the same defect, in the version a reader would
    never think to look for. Repairing the declaration the same way the line is repaired is
    what keeps the two in step.
    """
    stored = [c.hub.label for c in match.contributions]
    labels = stored + [phrase for label in stored if (phrase := speakable(label))]
    why = match.why.strip()
    if is_speakable(why, noun_phrases=labels):
        return match if why == match.why else match.model_copy(update={"why": why})
    repaired = speakable(why)
    if not repaired or not is_speakable(repaired, noun_phrases=labels):
        # Nothing survived the repair, or what survived still cannot be read aloud (a
        # spliced clause is a grammar fault, and `speakable` repairs only the mechanical
        # five). Either way there is no shared thing left to name that a host can say. R7
        # wants a why that names one; when the input carried none this module will NOT
        # invent it — a fabricated connection is worse on this product than an admitted
        # blank. Checking the repair rather than trusting it is what keeps "every spoken
        # line on the page satisfies R18" true clause-by-clause instead of only for the
        # clauses `speakable` happens to know how to fix.
        repaired = WHY_OF_LAST_RESORT
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


def _stronger(candidate: tuple[float, str], holder: tuple[float, str]) -> bool:
    """Higher confidence wins a document's one slot; an exact tie goes to the lower id."""
    return candidate[0] > holder[0] or (candidate[0] == holder[0] and candidate[1] < holder[1])


def _sources(facts: Iterable[Fact]) -> list[Provenance]:
    """R9/S6: one entry per document, in the order the page first leans on it.

    Two separate decisions live here, and only the first is R9's.

    **Position** is first-use order and stays that way: T-8 numbers a citation by a
    document's index in this list, so moving an entry renumbers the page.

    **Which of a document's provenances fills that slot** used to be a side effect of
    assembly order, and that was the defect. ``Provenance`` is PER FACT — it carries the
    fact's own ``quote`` and ``confidence``, not the document's — while this list holds one
    entry per ``doc_id``, so several facts compete for one slot whenever they were extracted
    from the same document, which on the grading corpus is most of them. Taking whichever
    arrived first meant the winner was decided by which SECTION reached the document first,
    and the visible result was a citation whose quote does not support the claim above it.
    Measured on ``runa-okonkwo``: the Meet row "Both backed by Foundry Seed 2019" cited a
    source displaying "I co-founded Quarrystone Labs in 2016 and I run the platform team
    there" — a quote that never mentions Foundry Seed — because the Who line reached that
    document three sections earlier. Two more rows did the same.

    One entry per document is frozen (the acceptance suite asserts the list is deduped by
    ``doc_id``), so no rule can give every citing fact its own quote. What a rule CAN do is
    stop the answer depending on page structure and state what it is: the slot holds the
    STRONGEST evidence the page has from that document — highest ``confidence``, ties to the
    lower ``fact_id``. That is the best single answer to "why should I believe this document
    is about this person", it is what the entry's own rendered confidence number then
    honestly describes, and it is identical whatever order the sections are assembled in.
    """
    order: list[str] = []
    winner: dict[str, tuple[float, str, Provenance]] = {}
    for fact in facts:
        provenance = fact.provenance
        doc_id = provenance.doc_id
        candidate = (provenance.confidence, fact.fact_id)
        holder = winner.get(doc_id)
        if holder is None:
            order.append(doc_id)
            winner[doc_id] = (*candidate, provenance)
        elif _stronger(candidate, holder[:2]):
            winner[doc_id] = (*candidate, provenance)
    return [winner[doc_id][2] for doc_id in order]


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


def _fallback_opener(candidates: Sequence[Fact]) -> tuple[str, Fact | None]:
    """DESIGN Decision 12's template, held to the SAME R18 bar as the model's line.

    :data:`OPENER_TEMPLATE` interpolates a fact verbatim, and a fact's own wording is
    not guaranteed speakable: facts run to 200 characters and nothing stops one carrying a
    parenthetical or a URL. Validating only the model's line and not the template's leaves
    R18 unenforced on the path taken by EVERY failure mode — timeout, transport error,
    rejected model line — which is precisely the path DESIGN Decision 12 exists to make
    reliable. So the template is validated too, and an unspeakable hook yields to the next
    candidate rather than to a line the host stumbles over.

    Returns the line and the fact it quotes, because a quoted fact is a fact SHOWN and R9
    requires it to be citable. ``None`` means nothing was quoted.
    """
    for hook in candidates:
        line = OPENER_TEMPLATE.format(text=hook.text.strip())
        if _validate_opener(line) is not None:
            return line, hook
    return OPENER_OF_LAST_RESORT, None


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
    candidates: Sequence[Fact],
    lately: Sequence[Fact],
    llm: LLMClient,
) -> tuple[str, Fact | None]:
    """DESIGN Decision 12: exactly one LLM call, bounded, validated, always answered.

    Every way the call can go wrong — a slow API, a transport error, a model that ignores
    R14 — converges on the same documented template, so the arrival path has no branch that
    can leave the host without a line to say.

    The second element of the return is the fact the line QUOTES, or ``None``. A model's
    opener is a paraphrase and cites nothing; the template's is the fact's own sentence,
    which R9 says must be checkable back to a document.
    """
    hook = candidates[0] if candidates else None
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
        return _fallback_opener(candidates)

    validated = _validate_opener(str(getattr(result, "line", "") or ""))
    if validated is not None:
        return validated, None
    return _fallback_opener(candidates)


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

    candidates = opener_hook_candidates(dossier, exclude=who_facts)
    say_out_loud, quoted = await _say_out_loud(dossier, candidates, lately, llm)

    cited: list[Fact] = [*who_facts, *lately]
    if non_obvious is not None:
        cited.append(non_obvious)
    cited.extend(_hub_evidence(dossier, meet))
    # R9: the templated opener quotes a fact's own sentence, so that fact is SHOWN and must
    # be checkable back to a document. Appended LAST so it can never disturb the first-use
    # order of the sections above it, and only when the template was actually used — a
    # model-written opener is a paraphrase and cites nothing.
    if quoted is not None:
        cited.append(quoted)

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
