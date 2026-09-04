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
   AND company, an SEC CIK matched on name AND company. Failing that, at least two `yes`
   verdicts citing DIFFERENT disambiguators — compared as ATTRIBUTES rather than as
   strings (`attribute_family`), because a model that writes `employer` on one document
   and `company` on the next has named one attribute twice, not two. Two `yes` verdicts on
   the same attribute are corroboration of one fact, not independence. Anything less is
   `unresolved`, with
   `accepted_doc_ids` empty — no facts, no dossier, no guess.

The strong-key arm is the part that invites a shortcut, and the shortcut
("the document is a wikidata/github/edgar page and the verdict is `yes`, so take the key")
is wrong in a way that only shows up on the cases that matter: a Wikidata item can match a
name and nothing else, a GitHub profile can confirm a name with its company field unset,
and an EDGAR filing can match name and city while naming an entirely different issuer.
Each of those documents is a genuine `yes` and IS accepted — refusing the key is not the
same as refusing the document. So the key checks read the document, not its `source_kind`,
and only documents that were accepted — and that do not themselves assert somebody else's
employer or work location — may carry a key at all.
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
    "DocVerdict",
    "attribute_family",
    "cites_document",
    "conflicting_identity_claim",
    "negative_evidence_veto",
    "resolve",
    "strong_keys_for",
    "verdict_prompt",
]

MATCH_VALUES = ("yes", "no", "unsure")

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

# Disambiguator spellings that name an identity attribute a document can CONTRADICT.
_EMPLOYER_WORDS = ("employer", "company", "organisation", "organization", "workplace", "firm")
_CITY_WORDS = ("city", "location", "town", "where they live", "residence")

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
    attributes = {
        attribute_family(verdict.disambiguator)
        for _, verdict in accepted
        if attribute_family(verdict.disambiguator)
    }
    independent = len(attributes) >= 2
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
    label = normalize_ws(disambiguator)
    if any(word in label for word in _EMPLOYER_WORDS):
        return "employer"
    if any(word in label for word in _CITY_WORDS):
        return "city"
    return None


def attribute_family(disambiguator: str) -> str:
    """The identity ATTRIBUTE a disambiguator names, independent of how it is spelled.

    Decision 4's second arm asks for two INDEPENDENT attributes, and the raw label is a
    free-text string the model chose: it will call one attribute `employer` on one
    document and `company` on the next. Counting raw strings would read that as two
    attributes and resolve a person on ONE fact corroborated twice — the precise failure
    the rule exists to prevent, arriving through spelling rather than through logic.

    A label this module has no rule for keeps its normalised spelling, so two unrelated
    labels still count as two. That is the only direction in which this can be generous,
    and it is the same generosity the un-canonicalised version had everywhere.
    """
    label = normalize_ws(disambiguator)
    if not label:
        return ""
    return _contradicted_attribute(label) or label


# --------------------------------------------------------------------------
# strong keys
# --------------------------------------------------------------------------


def strong_keys_for(person: PersonRef, docs: list[RawDoc]) -> dict[str, str]:
    """Every strong key earnable from ACCEPTED documents, in priority order.

    Only accepted documents are offered here. That is not an optimisation: the frozen decoy
    corpus's only Wikidata item belongs to the decoy and mentions the target's city (his
    papers are archived in Austin), so a QID check run over every document would match on
    name and city and take a key that identifies the wrong human being.
    """
    keys: dict[str, str] = {}
    qid = _wikidata_qid(person, docs)
    if qid:
        keys["wikidata_qid"] = qid
    domain = _company_domain(person, docs)
    if domain:
        keys["company_domain"] = domain
    handle = _github_handle(person, docs)
    if handle:
        keys["github"] = handle
    cik = _sec_cik(person, docs)
    if cik:
        keys["sec_cik"] = cik
    return keys


def _wikidata_qid(person: PersonRef, docs: list[RawDoc]) -> str:
    """A QID matched on name AND a detail. A name-only match is what Decision 4 rejects."""
    for doc in docs:
        if doc.source_kind != "wikidata":
            continue
        haystack = f"{doc.title}\n{doc.text}"
        if not _name_matches(haystack, person.name):
            continue
        if not _any_detail_matches(haystack, person):
            continue
        for candidate in (doc.url, doc.title, doc.text):
            found = _QID.search(candidate)
            if found:
                return found.group(0)
    return ""


def _github_handle(person: PersonRef, docs: list[RawDoc]) -> str:
    """A handle whose profile is confirmed by BOTH the name and the company fields."""
    employer = _employer(person)
    if not employer:
        return ""
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
            return handle
    return ""


def _sec_cik(person: PersonRef, docs: list[RawDoc]) -> str:
    """A CIK matched on name AND company. Matched on the name alone is a different filer."""
    employer = _employer(person)
    if not employer:
        return ""
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
        found = _CIK.search(haystack)
        if found:
            return found.group(1)
    return ""


def _company_domain(person: PersonRef, docs: list[RawDoc]) -> str:
    """The employer's own domain, when a document actually sits on it.

    Matched against the HOST only. `https://example.com/harrowgate-systems/research/team`
    is a page about Harrowgate Systems on somebody else's domain; treating its path as a
    domain match would hand out a strong key for every third-party profile page.
    """
    employer = _employer(person)
    if not employer:
        return ""
    joined = "".join(employer)
    hyphenated = "-".join(employer)
    if len(joined) < 4:
        return ""
    for doc in docs:
        host = urlsplit(doc.url).hostname or ""
        host = host.lower().removeprefix("www.")
        labels = host.split(".")[:-1]  # everything but the TLD
        if any(label in {joined, hyphenated} for label in labels):
            return host
    return ""


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


def _employer(person: PersonRef) -> list[str]:
    """The distinctive tokens of the person's employer, from their details.

    `"CFO at Ambervale Grain Co."` -> `["ambervale", "grain"]`;
    `"co-founder, Quarrystone Labs"` -> `["quarrystone", "labs"]`. The role half is dropped
    because "director" and "engineer" match half the internet, and the legal suffix is
    dropped because "Co." does too.
    """
    for detail in person.details:
        organisation = _organisation_part(detail)
        if organisation is None:
            continue
        tokens = [token for token in _tokens(organisation) if token not in _ORG_SUFFIXES]
        if tokens:
            return tokens
    return []


def _city(person: PersonRef) -> list[str]:
    """The distinctive tokens of the person's city detail: the detail naming no role."""
    for detail in person.details:
        if _organisation_part(detail) is not None:
            continue
        tokens = [token for token in _tokens(detail) if token not in _ORG_SUFFIXES]
        if tokens:
            return tokens
    return []


def _organisation_part(detail: str) -> str | None:
    """The organisation half of a `<role> at <Organisation>` detail, else None."""
    for separator in (" at ", ", ", " of ", " @ "):
        head, found, tail = detail.partition(separator)
        if found and tail.strip() and head.strip():
            return tail.strip()
    return None


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


def _any_detail_matches(text: str, person: PersonRef) -> bool:
    """True when the text matches the employer detail or the city detail (not just the name)."""
    return _mentions(text, _employer(person)) or _mentions(text, _city(person))
