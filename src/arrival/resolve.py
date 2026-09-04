"""Entity resolution — deciding which documents are actually about the arriving person.

SPEC R2 ("refuse to guess") and S4 (the same-name decoy) are decided here, and DESIGN
Decision 4 is the rule:

1. **Every document gets its own LLM verdict.** One document, one call, one
   `yes` / `no` / `unsure` with a verbatim span and the disambiguating detail it used. A
   document the model never saw was not judged, it was assumed.
2. **Every verdict must cite its own document.** `normalize_ws(evidence)` must be a
   substring of `normalize_ws(doc.text)` (DESIGN Decision 5). A verdict that fails this is
   downgraded to `unsure` — it is a hallucination, and hallucinated evidence is the one
   thing that can make a wrong answer look better-supported than a right one.
3. **Negative evidence vetoes, hard.** A `no` whose evidence asserts a conflicting
   employer or city rejects that document outright, however well the name matches and
   however confident any other verdict is. Confidences are never pooled or averaged: in
   the frozen decoy corpus the decoy's verdicts (0.96 / 0.94 / 0.91) are *more* confident
   than the target's (0.74 / 0.69), so any implementation that averages lands on the
   dead marine archaeologist. The veto has two halves, and they are judged by two
   different questions because they carry two different costs. Whether a document is
   ACCEPTED is decided by the verdict's polarity (`negative_evidence_veto`): the model
   already said `no` on employer or city grounds, so "the evidence does not corroborate
   our detail" is a safe reading of it. Whether a document may anchor a STRONG KEY is
   decided by the evidence alone (`conflicting_identity_claim`), polarity-free and
   strict: a key is a durable claim about which human being this is, so a document that
   names somebody ELSE's employer or work location must not anchor one even when its
   verdict is `yes` — while a `yes` that merely omits the employer string is a normal,
   keyable document and must not be punished for it.
4. **Resolution needs a strong key OR two independent attributes.** A strong key is a
   durable identifier matched on more than the name: a Wikidata QID matched on name AND a
   detail, a company domain derived from the detail, a GitHub profile confirmed by name
   AND company, an SEC CIK matched on name AND company, IN THAT ORDER
   (`STRONG_KEY_PRIORITY`). Failing that, at least two `yes` verdicts citing DIFFERENT
   attributes — and the attributes are `verdict_attributes`, which reads the verdict's
   EVIDENCE against the person's own details and only falls back to the model's free-text
   label when the span corroborates no detail at all. Two `yes` verdicts on the same
   attribute are corroboration of one fact, not independence. Anything less is
   `unresolved`, with `accepted_doc_ids` empty — no facts, no dossier, no guess.

   `disambiguator` is a string the model chose, so counting distinct labels lets word
   choice decide whether a person exists at all: `role` twice refuses the person, `role`
   plus `job title` admits them, and two spans that both quote the employer look
   independent the moment one of them is labelled `handle`. Measured on this module before
   the repair, all three. What the model does NOT choose is which of the person's own
   identifying details a verbatim span actually names, so that is what is counted, with
   `attribute_family` left to canonicalise the leftovers.

   Which of the person's details — ALL of them, one attribute apiece. The employer and the
   city are two of the things a roster writes down and not the only two, and a resolver
   that can read only those refuses people it has ten corroborating documents for. See
   `_corroborable_attributes`, which is where T-080 was measured and where the reason a
   phrase-split cannot mint independence out of one detail is written down.

The strong-key arm is the part that invites a shortcut, and the shortcut
("the document is a wikidata/github/edgar page and the verdict is `yes`, so take the key")
is wrong in a way that only shows up on the cases that matter: a Wikidata item can match a
name and nothing else, a GitHub profile can confirm a name with its company field unset,
and an EDGAR filing can match name and city while naming an entirely different issuer.
Each of those documents is a genuine `yes` and IS accepted — refusing the key is not the
same as refusing the document. So the key checks read the document, not its `source_kind`,
and only documents that were accepted — and that do not themselves assert somebody else's
employer or work location — may carry a key at all.

A strong key is also a DURABLE claim, so it may not be decided by which document happened
to arrive first. `research._interleave` orders documents by how many results each remote
API returned and in what ranking, and every key here used to be "the first accepted
document that matches", which made the identifier a function of the internet's mood:
`blog.example-co.com` or `example-co.com`, this QID or that one, depending on the batch.
Each extractor therefore collects EVERY candidate, ranks them on evidence (how many of the
person's details the document matches, then how many documents support the value), and
`_best` refuses to mint anything when the top two candidates are different values it
cannot separate — R2 applied to identity, not just to membership.

That refusal is a LAST RESORT, and how often it fires is a property of the extractors, not
of `_best`. Twice it was firing on input that was not ambiguous: `_company_domain` scored
every host `1`, so evidence could not separate two hosts at all and only the document count
could — the same remote ranking the refusal exists to distrust — and `_sec_cik` read the
FIRST CIK on a filing, which is the issuer's as often as the reporting person's, so two
filings about one human produced two values and neither survived. Both are repaired where
the evidence is read rather than where the tie is broken: a key that ranks on nothing will
tie on everything, and a tie is only honest when the extractor has already said everything
it knows.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from arrival.contracts import LLMError, PersonRef, RawDoc, Resolution, Verdict
from arrival.util import normalize_ws, slug

if TYPE_CHECKING:  # pragma: no cover - typing only
    from arrival.contracts import LLMClient

__all__ = [
    "MATCH_VALUES",
    "RESOLVE_SYSTEM",
    "STRONG_KEY_PRIORITY",
    "DocVerdict",
    "asserts_negation",
    "attribute_family",
    "cites_document",
    "city_detail",
    "conflicting_identity_claim",
    "negative_evidence_veto",
    "resolve",
    "strong_keys_for",
    "verdict_attribute",
    "verdict_attributes",
    "verdict_prompt",
]

MATCH_VALUES = ("yes", "no", "unsure")

#: The order T-2's acceptance criterion names the strong keys in, and the order
#: `strong_keys_for` returns them in. A durable identifier is worth more when it is
#: matched on more than a name, and this is that ranking made explicit rather than left to
#: the order four statements happen to sit in.
STRONG_KEY_PRIORITY = ("wikidata_qid", "company_domain", "github", "sec_cik")

#: One verdict is a few dozen tokens of JSON; the cap is a guard, not a budget.
_VERDICT_MAX_TOKENS = 600

#: Documents judged at once. Resolution is per-document and order-independent, so the
#: calls overlap; the cap keeps a 40-document person from opening 40 sockets at once.
_MAX_CONCURRENT_VERDICTS = 6

#: How much of a document is put in front of the model. `RawDoc.text` is capped at 20k by
#: T-1; the identity-bearing material is always near the top (title, byline, masthead).
#: The citation check in `cites_document` always runs against the FULL text.
_PROMPT_TEXT_CHARS = 8000

# Words that name an organisation-shaped detail rather than the organisation itself.
_ORG_SUFFIXES = frozenset(
    {"co", "inc", "corp", "corporation", "ltd", "llc", "plc", "gmbh", "ab", "as", "the"}
)

# The vocabulary `_names_a_role` reads, in `_tokens` form — `slug()`ged and split, so
# "co-founder" arrives here as `co` + `founder`. It exists to answer ONE question: is the
# half of a detail sitting before `", "` a job title or a place name? See `_names_a_role`
# for the two live rosters this got wrong.
_ROLE_TOKENS = frozenset(
    {
        "advisor", "analyst", "architect", "artist", "associate", "author", "blogger",
        "ceo", "cfo", "chair", "chairman", "chairperson", "chairwoman", "chief", "cio",
        "cmo", "cofounder", "consultant", "coo", "cro", "cto", "director", "editor",
        "engineer", "entrepreneur", "evp", "executive", "fellow", "founder", "general",
        "gp", "head", "investor", "journalist", "lead", "manager", "managing", "md",
        "member", "officer", "operator", "owner", "partner", "president", "principal",
        "professor", "researcher", "scientist", "staff", "svp", "trustee", "vp", "writer",
    }
)

# Words that hold a role phrase together without naming anything: "co-founder AND FORMER
# ceo" is one job title, not a job title plus an organisation.
_ROLE_GLUE = frozenset(
    {
        "a", "acting", "an", "and", "at", "board", "briefly", "co", "current",
        "currently", "deputy", "emeritus", "ex", "for", "former", "formerly", "global",
        "in", "interim", "junior", "of", "senior", "the", "with",
    }
)

# The six attributes RESOLVE_SYSTEM enumerates ("employer, city, role, handle, school, or
# coauthor"), each with the spellings a model reaches for instead. Matched as substrings of
# the lower-cased label, first family wins, so the order of the rows is the tie-break:
# `employer` and `city` come first because they are the two the veto can CONTRADICT.
#
# Only `employer` and `city` used to be canonicalised, which meant `role` and `job title`
# were two attributes and resolved a person that `role` twice refused. The vocabulary is
# closed because our OWN system prompt closes it — a label outside this table is
# off-contract, and `verdict_attribute` folds every such label into one bucket rather than
# letting invented words manufacture independence.
_FAMILY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "employer",
        ("employer", "company", "organisation", "organization", "workplace", "firm",
         "employment", "employed"),
    ),
    (
        "city",
        ("city", "location", "town", "where they live", "lives in", "based in",
         "residence", "hometown", "home town"),
    ),
    ("role", ("role", "title", "occupation", "position", "profession", "job", "post")),
    ("handle", ("handle", "username", "user name", "screen name", "nickname", "alias",
                "login", "profile")),
    ("school", ("school", "university", "college", "alma mater", "education", "degree",
                "alumni")),
    ("coauthor", ("coauthor", "co-author", "co author", "collaborator", "coauthorship")),
)

_KNOWN_FAMILIES = frozenset(family for family, _ in _FAMILY_WORDS)

# The bucket every off-contract label lands in. One bucket, not one per spelling: two
# labels this module cannot name are not two attributes, they are two unknowns.
_UNRECOGNISED_FAMILY = "other"

# The two families the NEGATIVE-evidence veto can read, and the two `_details_matched`
# scores a strong key on. Both are questions about a CONTRADICTION, and only these two are
# ever contradicted: `negative_evidence_veto` asks whether a `no` asserts a different
# employer or a different city, and `conflicting_identity_claim` asks the same of a
# structured `Employer:`/`Location:` claim. Widening this tuple would widen the veto, which
# is a different change from widening what a span may CORROBORATE — see
# `_corroborable_attributes`, which is the one this module counts for independence.
_CORROBORABLE = ("employer", "city")

# The prefix every attribute derived from a roster detail beyond the employer and the city
# carries. It exists so such an attribute can never collide with `_KNOWN_FAMILIES`, with
# `_UNRECOGNISED_FAMILY`, or with `employer`/`city` — a collision would silently MERGE two
# attributes into one, and while that only ever under-counts, an attribute name that says
# where it came from is what makes `/debug` readable.
_DETAIL_PREFIX = "detail:"

# Words that make a detail a NEGATIVE assertion. The live roster carries one —
# `"NOT the author/apologist Nabeel Qureshi who died in 2017"` — and it names, on purpose,
# the person this member is NOT. Deriving a corroborable attribute from it would let a
# document about the WRONG human being corroborate the right one, which is the exact SPEC
# S4 failure this module exists to prevent, arriving through the roster instead of through
# the internet. There is no way to tell which half of a negated sentence is negated without
# parsing English, so the whole detail is refused: no attribute, no employer, no city, and
# (in `connectors.propublica`) no query. Failing closed costs a disambiguator; failing open
# costs the person's identity.
_NEGATIONS = frozenset({"not", "never", "nor", "isnt", "arent", "wasnt", "werent", "dont"})

# `;` joins two INDEPENDENT clauses of a detail; the same reading `connectors.base`
# already gives one. Spelled here rather than imported for the reason `_names_a_role` is:
# `research` imports this module at module scope and is deliberately free of httpx, which
# every module under `connectors` pulls in.
_DETAIL_CLAUSE = re.compile(r"\s*;\s*")

# Where one organisation-shaped phrase ends and the next begins. `connectors.base`'s
# `_SPLIT_AFFILIATION` plus ` and ` and `/`, because a leftover detail is written as prose
# — `"formerly Greylock and Pinterest"` names TWO former employers and a rule that cannot
# see the conjunction demands a span quoting both of them.
_DETAIL_PHRASE = re.compile(r"\s+(?:of|at|for|with|and)\s+|\s*[,/&|@]\s*", re.IGNORECASE)

# A parenthetical annotates the phrase beside it: `"OpenAI (Nov 2023)"` names OpenAI in
# November 2023, and demanding a span that quotes the date makes the disambiguator
# unmatchable. The outside is read without it; the inside is read separately only when it
# is a single word, which is the shape of an acronym (`"(LTSE)"`) and not of a date.
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")

# A web address sitting in a detail, in either spelling `connectors.base` recognises. The
# whole CLAUSE carrying one is dropped, exactly as `affiliations` drops it: what is left of
# `"essays at nabeelqu.co"` once the address goes is the word "essays".
_WEB_ADDRESS = re.compile(
    r"https?://"
    r"|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
    r"\.(?:com|org|net|edu|gov|io|co|ai|dev|app|me|xyz|blog|news|tech|info|biz|so|to)\b",
    re.IGNORECASE,
)

# The shortest word that may carry a phrase on its own. A phrase whose every word is job
# vocabulary or three letters long is not a disambiguator — `"co-founder and partner"` and
# `"Ben"` both fail here — and an attribute a common word can corroborate is an attribute
# the wrong document earns.
_DISTINCTIVE_CHARS = 4

# Structured `Label: value` claims an identity document makes about employer and city.
# `conflicting_identity_claim` reads these; a document that makes none of them asserts no
# conflict, whatever else it says.
_IDENTITY_FIELDS = (
    ("employer", ("employer", "company", "workplace")),
    ("city", ("work location", "location", "city")),
)

# Every field label a run-together profile might use, so a captured value can be cut at
# the next one. Not only the identity fields: `Name:` and `Occupation:` sit between them.
_LABEL_ALTERNATION = "|".join(
    re.escape(label)
    for label in (
        "employer",
        "company",
        "workplace",
        "work location",
        "location",
        "city",
        "name",
        "occupation",
        "instance of",
        "country of citizenship",
        "title",
        "relationship",
        "issuer",
    )
)

_QID = re.compile(r"\bQ\d{1,12}\b")
_CIK = re.compile(r"\bCIK(?:\s+of\s+[a-z ]+)?\s*[:#]?\s*(\d{6,12})\b", re.IGNORECASE)
_UNSET = frozenset({"", "-", "n/a", "na", "none", "not set", "unset", "unknown", "null"})

RESOLVE_SYSTEM = """You decide whether ONE document is about ONE specific person.

You are given a person (a name plus one or two identifying details, typically an employer \
and a city) and a single document. Answer only about that document.

Rules:
- "yes" means the document is about THIS person and you can point at the span that proves \
it: the document ties the name to one of the person's details.
- "no" means the document is about someone else. Use it whenever the document asserts an \
employer, workplace, city or life history that CONTRADICTS the person's details, even if \
the name matches exactly. Same-name people are the normal case, not the exception.
- "unsure" means the name appears but nothing in the document confirms or contradicts a \
detail. Never guess: "unsure" costs nothing and a wrong "yes" poisons everything \
downstream.
- evidence MUST be copied verbatim from the document text, character for character. Do \
not paraphrase, do not summarise, do not repair punctuation. A span that is not in the \
document is discarded and your verdict with it.
- disambiguator names the single attribute your verdict turned on, lower case, one word \
where possible: employer, city, role, handle, school, or coauthor.
- confidence is 0..1 and describes THIS verdict alone.

Answer with the JSON object the schema describes and nothing else."""


class DocVerdict(BaseModel):
    """The model's answer about one document. Internal to this module by design.

    `contracts.Verdict` is the resolver's OUTPUT contract, not the shape the model is asked
    for; keeping them separate is what lets the judgement schema carry prompt-facing
    descriptions without touching the frozen contract.
    """

    doc_id: str = Field(default="", description="The id of the document being judged.")
    match: Literal["yes", "no", "unsure"] = Field(
        default="unsure", description="yes if this document is about the person."
    )
    confidence: float = Field(default=0.0, description="0..1 confidence in this verdict.")
    evidence: str = Field(default="", description="A verbatim span copied from the document.")
    disambiguator: str = Field(
        default="", description="The attribute the verdict turned on: employer, city, role…"
    )


# --------------------------------------------------------------------------
# the public entry point
# --------------------------------------------------------------------------


async def resolve(person: PersonRef, docs: list[RawDoc], llm: LLMClient) -> Resolution:
    """Decide which of `docs` are about `person`. See the module docstring for the rule."""
    unique = _unique_docs(docs)
    verdicts = await _judge_all(person, unique, llm)

    accepted: list[tuple[RawDoc, Verdict]] = []
    unaccepted: list[Verdict] = []
    for doc, verdict in zip(unique, verdicts, strict=True):
        # The veto is asked FIRST, of every verdict, and its answer decides the document's
        # fate on its own. Asking it only about verdicts that had already passed
        # `match == "yes"` made its positive branch unreachable from here — the caller and
        # the callee tested disjoint conditions, so the pipeline never once consulted
        # DESIGN Decision 4's hard reject however the model answered.
        if negative_evidence_veto(person, verdict):
            unaccepted.append(verdict)
            continue
        if verdict.match == "yes":
            accepted.append((doc, verdict))
        else:
            unaccepted.append(verdict)

    # Acceptance is not enough to anchor a strong key. An accepted document whose own
    # evidence names another employer or another work location identifies a DIFFERENT
    # human being with the same name, and a key taken from it is durable and wrong — the
    # exact SPEC S4 failure, arriving through a `yes` instead of through a `no`.
    keyable = [
        doc for doc, verdict in accepted if not conflicting_identity_claim(person, verdict)
    ]
    strong_keys = strong_keys_for(person, keyable)
    # `verdict_attributes`, never the raw `disambiguator`: the label is a word the model
    # chose and the evidence is a span it had to copy out of the document, so the span is
    # what gets to say which attribute was corroborated. See its docstring for the three
    # measured ways the label alone decided whether a person existed.
    attributes: set[str] = set()
    for _, verdict in accepted:
        attributes |= verdict_attributes(person, verdict)
    # Decision 4's second arm asks for two `yes` VERDICTS citing different attributes, and
    # the verdict count is now stated rather than implied. It used to be a side effect of
    # `verdict_attribute` returning exactly one attribute per verdict, which is why a span
    # naming both the employer and the city had to be filed under one of them and the other
    # corroboration thrown away. The requirement belongs here, where it is what it says.
    independent = len(accepted) >= 2 and len(attributes) >= 2
    resolved = bool(strong_keys) or independent

    if not resolved:
        # R2: an unresolved person stores NO documents. Every verdict is kept for /debug.
        return Resolution(
            person_id=person.person_id,
            status="unresolved",
            strong_keys={},
            accepted_doc_ids=[],
            rejected=list(verdicts),
            confidence=0.0,
        )
    return Resolution(
        person_id=person.person_id,
        status="resolved",
        strong_keys=strong_keys,
        accepted_doc_ids=[doc.doc_id for doc, _ in accepted],
        rejected=unaccepted,
        # The best single piece of evidence, never a pooled or averaged one.
        confidence=max(verdict.confidence for _, verdict in accepted),
    )


# --------------------------------------------------------------------------
# per-document judgement
# --------------------------------------------------------------------------


def _unique_docs(docs: list[RawDoc]) -> list[RawDoc]:
    """`docs` with duplicate `doc_id`s dropped, first occurrence kept, order preserved.

    Several connectors can return the same url for one person, and `doc_id` is
    `sha1(url)[:16]`, so duplicates are normal input rather than a caller error. Judging
    one twice would spend two LLM calls to reach the same verdict and could put the same
    document in `accepted_doc_ids` twice.
    """
    seen: set[str] = set()
    unique: list[RawDoc] = []
    for doc in docs:
        if doc.doc_id in seen:
            continue
        seen.add(doc.doc_id)
        unique.append(doc)
    return unique


async def _judge_all(person: PersonRef, docs: list[RawDoc], llm: LLMClient) -> list[Verdict]:
    """One verdict per document, in document order, judged with bounded concurrency."""
    gate = asyncio.Semaphore(_MAX_CONCURRENT_VERDICTS)

    async def judge(doc: RawDoc) -> Verdict:
        async with gate:
            return await _judge(person, doc, llm)

    return list(await asyncio.gather(*(judge(doc) for doc in docs)))


async def _judge(person: PersonRef, doc: RawDoc, llm: LLMClient) -> Verdict:
    """Ask the model about one document, then police the answer."""
    try:
        answer = await llm.structured(
            system=RESOLVE_SYSTEM,
            user=verdict_prompt(person, doc),
            schema=DocVerdict,
            max_tokens=_VERDICT_MAX_TOKENS,
            cache_prefix=True,
        )
    except LLMError:
        # A model that cannot answer has not said "yes". An unjudged document is `unsure`,
        # never an assumption in either direction, and the build survives.
        return Verdict(
            doc_id=doc.doc_id, match="unsure", confidence=0.0, evidence="", disambiguator=""
        )
    return _verdict_from(doc, answer)


def _verdict_from(doc: RawDoc, answer: object) -> Verdict:
    """Turn one model answer into a `contracts.Verdict`, applying the citation check."""
    match = str(getattr(answer, "match", "unsure") or "unsure").strip().lower()
    if match not in MATCH_VALUES:
        match = "unsure"
    evidence = str(getattr(answer, "evidence", "") or "")
    disambiguator = str(getattr(answer, "disambiguator", "") or "").strip()
    try:
        confidence = float(getattr(answer, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    if match != "unsure" and not cites_document(evidence, doc.text):
        # DESIGN Decision 5. The verdict is not thrown away — it is demoted to the value
        # that carries no weight, and the uncited span is kept so /debug can show it.
        match = "unsure"

    # The doc_id is OURS, never the model's echo of it.
    return Verdict(
        doc_id=doc.doc_id,
        match=match,
        confidence=confidence,
        evidence=evidence,
        disambiguator=disambiguator,
    )


def cites_document(evidence: str, text: str) -> bool:
    """True when `evidence` is a real span of `text` (DESIGN Decision 5)."""
    quote = normalize_ws(evidence)
    return bool(quote) and quote in normalize_ws(text)


def verdict_prompt(person: PersonRef, doc: RawDoc) -> str:
    """The per-document user prompt. Names the document so a verdict cannot be misfiled."""
    details = "\n".join(f"  - {detail}" for detail in person.details) or "  - (none given)"
    body = doc.text[:_PROMPT_TEXT_CHARS]
    truncated = "\n[document truncated]" if len(doc.text) > _PROMPT_TEXT_CHARS else ""
    return (
        f"PERSON\nname: {person.name}\nidentifying details:\n{details}\n\n"
        f"DOCUMENT\ndoc_id: {doc.doc_id}\nsource_kind: {doc.source_kind}\n"
        f"url: {doc.url}\ntitle: {doc.title}\ntext:\n{body}{truncated}\n\n"
        f"Is this document about {person.name}? Answer for doc_id {doc.doc_id}."
    )


# --------------------------------------------------------------------------
# negative evidence
# --------------------------------------------------------------------------


def negative_evidence_veto(person: PersonRef, verdict: Verdict) -> bool:
    """True when this `no` asserts an employer or city that contradicts the person.

    A veto is stronger than an ordinary rejection: the document cannot be accepted, and it
    cannot carry a strong key, no matter how exactly the name matches or how confident the
    other verdicts are. This is the whole of SPEC S4 — the decoy's documents carry the same
    name, overlapping subject matter, and even the target's city; the ONLY thing that
    separates the two people is a document saying, in as many words, that its subject works
    somewhere else and lives somewhere else.

    An uncited `no` never reaches here: `_verdict_from` has already demoted it to `unsure`,
    so hallucinated negative evidence cannot veto anything either.

    The polarity guard below is load-bearing and is NOT the thing to relax. `resolve` calls
    this on every verdict, so the guard is what confines the veto to verdicts the model has
    already decided against — and that is deliberate, because the test this applies to the
    evidence ("it does not mention our employer") reads absence of corroboration as
    conflict. On a `no` that is a sound reading of an answer the model has already given.
    On a `yes` it is not: a genuine `yes` routinely cites a span that never spells the
    employer out. The evidence-only question an accepted document has to answer instead is
    `conflicting_identity_claim`.
    """
    if verdict.match != "no":
        return False
    attribute = _contradicted_attribute(verdict.disambiguator)
    if attribute is None:
        return False
    detail = _employer(person) if attribute == "employer" else _city(person)
    if not detail:
        # Nothing of ours for the evidence to corroborate, and the model asserted a
        # conflicting attribute: the rejection stands as a veto.
        return True
    # If the negative evidence quotes the person's OWN employer/city it is not asserting a
    # conflicting one, whatever else it says, so it rejects without vetoing.
    return not _mentions(verdict.evidence, detail)


def conflicting_identity_claim(person: PersonRef, verdict: Verdict) -> bool:
    """True when this verdict's evidence NAMES an employer or city that is not the person's.

    Polarity-free on purpose, and deliberately a different question from
    `negative_evidence_veto`. That one reads a `no` the model has already committed to and
    can afford to treat "the evidence does not mention our employer" as a conflict. This
    one runs over documents that were ACCEPTED, where the same reading would be a
    disaster: `strong-key-sec-cik`'s winning verdict cites *"Relationship of reporting
    person to issuer: Officer. Title: Chief Financial Officer."*, which never spells the
    employer out, and treating that silence as a contradiction throws away a CIK that
    Decision 4 says is earned.

    So this asks for a POSITIVE assertion instead: a structured `Employer:` / `Company:` /
    `Work location:` / `Location:` / `City:` claim whose value is set and is not the
    person's. That is the shape identity documents actually use — a Wikidata item mirror,
    a GitHub profile — and it is the shape that makes a strong key look earned when it is
    not. Evidence that simply says nothing about the attribute is not a conflict.

    Only a strong key turns on this. Whether the document is accepted is decided by the
    verdict (see `resolve`), because a `yes` on a document that also names another
    employer is a judgement the model made with the whole text in front of it, and the
    frozen suite pins that reading.
    """
    for attribute, labels in _IDENTITY_FIELDS:
        detail = _employer(person) if attribute == "employer" else _city(person)
        if not detail:
            # Nothing of ours to conflict WITH. Unlike a `no`, an accepted document gets
            # the benefit of the doubt: we cannot call a claim wrong without a claim of
            # our own to weigh it against.
            continue
        for label in labels:
            value = _claimed_field(verdict.evidence, label)
            if not value or _is_unset(value):
                continue
            if not _mentions(value, detail):
                return True
    return False


def _claimed_field(text: str, label: str) -> str:
    """The value a `Label: value` claim asserts, or `""` when the claim is not made."""
    found = re.search(rf"\b{re.escape(label)}\s*:\s*([^\n.]*)", text, re.IGNORECASE)
    if not found:
        return ""
    value = found.group(1)
    # A run-together profile — `Name: X Company: not set Location: Y` — puts the NEXT
    # field inside this one's capture, which would read an unset company as the string
    # "not set Location: Y" and call it a conflicting employer. Cut at the next label.
    boundary = re.search(rf"\s+\b(?:{_LABEL_ALTERNATION})\s*:", value, re.IGNORECASE)
    if boundary:
        value = value[: boundary.start()]
    return value.strip()


def _contradicted_attribute(disambiguator: str) -> str | None:
    """The family a `no` can CONTRADICT — only the two the person carries as details."""
    family = attribute_family(disambiguator)
    return family if family in _CORROBORABLE else None


def attribute_family(disambiguator: str) -> str:
    """The identity ATTRIBUTE a disambiguator names, independent of how it is spelled.

    Decision 4's second arm asks for two INDEPENDENT attributes, and the raw label is a
    free-text string the model chose: it will call one attribute `employer` on one
    document and `company` on the next. Counting raw strings would read that as two
    attributes and resolve a person on ONE fact corroborated twice — the precise failure
    the rule exists to prevent, arriving through spelling rather than through logic.

    The table covers all six attributes `RESOLVE_SYSTEM` asks the model for, not just the
    two the veto needed: canonicalising `employer`/`company` while leaving `role` and
    `job title` apart fixed the failure in one family and left it standing in the other
    four, which is how `role` twice came to refuse a person that `role` plus `job title`
    admitted.

    A label outside the table keeps its normalised spelling, because this is the function
    a reader asks to NAME an attribute rather than to count one, and a lossy answer there
    helps nobody. Deciding that two unknown labels are not two attributes belongs to
    `verdict_attributes`, which is the caller that counts.
    """
    label = normalize_ws(disambiguator)
    if not label:
        return ""
    for family, words in _FAMILY_WORDS:
        if any(word in label for word in words):
            return family
    return label


def verdict_attributes(person: PersonRef, verdict: Verdict) -> frozenset[str]:
    """Every identity attribute this verdict's evidence corroborates. Empty for none.

    Decision 4's second arm is the reason a person exists in the product at all, and
    handing that decision to `verdict.disambiguator` hands it to the model's vocabulary.
    Three failures were measured on the label-only version, all with the documents held
    fixed: `role` twice refused a person that `role` + `job title` admitted; and two spans
    that both quote the employer resolved a person as soon as one of them was labelled
    `handle`. The model chooses the word. It does not choose which of the person's details
    its own verbatim span names, so that is what is asked first:

    1. If the evidence corroborates any of the person's own details, those ARE the
       attributes and the label is not consulted. The span is checked against the document
       (Decision 5); the label is checked against nothing. EVERY detail is read, not only
       the employer and the city — see `_corroborable_attributes` for the measurement that
       forced that (T-080) and for the one-detail-one-attribute rule that keeps the T-047
       attack closed underneath it.
    2. Otherwise fall back to the canonical family of the label — the only path for
       `role`, `handle`, `school` and `coauthor`, which `PersonRef.details` typically
       carries nothing to corroborate against — and an off-contract label becomes `other`,
       one bucket for everything the system prompt did not ask for, so invented words
       cannot add up to independence.

    **Step 1 costs real resolutions and is kept anyway; this is the trade.** The
    disambiguating detail a model quotes is overwhelmingly the employer, so a span
    labelled `role` or `handle` that also quotes the employer is the COMMON case, and it
    contributes `employer` alone. Measured, documents and labels held fixed: `role` +
    `employer` where both spans quote the employer gives `{employer}`, not
    `{employer, role}`; so does `handle` + `employer` where the handle span reads
    `github.com/dwhitfield - Harrowgate Systems`, even though that span really does name a
    handle. Those two people no longer resolve on the second arm.

    Weakening it is what does not work. Preferring an in-contract label whenever the
    corroborated detail is not the one the label names restores exactly the attack T-031
    closed: the model relabels one of two employer-quoting spans `handle`, `handle` is
    in-contract, and one fact counted twice becomes two independent attributes again.
    Measured on a patched copy — `{employer, handle}`, `independent=True`. The label is
    free to the model and the span is not, so a rule that lets the label outrank the span
    hands Decision 4's second arm back to word choice, which is the thing the arm exists to
    take away from it. Where a span genuinely turns on another attribute it can quote that
    attribute instead of the employer, and then it counts.

    What DID change is the other half, which was pure loss with nothing bought: a span
    naming BOTH details used to be filed under `employer` only, so a GitHub or Wikidata
    profile quoting the employer and the city corroborated one attribute instead of two.
    That collapse was standing in for "one document must not resolve a person by itself" —
    a requirement `resolve` now states directly by demanding two accepted verdicts, where
    it is checkable, instead of paying for it by discarding evidence here.
    """
    corroborated = _corroborated_details(verdict.evidence, person)
    if corroborated:
        return frozenset(corroborated)
    family = attribute_family(verdict.disambiguator)
    if not family:
        return frozenset()
    return frozenset({family if family in _KNOWN_FAMILIES else _UNRECOGNISED_FAMILY})


def verdict_attribute(person: PersonRef, verdict: Verdict) -> str:
    """The single attribute that best names what this verdict turned on, or `""`.

    `verdict_attributes` is what `resolve` counts; this is the one-word summary of the same
    rule. It agrees with the label when the evidence bears the label out, and otherwise
    names the first detail the span corroborates in canonical order — `employer`, then
    `city`, then the remaining roster details in the order the roster wrote them.

    It has no product caller today, and that is a gap in a file this module does not own
    rather than a spare part: `web/templates/debug.html` renders `verdict.disambiguator`
    raw, so the one surface that exists to explain a verdict shows the model's own word
    instead of the attribute the evidence actually bore out — the same word this module
    spent T-031 refusing to let decide anything. Rendering this function there is the
    one-line change that closes it.
    """
    attributes = verdict_attributes(person, verdict)
    if not attributes:
        return ""
    family = attribute_family(verdict.disambiguator)
    if family in attributes:
        return family
    for corroborated in _corroborated_details(verdict.evidence, person):
        if corroborated in attributes:
            return corroborated
    # `sorted(...)[0]` rather than `next(iter(...))`: the argument is a frozenset, so the
    # old line returned a value chosen by `PYTHONHASHSEED` — the same verdict could name a
    # different attribute on `/debug` between two runs of the same build.
    return sorted(attributes)[0]


def _corroborated_details(evidence: str, person: PersonRef) -> tuple[str, ...]:
    """Which of the person's own details this evidence span names, in canonical order.

    `employer` first, then `city`, then one attribute per REMAINING detail in roster order.
    """
    return tuple(
        name
        for name, groups in _corroborable_attributes(person)
        if any(_mentions(evidence, list(tokens)) for tokens in groups)
    )


#: One corroborable attribute: its name, and the alternative token groups any ONE of which
#: an evidence span may quote to corroborate it.
_Attribute = tuple[str, tuple[tuple[str, ...], ...]]


def _corroborable_attributes(person: PersonRef) -> tuple[_Attribute, ...]:
    """`(attribute, alternative token groups)` for every detail this person carries.

    **ONE DETAIL, ONE ATTRIBUTE — and that invariant is the whole safety argument.**

    T-080, measured live on 2026-09-04: `_CORROBORABLE` was `("employer", "city")` and the
    code comment justified it as *"the only two the person carries in `PersonRef.details`"*.
    That premise is false for seven of the ten people on the live roster, who carry a third
    detail. Sarah Tavel's is `"formerly Greylock and Pinterest"`; three of her ten accepted
    documents quote it verbatim, one of them TechCrunch's *"Tavel joined Benchmark in 2017
    after spending one and a half years as a partner at Greylock and three years as a
    product manager at Pinterest."* — and the resolver could not count a single one of them.
    Ten corroborating documents, one countable attribute, `unresolved`. The roster author
    supplied the disambiguator and the resolver was structurally unable to read it.

    What this does NOT do is raise or lower a threshold. `resolve` still demands two
    accepted `yes` verdicts, `_verdict_from` still demands a verbatim span, and
    `verdict_attributes` still asks the EVIDENCE first and the model's label second (T-031,
    T-047). The vocabulary of countable attributes is what widened, and only by the details
    the roster already wrote down.

    THE ATTACK THAT WIDENING COULD REOPEN, AND WHY IT DOES NOT. T-047 closed
    manufactured independence: two spans that both quote the employer are one fact however
    the model labels them, because the span outranks the label. A phrase-split re-opens the
    same shape one level down if it is allowed to mint TWO attributes out of ONE detail —
    `"formerly Ben and Jerry's"` split on ` and ` would make two accepted documents quoting
    that single employer look like two independent facts, which is the closed attack with
    punctuation standing in for the label. So the split happens BELOW the attribute: a
    detail's phrases are ALTERNATIVES (any one of them corroborates it) and never separate
    attributes. A person therefore has at most as many corroborable attributes as the
    roster gave them details, one apiece, whatever the punctuation.

    Measured consequence, live verdicts held fixed: Sarah Tavel goes from `{employer}` to
    `{employer, detail:formerly-greylock-and-pinterest}` and resolves; Josh Kopelman, whose
    two details are both already spent on the employer and the city and none of whose
    accepted spans quotes Philadelphia, stays `unresolved` — which is correct, not a
    shortfall.
    """
    attributes: list[_Attribute] = []
    spent: set[int] = set()
    for name, found in (("employer", _employer_detail(person)), ("city", _city_detail(person))):
        if found is None:
            continue
        index, tokens = found
        spent.add(index)
        attributes.append((name, (tuple(tokens),)))
    for index, detail in enumerate(person.details):
        if index in spent:
            continue
        groups = _detail_phrases(detail)
        if groups:
            attributes.append((_DETAIL_PREFIX + slug(detail), groups))
    return tuple(attributes)


def asserts_negation(detail: str) -> bool:
    """True when this roster detail says who the person is NOT. See `_NEGATIONS`.

    Public because `connectors.propublica` asks the same question of the same strings: a
    detail that negates something is not an organisation to search Nonprofit Explorer for
    either, and one spelling of the test is one answer to it.
    """
    return any(token in _NEGATIONS for token in _tokens(detail))


def _detail_phrases(detail: str) -> tuple[tuple[str, ...], ...]:
    """Token groups a span may quote to corroborate `detail`. ANY one group is enough.

    Alternatives rather than attributes — see `_corroborable_attributes` for why that
    distinction is the safety property and not a detail of the implementation.
    """
    if asserts_negation(detail):
        return ()
    groups: list[tuple[str, ...]] = []
    for clause in _DETAIL_CLAUSE.split(detail):
        if _WEB_ADDRESS.search(clause):
            # An address is not an affiliation, and what is left of the clause once it is
            # removed is worse than nothing. `connectors.base.affiliations` drops the same
            # clause for the same reason, and drops only the clause: a `;` can join a
            # company to a website (`"formerly Palantir; essays at nabeelqu.co"`).
            continue
        for fragment in _annotation_free(clause):
            for phrase in _DETAIL_PHRASE.split(fragment):
                tokens = _phrase_tokens(phrase)
                if _is_distinctive(tokens) and tokens not in groups:
                    groups.append(tokens)
    return tuple(groups)


def _annotation_free(clause: str) -> list[str]:
    """`clause` without its parentheticals, plus each parenthetical that is one word."""
    fragments = [_PARENTHETICAL.sub(" ", clause)]
    fragments.extend(inner for inner in _PARENTHETICAL.findall(clause) if len(_tokens(inner)) == 1)
    return fragments


def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    """The words a span has to quote for `phrase` to count — every one of them.

    Role GLUE is dropped rather than demanded: `_mentions` requires every token, so keeping
    `formerly` in `"formerly Greylock"` would demand a span that says "formerly", and the
    documents that actually quote the disambiguator do not.
    """
    return tuple(
        token
        for token in _tokens(phrase)
        if token not in _ORG_SUFFIXES and token not in _ROLE_GLUE
    )


def _is_distinctive(tokens: tuple[str, ...]) -> bool:
    """Is a word here specific enough to identify anybody? See `_DISTINCTIVE_CHARS`."""
    return any(
        len(token) >= _DISTINCTIVE_CHARS and token not in _ROLE_TOKENS for token in tokens
    )


# --------------------------------------------------------------------------
# strong keys
# --------------------------------------------------------------------------


def strong_keys_for(person: PersonRef, docs: list[RawDoc]) -> dict[str, str]:
    """Every strong key earnable from ACCEPTED documents, in `STRONG_KEY_PRIORITY` order.

    Only accepted documents are offered here. That is not an optimisation: the frozen decoy
    corpus's only Wikidata item belongs to the decoy and mentions the target's city (his
    papers are archived in Austin), so a QID check run over every document would match on
    name and city and take a key that identifies the wrong human being.

    Invariant, and the one worth testing: the result is a function of the SET of documents,
    never of their order. Each extractor below ranks candidates on evidence and refuses a
    tie, so permuting `docs` cannot move a single key.
    """
    extractors = {
        "wikidata_qid": _wikidata_qid,
        "company_domain": _company_domain,
        "github": _github_handle,
        "sec_cik": _sec_cik,
    }
    keys: dict[str, str] = {}
    for name in STRONG_KEY_PRIORITY:
        value = extractors[name](person, docs)
        if value:
            keys[name] = value
    return keys


def _best(candidates: list[tuple[str, int]]) -> str:
    """The best-evidenced value among `(value, details_matched)` candidates, or `""`.

    Two properties, and both are the point of this function existing at all:

    * **Order-independence.** Candidates are folded into a mapping keyed by VALUE and
      ranked on `(details matched, documents supporting it)`, so the answer depends on the
      set of documents and not on which one `research._interleave` put first. Every strong
      key used to be `for doc in docs: ... return`, which made a durable identifier a
      function of how many results each remote API happened to return.
    * **Refusal on a tie.** When the top two candidates are DIFFERENT values with identical
      evidence, this returns `""`. Picking either one would be arrival order wearing the
      costume of evidence, and a strong key is a claim about which human being this is —
      R2's "refuse to guess" applies to identity at least as hard as it applies to
      membership. The person can still resolve through another key or through the second
      arm; what they cannot do is carry an identifier nobody earned.
    """
    if not candidates:
        return ""
    strength: dict[str, tuple[int, int]] = {}
    for value, details in candidates:
        best_details, supporting = strength.get(value, (0, 0))
        strength[value] = (max(best_details, details), supporting + 1)
    ranked = sorted(strength.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ""
    return ranked[0][0]


def _wikidata_qid(person: PersonRef, docs: list[RawDoc]) -> str:
    """A QID matched on name AND a detail. A name-only match is what Decision 4 rejects.

    An item matching the name and BOTH details outranks one matching the name and one, so
    the QID — which is the identifier the rest of the graph spells hubs with — follows the
    evidence rather than the batch.
    """
    candidates: list[tuple[str, int]] = []
    for doc in docs:
        if doc.source_kind != "wikidata":
            continue
        haystack = f"{doc.title}\n{doc.text}"
        if not _name_matches(haystack, person.name):
            continue
        details = _details_matched(haystack, person)
        if not details:
            continue
        for candidate in (doc.url, doc.title, doc.text):
            found = _QID.search(candidate)
            if found:
                candidates.append((found.group(0), details))
                break
    return _best(candidates)


def _github_handle(person: PersonRef, docs: list[RawDoc]) -> str:
    """A handle whose profile is confirmed by BOTH the name and the company fields."""
    employer = _employer(person)
    if not employer:
        return ""
    candidates: list[tuple[str, int]] = []
    for doc in docs:
        if doc.source_kind != "github":
            continue
        name_field = _profile_field(doc.text, "name")
        company_field = _profile_field(doc.text, "company")
        if _is_unset(name_field) or _is_unset(company_field):
            # An unset company is not a conflicting company — the document is still
            # accepted — but half a confirmation is not a confirmation.
            continue
        if not _name_matches(name_field, person.name):
            continue
        if not _mentions(company_field, employer):
            continue
        handle = _github_handle_of(doc)
        if handle:
            # The company is already confirmed; a Location field naming the person's city
            # is the extra evidence that separates two otherwise identical profiles.
            located = _mentions(_profile_field(doc.text, "location"), _city(person))
            candidates.append((handle, 1 + int(located)))
    return _best(candidates)


def _sec_cik(person: PersonRef, docs: list[RawDoc]) -> str:
    """A CIK matched on name AND company. Matched on the name alone is a different filer."""
    employer = _employer(person)
    if not employer:
        return ""
    candidates: list[tuple[str, int]] = []
    for doc in docs:
        if doc.source_kind != "edgar":
            continue
        haystack = f"{doc.title}\n{doc.text}"
        if not _name_matches(haystack, person.name):
            continue
        # The company on a filing is often the ISSUER rather than the reporting person's
        # employer, which is exactly the case Decision 4 excludes.
        if not _mentions(haystack, employer):
            continue
        details = _details_matched(haystack, person)
        for cik in _person_ciks(haystack, person):
            candidates.append((cik, details))
    return _best(candidates)


def _person_ciks(haystack: str, person: PersonRef) -> tuple[str, ...]:
    """The CIKs this filing gives THIS person, in text order; the leading one as fallback.

    An ownership filing names at least two entities and gives each its own CIK — the
    reporting owner and the issuer — and `connectors.edgar` renders EDGAR's `display_names`
    verbatim into both the title and the body, so the page carries both numbers. Taking
    `_CIK.search(haystack)`, the FIRST number on the page, therefore read whichever entity
    EDGAR happened to list first: the person on one filing and her employer on the next.
    Two filings about one human then produced two different values with identical evidence,
    `_best` could not separate them, and the CIK it refused was one the corpus had stated
    twice. `research._by_display_priority` makes that arrive more often — keeping the
    edgar-stamped copy of a page `search` also indexed is its whole purpose, and it is
    right to — but the wrong number was always being read; the merge only stopped hiding it
    behind a `search` stamp that `_sec_cik` skipped.

    So the number is chosen by the name standing next to it. Each `,`/newline-separated
    entry of the rendered filing is one named entity, and the CIK that counts is the one in
    an entry naming the person — `Quennebeck Marisol (CIK 0001742119)` and
    `CIK of reporting person: 0009000701` are both that shape, and
    `Thornfield Loom Inc. (CIK 0009876543)` is not. Two DIFFERENT CIKs both attributed to
    the person are a contradiction, not a choice, so both are returned and `_best` refuses
    them; the fallback for a rendering that co-locates nothing is the old leading match,
    which keeps a page this rule cannot read no worse off than it already was.
    """
    named: list[str] = []
    for entry in re.split(r"[\n,;]", haystack):
        found = _CIK.search(entry)
        if not found or not _name_matches(entry, person.name):
            continue
        if found.group(1) not in named:
            named.append(found.group(1))
    if named:
        return tuple(named)
    leading = _CIK.search(haystack)
    return (leading.group(1),) if leading else ()


def _company_domain(person: PersonRef, docs: list[RawDoc]) -> str:
    """The employer's own registrable domain, when a document actually sits on it.

    Matched against the HOST only. `https://example.com/harrowgate-systems/research/team`
    is a page about Harrowgate Systems on somebody else's domain; treating its path as a
    domain match would hand out a strong key for every third-party profile page.

    The host is then cut back to the matching label and everything right of it, so
    `blog.harrowgatesystems.com` and `harrowgatesystems.com` are ONE identifier for one
    company instead of two that trade places with the arrival order. Everything left of
    the company's own label is a subdomain, and a subdomain names a section of a site, not
    a different employer.

    Each candidate is scored on what its document SAYS, exactly as the other three
    extractors are, and that is not a refinement — it is the difference between this
    extractor ranking on evidence and it ranking on nothing. Scoring every host `1` left
    `_best` with a single usable signal, the number of documents each host happened to
    get, which is chosen by the same remote APIs whose ranking `_best` exists to stop
    trusting; and on the ordinary shape — one `.com`, one `.io` or `.co.uk` or
    GitHub-Pages docs site, one document each — the counts tied and the key was refused on
    input that is not ambiguous at all. A page that names the person, the employer and the
    city is a better claim that this host is the employer's own domain than a bare docs
    host that merely spells the company's name, so it is ranked as one. The refusal stays
    for hosts that really are inseparable; it is no longer the common path.
    """
    employer = _employer(person)
    if not employer:
        return ""
    joined = "".join(employer)
    hyphenated = "-".join(employer)
    if len(joined) < 4:
        return ""
    candidates: list[tuple[str, int]] = []
    for doc in docs:
        host = urlsplit(doc.url).hostname or ""
        host = host.lower().removeprefix("www.")
        labels = host.split(".")
        for index, label in enumerate(labels[:-1]):  # everything but the TLD
            if label in {joined, hyphenated}:
                haystack = f"{doc.title}\n{doc.text}"
                # 0..3: the two identifying details, plus the person's own name. The name
                # is scored rather than required — a company's own domain is still the
                # company's domain on a page that never names this employee — but a page
                # that DOES name them is the stronger claim about whose employer it is.
                evidence = _details_matched(haystack, person) + int(
                    _name_matches(haystack, person.name)
                )
                candidates.append((".".join(labels[index:]), evidence))
                break
    return _best(candidates)


def _github_handle_of(doc: RawDoc) -> str:
    """The handle a GitHub profile document is about, from its url or its title."""
    path = [segment for segment in urlsplit(doc.url).path.split("/") if segment]
    if path:
        return path[-1]
    title = doc.title.strip()
    if title:
        return title.split()[-1]
    return ""


def _profile_field(text: str, field: str) -> str:
    """The value of a `Field: value` line, whether the profile is line- or run-together."""
    found = re.search(rf"\b{field}\s*:\s*([^\n.]*)", text, re.IGNORECASE)
    return found.group(1).strip() if found else ""


def _is_unset(value: str) -> bool:
    return normalize_ws(value).strip(" .") in _UNSET


# --------------------------------------------------------------------------
# details, names, tokens
# --------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return [token for token in slug(text).split("-") if token]


def _employer_detail(person: PersonRef) -> tuple[int, list[str]] | None:
    """`(detail index, distinctive tokens)` of the person's employer, or None.

    `"CFO at Ambervale Grain Co."` -> `["ambervale", "grain"]`;
    `"co-founder, Quarrystone Labs"` -> `["quarrystone", "labs"]`. The role half is dropped
    because "director" and "engineer" match half the internet, and the legal suffix is
    dropped because "Co." does too.

    Only the FIRST `;` clause of the organisation half is read. `_mentions` requires EVERY
    token, so `"co-founder, Foundry Group; co-founder, Techstars"` used to demand
    `foundry AND group AND founder AND techstars` in one evidence span — four words no
    real document about Brad Feld puts together, which made his employer permanently
    uncorroborable. A second `;` clause is a second affiliation, not more of the first
    one's name.

    The INDEX is returned, not just the tokens, because `_corroborable_attributes` has to
    know which details are already spent before it may read the rest of them.
    """
    for index, detail in enumerate(person.details):
        if asserts_negation(detail):
            # A detail naming who she is NOT never names where she works. See `_NEGATIONS`.
            continue
        organisation = _organisation_part(detail, person)
        if organisation is None:
            continue
        tokens = [
            token
            for token in _tokens(organisation.split(";", 1)[0])
            if token not in _ORG_SUFFIXES
        ]
        if tokens:
            return index, tokens
    return None


def _city_detail(person: PersonRef) -> tuple[int, list[str]] | None:
    """`(detail index, distinctive tokens)` of the city detail: the one naming no role."""
    for index, detail in enumerate(person.details):
        if asserts_negation(detail):
            continue
        if _organisation_part(detail, person) is not None:
            continue
        tokens = [token for token in _tokens(detail) if token not in _ORG_SUFFIXES]
        if tokens:
            return index, tokens
    return None


def _employer(person: PersonRef) -> list[str]:
    """The distinctive tokens of the person's employer. See `_employer_detail`."""
    found = _employer_detail(person)
    return found[1] if found else []


def _city(person: PersonRef) -> list[str]:
    """The distinctive tokens of the person's city detail. See `_city_detail`."""
    found = _city_detail(person)
    return found[1] if found else []


def city_detail(person: PersonRef) -> str:
    """The raw detail this module reads as the person's CITY, or `""`.

    Public for `connectors.propublica`, which must not send a city to an index of
    organisation NAMES and has no gazetteer to recognise one with. It does not need one:
    which detail is the place is already decided here, structurally — it is the detail that
    names no role and no organisation — and a second spelling of that question in the
    connector would be a second answer to it.
    """
    found = _city_detail(person)
    return person.details[found[0]] if found else ""


def _names_a_role(head: str) -> bool:
    """Is `head` a JOB rather than a name — every word a role word or the glue between?

    This is the test that separates `"co-founder, Foundry Group"` from
    `"Boulder, Colorado"`. `", "` is the separator a roster uses for BOTH, so a rule that
    reads the separator alone reads a city as an employer. Measured on the live roster:
    `"Boulder, Colorado"` became the organisation `"Colorado"`, which pushed Brad Feld's
    city detail off the end of `_city` and left `["feld", "com"]` — his blog's domain —
    standing in for the city he lives in; `"Sydney, Australia"` did the same to Melanie
    Perkins and left her with no city at all.

    Spelled here rather than imported from `connectors.identity`: `research` imports this
    module at module scope and is deliberately free of httpx, which every module under
    `connectors` pulls in.
    """
    tokens = [token for token in _tokens(head) if token]
    if not tokens:
        return False
    return all(token in _ROLE_TOKENS or token in _ROLE_GLUE for token in tokens)


def _splits(detail: str, *, role_headed: bool) -> str | None:
    """The tail of the FIRST split `detail` admits, or None.

    First-separator-wins, and the strict form does not fall through to a later one. It
    used to: `"Head of research at Quarrystone Labs"` splits on `" at "` with a head that
    is not a role phrase, and a `continue` there went on to try `" of "`, which splits the
    same detail into `"Head"` and `"research at Quarrystone Labs"` — a head that IS a role
    phrase and a tail that is not an organisation. A separator that fails the role test is
    this detail's answer being "no", not a reason to look for a worse cut of it.
    """
    for separator in (" at ", ", ", " of ", " @ "):
        head, found, tail = detail.partition(separator)
        if not found or not tail.strip() or not head.strip():
            continue
        if role_headed and not _names_a_role(head):
            return None
        return tail.strip()
    return None


def _organisation_part(detail: str, person: PersonRef | None = None) -> str | None:
    """The organisation half of a `<role> at <Organisation>` detail, else None.

    Two passes, and `person` is what makes the second one safe. The STRICT pass accepts a
    split only when the head names a role, which is what tells `"co-founder, Foundry
    Group"` from `"Boulder, Colorado"`. The LENIENT pass is the original separator-only
    rule, and it runs only when the strict pass found no organisation ANYWHERE in this
    person's details — a roster that writes an employer in a shape this module cannot
    recognise keeps the employer it used to get instead of losing it to a stricter
    reading. Passing no `person` asks the strict question alone.
    """
    strict = _splits(detail, role_headed=True)
    if strict is not None:
        return strict
    if person is None:
        return None
    if any(_splits(other, role_headed=True) is not None for other in person.details):
        return None
    return _splits(detail, role_headed=False)


def _mentions(text: str, tokens: list[str]) -> bool:
    """True when every token appears in `text` as a whole word."""
    if not tokens:
        return False
    haystack = normalize_ws(text)
    return all(re.search(rf"\b{re.escape(token)}\b", haystack) for token in tokens)


def _name_matches(text: str, name: str) -> bool:
    """True when every part of `name` appears in `text`, in any order.

    Order-free on purpose: EDGAR writes a reporting person as `BRENNINKMEYER HALVARD`, and
    a resolver that only recognises `Halvard Brenninkmeyer` would fail to see a name match
    it then has to refuse for a different reason.
    """
    parts = [token for token in _tokens(name) if len(token) > 1]
    return _mentions(text, parts)


def _details_matched(text: str, person: PersonRef) -> int:
    """How many of the person's identifying details this text matches: 0, 1 or 2.

    The count, not the boolean, because it is the evidence score every strong key is ranked
    on: an item matching name + employer + city is a better claim about which human this is
    than one matching name + city, and "better" has to be measurable or the tie goes to
    whoever arrived first.
    """
    return int(_mentions(text, _employer(person))) + int(_mentions(text, _city(person)))
