"""Offline corpus for the T-077 connection tests. NOT a test module.

Two people who really do have something in common and whose documents say so in DIFFERENT
words — which is the whole defect T-077 exists to remove. Written here rather than borrowed
from the frozen acceptance corpus for the reason `t3_corpus` gives: a project test reading
`.swarm-loop/acceptance/fixtures/` grades the gradee against a file it does not own.

The prose is deliberately short and every span a test quotes is a real substring of it, so
the citation guard is exercised against real text rather than routed around.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from arrival.contracts import PersonRef, RawDoc, Resolution
from arrival.util import doc_id

FETCHED = datetime(2026, 2, 20, 14, 0, tzinfo=UTC)
PUBLISHED = date(2026, 1, 5)

#: Two investors in one city. The roster detail spells the city plainly for one of them and
#: with an administrative tail for the other, because that is how the live roster spells it
#: ("New York" beside "Boulder, Colorado" beside "Sydney, Australia").
HARLOW = PersonRef(
    person_id="ada-harlow",
    name="Ada Harlow",
    details=["partner, Quillmark Capital", "Porthaven"],
)
BRIDGES = PersonRef(
    person_id="ines-bridges",
    name="Ines Bridges",
    details=["founder, Larkfield Group", "Porthaven, East Riding"],
)
#: A member the roster gives no place for at all — the silence that must NOT be read as a
#: claim that they are nowhere.
NORELL = PersonRef(
    person_id="tomas-norell",
    name="Tomas Norell",
    details=["writer and researcher"],
)

HARLOW_TEXT = (
    "Ada Harlow is a partner at Quillmark Capital, a seed-stage venture capital firm "
    "based in Porthaven.\n\n"
    "She has backed developer tools companies since 2014 and sits on four boards."
)
HARLOW_SPAN = "Ada Harlow is a partner at Quillmark Capital, a seed-stage venture capital firm"
HARLOW_CITY_SPAN = "She has backed developer tools companies since 2014 and sits on four boards."

BRIDGES_TEXT = (
    "Ines Bridges founded Larkfield Group and has worked in venture capital for twenty "
    "years.\n\n"
    "Larkfield keeps its only office in Porthaven and writes cheques out of it."
)
BRIDGES_SPAN = "Ines Bridges founded Larkfield Group and has worked in venture capital"
BRIDGES_CITY_SPAN = "Larkfield keeps its only office in Porthaven and writes cheques out of it."

#: A document naming a city that is NOT the member's, in the shape that produced the live
#: corpus's false positives: the place belongs to an institution the sentence mentions.
NORELL_TEXT = (
    "Tomas Norell writes essays and reviews for a small readership.\n\n"
    "He is a fellow of the Marrowfield Institute, a Porthaven-based research body, and "
    "works from Calderstane."
)
NORELL_SPAN = "Tomas Norell writes essays and reviews for a small readership."
NORELL_CITY_SPAN = "He is a fellow of the Marrowfield Institute, a Porthaven-based research body"


def _doc(url: str, title: str, text: str, kind: str = "search") -> RawDoc:
    return RawDoc(
        doc_id=doc_id(url),
        source_kind=kind,
        url=url,
        title=title,
        text=text,
        published_at=PUBLISHED,
        fetched_at=FETCHED,
    )


def harlow_doc() -> RawDoc:
    return _doc("https://example.test/harlow", "Ada Harlow", HARLOW_TEXT)


def bridges_doc() -> RawDoc:
    return _doc("https://example.test/bridges", "Ines Bridges", BRIDGES_TEXT)


def norell_doc() -> RawDoc:
    return _doc("https://example.test/norell", "Tomas Norell", NORELL_TEXT)


def resolution_for(person: PersonRef, *docs: RawDoc) -> Resolution:
    return Resolution(
        person_id=person.person_id,
        status="resolved",
        accepted_doc_ids=[doc.doc_id for doc in docs],
        rejected=[],
        confidence=0.9,
    )
