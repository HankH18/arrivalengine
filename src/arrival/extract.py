"""Fact and hub extraction — the mechanical citation check is the hallucination guard.

`extract` turns the documents the resolver accepted into `Fact`s every downstream surface
can trust, plus the canonical `Hub`s T-5 joins people on.

The model proposes; this module disposes, and every disposal is MECHANICAL (DESIGN
Decision 5 — the citation check is not an LLM judgement):

* a fact whose `quote` is not a whole-word span of one identifiable source document,
  long enough to be evidence of anything, is DROPPED and counted. Never repaired, never
  softened, never shown (SPEC C8 / R9).
* every provenance field is copied from the `RawDoc` — INCLUDING the quote, which is the
  document's own characters for the span the model pointed at, not the model's retyping
  of them. `url`, `source_kind`, `published_at` and `retrieved_at` likewise. The model is
  never the source of a citation's content, only of the sentence and of where to look.
* `hub_id` is computed here from the label (or from a Wikidata QID the document itself
  states), so two dossiers built months apart still join on one node. Where the model
  described one label inconsistently, the descriptions are RECONCILED by majority vote —
  the same repair `graph._hub_identity` makes downstream — so neither the model's output
  order nor a stray second opinion can split a node or move a match score.
* nothing the model NAMES is an identity until a document corroborates it. The `doc_id` it
  puts on a hub is believed only if that document names the hub (`_states_label`), and the
  facts borrowed from it are only those that name the hub too — bulk-attaching a
  document's other facts is what let a hub outlive the exclusion of its own evidence and
  reopened R11. The QID it puts on a hub is ranked against every QID the hub's own
  evidence offers and REFUSED on a tie (`_hub_qid`), because `hub_id` is the graph-wide
  join key. The ids it invents for its own facts never overwrite ours, and an id it used
  twice identifies neither fact (`_id_map`).
* `category="non_obvious"` survives only on the source kinds R7 makes eligible. A
  subject's own about page IS the first page, so nothing on it is "not on the first page"
  however the model labelled it.

Nothing here makes a taste decision: T-4 owns `excluded` / `exclusion_reason`, and every
`Fact` leaves this module with the defaults (`excluded=False`, `exclusion_reason=None`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from itertools import batched

from pydantic import BaseModel, Field

from arrival.contracts import (
    Fact,
    FactCategory,
    Hub,
    HubType,
    LLMClient,
    LLMError,
    PersonRef,
    Provenance,
    RawDoc,
    Resolution,
    SourceKind,
)
from arrival.util import normalize_ws, slug

__all__ = [
    "FIELD_HUB_VOCABULARY",
    "MAX_DOCS_PER_CALL",
    "MAX_FACT_CHARS",
    "MAX_FIELD_HUBS",
    "MAX_TOKENS_PER_CALL",
    "MIN_FIELD_HUB_WORDS",
    "MIN_QUOTE_CHARS",
    "MIN_QUOTE_WORDS",
    "NON_OBVIOUS_ELIGIBLE_KINDS",
    "STOP_HUB_LABELS",
    "CandidateFact",
    "CandidateHub",
    "ExtractionResult",
    "ExtractionStats",
    "canonical_hub_id",
    "cited_span",
    "extract",
    "is_cited",
    "recency_for",
]

log = logging.getLogger(__name__)

#: `Fact.text` is one sentence of at most this many characters (DESIGN §Interfaces).
MAX_FACT_CHARS = 200

#: Documents per `llm.structured` call. The ticket allows one call per accepted document
#: or a batch of at most three; batching keeps a 40-document build inside `Budget`'s
#: 80-call ceiling while still leaving each document its own labelled block in the prompt.
MAX_DOCS_PER_CALL = 3

#: Output ceiling per call. Three documents of facts and hubs do not fit in the protocol
#: default of 2000, and a truncated JSON body costs the whole batch.
MAX_TOKENS_PER_CALL = 4000

#: The floor a quote must clear before "it is a substring of the document" means anything.
#: Below it the test degenerates: `"a"` is a verbatim span of every document ever written,
#: so a one-character quote was a UNIVERSAL citation and certified whatever sentence the
#: model attached to it. Measured on the pre-repair code: `is_cited("a", doc)` was True
#: against every document in the corpus, and the invented sentence it carried was kept.
MIN_QUOTE_CHARS = 16
MIN_QUOTE_WORDS = 3

# DESIGN Decision 3, verbatim: never nodes, after lower-casing.
#
# These are hub LABELS. They are matched against `Hub.label`, NEVER against `Hub.type` and
# NEVER against the `{type}:` prefix of a canonical `hub_id` — `investor` and `technology`
# are each simultaneously a stop word and a `HubType`, so a prefix/type matcher deletes
# `investor:foundry-seed-2019` and `technology:developer-platform`, which are exactly the
# rare, high-signal nodes T-5's IDF-weighted score is built on.
STOP_HUB_LABELS: frozenset[str] = frozenset(
    {"texas", "startup", "founder", "ai", "technology", "business", "ceo", "investor"}
)

#: How many FIELD hubs (`CandidateHub.field`) one person may carry. A field is an
#: abstraction over entities — "venture capital" rather than "Union Square Ventures" — and
#: it exists because nobody shares a portfolio company by accident, so a corpus of proper
#: nouns joins nobody to anybody. The cap is the structural half of the guard DESIGN
#: Decision 3's stop list is the lexical half of: a person with two field hubs cannot be
#: joined to everybody through a cloud of vague topics however the model behaves.
MAX_FIELD_HUBS = 2

#: A field label must be at least this many words. Every one of DESIGN Decision 3's stop
#: hubs — texas, startup, founder, ai, technology, business, ceo, investor — is a BARE
#: SINGLE WORD, and that is not a coincidence: the vagueness that makes a label worthless
#: is what lets it be said in one word. So the rule generalises the decision rather than
#: contradicting it, and it needs no list to maintain. It applies only to labels the model
#: marked `field`; a one-word NAME ("Tech:NYC", "Kubernetes", "Twitch") is unaffected.
MIN_FIELD_HUB_WORDS = 2

#: The closed vocabulary a FIELD hub must land in, and the one hand-authored artefact in
#: this module. It exists because free-form field labels were MEASURED not to converge: run
#: against the live ten-person corpus, the model produced "seed-stage venture capital" for
#: one venture capitalist, "seed-stage venture" for the same person in another batch, and
#: nothing at all for the other one — three descriptions of one field, joining nobody, and
#: five new topic nodes of pure noise. An abstraction that every carrier spells differently
#: is not an abstraction; a shared field needs a shared word for it.
#:
#: **How this reconciles with DESIGN Decision 3.** The stop list is the same mechanism with
#: the sign flipped: a hand-authored set of labels that decides what may be a node. Decision
#: 3 says vague hubs are worthless and names eight of them; this says the cure for vagueness
#: is a NAMED level of abstraction rather than no abstraction at all. Two rules keep them
#: from colliding: a stop hub may never appear here, and neither may a synonym of one — so
#: there is no "artificial intelligence" (Decision 3 stopped "ai"), no "technology sector"
#: (it stopped "technology"), no "early-stage startups" (it stopped "startup"). Every entry
#: is a level BELOW the one Decision 3 judged universal.
#:
#: Membership is necessary and never sufficient. A term becomes a hub only when a document
#: about the person carries the phrase (`_attested_evidence`), at most `MAX_FIELD_HUBS` per
#: person, and `graph.hub_idf` still clamps to zero anything the whole population carries.
#: The list is deliberately generic rather than fitted to a roster; it belongs in
#: configuration rather than in code, which this ticket does not own.
FIELD_HUB_VOCABULARY: frozenset[str] = frozenset(
    {
        "venture capital",
        "private equity",
        "angel investing",
        "developer tools",
        "consumer social",
        "social media",
        "enterprise software",
        "open source",
        "cloud infrastructure",
        "information security",
        "digital health",
        "life sciences",
        "climate tech",
        "financial technology",
        "online marketplaces",
        "video games",
        "creative software",
        "online education",
        "digital media",
        "consumer hardware",
        "supply chain",
        "public policy",
    }
)

#: R7 / DESIGN §Data models: the source kinds whose facts may carry `non_obvious`.
#: `self_page` and `search` are deliberately absent — the subject's own about page and the
#: first page of search results are the definition of "obvious".
NON_OBVIOUS_ELIGIBLE_KINDS: frozenset[SourceKind] = frozenset(
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

#: The category a flagged fact falls back to when its source kind is not R7-eligible and
#: the model offered no second choice.
FALLBACK_CATEGORY: FactCategory = "current_work"

# Recency bands (T-3 acceptance 4): 1.0 within 12 months, 0.6 within 3 years, 0.3 older.
_RECENCY_BANDS: tuple[tuple[int, float], ...] = ((365, 1.0), (1095, 0.6))
_RECENCY_OLDER = 0.3
_RECENCY_UNKNOWN = 0.5

_QID = re.compile(r"Q[1-9][0-9]*")

#: The same identifier, scanned OUT of running prose rather than matched against a whole
#: string. Word-bounded so that the `Q4242` inside a longer token is not a QID, and
#: case-insensitive because `_states_qid` compares case-folded and this must never be the
#: stricter of the two — a QID it failed to see is a competitor `_hub_qid` cannot rank.
_QID_SCAN = re.compile(r"\bQ[1-9][0-9]*\b", re.IGNORECASE)

# Typographic variants folded to one spelling before the substring test (T-015). A model
# that retypes `O'Neil` as `O’Neil`, or an en dash as a hyphen, is quoting the same words;
# dropping the fact costs a real citation over a difference no reader would call one.
#
# This is the ONLY direction the guard is ever loosened, and it is paid for on the spot:
# `cited_span` returns the DOCUMENT's characters, so what is stored still satisfies
# `normalize_ws(quote) in normalize_ws(doc.text)`. Only variants of the SAME character
# belong here — never a mapping that could turn one word into a different one.
_TYPOGRAPHIC: dict[str, str] = {
    "‘": "'",  # left single quotation mark
    "’": "'",  # right single quotation mark (the typographic apostrophe)
    "‚": "'",  # single low-9 quotation mark
    "‛": "'",  # single high-reversed-9 quotation mark
    "ʼ": "'",  # modifier letter apostrophe
    "ʹ": "'",  # modifier letter prime
    "′": "'",  # prime
    "´": "'",  # acute accent used as an apostrophe
    "`": "'",  # grave accent used as an apostrophe
    "“": '"',  # left double quotation mark
    "”": '"',  # right double quotation mark
    "„": '"',  # double low-9 quotation mark
    "‟": '"',  # double high-reversed-9 quotation mark
    "″": '"',  # double prime
    "«": '"',  # left guillemet
    "»": '"',  # right guillemet
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "…": "...",  # horizontal ellipsis
}


# --------------------------------------------------------------------------
# the internal extraction schema
#
# Internal on purpose: nothing outside this module may depend on the shape we ask the
# model for. `contracts.Fact` and `contracts.Hub` are what leave here, and they are built
# from the RawDoc plus the checked parts of the model's answer, never validated straight
# out of it.
# --------------------------------------------------------------------------


class CandidateFact(BaseModel):
    """One fact the model proposes, before any of it has been believed."""

    doc_id: str = Field(description="id of the document this fact came from, copied verbatim")
    text: str = Field(
        description=(
            f"one sentence about the person, at most {MAX_FACT_CHARS} characters. "
            "Longer sentences are discarded, so keep it short."
        )
    )
    quote: str = Field(
        description=(
            "a span copied WORD FOR WORD out of that document's text that supports the "
            "sentence. Invented or paraphrased quotes are detected and the fact is thrown "
            "away, so copy, never summarise."
        )
    )
    category: FactCategory = Field(
        default=FALLBACK_CATEGORY,
        description=(
            "use 'non_obvious' only for something a well-prepared person would NOT already "
            "know from the subject's own bio or the first page of search results"
        ),
    )
    natural_category: FactCategory = Field(
        default=FALLBACK_CATEGORY,
        description="the best category for this fact IGNORING whether it is non-obvious",
    )
    fact_id: str = Field(
        default="",
        description=(
            "a short name for this fact, so a hub below can point at it in "
            "`evidence_fact_ids`. It MUST be different from every other fact_id in this "
            "answer: an id you use twice identifies neither fact and is discarded, along "
            "with every hub that pointed at it. We assign our own ids regardless, so this "
            "one is only ever a label for the references inside this answer."
        ),
    )
    confidence: float = Field(
        default=0.5,
        description=(
            "how sure you are the sentence is true OF THIS PERSON, 0 to 1. This number is "
            "load-bearing and it is not a formality: anything below 0.7 is withheld from "
            "staff entirely. Use 0.9+ when the document names the person and states the "
            "fact outright, and something low when you are inferring across a name match."
        ),
    )


class CandidateHub(BaseModel):
    """One entity the model proposes as a shared node in the graph."""

    label: str = Field(
        description=(
            "the entity's name as written IN THE DOCUMENT, e.g. 'Foundry Seed'. It is "
            "checked against the document's own text, so copy the spelling you found"
        )
    )
    type: HubType = Field(default="topic", description="what kind of entity this is")
    doc_id: str = Field(
        default="",
        description=(
            "id of the document this entity appears in. Only used when you give no "
            "`evidence_fact_ids`, and only believed if that document really names the "
            "entity — it is not a way to attach the document's other facts to this hub"
        ),
    )
    evidence_fact_ids: list[str] = Field(
        default_factory=list, description="fact_ids from this same answer that evidence the hub"
    )
    qid: str | None = Field(
        default=None,
        description=(
            "the Wikidata QID (e.g. 'Q42') ONLY when the document is a Wikidata item that "
            "states it; otherwise leave this out"
        ),
    )
    field: bool = Field(
        default=False,
        description=(
            "true when this label names a FIELD or PRACTICE the person works in — "
            "'venture capital', 'developer tools', 'consumer social' — rather than a "
            "named entity. Leave it false for anything with a name."
        ),
    )


class ExtractionResult(BaseModel):
    """The whole answer to one extraction call."""

    facts: list[CandidateFact] = Field(default_factory=list)
    hubs: list[CandidateHub] = Field(default_factory=list)
    based_in: str | None = Field(
        default=None,
        description=(
            "the place the Known details say this person is based, copied EXACTLY as the "
            "details write it. Leave it out when the details name no place. It is checked "
            "against them, so a place they do not state is discarded."
        ),
    )


@dataclass
class ExtractionStats:
    """What extraction did and what it threw away.

    The citation guard is only credible if its drops are visible, so every discard is
    counted here as well as logged. Pass one in to `extract` when you want the numbers —
    T-6's `BuildReport` is the intended reader.
    """

    documents_prompted: int = 0
    llm_calls: int = 0
    llm_failures: int = 0
    facts_proposed: int = 0
    facts_kept: int = 0
    dropped_uncited: int = 0
    dropped_over_length: int = 0
    dropped_empty: int = 0
    downgraded_non_obvious: int = 0
    hubs_proposed: int = 0
    hubs_kept: int = 0
    dropped_stop_hubs: int = 0
    dropped_unsupported_hubs: int = 0


def _most_common(values: list[str]) -> str | None:
    """The commonest value, ties broken lexicographically; None for nothing at all.

    Deliberately the same rule `graph._hub_identity` uses downstream, for the same reason:
    the answer must not depend on the order the descriptions arrived in. Duplicated rather
    than imported because `graph` sits DOWNSTREAM of this module — it reads the dossiers
    this one writes — and an upward import would invert that. See DECISIONS in the T-010
    report: the honest home for this is `arrival.util`, which this ticket does not own.
    """
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _unambiguous(values: list[str], *, what: str) -> str | None:
    """`_most_common`, except that a TIE between two different values refuses.

    The difference from `_most_common` is deliberate and narrow, and it exists for exactly
    one kind of value: an IDENTITY. `_most_common` breaks a tie lexicographically, which is
    the right answer for a hub's displayed type and label — some spelling has to win, every
    candidate is a description of the same node, and the lexicographic rule at least makes
    the winner a function of the set rather than of the model's output order.

    A Wikidata QID is not a description, it is a claim about WHICH entity this is, and it
    becomes `hub_id`, which `graph._canonical_hub_ids` lets win the identity election
    outright — so two hubs electing one QID are welded onto a single node with their
    labels, types and `evidence_fact_ids` pooled. Alphabetical order is not evidence about
    which of two entities is being described, and `Q4242` beating `Q7777` because "4" sorts
    before "7" is arrival order in a better costume. `resolve._best` reached the same
    conclusion about strong keys, for the same reason: "R2's refuse-to-guess applies to
    identity at least as hard as it applies to membership."

    Refusing is cheap here in a way it is not in `resolve`: `canonical_hub_id` is TOTAL, so
    a refused QID leaves the hub with `{type}:{slug(label)}` — a complete identity that
    still joins every dossier spelling the label the same way. See `_hub_qid`.
    """
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        log.info(
            "refusing to choose %s: %r are equally evidenced, and an alphabetical winner "
            "would be arrival order wearing the costume of evidence",
            what,
            [value for value, _ in ranked[:2]],
        )
        return None
    return ranked[0][0]


@dataclass
class _HubGroup:
    """Every candidate that canonicalises to one node, accumulated across documents.

    A group is keyed on `slug(label)` ALONE. The type is deliberately not part of the key:
    it is the model's opinion about an entity whose LABEL is the evidence — the label is
    what appears verbatim in the documents — and one label described as `investor` in one
    document and `company` in the next is one entity described twice, not two entities.
    Grouping on `(type, key)` split exactly that case into two hubs with the evidence
    divided between them, and two people who genuinely shared the hub then scored 0.

    So every description is COLLECTED and the survivor is voted on (`_most_common`), which
    makes the emitted label, type and `hub_id` a function of the evidence rather than of
    the model's output order.
    """

    key: str
    descriptions: list[tuple[str, str]] = field(default_factory=list)
    qids: list[str] = field(default_factory=list)
    recency: float = 0.0
    evidence: list[str] = field(default_factory=list)
    #: True once any candidate in the group was marked a FIELD rather than a named entity.
    #: Sticky on purpose: `MAX_FIELD_HUBS` is a ceiling on abstraction, and a group the
    #: model called a field once has to stay inside it however it labels the next mention.
    is_field: bool = False

    def describe(self, hub_type: HubType, label: str) -> None:
        self.descriptions.append((hub_type, label))

    def add_evidence(self, fact_ids: list[str]) -> None:
        for fact_id in fact_ids:
            if fact_id not in self.evidence:
                self.evidence.append(fact_id)

    def absorb(self, other: _HubGroup) -> None:
        """Fold another group into this one. Every operation is commutative on purpose."""
        self.descriptions.extend(other.descriptions)
        self.qids.extend(other.qids)
        self.add_evidence(other.evidence)
        self.recency = max(self.recency, other.recency)
        self.is_field = self.is_field or other.is_field

    @property
    def qid(self) -> str | None:
        """The group's QID, or None when its candidates named two of them equally often.

        `_unambiguous`, not `_most_common`: this value becomes `hub_id`, and a hub_id is
        an identity rather than a description. See `_unambiguous` and `_hub_qid`.
        """
        return _unambiguous(self.qids, what=f"a Wikidata QID for the hub {self.key!r}")

    @property
    def identity(self) -> tuple[str, str]:
        """The reconciled `(type, label)`. Independent of the order they were seen in.

        The fallbacks are unreachable for a live group — one is only created alongside its
        first description — and are spelled out rather than asserted so that `-O` cannot
        turn a defensive check into a crash.
        """
        hub_type = _most_common([hub_type for hub_type, _ in self.descriptions]) or "topic"
        label = _most_common([label for _, label in self.descriptions]) or self.key
        return hub_type, label


# --------------------------------------------------------------------------
# the mechanical checks, each usable on its own
# --------------------------------------------------------------------------


def _folded(text: str) -> tuple[str, list[int]]:
    """`normalize_ws`, plus typographic folding, plus a map back into `text`'s offsets.

    Every character of the returned string records the index in `text` it came from, so a
    match found in the folded form can be sliced back out of the ORIGINAL document. That
    index map is what lets the guard be forgiving about typography without ever storing a
    quote its source does not literally contain: the tolerant search finds the span, and
    the document's own characters are what gets kept.
    """
    folded: list[str] = []
    origin: list[int] = []
    pending_space = False
    for index, char in enumerate(text):
        if char.isspace():
            pending_space = bool(folded)
            continue
        if pending_space:
            folded.append(" ")
            origin.append(index)
            pending_space = False
        # `casefold` may expand one character into several (ß -> ss); each of them maps
        # back to the single source character, which is all the slice below needs.
        for piece in _TYPOGRAPHIC.get(char, char).casefold():
            folded.append(piece)
            origin.append(index)
    return "".join(folded), origin


def _is_evidence(folded_quote: str) -> bool:
    """A span too small to identify anything is not evidence, however verbatim it is."""
    return (
        len(folded_quote) >= MIN_QUOTE_CHARS
        and len(folded_quote.split(" ")) >= MIN_QUOTE_WORDS
    )


def _whole_words(haystack: str, start: int, end: int) -> bool:
    """The span may not begin or end in the middle of a word.

    Without this, `"plat"` is a verbatim span of any document containing "platform" — the
    substring test says yes to a fragment that is not a word of the document at all, and a
    short enough fragment matches everything.
    """
    before_ok = start == 0 or not haystack[start - 1].isalnum()
    after_ok = end == len(haystack) or not haystack[end].isalnum()
    return before_ok and after_ok


def cited_span(quote: str, doc: RawDoc) -> str | None:
    """The DOCUMENT's own text for `quote`, or None when the document does not carry it.

    This is the citation check (DESIGN Decision 5) and the repair in one step. The search
    is forgiving about typography — whitespace runs, letter case, and the typographic
    variants in `_TYPOGRAPHIC`, so a model that retypes `O'Neil` as `O’Neil` has not lost
    the fact — and unforgiving about everything else: the span must clear
    `MIN_QUOTE_CHARS`/`MIN_QUOTE_WORDS`, must land on word boundaries, and must differ
    from the source in nothing but typography.

    What comes back is a slice of `doc.text`, never the caller's string. That is what pays
    for the tolerance: `contracts.Provenance` is pinned on
    `normalize_ws(quote) in normalize_ws(doc.text)`, and storing the source's own
    characters keeps that true for every fact this module emits.
    """
    needle, _ = _folded(quote)
    if not _is_evidence(needle):
        return None
    haystack, origin = _folded(doc.text)
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        if _whole_words(haystack, start, end):
            return doc.text[origin[start] : origin[end - 1] + 1]
        start = haystack.find(needle, start + 1)
    return None


def is_cited(quote: str, doc: RawDoc) -> bool:
    """Whether this document carries the span `quote` points at. See `cited_span`.

    The asymmetry is the whole guard: forgiving about typography, unforgiving about
    content. A quote differing by one WORD, a paraphrase, an ellipsis join, an empty
    string, a fragment below the evidence floor and a fragment cut mid-word are all
    refused; only reflowing, re-casing and typographic punctuation are forgiven.
    """
    return cited_span(quote, doc) is not None


def recency_for(published_at: date | None, *, today: date | None = None) -> float:
    """T-3 acceptance 4: 1.0 within 12 months, 0.6 within 3 years, 0.3 older, 0.5 unknown.

    An unknown date is 0.5 rather than 0.3 on purpose: "we do not know when this happened"
    is genuinely less informative than "this happened, and it was a long time ago", and
    burying an undated hub below a decade-old one would be the wrong bias.
    """
    if published_at is None:
        return _RECENCY_UNKNOWN
    age_days = ((today or date.today()) - published_at).days
    for limit, value in _RECENCY_BANDS:
        if age_days <= limit:
            return value
    return _RECENCY_OLDER


def canonical_hub_id(hub_type: str, label: str, qid: str | None = None) -> str:
    """`wd:Q…` when a Wikidata QID is known, else `{type}:{slug(label)}`.

    `slug` comes from `arrival.util` and is never re-spelled here: two spellings of `slug`
    means two spellings of every `hub_id`, and the graph stops joining people who should
    join.
    """
    if qid:
        return f"wd:{qid}"
    return f"{hub_type}:{slug(label)}"


def _normalise_sentence(text: str) -> str:
    """Collapse whitespace runs WITHOUT case-folding — this text is displayed to a human."""
    return " ".join(text.split())


def _valid_qid(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip().upper()
    return candidate if _QID.fullmatch(candidate) else None


def _states_qid(doc: RawDoc, qid: str) -> bool:
    """The QID is believed only when the Wikidata document itself carries it.

    The same mechanical discipline as the quote check, applied to the one identifier that
    outranks every other join key: a QID the model produced from memory rather than from
    the item in front of it would silently merge two different people into one node.
    """
    if doc.source_kind != "wikidata":
        return False
    needle = normalize_ws(qid)
    return needle in normalize_ws(doc.text) or needle in normalize_ws(doc.url)


def _stated_qids(doc: RawDoc) -> set[str]:
    """Every QID this Wikidata document carries — the field `_states_qid` asks about once.

    `_states_qid` answers "does this document carry the id the model named", which is the
    right question for one candidate and the wrong one for a corpus: it cannot see that the
    document ALSO carries a different id, which is what makes the model's naming of one of
    them a choice rather than a reading. Enumerating them is what lets `_hub_qid` rank.
    """
    if doc.source_kind != "wikidata":
        return set()
    haystack = f"{doc.url}\n{doc.title}\n{doc.text}"
    return {found.group(0).upper() for found in _QID_SCAN.finditer(haystack)}


def _mentions(text: str, phrase: str) -> bool:
    """Whether `phrase` occurs in `text` as whole words, tolerant of typography only.

    The same asymmetry as `cited_span` — forgiving about reflowing, case and typographic
    punctuation, unforgiving about content — minus the evidence floor, which is a rule
    about QUOTES. A hub label may legitimately be shorter than `MIN_QUOTE_CHARS` ("IBM"),
    and unlike a quote it is not being offered as proof of a sentence; it is being used to
    ask whether this document is talking about this entity at all.
    """
    needle, _ = _folded(phrase)
    if not needle:
        return False
    haystack, _ = _folded(text)
    start = haystack.find(needle)
    while start >= 0:
        if _whole_words(haystack, start, start + len(needle)):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _states_label(doc: RawDoc, label: str) -> bool:
    """Whether the document names the entity — the corroboration a hub's `doc_id` lacks.

    A hub's LABEL is the one part of it that is evidence rather than opinion: the group
    docstring is explicit that "the label is what appears verbatim in the documents". So
    the same question `_source_doc` asks of a quote can be asked of a hub — is the thing
    the model pointed at actually in the document it pointed at — and answered the same
    mechanical way, with no LLM judgement anywhere in it.
    """
    return _mentions(f"{doc.title}\n{doc.text}", label)


def _attested_evidence(fact_ids: list[str], label: str, sources: dict[str, RawDoc]) -> list[str]:
    """A FIELD hub's evidence facts whose OWN document names it — the rest are dropped.

    A hub arriving WITH `evidence_fact_ids` is never checked against a document: the ids are
    translated by `_id_map` and believed. `_corroborated_evidence` applies the corroboration
    rule only on the `doc_id` fallback path, i.e. only to a hub the model gave no fact ids
    for. So the model may attach any label to any fact it has just produced, and the label
    is the graph-wide join key.

    For a NAMED entity that latitude is mostly harmless and removing it is measurably wrong:
    a model reading "raised its first outside money from Foundry Seed in 2019" and labelling
    the hub "Foundry Seed 2019" has composed a real name out of a real sentence, and four
    tests in `tests/extract/` encode exactly that shape. Requiring the label to be a verbatim
    span there splits hubs that ought to join, which is the defect this whole ticket exists
    to remove.

    For a FIELD it is the guard. "Venture capital" is not a name that can be slightly
    misspelled; it is a CLAIM about what somebody does, and a model that has read one funding
    round can make it about anybody. So the phrase must be IN the document the fact came
    from. That is the mechanical half; the prompt asks for the other half — that the document
    says it OF THIS PERSON — which no substring test can check, and which is stated plainly
    in the report as the limit of this design.

    Per-FACT rather than per-hub, deliberately: the field keeps the citations that name it
    and loses the ones that do not, so `digest._hub_evidence` cannot print a source for a
    field that source never mentions. A field left with nothing is unsupported and the caller
    drops it.
    """
    return [
        fact_id
        for fact_id in fact_ids
        if (doc := sources.get(fact_id)) is not None and _states_label(doc, label)
    ]


def _field_label(label: str) -> str | None:
    """The vocabulary term this proposed field names, or None to refuse it.

    Containment, not equality, and that is the whole point. The model reads "Homebrew, a
    seed-stage venture capital firm" and proposes "seed-stage venture capital"; the term it
    is a specialisation OF is `venture capital`, and normalising to that is what lets a
    second venture capitalist — whose own documents say "a New York City-based venture
    capital firm" — land on the same node. Left alone, those two spellings are two nodes and
    the pair scores zero, which is the defect this ticket exists to remove.

    Whole-word via `_mentions`, so "developer tools" is not found inside "developer toolset"
    by accident. The longest matching term wins, so a label containing both a broad and a
    narrow entry keeps the narrow one. Ties are broken lexicographically, never by set
    iteration order, which is not stable across processes.
    """
    matches = sorted(
        (term for term in FIELD_HUB_VOCABULARY if _mentions(label, term)),
        key=lambda term: (-len(term), term),
    )
    if not matches:
        log.info("refusing the field %r: it names no term in the field vocabulary", label)
        return None
    return matches[0]


def _confirmed_place(claimed: str | None, person: PersonRef) -> str | None:
    """The place the ROSTER states for this member, or None. The model may not add one.

    `PersonRef.details` is the club's own record of its member — the one thing in this
    pipeline that is first-party about where somebody is based, and the roster's own comment
    is careful about it: "the pipeline verifies them, it does not trust them". So this is
    `_hub_qid`'s discipline applied to a place: **confirm or refuse, never adopt.** The model
    reads which free-text detail is a location, which it is good at and a regex is not
    ("Boulder, Colorado", "Sydney, Australia", "formerly Palantir; essays at nabeelqu.co"),
    and the answer is believed only where the details literally carry it.

    Whole-word, typography-tolerant, via `_mentions` — the same asymmetry as every other
    check here. A member whose details name no place gets None, which is a real answer and
    not a failure: `_city_veto` reads it as "the club has nothing to say", not as "nowhere".
    """
    place = _normalise_sentence(claimed or "")
    if not place:
        return None
    if not _mentions("\n".join(person.details), place):
        log.info(
            "discarding the claimed location %r for %s: the roster details do not state it",
            place,
            person.name,
        )
        return None
    return place


def _hub_qid(qid: str | None, label: str, evidence_docs: list[RawDoc]) -> str | None:
    """The QID this hub has EARNED from its own evidence documents, or None.

    `_states_qid` alone was `any(...)` over those documents, which asks only whether SOME
    document in hand carries the string. A person's own Wikidata item names their employer,
    so for a hub about the employer that question answers yes about the PERSON's id — and
    `hub_id` is the graph-wide join key, so the wrong answer welds two entities onto one
    node for every person in the graph (`graph._canonical_hub_ids`: a `wd:` id wins the
    election however few carriers state it).

    `resolve._best` is the shape of the fix, ported: collect EVERY candidate the evidence
    offers, rank them on evidence — here, how many of the hub's evidence documents state
    both that QID and this hub's own label — and REFUSE when the top two tie. The model's
    id is believed only when it is the unique winner.

    Confirm-or-refuse, never adopt: a QID the model did not name is not taken even when it
    wins, because "this document states a QID and also mentions this label" does not make
    the QID that label's. The frozen corpus proves it — the item Q900000411 belongs to Ilse
    Vandermolen and states her employer Belmarch Optics — so adopting the winner would be
    exactly the wrong-entity merge this function exists to prevent. Ranking may only ever
    veto the model, never speak for it.
    """
    if qid is None:
        return None
    scores: dict[str, int] = {}
    for doc in evidence_docs:
        names_label = _states_label(doc, label)
        offered = _stated_qids(doc) | ({qid} if _states_qid(doc, qid) else set())
        for found in offered:
            scores[found] = scores.get(found, 0) + (1 if names_label else 0)
    if qid not in scores:
        # Unchanged from the original guard: nothing we are holding states it at all, so
        # the model produced it from memory rather than from the item in front of it.
        return None
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        log.info(
            "refusing a QID for hub %r: %s are equally evidenced by its own documents, so "
            "the model's pick is a choice rather than a reading",
            label,
            [found for found, _ in ranked[:2]],
        )
        return None
    winner = ranked[0][0]
    if winner != qid:
        log.info(
            "refusing the QID %s for hub %r: %s is better evidenced by the hub's own "
            "documents, so the model's choice is not a reading of them",
            qid,
            label,
            winner,
        )
        return None
    return qid


# --------------------------------------------------------------------------
# prompting
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""\
You read documents about one person and pull out short, checkable facts about them, plus \
the notable entities those facts connect them to.

Rules, in order of importance:

1. QUOTE VERBATIM. Every fact carries a `quote` copied word for word from the text of the \
document you took it from. The quote is checked against the document mechanically and any \
fact whose quote is not found is thrown away, so never paraphrase, never tidy, never join \
two spans with an ellipsis. Copying a slightly longer span is always safer than editing one.
2. ONE SENTENCE, AT MOST {MAX_FACT_CHARS} CHARACTERS. Longer facts are discarded outright.
3. STAY WITH THE DOCUMENT. Set `doc_id` to the id of the block the fact came from. Do not \
merge two documents into one fact and do not use anything you know from outside them.
4. FACTS ABOUT THE PERSON. Something about the company is a fact when it says something \
about this person's work, affiliations, interests or recent activity.
5. CATEGORY. Use `non_obvious` only for material a well-prepared person would NOT already \
have from the subject's own bio or the first page of search results. Always also fill \
`natural_category` with the best category ignoring non-obviousness.
6. CONFIDENCE. Fill `confidence` on every fact. A fact below 0.7 is withheld from staff \
entirely, and an omitted one defaults below that line, so a fact you are sure of and did \
not rate is a fact nobody sees.
7. HUBS are the things worth joining two people on: companies, investors, schools, \
boards, events, causes, cities, technologies, named topics, named people. Give each one \
the `evidence_fact_ids` of the facts in this same answer that support it. Use the spelling \
the document uses: a hub whose label is not in the document it cites is thrown away. Skip \
generic labels that would connect everybody — {", ".join(sorted(STOP_HUB_LABELS))} and the \
like. Set `qid` only when the document is a Wikidata item that states the QID.
8. WHERE THEY ARE BASED. If the Known details name the place this person is based, copy it \
into `based_in` exactly as the details write it, and — when one of these documents names \
that place too — emit it as a `city` hub as well. Leave `based_in` out when the details \
name no place. A place the details do not state is discarded, so never put one there.
9. WHAT THEY WORK ON, not only who they work for. Two people almost never share an \
employer or a portfolio company, so alongside the named entities emit at most \
{MAX_FIELD_HUBS} hubs for the FIELD this person works in, set `field` to true on them, and \
leave their `type` as `topic`. Use one of these exact phrases and no other — anything else \
is discarded: {"; ".join(sorted(FIELD_HUB_VOCABULARY))}. Two conditions, both required: the \
phrase appears in a document you were given, and that document says it OF THIS PERSON — not \
of some company the document happens to mention. Where none of the phrases fits the person, \
emit none: a field nobody can point at in the text is worse than no field at all.

Return nothing you cannot point at in the text.\
"""


def _document_block(doc: RawDoc) -> str:
    published = doc.published_at.isoformat() if doc.published_at else "unknown"
    return (
        f"<document doc_id=\"{doc.doc_id}\" source_kind=\"{doc.source_kind}\" "
        f"published_at=\"{published}\">\n"
        f"url: {doc.url}\n"
        f"title: {doc.title}\n\n"
        f"{doc.text}\n"
        f"</document>"
    )


def _user_prompt(person: PersonRef, docs: tuple[RawDoc, ...]) -> str:
    details = "; ".join(person.details) if person.details else "no further details"
    ids = ", ".join(doc.doc_id for doc in docs)
    blocks = "\n\n".join(_document_block(doc) for doc in docs)
    return (
        f"Person: {person.name}\n"
        f"Known details: {details}\n\n"
        f"Documents ({len(docs)}), whose doc_id values are {ids}:\n\n"
        f"{blocks}\n\n"
        f"Extract the facts and hubs supported by these documents. "
        f"Every fact's doc_id must be one of: {ids}."
    )


async def _ask(
    llm: LLMClient, person: PersonRef, docs: tuple[RawDoc, ...], tally: ExtractionStats
) -> ExtractionResult | None:
    """One structured call. A failing batch costs its own documents, never the build."""
    tally.llm_calls += 1
    try:
        answer = await llm.structured(
            system=_SYSTEM_PROMPT,
            user=_user_prompt(person, docs),
            schema=ExtractionResult,
            max_tokens=MAX_TOKENS_PER_CALL,
            cache_prefix=True,
        )
    except LLMError:
        tally.llm_failures += 1
        log.warning(
            "extraction call failed for %d document(s): %s",
            len(docs),
            ", ".join(doc.doc_id for doc in docs),
        )
        return None
    if not isinstance(answer, ExtractionResult):
        # The LLMClient contract says an instance of some OTHER model is a violation, not
        # a response. Losing the batch is the honest outcome; inventing facts is not.
        tally.llm_failures += 1
        log.warning("extraction call returned %s, not ExtractionResult", type(answer).__name__)
        return None
    return answer


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def _accepted_docs(resolution: Resolution, docs: list[RawDoc]) -> list[RawDoc]:
    """The accepted documents, in the resolver's order, deduplicated.

    Only these are ever prompted, which is what makes "every surviving fact cites an
    accepted document" true by construction rather than by a filter afterwards.
    """
    by_id: dict[str, RawDoc] = {}
    for doc in docs:
        by_id.setdefault(doc.doc_id, doc)
    accepted: list[RawDoc] = []
    seen: set[str] = set()
    for doc_id in resolution.accepted_doc_ids:
        doc = by_id.get(doc_id)
        if doc is None or doc_id in seen:
            continue
        seen.add(doc_id)
        accepted.append(doc)
    return accepted


def _source_doc(
    candidate: CandidateFact, quote: str, batch: tuple[RawDoc, ...], by_id: dict[str, RawDoc]
) -> tuple[RawDoc, str] | None:
    """The document that actually contains this quote and its own text for it, or None.

    The id the model claimed is tried FIRST and only accepted if the quote really is in
    that document. The fallback — another document in the same batch that does contain the
    span — repairs a mixed-up id without inventing a citation, because a document holding
    the span is a true source of it whatever the model wrote down.

    The fallback fires only when the batch answers UNAMBIGUOUSLY. Two documents both
    carrying the span is not a repair, it is a coin toss: `source_kind`, `url` and
    `published_at` all come out of whichever one the loop reached first, and
    `published_at` feeds `recency_for` and therefore T-5's score. Measured on the
    pre-repair code, one candidate and two documents differing only in list order came
    back as `search`/2026-02-11 or as `hn`/2019-03-02. Refusing costs one fact; guessing
    prints a citation nobody chose under a sentence a host reads out loud.
    """
    declared = by_id.get(candidate.doc_id.strip())
    if declared is not None:
        span = cited_span(quote, declared)
        if span is not None:
            return declared, span
    found: list[tuple[RawDoc, str]] = []
    for doc in batch:
        span = cited_span(quote, doc)
        if span is not None:
            found.append((doc, span))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        log.info(
            "dropping a fact whose quote is in %d prompted documents (%s); the batch cannot "
            "say which one it came from",
            len(found),
            ", ".join(doc.doc_id for doc, _ in found),
        )
    return None


def _category_for(candidate: CandidateFact, doc: RawDoc, tally: ExtractionStats) -> FactCategory:
    """T-3 acceptance 5: `non_obvious` needs BOTH the model's flag and an eligible source.

    Neither half alone: labelling by source kind makes every wayback fact non-obvious, and
    trusting the flag alone lets the subject's own about page — literally the first page —
    fill the "not on the first page" slot.
    """
    if candidate.category != "non_obvious":
        return candidate.category
    if doc.source_kind in NON_OBVIOUS_ELIGIBLE_KINDS:
        return "non_obvious"
    tally.downgraded_non_obvious += 1
    natural = candidate.natural_category
    return natural if natural != "non_obvious" else FALLBACK_CATEGORY


def _build_fact(
    candidate: CandidateFact,
    doc: RawDoc,
    span: str,
    fact_id: str,
    tally: ExtractionStats,
) -> Fact:
    """`span` is the document's own text for the quote, never the model's retyping of it."""
    return Fact(
        fact_id=fact_id,
        text=_normalise_sentence(candidate.text),
        category=_category_for(candidate, doc, tally),
        provenance=Provenance(
            doc_id=doc.doc_id,
            url=doc.url,
            source_kind=doc.source_kind,
            quote=_normalise_sentence(span),
            published_at=doc.published_at,
            retrieved_at=doc.fetched_at,
            confidence=min(1.0, max(0.0, float(candidate.confidence))),
        ),
    )


def _id_map(
    claims: list[tuple[str, str]], ours: list[str], reserved: frozenset[str]
) -> dict[str, str]:
    """Translate the ids the MODEL used into the ids WE assigned. Two refusals, no repairs.

    * **Ours always win, and they are written last.** Our ids are `{doc_id}-f{n}` and the
      prompt prints every `doc_id`, so `"<doc_id>-f1"` is a string the model can guess.
      Folding both namespaces into one mapping with `setdefault`, in the order the facts
      were built, let a claim staked on that string by an earlier candidate sit in the map
      before fact #1 existed — and `setdefault` then declined to record the real one, so a
      hub naming the id CORRECTLY reached the wrong fact. `reserved` extends the same
      protection to the ids earlier batches already handed out, which is the cross-call
      version of the bug `taste._absorb` records ("it may name a fact from an EARLIER
      batch. `rulings` outlives one call").
    * **An id two facts claimed is deleted, not resolved.** The schema asks for uniqueness
      in prose only ("Any string will do"), so a model reusing `"f1"` across fifteen facts
      is ordinary input rather than an attack — and `setdefault` resolved the collision
      first-wins, which is the model's output order. Every hub pointing at the second fact
      silently claimed the first as its evidence, and those ids are printed as citations by
      `digest._hub_evidence` and `web.render._hub_evidence`. This is `taste._positions`'
      rule verbatim: both sentences appear under that id, so no reference to it can be
      attributed to one of them rather than the other.

    A blank claim is not an id, for the reason `_positions` gives: a model gets `""` for
    free by omitting the field.
    """
    mine = {fact_id: fact_id for fact_id in ours}
    claimed: dict[str, str] = {}
    ambiguous: set[str] = set()
    for name, fact_id in claims:
        if not name:
            continue
        if name in claimed:
            ambiguous.add(name)
        claimed[name] = fact_id
    for name in sorted(ambiguous):
        del claimed[name]
        log.info(
            "two facts in one extraction answer claim the id %r; it resolves to neither, "
            "because no hub referencing it can be attributed to one of them",
            name,
        )

    resolved = dict(mine)
    for name, fact_id in claimed.items():
        if name in mine or name in reserved:
            log.info(
                "ignoring the claimed fact id %r: it is one of ours, and a hub naming it "
                "means the fact we assigned it to",
                name,
            )
            continue
        resolved[name] = fact_id
    return resolved


def _collect_facts(
    result: ExtractionResult,
    batch: tuple[RawDoc, ...],
    counters: dict[str, int],
    tally: ExtractionStats,
    reserved: frozenset[str] = frozenset(),
) -> tuple[list[Fact], dict[str, str], dict[str, RawDoc], dict[str, list[Fact]]]:
    """Check, build and identify every candidate fact from one answer.

    Returns the surviving facts, the map from the id the model used to the id we assigned
    (so a hub's `evidence_fact_ids` can be translated and dangling references dropped), the
    source document of each surviving fact, and the surviving facts per document.

    `reserved` is every id assigned by EARLIER batches; see `_id_map`.
    """
    by_id = {doc.doc_id: doc for doc in batch}
    facts: list[Fact] = []
    sources: dict[str, RawDoc] = {}
    kept_by_doc: dict[str, list[Fact]] = {}
    claims: list[tuple[str, str]] = []

    for candidate in result.facts:
        tally.facts_proposed += 1
        text = _normalise_sentence(candidate.text)
        quote = _normalise_sentence(candidate.quote)
        if not text or not quote:
            tally.dropped_empty += 1
            continue
        if len(text) > MAX_FACT_CHARS:
            # Dropped rather than truncated: a sentence cut at 200 characters can lose the
            # clause that carried its meaning, and a fact shown to staff has to be true.
            tally.dropped_over_length += 1
            log.info("dropping a %d-character fact (cap is %d)", len(text), MAX_FACT_CHARS)
            continue
        located = _source_doc(candidate, quote, batch, by_id)
        if located is None:
            tally.dropped_uncited += 1
            log.info(
                "dropping an uncited fact; its quote is not a whole-word span of exactly one "
                "prompted document"
            )
            continue
        doc, span = located

        index = counters.get(doc.doc_id, 0)
        counters[doc.doc_id] = index + 1
        fact_id = f"{doc.doc_id}-f{index}"

        fact = _build_fact(candidate, doc, span, fact_id, tally)
        facts.append(fact)
        tally.facts_kept += 1
        sources[fact_id] = doc
        kept_by_doc.setdefault(doc.doc_id, []).append(fact)
        claims.append((candidate.fact_id.strip(), fact_id))

    id_map = _id_map(claims, [fact.fact_id for fact in facts], reserved)
    return facts, id_map, sources, kept_by_doc


def _source_hub_doc(
    label: str, declared_id: str, batch: tuple[RawDoc, ...], by_id: dict[str, RawDoc]
) -> RawDoc | None:
    """The prompted document that actually names this entity, or None.

    `_source_doc` for hubs, and deliberately the same three steps: the id the model claimed
    is tried FIRST and accepted only if the document really names the label; a batch that
    answers UNAMBIGUOUSLY repairs a mixed-up id; and a batch that answers twice refuses.

    The refusal matters more here than it does for a fact, because what the caller does
    with the answer is attach a document's facts to the hub. Two documents naming the
    entity is not a repair, it is a coin toss over which one's `published_at` becomes
    `Hub.recency` — an edge weight in T-5's score — and over which document's facts become
    the hub's citations.
    """
    declared = by_id.get(declared_id.strip())
    if declared is not None and _states_label(declared, label):
        return declared
    found = [doc for doc in batch if _states_label(doc, label)]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        log.info(
            "refusing to guess a source document for the hub %r: %d prompted documents "
            "name it (%s) and the model pointed at none of them",
            label,
            len(found),
            ", ".join(doc.doc_id for doc in found),
        )
    return None


def _corroborated_evidence(
    label: str,
    declared_id: str,
    batch: tuple[RawDoc, ...],
    by_id: dict[str, RawDoc],
    kept_by_doc: dict[str, list[Fact]],
) -> list[str]:
    """The facts that may stand as evidence for a hub the model gave no fact ids for.

    Two narrowings on what used to be `list(kept_by_doc[declared.doc_id])`, i.e. the whole
    of whatever document the model named, believed without a check:

    * the document must NAME the entity (`_source_hub_doc`). `CandidateHub.doc_id` is a
      claim, and this module's own best function accepts a claim only when the document
      carries the thing claimed;
    * within that document, only the facts that themselves name the entity. Bulk-attaching
      is what reopens SPEC R11. `research._supported_hubs` drops a hub only when EVERY
      evidence fact it can resolve was excluded by taste, so one borrowed survivor keeps a
      hub alive — and the leak that function's docstring records is exactly this shape: a
      `home_or_property` sentence excluded, and `city:pecan-street` left standing as a
      joinable node and a match reason because the same document also carried innocuous
      facts. A hub evidenced only by facts that mention it goes down with them.
    """
    doc = _source_hub_doc(label, declared_id, batch, by_id)
    if doc is None:
        return []
    return [
        fact.fact_id
        for fact in kept_by_doc.get(doc.doc_id, [])
        if _mentions(f"{fact.text}\n{fact.provenance.quote}", label)
    ]


def _collect_hubs(
    result: ExtractionResult,
    batch: tuple[RawDoc, ...],
    id_map: dict[str, str],
    sources: dict[str, RawDoc],
    kept_by_doc: dict[str, list[Fact]],
    groups: dict[str, _HubGroup],
    tally: ExtractionStats,
) -> None:
    """Canonicalise, filter and merge every candidate hub from one answer, into `groups`."""
    by_id = {doc.doc_id: doc for doc in batch}
    for candidate in result.hubs:
        tally.hubs_proposed += 1
        label = _normalise_sentence(candidate.label)
        key = slug(label)
        if not label or not key:
            continue
        if label.casefold() in STOP_HUB_LABELS:
            # LABELS, not types and not id prefixes. See STOP_HUB_LABELS.
            tally.dropped_stop_hubs += 1
            continue
        if candidate.field:
            if len(label.split()) < MIN_FIELD_HUB_WORDS:
                # A one-word field is the shape of every DESIGN Decision 3 stop hub, whether
                # or not this particular word is on the list. See MIN_FIELD_HUB_WORDS.
                tally.dropped_stop_hubs += 1
                log.info("dropping the one-word field %r as too vague to join anyone", label)
                continue
            term = _field_label(label)
            if term is None:
                tally.dropped_stop_hubs += 1
                continue
            # Normalised BEFORE anything downstream sees it: the vocabulary term is the join
            # key, so the attestation below, the group key and the emitted label are all the
            # canonical form rather than this document's specialisation of it.
            label = term
            key = slug(label)

        claimed = [id_map[raw] for raw in candidate.evidence_fact_ids if raw in id_map]
        evidence = _attested_evidence(claimed, label, sources) if candidate.field else claimed
        if claimed and not evidence:
            log.info(
                "the field hub %r cites %d fact(s) whose own documents never name it",
                label,
                len(claimed),
            )
        if not evidence:
            evidence = _corroborated_evidence(
                label, candidate.doc_id, batch, by_id, kept_by_doc
            )
        if not evidence:
            # A hub whose every evidence fact failed the citation check -- or whose every
            # cited document never names it -- is exactly as unsupported as those facts were.
            tally.dropped_unsupported_hubs += 1
            continue

        evidence_docs = [sources[fact_id] for fact_id in evidence]
        qid = _hub_qid(_valid_qid(candidate.qid), label, evidence_docs)
        recency = max(recency_for(doc.published_at) for doc in evidence_docs)

        group = groups.get(key)
        if group is None:
            group = _HubGroup(key=key)
            groups[key] = group
        # A field is an abstraction and `topic` is the only HubType that can hold one:
        # `contracts.HubType` is frozen by convention and nine tickets import it, so the
        # abstraction layer takes the type whose boost DESIGN already prices at 1.0 rather
        # than inventing an eleventh member. See DECISIONS in the T-077 report.
        group.describe("topic" if candidate.field else candidate.type, label)
        group.is_field = group.is_field or candidate.field
        group.add_evidence(evidence)
        group.recency = max(group.recency, recency)
        if qid is not None:
            group.qids.append(qid)


def _merge_groups(
    groups: dict[str, _HubGroup], order: dict[str, int], tally: ExtractionStats
) -> list[Hub]:
    """One `Hub` per canonical id, evidence merged, everything about it order-independent.

    Two groups reach one id only through a shared Wikidata QID — two labels for one item,
    `Foundry Seed 2019` and `Foundry Capital` on `wd:Q4242`. That branch used to keep the
    FIRST group's label and type, and "first" was the model's output ordering, so
    permuting the model's list moved the type and therefore T-5's `TYPE_BOOST`
    (investor 1.5 vs city 0.5) and the score with it. Now the descriptions are pooled and
    voted on, which is commutative, and every other field already was: evidence is a
    union, recency a max.

    `order` maps a fact id to the position its fact was created in, so the merged evidence
    list reads in discovery order however the model happened to list its hubs.
    """
    merged: dict[str, _HubGroup] = {}
    for group in groups.values():
        hub_type, label = group.identity
        hub_id = canonical_hub_id(hub_type, label, group.qid)
        existing = merged.get(hub_id)
        if existing is None:
            merged[hub_id] = group
            continue
        existing.absorb(group)

    surplus = _surplus_field_ids(merged, order)
    hubs = []
    for hub_id, group in merged.items():
        if hub_id in surplus:
            tally.dropped_stop_hubs += 1
            continue
        hub_type, label = group.identity
        hubs.append(
            Hub(
                hub_id=hub_id,
                label=label,
                type=hub_type,
                recency=group.recency,
                evidence_fact_ids=sorted(
                    group.evidence, key=lambda fact_id: order.get(fact_id, len(order))
                ),
            )
        )
    hubs.sort(key=lambda hub: hub.hub_id)
    tally.hubs_kept += len(hubs)
    return hubs


def _surplus_field_ids(merged: dict[str, _HubGroup], order: dict[str, int]) -> set[str]:
    """The FIELD hubs past `MAX_FIELD_HUBS`, by an order that is not the model's.

    The cap is what keeps the abstraction layer from becoming the thing DESIGN Decision 3
    banned. A named entity is self-limiting — nobody has forty employers — but a field is
    generated rather than found, and a model asked for "what they work on" will happily
    produce eight overlapping paraphrases of one career, every one of which joins its owner
    to somebody. Two per person is the whole abstraction budget.

    Ranked by how many facts evidence the field (a field two documents say of somebody
    outranks one said once), then by the position that evidence was DISCOVERED at, then by
    hub id. Deterministic, and never the order the model listed its hubs in — the same
    property `_merge_groups` and `_hub_identity` are built for.
    """
    fields = [(hub_id, group) for hub_id, group in merged.items() if group.is_field]
    if len(fields) <= MAX_FIELD_HUBS:
        return set()
    ranked = sorted(
        fields,
        key=lambda item: (
            -len(item[1].evidence),
            min((order.get(f, len(order)) for f in item[1].evidence), default=len(order)),
            item[0],
        ),
    )
    dropped = {hub_id for hub_id, _ in ranked[MAX_FIELD_HUBS:]}
    log.info(
        "keeping %d field hub(s) and dropping %s: a person's abstraction budget is %d",
        MAX_FIELD_HUBS,
        sorted(dropped),
        MAX_FIELD_HUBS,
    )
    return dropped


def _place_candidates(place: str) -> list[str]:
    """The roster's place narrowed to its leading segment first: "Boulder, Colorado" first
    tries "Boulder".

    A purely syntactic narrowing of a string the ROSTER wrote, never a widening: "City,
    Region" is how the roster spells four of its ten entries and how no document spells any
    of them. The SHORT form is tried first because it is the joinable one — a member whose
    roster says "Sydney, Australia" and one whose roster says "Sydney" must reach the same
    node, and only the head does that. Nothing is invented: the shorter form is a prefix of
    the longer, and both are still checked against a document before anything is emitted.
    """
    head = _normalise_sentence(place.split(",")[0])
    return [place] if head == place or not head else [head, place]


def _roster_city_hub(
    place: str, facts: list[Fact], sources: dict[str, RawDoc], order: dict[str, int]
) -> Hub | None:
    """The member's own city as a hub, when their documents corroborate the roster.

    Rule 8 of the prompt asks the model for this and the model usually obliges, but "usually"
    is not a pipeline, and the shape it obliges in is not joinable. Measured on the live
    corpus: with the prompt alone, one member's city arrived as `San Francisco` and another's
    as `San Francisco, California, United States` — two nodes, no join, and a spoken line
    reading "both rooted in San Francisco, California, United States". So the city is
    CONSTRUCTED here and the model contributes exactly one thing: which free-text roster
    detail was a place (`_confirmed_place`), which it is good at and a regex is not.

    Both halves are required and neither is sufficient. The roster says the place is this
    member's — that is the part no document states, and the part "Hackbright Academy, a San
    Francisco-based coding school" gets wrong. A document names it — that is the part the
    roster cannot evidence, and without it the hub would cite nothing and print no source
    under the match reason.

    Evidence is every fact whose own DOCUMENT names the place, which is the rule the frozen
    T-3 suite states for all hubs; where some of those facts name the place in their own
    sentence or quote, only those are kept, because a citation whose text carries the words
    is the better one to print. Requiring that STRICTLY was measured to cost two of the four
    members whose documents genuinely place them: one Wikipedia page says San Francisco in
    prose the extractor did not turn into a fact.
    """
    for candidate in _place_candidates(place):
        from_doc = [
            fact
            for fact in facts
            if (doc := sources.get(fact.fact_id)) is not None
            and _states_label(doc, candidate)
        ]
        if not from_doc:
            continue
        spoken = [
            fact
            for fact in from_doc
            if _mentions(f"{fact.text}\n{fact.provenance.quote}", candidate)
        ]
        evidence = spoken or from_doc
        return Hub(
            hub_id=canonical_hub_id("city", candidate),
            label=candidate,
            type="city",
            recency=max(recency_for(sources[fact.fact_id].published_at) for fact in evidence),
            evidence_fact_ids=sorted(
                (fact.fact_id for fact in evidence),
                key=lambda fact_id: order.get(fact_id, len(order)),
            ),
        )
    log.info("no document corroborates the roster place %r; emitting no city hub", place)
    return None


def _place_hubs(
    hubs: list[Hub],
    place: str | None,
    facts: list[Fact],
    sources: dict[str, RawDoc],
    order: dict[str, int],
    tally: ExtractionStats,
) -> list[Hub]:
    """Replace every model-proposed city with the one the roster and the documents agree on.

    A city is the only hub type this pipeline has a first-party answer for, and also the one
    whose false positives are cheapest to produce: every document about a person names places
    that are not theirs. Measured on the live corpus — "author of New York Times bestseller
    The Lean Startup" for a member the roster puts in San Francisco. So where the club states
    a place, the model's city hubs are not filtered, they are REPLACED: one node, one
    spelling, the roster's own shortest form, evidenced by documents. R2's refuse-to-guess
    doctrine, applied to somewhere a host will say out loud.

    Where the club states nothing (`place` is None) this does nothing at all, and a city the
    documents genuinely support — Wikidata's structured "work location: New York City" for a
    member whose roster gives no city — stands untouched. Silence in the roster is not a
    claim that the member is nowhere.

    Runs after `_merge_groups` rather than inside `_collect_hubs` because `based_in` is a
    reading of the whole answer set — one batch of documents may carry it and the next may
    not — and because the construction needs every fact, not one batch's.
    """
    if place is None:
        return hubs
    kept = [hub for hub in hubs if hub.type != "city"]
    dropped = len(hubs) - len(kept)
    if dropped:
        log.info(
            "replacing %d model-proposed city hub(s) with the roster's own place %r",
            dropped,
            place,
        )
    tally.hubs_kept -= dropped
    mine = _roster_city_hub(place, facts, sources, order)
    if mine is not None:
        tally.hubs_kept += 1
        kept.append(mine)
    kept.sort(key=lambda hub: hub.hub_id)
    return kept


async def extract(
    person: PersonRef,
    resolution: Resolution,
    docs: list[RawDoc],
    llm: LLMClient,
    *,
    stats: ExtractionStats | None = None,
) -> tuple[list[Fact], list[Hub]]:
    """Turn the resolver's accepted documents into cited facts and canonical hubs.

    Only `resolution.accepted_doc_ids` are read, in the resolver's own order, at most
    `MAX_DOCS_PER_CALL` per structured call. Every returned `Fact` carries a quote that is
    verbatim in the document it names, is at most `MAX_FACT_CHARS` long, and leaves here
    un-excluded — T-4 makes the taste decisions.

    Pass `stats` to receive the counts, including how many facts the citation check threw
    away; they are logged either way. Every counter ACCUMULATES, so one `ExtractionStats`
    may be shared across a whole roster to get the build-wide numbers.
    """
    tally = stats if stats is not None else ExtractionStats()
    accepted = _accepted_docs(resolution, docs)
    tally.documents_prompted += len(accepted)
    if not accepted:
        log.info("nothing to extract for %s: the resolver accepted no documents", person.name)
        return [], []

    facts: list[Fact] = []
    groups: dict[str, _HubGroup] = {}
    counters: dict[str, int] = {}
    # Discovery order, so a merged hub's evidence list does not depend on the order the
    # model listed its hubs in. Spans every batch, as the groups themselves do.
    fact_order: dict[str, int] = {}
    # Every fact's source document, pooled across batches: `_place_hubs` runs once, after
    # the last answer, and needs to reach a fact any batch produced.
    all_sources: dict[str, RawDoc] = {}
    places: list[str] = []

    for batch in batched(accepted, MAX_DOCS_PER_CALL):
        result = await _ask(llm, person, batch, tally)
        if result is None:
            continue
        # `fact_order` holds every id assigned so far, which is exactly the set a claimed
        # id may not overwrite. See `_id_map`.
        batch_facts, id_map, sources, kept_by_doc = _collect_facts(
            result, batch, counters, tally, frozenset(fact_order)
        )
        for fact in batch_facts:
            fact_order.setdefault(fact.fact_id, len(fact_order))
        facts.extend(batch_facts)
        all_sources.update(sources)
        _collect_hubs(result, batch, id_map, sources, kept_by_doc, groups, tally)
        confirmed = _confirmed_place(result.based_in, person)
        if confirmed is not None:
            places.append(confirmed)

    hubs = _merge_groups(groups, fact_order, tally)
    # `_most_common`, not "the first batch that answered": which documents a batch happened
    # to hold is not evidence about where somebody lives, and every other reconciliation in
    # this module is a vote for the same reason.
    hubs = _place_hubs(hubs, _most_common(places), facts, all_sources, fact_order, tally)
    log.info(
        "extracted %d fact(s) and %d hub(s) for %s from %d document(s); dropped %d uncited, "
        "%d over-length, %d stop-hub(s), %d unsupported hub(s)",
        len(facts),
        len(hubs),
        person.name,
        tally.documents_prompted,
        tally.dropped_uncited,
        tally.dropped_over_length,
        tally.dropped_stop_hubs,
        tally.dropped_unsupported_hubs,
    )
    return facts, hubs
