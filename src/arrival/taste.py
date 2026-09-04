"""R11-R14 — the taste filter: the "seen vs. dossiered" line, encoded so it fails closed.

The exclusion exists so the club does not surface what a member did not choose to make
public. Two principles decide every hard case, and both are implemented here rather than
being left to a keyword list:

**1. SUBJECT.** R11 protects the *person*. A commercial event that happens to a *company*
— a funding round, an acquisition, a trademark settlement — is a business fact about an
organisation. A filter that kills it because it contains a money word, or the word
"settled", destroys the product while looking careful. Where the sentence is about the
person's own money, own court record, own health, own home, own family or own politics it
is excluded no matter how professional the framing. So the personal cues here are anchored
to the subject (``their salary``, ``their personal stake``, ``they personally cleared``)
rather than to the bare vocabulary (``salary``, ``stake``, ``cleared``).

**2. DISCLOSURE.** Health and personal history are excluded when the person did not put
them in public themselves. A member's own published professional material is theirs to
have said — repeating it back is hospitality, not surveillance. So a third party revealing
someone's struggle ("a colleague told a reporter…") is a deterministic exclude, while the
same topic in the person's own voice defers to the classifier, which reads the framing.

**3. PREDICATION.** A forbidden word can appear in a sentence in two grammatically
different jobs, and only one of them is an R11 fact. Either the category is *predicated of
the member* — their registry entry, their psychiatrist, their salary — or it is the
*subject matter of their work*: the market a company sells into, the data a product
indexes, the beat a writer covers. SPEC Q4 says this roster is investors and writers, so
the second job is their core professional vocabulary: a cancer-imaging startup, a medical
records product, a mortgage analytics firm, an equity research desk, a children's-media
studio, a divorce-mediation tool, an independent contractor. No word list separates these
from "their net worth" and "the sex-offender registry", because the *word is the same*.
The position is not. Two surface tests, applied to each individual cue MATCH rather than
to the sentence, do the separating:

* :func:`_head_noun_follows` — the cue is immediately followed, inside the same noun
  phrase, by a professional head noun ("cancer **-imaging startup**", "their portfolio
  **companies**", "registered independent **contractor**"). The category word is then an
  attributive modifier naming a sector, not a predicate about the member.
* :func:`_product_verb_precedes` — the cue is the direct object of a making-or-selling
  verb ("to **track** court records", "**underwrites** property records data"). The
  member is then the one doing professional work ON the category, not the one it is about.

Both are deliberately local. A whole-sentence test would be a hole: "They founded a
cancer-imaging startup, and their psychiatrist spoke to a podcast" must still lose its
second clause. The windows therefore stop at a comma, a full stop, a preposition or a
determiner — which is why "their home **above** Ravensworth Hill has a detached studio"
and "they now speak about mental health **in** engineering teams" are unaffected.

DESIGN Decision 6 stages that judgment:

``apply_taste_rules``  the cheap deterministic layer. It settles a sentence only when it
                       carries an unambiguous R11 surface marker whose subject can only be
                       the person, or when it carries a dominant professional marker and
                       no live category cue. Everything else it marks *unsure*.
``apply_taste``        runs the rule layer, sends **only** the unsure facts to the LLM, and
                       **fails closed**: a fact still unsure after both stages is excluded
                       with reason ``low_confidence``. A sentence whose only content is an
                       unexplained absence lands here on purpose — the system genuinely
                       cannot tell a sabbatical from a medical leave, and guessing a
                       category would invent the very thing R11 protects.

**A ruling is addressed by POSITION, never by the id the model echoed.** The classifier is
asked to key its answers by fact id, so every id that comes back is a string the model
chose, and this stage is the one where mis-addressing is most expensive: a verdict landing
on the wrong fact does not lose a fact, it PUBLISHES one — the R11 sentence keeps the
innocent fact's ``keep``. So an echoed id is treated as a *claim* to be resolved against
the ids that call actually sent (:func:`_positions`), and what survives is stored under
**our** index into the unsure list. This is the same refusal ``resolve._verdict_from``
states outright ("the doc_id is OURS, never the model's echo of it") and
``extract._collect_facts`` implements with its ``id_map``. Anything unresolvable — an id no
prompt carried, two contradictory rulings for one id, an id two facts in one prompt share
— is not a ruling at all, and the fact it might have been about fails closed.

``is_displayable`` is a separate, later gate with three *independent* clauses (R12): not
excluded, confidence >= 0.7, and a whitelisted source kind. They are deliberately not
collapsed — a fact can be perfectly tasteful and still be undisplayable because it came
from a source kind (``fec``, ``courtlistener``) the design never permits on screen.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel, Field

from arrival.contracts import ExclusionReason, Fact, LLMClient, LLMError, SourceKind

log = logging.getLogger(__name__)

__all__ = [
    "CONFIDENCE_FLOOR",
    "DISPLAYABLE_KINDS",
    "EXCLUSION_POLICY",
    "NEVER_DISPLAYABLE_KINDS",
    "R11_CATEGORIES",
    "RuleVerdict",
    "TasteRuling",
    "TasteRulings",
    "apply_taste",
    "apply_taste_rules",
    "is_displayable",
    "rule_verdict",
]


# --------------------------------------------------------------------------- R12 display gate

#: R12's confidence floor. The comparison is ``>=``: 0.7 displays, 0.6999 does not.
CONFIDENCE_FLOOR = 0.7

#: DESIGN §Data models pins this whitelist. Only these source kinds may ever reach a
#: screen. It is a contract, not an implementation detail — do not widen it.
DISPLAYABLE_KINDS: frozenset[str] = frozenset(
    {
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
    }
)

#: C1 permits fetching these; R11/DESIGN forbid ever displaying them. Derived, so that a
#: source kind added to the contract without a display ruling lands on the safe side.
NEVER_DISPLAYABLE_KINDS: frozenset[str] = frozenset(get_args(SourceKind)) - DISPLAYABLE_KINDS


# --------------------------------------------------------------------------- R13 policy text

#: R13: the one-paragraph statement of what is never surfaced, shown with every digest.
EXCLUSION_POLICY = (
    "This digest deliberately withholds six kinds of information about a member, however "
    "easily a public source gives them up: their home address, property records or where "
    "they live; their family, spouse, children or personal relationships; their health and "
    "medical history; their litigation, criminal, divorce or other personal court records; "
    "their net worth, compensation, salary or personal wealth; and their political "
    "donations, party affiliations or campaign giving. The line is who the fact is about "
    "and who made it public: a member's own published professional work stays in even when "
    "its topic is sensitive, and a company's business events are the company's, not the "
    "member's. Anything the sources leave genuinely unresolved is withheld rather than "
    "guessed."
)

#: The six R11 categories, in the order the rule layer consults them.
R11_CATEGORIES: tuple[ExclusionReason, ...] = (
    "health",
    "family",
    "legal",
    "home_or_property",
    "wealth",
    "political",
)


# --------------------------------------------------------------------------- the rule layer


def _rx(pattern: str, *, ignorecase: bool = True) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE if ignorecase else 0)


# ------------------------------------------------------- principle 3: predication tests

#: Head nouns that make whatever precedes them an attributive MODIFIER — a sector, a
#: market, a dataset, a job title. Deliberately restricted to nouns naming an
#: organisation, a product, a publication or a work function.
#:
#: `school`, `university`, `hospital`, `clinic`, `foundation` and `committee` are
#: deliberately ABSENT even though they read as institutional: "their children's school"
#: and "their party committee" would be neutralised by them, which is the leak this whole
#: mechanism exists to avoid. The professional readings of those words are already carried
#: by ``_EXEMPT`` ("business school", "school of engineering", "children's hospital").
_HEAD_NOUN = (
    r"(?:compan(?:y|ies)|startups?|firms?|business(?:es)?|studios?|labs?|desks?|teams?|"
    r"groups?|divisions?|units?|departments?|practices?|practitioners?|agenc(?:y|ies)|"
    r"funds?|platforms?|products?|software|apps?|tools?|toolkits?|systems?|services?|"
    r"solutions?|datasets?|databases?|marketplaces?|networks?|newsletters?|podcasts?|"
    r"magazines?|columns?|journals?|blogs?|books?|guides?|reports?|papers?|essays?|talks?|"
    r"courses?|seminars?|conferences?|summits?|programmes?|programs?|initiatives?|"
    r"institutes?|associations?|societ(?:y|ies)|contractors?|consultants?|"
    r"consultanc(?:y|ies)|advisers?|advisors?|analysts?|officials?|suppliers?|vendors?|"
    r"customers?|clients?|carriers?|insurers?|insurance|underwriting|analytics|research|"
    r"engineering|operations|logistics|imaging|processing|benchmarking|scheduling|"
    r"mediation|planning|management|monitoring|screening|data|pipelines?|workflows?|"
    r"roadmaps?|standards?|protocols?|apis?|sdks?|factor(?:y|ies)|plants?|lines?|"
    r"portfolios?|sectors?|industr(?:y|ies)|markets?|media|law)"
)

#: Words that CANNOT be an attributive modifier: determiners, prepositions, conjunctions,
#: pronouns and auxiliaries. One of these between a cue and a head noun means the head
#: noun belongs to a different phrase, so the cue is predicated of the member after all.
_FUNCTION_WORDS = frozenset(
    """a an the this that these those and or but nor of in on at to for from by with
    without into onto over under above below near about as than then when while where
    who whom whose which their his her its our your they he she it we you them him
    is are was were be been being am has have had do does did will would shall should
    can could may might must not no nor if unless because since until during after
    before per via there here now still yet also however""".split()
)

#: Making-and-selling verbs. When one of these governs the cue, the member is doing
#: professional work ON the category rather than being described by it. Verbs of REPORTING
#: ("show", "list", "name", "record", "file", "reveal", "say") are deliberately absent:
#: "Court records **show** a divorce" is exactly the sentence R11 is about.
_PRODUCT_VERBS = (
    r"(?:build|builds|built|building|ship|ships|shipped|shipping|sell|sells|sold|selling|"
    r"design|designs|designed|designing|develop|develops|developed|developing|"
    r"automate|automates|automated|automating|track|tracks|tracked|tracking|"
    r"index|indexes|indexed|indexing|parse|parses|parsed|parsing|"
    r"monitor|monitors|monitored|monitoring|analyse|analyses|analysed|analysing|"
    r"analyze|analyzes|analyzed|analyzing|underwrite|underwrites|underwritten|underwriting|"
    r"price|prices|priced|pricing|licence|license|licences|licenses|licensed|licensing|"
    r"benchmark|benchmarks|benchmarked|benchmarking|serve|serves|served|serving|"
    r"engineer|engineers|engineered|prototype|prototypes|prototyped|"
    r"integrate|integrates|integrated|streamline|streamlines|streamlined|"
    r"digitise|digitize|digitised|digitized)"
)

#: The tail of an attributive phrase: possessive/hyphen glue, then at most two modifier
#: words, then a head noun. Anchored at the end of the cue's own match.
_ATTRIBUTIVE_TAIL = _rx(
    r"^(?:['’]s|['’]|-)*[\s-]*"
    r"((?:[A-Za-z][\w'’]*[\s-]+){0,2})"
    rf"{_HEAD_NOUN}\b"
)

#: A product verb sitting immediately before the cue, optionally across one determiner or
#: preposition ("to track court records", "underwrites property records data").
_PRODUCT_VERB_TAIL = _rx(
    rf"\b{_PRODUCT_VERBS}\b(?:\s+(?:a|an|the|its|their|our|to|for|of|on|in))?[\s-]*$"
)


def _head_noun_follows(text: str, end: int) -> bool:
    """Is the cue an attributive modifier of a professional head noun?"""
    match = _ATTRIBUTIVE_TAIL.match(text[end:])
    if match is None:
        return False
    modifiers = [word for word in re.split(r"[\s-]+", match.group(1)) if word]
    return all(word.lower() not in _FUNCTION_WORDS for word in modifiers)


def _product_verb_precedes(text: str, start: int) -> bool:
    """Is the cue the direct object of a making-or-selling verb?"""
    before = re.split(r"[.,;:!?()\"“”]", text[:start])[-1]
    return _PRODUCT_VERB_TAIL.search(before) is not None


def _is_attributive(text: str, match: re.Match[str]) -> bool:
    """Principle 3: this particular MATCH names the member's subject matter, not the member."""
    return _head_noun_follows(text, match.end()) or _product_verb_precedes(text, match.start())


#: Relative nouns. Bare `partner` is deliberately absent — "a partner at Marram Ventures"
#: is a job title and the word alone cannot tell the two apart; the QUALIFIED forms
#: ("their partner of twenty years", "they and their partner", "their life partner") are
#: separate STRONG patterns below and the bare possessive defers via ``_WEAK``.
_RELATIVE = (
    r"(?:daughters?|sons?|child|children|kids?|"
    r"mother|father|parents?|mum|mom|dad|"
    r"brothers?|sisters?|siblings?|"
    r"spouse|husband|wife|widow|widower|fianc\w*|"
    r"grand(?:mother|father|son|daughter|parents?)|"
    r"niece|nephew|aunt|uncle|cousins?|stepson|stepdaughter|stepchild|"
    r"ex-wife|ex-husband|in-laws?)"
)

#: A literal street address. Case-SENSITIVE on purpose: the capitalised street name is
#: half the signal, and lowercasing it would let a bare year plus a common noun match.
_STREET_ADDRESS = _rx(
    r"\b\d{1,5}\s+(?:[A-Z][\w'’-]*\s+){1,3}"
    r"(?:Court|Ct|Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Row|Way|Boulevard|Blvd|"
    r"Place|Terrace|Circle|Trail|Parkway|Crescent|Close)\b",
    ignorecase=False,
)

#: Dwelling nouns. A residence, not a building type: "office" and "warehouse" are absent
#: because a member's workplace is a professional fact. ``estate`` carries lookbehinds for
#: the same reason — "a warehouse estate", "real estate", "an industrial estate" are
#: commercial property and belong to the company, and one of them ("Their company purchased
#: a warehouse estate") was a measured over-block. ``silo`` and ``place`` stay OUT: both
#: corpora deliberately hand "the converted grain silo they live in" and "the place on
#: Tannery Row" to the classifier, and a marker here would settle them.
_ESTATE = (
    r"(?<!real )(?<!industrial )(?<!warehouse )(?<!business )(?<!trading )(?<!retail )"
    r"(?<!logistics )estate"
)
_DWELLING = (
    r"(?:house|home|apartment|flat|condo|condominium|bungalow|cottage|penthouse|villa|"
    r"residence|ranch|farmhouse|townhouse|townhome|duplex|maisonette|chalet|cabin|"
    rf"manor|mansion|lodge|brownstone|homestead|chateau|{_ESTATE})"
)

#: STRONG cues: an unambiguous R11 surface marker whose subject can only be the person.
#: A hit here is settled by the rule layer alone, with a named category.
_STRONG: dict[str, tuple[re.Pattern[str], ...]] = {
    "health": (
        _rx(r"\bdiagnos(?:ed|is|es)\b"),
        _rx(r"\b(?:in|to|into|from)\s+(?:the\s+)?hospital\b"),
        _rx(r"\bhospitali[sz]ed\b|\bhospital\s+stay\b|\bintensive\s+care\b"),
        _rx(r"\bmedications?\b|\bprescri(?:bed|ption)\b|\bmedicated\b"),
        _rx(
            r"\b(?:pneumonia|diabetes|cancer|leukaemia|leukemia|stroke|heart\s+attack|"
            r"bipolar|schizophreni\w+|epilepsy|thyroid|dementia|alzheimer\w*)\b"
        ),
        _rx(r"\bdepress(?:ion|ive)\b"),
        _rx(r"\b(?:surgery|chemotherapy|chemo|dialysis|transplant|remission|relapse)\b"),
        _rx(r"\bmedical\s+(?:leave|history|records?|condition|treatment)\b|\bsick\s+leave\b"),
        # An inpatient stay is a hospital stay by another name; the siblings above
        # already settle "hospitalised" and "intensive care" and this is the same fact.
        _rx(r"\bin-?patient\b"),
        # Principle 2 again, in its RECORDS form. The pattern below catches a person
        # telling a reporter; a letter, a chart or a claim file is the same disclosure
        # made by paper, and is never the member's own published material.
        _rx(
            r"\b(?:medical|insurer|insurance|hospital|pharmacy|clinic|treatment)\s+"
            r"(?:letters?|records?|files?|charts?|notes?|claims?|bills?)\b"
        ),
        # Principle 2, the exclude side: somebody ELSE put a personal struggle on the
        # record. The mirror of a member's own published material, which defers instead.
        _rx(
            r"\b(?:former\s+|ex-)?(?:colleagues?|co-?workers?|friends?|associates?|"
            r"acquaintances?|neighbou?rs?|employees?|assistants?|classmates?|"
            r"investors?|reporters?)\b[^.]{0,60}?"
            r"\b(?:told|said|says|revealed|reveals|disclosed|confided|described|recounted|"
            r"claimed)\b[^.]{0,90}?"
            r"\b(?:struggl\w*|burn-?out|depress\w*|anxiety|illness|health|addiction|"
            r"drinking|sober\w*|breakdown|treatment|therapy|diagnos\w*|hospital|rehab\w*)\b"
        ),
        # R11 names "health/medical information" without qualification, and a named
        # clinician IS the diagnosis by implication -- "their psychiatrist of nine years"
        # tells a host everything the diagnosis would. Anchored to the possessive so that
        # the member's own medical practice, or a doctor they interviewed, is untouched.
        _rx(
            r"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){0,2}"
            r"(?:psychiatrist|psychologist|therapist|oncologist|cardiologist|neurologist|"
            r"physician|surgeon|doctor|dentist|counsellor|counselor|dietician|dietitian|"
            r"midwife|nurse)\b"
        ),
        # Reproductive medicine. R11's health clause is not restricted to illness, and
        # this family was entirely absent: an IVF feature went straight to the digest.
        _rx(
            r"\bivf\b|\bin\s?vitro\s+fertili[sz]ation\b|\begg\s+freezing\b|\bsurrogac\w+\b|"
            r"\bfertility\s+(?:treatment|struggles?|journey|issues|problems|clinic)\b|"
            r"\bmiscarriage\b|\bstillbirth\b|\bstillborn\b"
        ),
        # Emergency and residential treatment. The head noun is consumed by the pattern on
        # purpose: leaving "clinic" outside the match would let principle 3 read
        # "residential rehab clinic" as a sector and neutralise it.
        _rx(
            r"\bambulances?\b|\bparamedics?\b|\bemergency\s+room\b|\bair-?lifted\b|"
            r"\blife\s+support\b|"
            r"\b(?:residential\s+)?rehab(?:ilitation)?\s+"
            r"(?:clinics?|centres?|centers?|facilit(?:y|ies)|programmes?|programs?)\b|"
            r"\bin\s+rehab\b|\bresidential\s+rehab\b"
        ),
    ),
    "family": (
        _rx(rf"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){{0,2}}{_RELATIVE}\b"),
        _rx(r"\bmarried\s+(?:to|since)\b|\b(?:has|have|had)\s+been\s+married\b"),
        # "They married an architect in June" names a spouse just as plainly as
        # "married to"; only the preposition differed, and the sentence leaked.
        _rx(r"\b(?:they|he|she)\s+married\b"),
        _rx(r"\bmarriage\s+to\b|\bengaged\s+to\s+be\s+married\b"),
        _rx(rf"\bthey\s+(?:have|has)\s+(?:a|one|two|three|four|no)\s+{_RELATIVE}\b"),
        # QUALIFIED partner only. R11 says "relationships"; a twenty-year partner is one
        # and a partner at a venture firm is a job. The qualifier is what tells them apart,
        # so the bare possessive defers in ``_WEAK`` rather than being ruled here.
        _rx(
            r"\b(?:their|his|her)\s+(?:life|domestic|romantic|long-?term|civil)\s+partner\b|"
            r"\b(?:their|his|her)\s+partner\s+of\s+[\w-]+\s+years?\b|"
            r"\b(?:they|he|she)\s+and\s+(?:their|his|her)\s+partner\b"
        ),
        # The end of a relationship, and the arrangements a court makes about children.
        # R11 names "family members, relationships, children" and both were uncovered.
        _rx(
            r"\bchild\s+support\b|\balimony\b|\bspousal\s+support\b|"
            r"\b(?:joint|shared|sole|physical|legal|child)\s+custody\b|"
            r"\bcustody\s+(?:arrangements?|agreements?|battles?|disputes?|hearings?|"
            r"orders?|cases?|proceedings?)\b|"
            r"\blegally\s+separated\b|\bmarital\s+separation\b"
        ),
    ),
    "legal": (
        _rx(r"\b(?:co-)?defendants?\b|\bplaintiffs?\b"),
        _rx(r"\bcourt\s+(?:records?|filings?|documents?|papers|orders?)\b"),
        _rx(r"\bdivorc\w+\b"),
        _rx(r"\bplead(?:ed|ing|s)?\s+(?:no\s+contest|guilty|not\s+guilty)\b|\bpleaded\b"),
        _rx(
            r"\bmisdemean(?:our|or)s?\b|\bfelon(?:y|ies)\b|\bconvict(?:ed|ion)\b|"
            r"\bindict(?:ed|ment)\b|\barrested\b|\bprobation\b|\bparole[dn]?\b|"
            r"\bacquitted\b|\bmugshots?\b|\brap\s+sheet\b|"
            r"\bsentenced\b|\bcriminal\s+(?:charge|record|case|conviction)\w*\b"
        ),
        # "Charged with" is the single worst word in this file: in the criminal sense it is
        # the plainest R11 marker there is, and in the ordinary sense of *tasked with* it
        # is how a roster of operators describes a promotion. The OBJECT decides. An
        # offence object rules here; a gerund object ("charged with rebuilding the platform
        # team") is professional and carries no cue at all; anything else defers in
        # ``_WEAK``. There is no reading of the bare phrase that is safe in both
        # directions, which is why it stopped being one pattern.
        _rx(
            r"\bcharged\s+with\b[^.]{0,40}?\b(?:felon\w*|misdemean\w*|murder|manslaughter|"
            r"homicide|assault|batter(?:y|ies)|fraud\w*|theft|larceny|burglar\w*|robber\w*|"
            r"arson|perjury|briber\w*|embezzl\w*|extortion|stalking|harassment|solicitation|"
            r"conspirac\w*|possession|trespass\w*|vandalism|laundering|evasion|"
            r"insider\s+trading|counts?\s+of|a\s+crime|criminal\w*|dui|dwi|"
            r"drink[-\s]driving|reckless\s+driving|ponzi|scheme)\b"
        ),
        # The criminal record by its other names. R11 says "criminal ... records" and the
        # corpus carried exactly one misdemeanour plea, so a registry entry, a police stop
        # and a paternity action all reached the digest.
        _rx(
            r"\bsex[-\s]?offender\b|\boffender\s+registr\w+\b|"
            r"\b(?:taken|took\s+(?:them|him|her))\s+into\s+(?:police\s+)?custody\b|"
            r"\bin\s+police\s+custody\b|"
            r"\bpolice\s+(?:stopped|arrested|detained|questioned|cautioned|booked)\b|"
            r"\b(?:stopped|detained|questioned)\s+by\s+police\b|"
            r"\bpaternity\s+(?:suits?|tests?|cases?|claims?|actions?|disputes?)\b"
        ),
        _rx(r"\b(?:protective|restraining)\s+order\b"),
        _rx(r"\b(?:sued|suing)\s+(?:them|him|her)\b|\bthey\s+(?:were|was)\s+sued\b"),
    ),
    "home_or_property": (
        _rx(r"\blives?\s+at\b|\bresides?\s+(?:at|in)\b|\bwhere\s+they\s+(?:live|sleep)\b"),
        _rx(
            r"\bproperty\s+records?\b|\bland\s+registry\b|\btitle\s+deed\b|\bdeeds?\s+"
            r"(?:show|list|record)\b|\bmortgages?\b|\btax\s+assessor\b"
        ),
        _rx(rf"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){{0,2}}{_DWELLING}\b"),
        # The BUYER must be the person. "Their company purchased a warehouse estate" is a
        # commercial acquisition and was a measured over-block; the subject principle is
        # what separates it from "they bought the house on Halberd Row".
        _rx(
            r"\b(?:they|he|she)\s+(?:[\w'’-]+\s+){0,3}(?:bought|buying|purchased|owns?)\b"
            rf"[^.]{{0,40}}?\b{_DWELLING}\b"
        ),
        # The same fact with the words the other way round: "the farmhouse they purchased".
        # Only the clause order differed and the sentence went straight through. Restoring
        # and renovating say whose dwelling it is exactly as buying does.
        _rx(
            rf"\b{_DWELLING}\b[^.]{{0,30}}?\bthey\s+"
            r"(?:bought|purchased|own|owned|restored|renovated|refurbished|rebuilt)\b"
        ),
        _rx(
            r"\bbuyer\s+of\b[^.]{0,40}?"
            r"\b(?:house|home|apartment|flat|condo|bungalow|cottage|property|estate)\b"
        ),
        _rx(r"\breal[-\s]?estate\s+listing\b"),
        _rx(r"\b(?:home|street|residential)\s+address\b"),
        # A property portal names a dwelling as surely as a deed does, and a bedroom count
        # is a description of a home and of nothing else.
        _rx(
            r"\b(?:zillow|redfin|rightmove|zoopla|realtor\.com)\b|"
            r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)[-\s]bed(?:room)?\b"
        ),
        _STREET_ADDRESS,
    ),
    "wealth": (
        _rx(r"\bnet\s+worth\b"),
        _rx(
            r"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){0,2}"
            r"(?:salary|salaries|compensation|pay|paycheck|earnings|bonus|wages?|income)\b"
        ),
        # `equity`, `shares` and `portfolio` are NOT here in their bare possessive form.
        # "They advise founders on how to structure their equity", "their portfolio
        # companies", "their portfolio construction method" and "their shares of the
        # workload" are the daily vocabulary of the roster SPEC Q4 describes, and `their`
        # in them is usually not even the member. The strict readings below still rule.
        _rx(
            r"\b(?:their|his|her)\s+(?:personal\s+)?(?:[\w'’-]+\s+){0,1}"
            r"(?:stakes?|holdings?|shareholdings?|fortune|wealth|assets)\b"
        ),
        _rx(
            r"\b(?:their|his|her)\s+(?:personal|remaining|own|total|estimated|residual)\s+"
            r"(?:[\w'’-]+\s+){0,1}(?:equity|shares|portfolio)\b"
        ),
        # Carried interest IS compensation, named as such by R11's "compensation" clause,
        # and no marker in this file covered it.
        _rx(
            r"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){0,2}carried\s+interest\b|"
            r"\b(?:family\s+)?trust\s+they\s+(?:control|own|hold)\b"
        ),
        _rx(r"\bvested\s+shares\b|\bthey\s+hold\b[^.]{0,40}?\bshares\b"),
        _rx(
            r"\bpersonally\s+(?:cleared|made|earned|received|pocketed|banked|netted|"
            r"took\s+home|walked\s+away\s+with)\b"
        ),
        _rx(r"\b(?:millionaire|billionaire)\b"),
        _rx(r"\bthey\s+(?:are|were)\s+(?:worth|paid)\b"),
        # A MONEY object is required. Bare "they earned" also matches "they earned a
        # master's in materials science", which is a professional credential and one of
        # the plainest keeps a digest has - measured on an independently written case.
        # Nothing that is genuinely about the member's money leaves the category: the
        # sums, the salary words and the "personally cleared" family above all remain.
        _rx(
            r"\bthey\s+(?:earn|earns|earned|earning)\b[^.]{0,40}?"
            r"\b(?:\$[\d.,]+|[\d.,]+\s*(?:million|billion|thousand)|million|billion|"
            r"salar(?:y|ies)|compensation|bonus|wages?|dollars?|"
            r"(?:six|seven|eight|nine|ten)-figure)\b"
        ),
    ),
    "political": (
        _rx(r"\bpolitical\s+part(?:y|ies)\b|\bparty\s+affiliation\b"),
        _rx(r"\bpolitical\s+action\s+committees?\b|\bsuper\s?pacs?\b"),
        # CASE-SENSITIVE. The acronym is written in capitals; lowercased, `\bpacs?\b`
        # also matches the "Pac" of "Pac-12", and a sponsored robotics tournament became
        # a political donation.
        _rx(r"\bPACs?\b", ignorecase=False),
        _rx(r"\bregistered\s+(?:democrat|republican|independent|voter)\b"),
        _rx(r"\bcampaign\s+(?:contribution|donation|finance\s+records?)\b"),
        _rx(r"\bindividual\s+donors\b|\bdonor\s+lists?\b"),
        # R11 says "affiliations" as well as "donations", and party OFFICE is the plainest
        # affiliation there is.
        _rx(
            r"\bprecinct\s+captains?\b|\bward\s+chairs?\b|\bvoter\s+(?:rolls?|files?)\b|"
            r"\bparty\s+(?:official|activist|delegate|member)s?\b|"
            r"\bdonor\s+to\b[^.]{0,50}?\b(?:campaigns?|candidates?|committees?|party)\b"
        ),
        # A donation VERB with a political OBJECT. Both halves are required: "donated a
        # patent to the public domain" is one of the most flattering facts a digest can
        # carry, and a filter keyed on "donated" alone deletes it.
        _rx(
            r"\b(?:donat\w+|contribut\w+|gave|gives|giving|maxed\s+out|bundled|"
            r"wrote\s+a\s+che(?:ck|que))\b[^.]{0,80}?"
            r"\b(?:candidates?|campaigns?|political\s+action\s+committee|pacs?|"
            r"party\s+committee|political\s+part(?:y|ies)|ballot\s+(?:measure|initiative)|"
            r"senate\s+race|congressional|mayoral|gubernatorial)\b"
        ),
    ),
}

#: WEAK cues: category-shaped vocabulary that does NOT say whose money, court record,
#: house or politics it is, or who disclosed it. A hit here is handed to the classifier,
#: never decided by the rule layer.
_WEAK: dict[str, tuple[re.Pattern[str], ...]] = {
    "health": (
        _rx(r"\bburn-?out\b|\bburn(?:ing|ed|t)\s+out\b"),
        _rx(r"\balcohol(?:ism|\s+(?:dependence|dependency|abuse|misuse))\b|"
            r"\bsubstance\s+(?:abuse|use\s+disorder|dependence)\b"),
        _rx(r"\bstruggl\w*\b"),
        _rx(r"\brecover(?:y|ing|ed)\b"),
        _rx(r"\bmental\s+health\b|\bwell-?being\b|\btherapy\b|\brehab\w*\b"),
        _rx(r"\baddiction\b|\bsobriety\b|\bhealth\b"),
        _rx(r"\bfull\s+strength\b|\bback\s+on\s+their\s+feet\b|\btime\s+off\s+to\s+recover\b"),
        _rx(r"\bfertility\b|\bpsychiatrist\b|\btherapist\b|\bcounsell?ing\b|\bclinician\b"),
    ),
    "family": (
        # Bare `their partner`, which the STRONG layer deliberately will not rule on. The
        # lookahead keeps "a partner AT Marram Ventures" out: a partner introduced by a
        # preposition is a firm, and that is how an investor roster talks.
        _rx(r"\b(?:their|his|her)\s+partner\b(?!\s+(?:at|in|on|for|to)\b)"),
        _rx(r"\b(?:parental|paternity|maternity)\s+leave\b|\bseparation\b|\bcustody\b"),
        _rx(r"\bschool\s+(?:pick-?up|run|drop-?off|gate)\b|\bpick-?up\s+window\b"),
        _rx(r"\b(?:day\s?care|nursery|creche|kindergarten|pre-?school)\b"),
        _rx(r"\bschool\b"),
        _rx(r"\bsurnames?\b|\bmaiden\s+name\b"),
        _rx(r"\bare\s+related\b|\brelated\s+to\s+(?:them|him|her)\b|\ba\s+relative\b"),
        _rx(r"\bfamil(?:y|ies)\b|\bwedding\b|\bhoneymoon\b"),
    ),
    "legal": (
        _rx(r"\barbitrations?\b|\bmediation\b"),
        _rx(r"\bsettle(?:d|ment|ments)\b"),
        _rx(r"\bdisputes?\b|\bdisputed\b"),
        _rx(r"\blawsuits?\b|\blitigation\b|\blegal\s+(?:action|proceeding|battle|dispute)\b"),
        # The STRONG forms above catch the member as DEFENDANT ("they were sued"). As
        # plaintiff - "the four years they spent suing their former co-founder" - the
        # member is just as much a party, but "their company sued a rival" is a business
        # fact, so the subject decides and this defers instead of ruling.
        _rx(r"\b(?:sued|suing|sues)\b"),
        _rx(r"\bconfidential\b|\bsealed\b|\bnon-?disclosure\b"),
        _rx(r"\ballegation\w*\b|\baccused\b|\bcomplaint\s+(?:filed|against)\b"),
        _rx(r"\bbankruptc\w+\b"),
        # Every OTHER reading of "charged with": not an offence, not a gerund. The gerund
        # is excluded here rather than ruled, because "charged with rebuilding the platform
        # team" is a promotion and withholding it is the over-block T-069 is about.
        _rx(r"\bcharged\s+with\b(?!\s+(?:the\s+|a\s+|an\s+)?\w+ing\b)"),
    ),
    "home_or_property": (
        _rx(r"\bescrow\b|\bclosed\s+on\b|\bclosing\s+costs?\b"),
        _rx(r"\blandlords?\b|\brents?\b|\brenting\b|\brented\b|\bleases?\s+a\b"),
        _rx(r"\bspends?\b[^.]{0,45}?\bthere\b"),
        _rx(r"\bneighbou?rhood\b"),
        _rx(r"\bmoved\s+(?:to|into)\b"),
        # "the converted grain silo they live in" is a dwelling; "they live in Lisbon" is
        # a city hub and a keep. The surface cannot tell them apart, so it defers - the
        # STRONG "lives at / resides at" forms above still settle an address outright.
        _rx(r"\b(?:they|he|she)\s+(?:live|lives|lived)\s+in\b"),
    ),
    "wealth": (
        _rx(r"\b(?:million|billion|thousand)[\s-]?(?:dollar|dollars)\b|\$[\d.,]+"),
        _rx(r"\bacquisitions?\b|\bacquired\b|\bacquire\b"),
        _rx(r"\bsalar(?:y|ies)\b|\bcompensation\b|\bpayouts?\b|\bproceeds\b|\bwindfall\b"),
        _rx(r"\bequity\b|\bvaluation\b|\bvalued\s+at\b"),
        _rx(r"\bwealth\w*\b|\bfortune\b|\brich\b"),
        _rx(r"\bnever\s+need\s+to\s+work\s+again\b|\bset\s+up\s+(?:well\s+)?enough\b"),
        _rx(r"\bcarried\s+interest\b"),
    ),
    "political": (
        _rx(r"\bcandidates?\b|\bfundraisers?\b|\bcampaign\w*\b|\belections?\b"),
        _rx(r"\bpolitic\w*\b|\bpartisan\b|\bpart(?:y|ies)\b|\bcaucus\b|\bprimaries\b"),
        _rx(r"\bcity\s+council\b|\bcouncils?\b|\btestif\w+\b|\bhearings?\b"),
        _rx(r"\blegislat\w+\b|\blobb\w+\b|\bballot\b|\bgovernments?\b"),
        _rx(r"\bsenates?\b|\bcongress\w*\b|\bmayor\w*\b|\bgovernors?\b"),
    ),
}

#: EXEMPTIONS: a dominant professional marker that neutralises a category's WEAK cues.
#: They never override a STRONG cue — "their personal stake … after the growth round" is
#: still personal wealth, and an exemption that could switch off a named marker would be a
#: hole in R11 rather than a nuance in it.
_EXEMPT: dict[str, tuple[re.Pattern[str], ...]] = {
    "health": (
        _rx(
            r"\bchildren'?s\s+(?:hospital|hospice|clinic|charity|foundation|museum|"
            r"centre|center)\b"
        ),
    ),
    "family": (
        _rx(
            r"\bchildren'?s\s+(?:hospital|hospice|clinic|charity|foundation|museum|"
            r"centre|center|library|theatre|theater)\b"
        ),
        _rx(
            r"\b(?:business|law|medical|graduate|grad|engineering|design|art|music|"
            r"nursing|divinity)\s+school\b|\bschool\s+of\s+\w+"
        ),
        _rx(r"\bfamily\s+(?:office|business|firm|of\s+products)\b"),
    ),
    "legal": (
        _rx(r"\bamicus(?:\s+curiae)?\s+briefs?\b"),
        _rx(r"\bexpert\s+witness\b"),
    ),
    "home_or_property": (),
    "wealth": (
        # A compensation figure possessively attached to somebody who is NOT the subject
        # is a yardstick, not the member's pay.
        _rx(r"\b[\w-]+'’?s\s+(?:salary|pay|compensation|wages?)\b|\b[\w-]+'s\s+salary\b"),
        _rx(r"\bkeynot\w+\b"),
        _rx(r"\bseries\s+[a-h]\b"),
        _rx(r"\b(?:seed|growth|funding|financing|venture|bridge|pre-seed)\s+rounds?\b"),
        _rx(r"\braised\s+(?:a|an|\$)\b"),
        _rx(r"\b990\b|\bboard\s+officer\b|\bangel\s+investor\b|\bnon-?profit\b"),
    ),
    "political": (
        _rx(r"\bpublic\s+domain\b|\bstandards\s+body\b"),
    ),
}

#: The source itself declines to resolve the question — "does not say why", "no source
#: says whether", "declined to describe". A sentence whose only content is an unexplained
#: absence cannot be ruled, so it defers and (if the classifier shrugs too) fails closed.
#: This is a cue, not a memorised sentence: it is what makes the fail-closed rule
#: something the deterministic layer can hold.
_DEFER: tuple[re.Pattern[str], ...] = (
    _rx(r"\b(?:does|did|do)\s+not\s+say\b|\bdoesn'?t\s+say\b|\bdidn'?t\s+say\b"),
    _rx(r"\bno\s+(?:public\s+)?source\s+(?:says|explains|confirms|establishes|records)\b"),
    _rx(r"\bno\s+source\s+says\b|\bnobody\s+(?:says|will\s+say)\b"),
    _rx(
        r"\bdeclined\s+to\s+(?:describe|say|discuss|elaborate|comment|explain|name|"
        r"specify|confirm)\b|\bwould\s+not\s+say\b|\brefused\s+to\s+say\b"
    ),
    _rx(
        r"\bnever\s+(?:talked|spoken|written|been\s+open)\s+about\b|"
        r"\b(?:has|have)\s+never\s+discussed\b"
    ),
    _rx(r"\bwithout\s+(?:saying|explaining|specifying)\b|\bunexplained\b"),
    _rx(r"\bunclear\s+(?:why|whether|what|if)\b|\bit\s+is\s+not\s+known\b"),
)


@dataclass(frozen=True)
class RuleVerdict:
    """What the deterministic layer concluded about one sentence.

    ``decision`` is ``keep``, ``exclude`` or ``unsure``. ``unsure`` is not a failure: it is
    the rule layer correctly declining to answer a question that turns on the subject or
    the discloser, which is what the LLM stage exists for.
    """

    decision: Literal["keep", "exclude", "unsure"]
    reason: ExclusionReason | None = None
    cue: str = ""


def _first_match(patterns: Iterable[re.Pattern[str]], text: str) -> str:
    """The first pattern that matches anywhere. Used for whole-sentence exemptions."""
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return ""


def _first_live_cue(patterns: Iterable[re.Pattern[str]], text: str) -> str:
    """The first pattern with a match that is not merely attributive (principle 3).

    Every match of every pattern is examined, not just the leftmost: a sentence can name
    a sector in one clause and the member in the next ("they founded a cancer-imaging
    startup, and their psychiatrist spoke to a podcast"), and neutralising the whole
    pattern on the strength of its first, attributive hit is how the second clause would
    have leaked.
    """
    for pattern in patterns:
        for match in pattern.finditer(text):
            if not _is_attributive(text, match):
                return pattern.pattern
    return ""


def rule_verdict(text: str) -> RuleVerdict:
    """Rule on one fact sentence without an LLM.

    Order is the whole design: a named R11 marker settles the case; failing that, an
    explicit "the source does not resolve this" cue defers; failing that, live category
    vocabulary defers; and a sentence with nothing on it is a professional fact and is
    kept.

    A marker only counts if it is PREDICATED OF THE MEMBER. A cue that is an attributive
    modifier of a professional head noun, or the direct object of a making-or-selling
    verb, names the member's subject matter and is not a marker at all — see principle 3
    in the module docstring and :func:`_is_attributive`.
    """
    exempt = {
        category
        for category, patterns in _EXEMPT.items()
        if patterns and _first_match(patterns, text)
    }

    for category in R11_CATEGORIES:
        cue = _first_live_cue(_STRONG[category], text)
        if cue:
            return RuleVerdict("exclude", category, cue)

    cue = _first_match(_DEFER, text)
    if cue:
        return RuleVerdict("unsure", None, cue)

    for category in R11_CATEGORIES:
        if category in exempt:
            continue
        cue = _first_live_cue(_WEAK[category], text)
        if cue:
            return RuleVerdict("unsure", None, cue)

    return RuleVerdict("keep")


def _decide(fact: Fact, verdict: RuleVerdict) -> Fact:
    """A copy of ``fact`` carrying ``verdict``. Unsure fails closed."""
    if verdict.decision == "exclude":
        return fact.model_copy(update={"excluded": True, "exclusion_reason": verdict.reason})
    if verdict.decision == "unsure":
        return fact.model_copy(update={"excluded": True, "exclusion_reason": "low_confidence"})
    return fact.model_copy(update={"excluded": False, "exclusion_reason": None})


def apply_taste_rules(facts: Iterable[Fact]) -> list[Fact]:
    """The deterministic stage of DESIGN Decision 6, used alone.

    Clear cases — a named R11 marker, or a professional sentence with no live category cue
    — are settled here with no LLM call. Anything the rules cannot settle is **failed
    closed** with ``low_confidence``, so this function is safe to use on its own; call
    :func:`apply_taste` to give the classifier its turn on those first.
    """
    return [_decide(fact, rule_verdict(fact.text)) for fact in facts]


# --------------------------------------------------------------------------- the LLM stage

#: The verdicts the classifier may return. `unsure` is mandatory: without a way to say it,
#: DESIGN Decision 6's fail-closed rule can never be reached from the second stage.
TasteVerdict = Literal[
    "keep",
    "home_or_property",
    "family",
    "health",
    "legal",
    "wealth",
    "political",
    "unsure",
]


class TasteRuling(BaseModel):
    """One fact's ruling from the classifier stage."""

    fact_id: str
    verdict: TasteVerdict
    rationale: str = ""


class TasteRulings(BaseModel):
    """The classifier's response: one ruling per fact it was asked about."""

    rulings: list[TasteRuling] = Field(default_factory=list)


_CLASSIFIER_SYSTEM = (
    "You are the taste filter for a private club's arrival digest. Staff read these facts "
    "aloud to greet a member. Rule on each fact.\n\n"
    "NEVER surface six kinds of information about the member: home_or_property (their "
    "address, where they live, property records), family (spouse, children, relatives, "
    "personal relationships), health (diagnoses, treatment, medical history), legal "
    "(their own litigation, criminal record, divorce, court records), wealth (their net "
    "worth, compensation, salary, personal proceeds), political (their donations, party "
    "affiliation, campaign giving).\n\n"
    "Two principles decide the hard cases.\n"
    "1. SUBJECT. The exclusions protect the PERSON. A commercial event that happened to a "
    "COMPANY -- a funding round, an acquisition, a trademark settlement, an office opening "
    "-- is a business fact and must be kept, even though it contains money or legal "
    "vocabulary. Where the money, the court record, the house or the politics is the "
    "person's own, exclude it however professional the framing.\n"
    "2. DISCLOSURE. Exclude personal history the person did not make public themselves. "
    "The member's OWN published professional material -- their podcast episode, their "
    "essay, their talk -- is theirs to have said, and is a keep even when its topic is "
    "sensitive. The same topic revealed by a colleague, a reporter or a records search is "
    "an exclude.\n\n"
    "Answer 'keep' for a professional fact. Answer with one of the six category names to "
    "exclude. Answer 'unsure' when the sentence genuinely does not settle whose fact it is "
    "or who disclosed it -- an unexplained absence is the archetype. Do not guess: an "
    "unsure answer withholds the fact, which is the correct outcome when you cannot tell."
)


def _classifier_prompt(facts: Sequence[Fact]) -> str:
    lines = [
        "Rule on each of the following facts. Return one ruling per fact, keyed by its id.",
        "Use only the ids listed below, copied exactly, and rule on each id at most once. "
        "An id that is not in this list is discarded, and so is a fact you rule on twice.",
        "",
    ]
    for fact in facts:
        lines.append(f"- id: {fact.fact_id}")
        lines.append(f"  fact: {fact.text}")
    return "\n".join(lines)


#: Facts per classifier call. One call per fact would work but costs a round trip each;
#: an unbounded batch would blow the response budget on a large dossier.
_BATCH_SIZE = 20


def _positions(batch: Sequence[Fact], offset: int) -> dict[str, int]:
    """The ids THIS call is entitled to answer about → our index for each.

    The index, not the id, is the identity: it is ours, it is unique, and it cannot be
    written by a model. Everything downstream addresses a fact by it.

    An id carried by TWO facts in the same prompt is deleted rather than resolved. Both
    sentences appear under that id, so no ruling in the response can be attributed to one
    of them rather than the other, and applying it to both is precisely how one sentence's
    ``keep`` becomes another sentence's licence to be displayed. Dropping it costs two
    facts; guessing publishes one. (Nothing in the pipeline produces a duplicate — T-3
    numbers facts ``{doc_id}-f{n}`` from a counter shared across batches — so this is a
    guard on the contract, not a repair of a known caller.)

    A BLANK id is left out for the same reason. ``Fact.fact_id`` has no minimum length,
    and a response object with no ``fact_id`` at all reads as ``""`` — so an empty string
    would otherwise be an id that a model gets for free by omitting the field.
    """
    positions: dict[str, int] = {}
    ambiguous: set[str] = set()
    for index, fact in enumerate(batch, start=offset):
        key = fact.fact_id.strip()
        if not key:
            continue
        if key in positions:
            ambiguous.add(key)
        positions[key] = index
    for fact_id in ambiguous:
        del positions[fact_id]
        log.info(
            "two facts in one taste batch share the id %r; both fail closed because no "
            "ruling in the answer can be attributed to one of them",
            fact_id,
        )
    return positions


def _absorb(response: object, batch: Sequence[Fact], offset: int, rulings: dict[int, str]) -> None:
    """Fold one classifier answer into ``rulings``, keyed by our index.

    Two kinds of noise are discarded here rather than stored:

    * **an id this call did not send.** It may be invented, or — the case that actually
      bites — it may name a fact from an EARLIER batch. ``rulings`` outlives one call, so
      before this check a batch-2 answer echoing a batch-1 id overwrote a ruling that was
      already made and already correct, and a ``health`` exclusion became a ``keep``.
    * **two rulings for one id that disagree.** That is not an answer, it is two; taking
      the later one makes the outcome a function of the order the model emitted its list,
      and half of those orders publish an R11 sentence. Repetition is not contradiction,
      so the check compares verdicts rather than counting rulings.
    """
    positions = _positions(batch, offset)
    answered: dict[int, str] = {}
    conflicted: set[int] = set()

    for ruling in getattr(response, "rulings", []) or []:
        claimed = str(getattr(ruling, "fact_id", "") or "").strip()
        index = positions.get(claimed) if claimed else None
        if index is None:
            log.info(
                "discarding a taste ruling for %r; that id was not one of the %d facts this "
                "call asked about",
                claimed,
                len(batch),
            )
            continue
        verdict = str(getattr(ruling, "verdict", "") or "").strip()
        if index in answered and answered[index] != verdict:
            conflicted.add(index)
        answered[index] = verdict

    for index in conflicted:
        del answered[index]
        log.info(
            "discarding contradictory taste rulings for %r; a fact ruled two different ways "
            "has not been ruled on",
            batch[index - offset].fact_id,
        )
    rulings.update(answered)


async def _classify(facts: Sequence[Fact], llm: LLMClient) -> dict[int, str]:
    """Ask the classifier about ``facts``. A failed call yields no rulings, not an error —
    the caller fails closed on anything it did not get an answer for.

    Keys are indices into ``facts``, never the ids the model echoed back. See
    :func:`_absorb`.
    """
    rulings: dict[int, str] = {}
    for start in range(0, len(facts), _BATCH_SIZE):
        batch = facts[start : start + _BATCH_SIZE]
        try:
            response = await llm.structured(
                system=_CLASSIFIER_SYSTEM,
                user=_classifier_prompt(batch),
                schema=TasteRulings,
                max_tokens=min(4000, 200 + 120 * len(batch)),
                cache_prefix=True,
            )
        except LLMError:
            continue
        _absorb(response, batch, start, rulings)
    return rulings


async def apply_taste(facts: Iterable[Fact], llm: LLMClient | None) -> list[Fact]:
    """Both stages of DESIGN Decision 6, in order, failing closed.

    The rule layer runs first and settles what it can. **Only** the facts it marked unsure
    reach ``llm`` — a deterministic case must never cost a call, and a case the rules
    cannot settle must never be answered by them. Anything still unsure once the classifier
    has spoken (including a classifier that shrugged, errored, was not supplied, or
    answered about some fact other than this one) is excluded with reason
    ``low_confidence``.

    ``rulings`` is keyed by a fact's POSITION in ``unsure``, which is why the walk below
    keeps its own counter instead of looking a fact up by its id. That is the whole point:
    the position is ours and the id came back from a model. See :func:`_absorb`.

    Returns every input fact, in order, excluded flag set. Nothing is dropped: the digest
    layer needs the excluded ones to count them, and ``/debug`` needs to show them.
    """
    ruled = [(fact, rule_verdict(fact.text)) for fact in facts]
    unsure = [fact for fact, verdict in ruled if verdict.decision == "unsure"]

    rulings: dict[int, str] = {}
    if unsure and llm is not None:
        rulings = await _classify(unsure, llm)

    out: list[Fact] = []
    position = 0
    for fact, verdict in ruled:
        if verdict.decision != "unsure":
            out.append(_decide(fact, verdict))
            continue
        answer = rulings.get(position, "unsure")
        position += 1
        if answer == "keep":
            out.append(_decide(fact, RuleVerdict("keep")))
        elif answer in R11_CATEGORIES:
            out.append(_decide(fact, RuleVerdict("exclude", answer, "llm")))  # type: ignore[arg-type]
        else:
            out.append(_decide(fact, RuleVerdict("unsure", None, "llm")))
    return out


# --------------------------------------------------------------------------- R12 display gate


def is_displayable(fact: Fact) -> bool:
    """R12: may this fact reach a screen?

    Three INDEPENDENT clauses, deliberately not collapsed:

    1. it survived the taste filter (R11);
    2. its provenance confidence is at least :data:`CONFIDENCE_FLOOR` (0.7, inclusive);
    3. its source kind is on :data:`DISPLAYABLE_KINDS`.

    Clause 3 bites on its own: an ``fec`` filing can be a perfectly tasteful, high
    confidence, non-excluded fact and still never be shown.
    """
    if fact.excluded:
        return False
    if fact.provenance.confidence < CONFIDENCE_FLOOR:
        return False
    return fact.provenance.source_kind in DISPLAYABLE_KINDS
