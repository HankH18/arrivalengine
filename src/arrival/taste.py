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


#: Relative nouns. `partner` is deliberately absent — "business partner" is a professional
#: fact and the word alone cannot tell the two apart.
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
#: because a member's workplace is a professional fact.
_DWELLING = (
    r"(?:house|home|apartment|flat|condo|condominium|bungalow|cottage|penthouse|villa|"
    r"residence|ranch|farmhouse|townhouse|duplex|maisonette|chalet|cabin|estate)"
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
            r"drinking|sober\w*|breakdown|treatment|therapy|diagnos\w*|hospital)\b"
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
    ),
    "legal": (
        _rx(r"\b(?:co-)?defendants?\b|\bplaintiffs?\b"),
        _rx(r"\bcourt\s+(?:records?|filings?|documents?|papers|orders?)\b"),
        _rx(r"\bdivorc\w+\b"),
        _rx(r"\bplead(?:ed|ing|s)?\s+(?:no\s+contest|guilty|not\s+guilty)\b|\bpleaded\b"),
        _rx(
            r"\bmisdemean(?:our|or)s?\b|\bfelon(?:y|ies)\b|\bconvict(?:ed|ion)\b|"
            r"\bindict(?:ed|ment)\b|\barrested\b|\bcharged\s+with\b|\bprobation\b|"
            r"\bsentenced\b|\bcriminal\s+(?:charge|record|case|conviction)\w*\b"
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
        _rx(
            r"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){0,2}"
            r"(?:home|house|apartment|flat|condo|condominium|bungalow|cottage|penthouse|"
            r"villa|residence|ranch)\b"
        ),
        _rx(
            r"\b(?:bought|buying|purchased|owns?)\b[^.]{0,40}?"
            rf"\b{_DWELLING}\b"
        ),
        # The same fact with the words the other way round: "the farmhouse they purchased".
        # Only the clause order differed and the sentence went straight through.
        _rx(rf"\b{_DWELLING}\b[^.]{{0,30}}?\bthey\s+(?:bought|purchased|own|owned)\b"),
        _rx(
            r"\bbuyer\s+of\b[^.]{0,40}?"
            r"\b(?:house|home|apartment|flat|condo|bungalow|cottage|property|estate)\b"
        ),
        _rx(r"\breal[-\s]?estate\s+listing\b"),
        _rx(r"\b(?:home|street|residential)\s+address\b"),
        _STREET_ADDRESS,
    ),
    "wealth": (
        _rx(r"\bnet\s+worth\b"),
        _rx(
            r"\b(?:their|his|her)\s+(?:[\w'’-]+\s+){0,2}"
            r"(?:salary|salaries|compensation|pay|paycheck|earnings|bonus|wages?|income)\b"
        ),
        _rx(
            r"\b(?:their|his|her)\s+(?:personal\s+)?(?:[\w'’-]+\s+){0,1}"
            r"(?:stakes?|holdings?|shareholdings?|equity|shares|fortune|wealth|assets|"
            r"portfolio)\b"
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
        _rx(r"\bpolitical\s+action\s+committee\b|\bsuper\s?pacs?\b|\bpacs?\b"),
        _rx(r"\bregistered\s+(?:democrat|republican|independent|voter)\b"),
        _rx(r"\bcampaign\s+(?:contribution|donation|finance\s+records?)\b"),
        _rx(r"\bindividual\s+donors\b|\bdonor\s+lists?\b"),
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
    ),
    "family": (
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
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return ""


def rule_verdict(text: str) -> RuleVerdict:
    """Rule on one fact sentence without an LLM.

    Order is the whole design: a named R11 marker settles the case; failing that, an
    explicit "the source does not resolve this" cue defers; failing that, live category
    vocabulary defers; and a sentence with nothing on it is a professional fact and is
    kept.
    """
    exempt = {
        category
        for category, patterns in _EXEMPT.items()
        if patterns and _first_match(patterns, text)
    }

    for category in R11_CATEGORIES:
        cue = _first_match(_STRONG[category], text)
        if cue:
            return RuleVerdict("exclude", category, cue)

    cue = _first_match(_DEFER, text)
    if cue:
        return RuleVerdict("unsure", None, cue)

    for category in R11_CATEGORIES:
        if category in exempt:
            continue
        cue = _first_match(_WEAK[category], text)
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
