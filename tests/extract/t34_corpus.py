"""Documents for the T-034..T-037 repairs. NOT a test module.

Written here rather than borrowed from the frozen acceptance corpus (a project test that
reads `.swarm-loop/acceptance/fixtures/` grades the gradee against a file it does not own)
and kept separate from `t3_corpus` so that nothing already asserted against that corpus
can move when a document here is edited.

Every document is deliberately about a DIFFERENT entity from every other, because the
whole class of defect these tickets cover is one document's material being attached to
another document's entity. Two documents that mention the same company cannot show that.

Publication dates are chosen so that no assertion in these tests depends on the wall
clock: `KESTREL` is old enough that it will be in the `0.3` band forever, and the other
documents carry no date at all, which is the fixed `0.5` unknown band.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from arrival.contracts import PersonRef, RawDoc, Resolution
from arrival.util import doc_id

FETCHED = datetime(2026, 2, 20, 14, 0, tzinfo=UTC)

PERSON = PersonRef(
    person_id="runa-okonkwo",
    name="Runa Okonkwo",
    details=["Co-founder of Quarrystone Labs", "Austin"],
)

# -- two documents about two DIFFERENT companies ---------------------------

#: Old enough to sit in the `0.3` recency band for good.
KESTREL_PUBLISHED = date(2017, 6, 14)
KESTREL_TEXT = (
    "Archived capture, 14 June 2017. Kestrel Yards status page.\n\n"
    "Kestrel Yards shipped a public status page in 2017, which at the time was unusual "
    "for a company of eleven people."
)
KESTREL_SPAN = "Kestrel Yards shipped a public status page in 2017"
KESTREL_SENTENCE = "Kestrel Yards shipped a public status page in 2017."

#: Undated on purpose -> the fixed 0.5 "unknown" band, so the assertions never drift.
HARBOUR_TEXT = (
    "I am Runa Okonkwo. I joined Harbourline Systems in 2024 and I run the platform "
    "team there.\n\n"
    "Harbourline Systems publishes every one of its incident reviews in the open."
)
HARBOUR_SPAN = "I joined Harbourline Systems in 2024 and I run the platform team there."
HARBOUR_SPAN_2 = "Harbourline Systems publishes every one of its incident reviews in the open."
HARBOUR_SENTENCE = "Runa Okonkwo joined Harbourline Systems in 2024."
HARBOUR_SENTENCE_2 = "Harbourline Systems publishes its incident reviews in the open."

# -- two Wikidata mirrors, each stating its OWN qid ------------------------

#: The PERSON's item. It names her employer, so a naive "does some evidence document
#: state this QID" check confirms it for a hub that has nothing to do with it.
PERSON_ITEM_TEXT = (
    "Runa Okonkwo (Q900000411)\n\n"
    "Item mirror. Instance of: human. Employer: Quarrystone Labs. Work location: Austin."
)
PERSON_ITEM_SPAN = "Instance of: human. Employer: Quarrystone Labs."

#: The FUND's item, a different entity with a different QID.
FUND_ITEM_TEXT = (
    "Foundry Seed 2019 (Q4242)\n\n"
    "Item mirror. Instance of: venture capital fund.\n"
    "Foundry Seed 2019 led the seed round in Quarrystone Labs."
)
FUND_ITEM_SPAN = "Item mirror. Instance of: venture capital fund."

#: A SECOND mirror of the fund, under a different QID — the ambiguity `_best` refuses.
#: Its span shares no wording with `FUND_ITEM_SPAN`, so the citation check can never see
#: one document's quote in the other and the ambiguity under test stays the QID's alone.
FUND_MIRROR_TEXT = (
    "Foundry Seed 2019 (Q7777)\n\n"
    "Second mirror record, imported from a different catalogue.\n"
    "Foundry Seed 2019 has backed eleven infrastructure companies since it closed."
)
FUND_MIRROR_SPAN = "Second mirror record, imported from a different catalogue."


def make_doc(
    url: str,
    source_kind: str,
    text: str,
    *,
    published_at: date | None = None,
    title: str = "",
) -> RawDoc:
    """A `RawDoc` whose `doc_id` is derived the way T-1 derives it."""
    return RawDoc(
        doc_id=doc_id(url),
        source_kind=source_kind,
        url=url,
        title=title,
        text=text,
        published_at=published_at,
        fetched_at=FETCHED,
    )


def kestrel_doc() -> RawDoc:
    return make_doc(
        "https://web.example.org/web/20170614/kestrel-yards/status",
        "wayback",
        KESTREL_TEXT,
        published_at=KESTREL_PUBLISHED,
    )


def harbour_doc() -> RawDoc:
    return make_doc(
        "https://example.com/runa-okonkwo/about",
        "self_page",
        HARBOUR_TEXT,
        title="About",
    )


def person_item_doc() -> RawDoc:
    return make_doc("https://example.org/wikidata/Q900000411", "wikidata", PERSON_ITEM_TEXT)


def fund_item_doc() -> RawDoc:
    return make_doc("https://example.org/wikidata/Q4242", "wikidata", FUND_ITEM_TEXT)


def fund_mirror_doc() -> RawDoc:
    return make_doc("https://example.org/wikidata/Q7777", "wikidata", FUND_MIRROR_TEXT)


def resolution_for(*docs: RawDoc) -> Resolution:
    return Resolution(
        person_id=PERSON.person_id,
        status="resolved",
        strong_keys={},
        accepted_doc_ids=[d.doc_id for d in docs],
        rejected=[],
        confidence=0.91,
    )
