"""Offline corpus for the T-3 extractor tests. NOT a test module.

Owned by this ticket and written here rather than borrowed from the frozen acceptance
corpus: a project test that reads `.swarm-loop/acceptance/fixtures/` is grading the
gradee against a file it does not own, and it breaks the moment the orchestrator re-cuts
the corpus. The prose below is deliberately short — every quote a test uses is a span of
it, so the citation check is exercised against real text.
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

ABOUT_TEXT = (
    "I am Runa Okonkwo. I co-founded Quarrystone Labs in 2016 and I run the platform\n"
    "team there.\n\n"
    "Quarrystone Labs raised its first outside money from Foundry Seed in 2019, and that "
    "seed round is still the one I point new founders at."
)
ABOUT_SPAN = "I co-founded Quarrystone Labs in 2016 and I run the platform team there."
FOUNDRY_SPAN = "Quarrystone Labs raised its first outside money from Foundry Seed in 2019"

STATUS_TEXT = (
    "Archived capture, 14 June 2017. Quarrystone Labs status.\n\n"
    "Quarrystone Labs shipped a public status page in 2017, which at the time was unusual "
    "for a company of eleven people.\n\n"
    "We will keep this page up even on the days it makes us look bad."
)
STATUS_SPAN = "Quarrystone Labs shipped a public status page in 2017"
STATUS_SPAN_2 = "We will keep this page up even on the days it makes us look bad."

WIKIDATA_TEXT = (
    "Runa Okonkwo (Q900000411)\n\n"
    "Item mirror. Instance of: human. Employer: Quarrystone Labs. Work location: Austin."
)
WIKIDATA_SPAN = "Employer: Quarrystone Labs. Work location: Austin."

ROADMAP_TEXT = (
    "Quarrystone Labs opened its platform team roadmap to customers this month, a move its "
    "co-founder Runa Okonkwo described as the least surprising thing the company could do."
)
ROADMAP_SPAN = "Quarrystone Labs opened its platform team roadmap to customers this month"


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


def about_doc() -> RawDoc:
    return make_doc(
        "https://example.com/runa-okonkwo/about",
        "self_page",
        ABOUT_TEXT,
        published_at=date(2026, 1, 5),
        title="About",
    )


def status_doc() -> RawDoc:
    return make_doc(
        "https://web.example.org/web/20170614/quarrystone/status",
        "wayback",
        STATUS_TEXT,
        published_at=date(2017, 6, 14),
    )


def wikidata_doc() -> RawDoc:
    return make_doc("https://example.org/wikidata/Q900000411", "wikidata", WIKIDATA_TEXT)


def roadmap_doc() -> RawDoc:
    return make_doc(
        "https://example.org/tradepress/quarrystone-platform-roadmap",
        "search",
        ROADMAP_TEXT,
        published_at=date(2026, 2, 11),
    )


def resolution_for(*docs: RawDoc, accepted: list[str] | None = None) -> Resolution:
    """A `resolved` Resolution accepting the given documents unless told otherwise."""
    return Resolution(
        person_id=PERSON.person_id,
        status="resolved",
        strong_keys={},
        accepted_doc_ids=[d.doc_id for d in docs] if accepted is None else accepted,
        rejected=[],
        confidence=0.91,
    )


# --------------------------------------------------------------------------
# Documents added for the hub-identity and citation-guard repairs
# (T-010 / T-011 / T-014 / T-015). Additive: nothing above changed.
# --------------------------------------------------------------------------

FUND_TEXT = (
    "Foundry Seed 2019 (Q4242)\n\n"
    "Item mirror. Instance of: venture capital fund. Also known as Foundry Capital.\n"
    "Foundry Seed 2019 led the seed round in Quarrystone Labs."
)
FUND_SPAN = "Instance of: venture capital fund. Also known as Foundry Capital."

TRADE_TEXT = (
    "Foundry Seed 2019 has backed eleven infrastructure companies since it closed, and "
    "Quarrystone Labs was the first of them."
)
TRADE_SPAN = "Foundry Seed 2019 has backed eleven infrastructure companies since it closed"

#: The subject's own page carries an ASCII apostrophe and an em dash; a model that
#: re-types the span with typographic punctuation is quoting the same words.
PUNCTUATION_TEXT = (
    "Jane O'Neil ships the parser every Friday, and the team's release notes have named "
    "her in every one of the last nine—a streak that is now a running joke internally."
)
PUNCTUATION_SPAN = "Jane O'Neil ships the parser every Friday"
PUNCTUATION_DASH_SPAN = "the last nine—a streak that is now a running joke internally."


def fund_doc() -> RawDoc:
    """A Wikidata item mirror that states Q4242."""
    return make_doc("https://example.org/wikidata/Q4242", "wikidata", FUND_TEXT)


def trade_doc(n: int = 0) -> RawDoc:
    """One of several distinct documents carrying the SAME prose, for ambiguity tests."""
    return make_doc(
        f"https://example.org/tradepress/foundry-seed-{n}",
        "search" if n % 2 == 0 else "hn",
        TRADE_TEXT,
        published_at=date(2026, 2, 11) if n % 2 == 0 else date(2019, 3, 2),
    )


def punctuation_doc() -> RawDoc:
    return make_doc(
        "https://example.com/jane-oneil/notes",
        "github",
        PUNCTUATION_TEXT,
        published_at=date(2026, 2, 1),
    )
