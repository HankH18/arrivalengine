"""The sealed wrapper in `BaseConnector`, graded independently of the ten subclasses.

WHY THIS FILE EXISTS, measured rather than assumed.  `BaseConnector._finalise` caps the
result at `budget` and de-duplicates by `doc_id`.  Every one of the ten connectors ALSO
self-caps, so the base-class cap is redundant today -- and a redundant guarantee is an
ungraded one.  Sabotage check run while writing this suite: replacing `if len(kept) >=
budget` with `if len(kept) >= 999999` left **both** `pytest --ticket T-1` (187 passed) and
the frozen t1 suite (19 passed) completely green.

That cap is a safety net for the connector that does not yet exist, which is precisely the
kind of code that gets removed as dead. So the base class is graded here through
deliberately misbehaving subclasses, where the guarantee is the only thing standing
between the caller and the mess.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from arrival.connectors.base import (
    BaseConnector,
    affiliations,
    hosts_in,
    parse_date,
    text_block,
    urls_in,
)
from arrival.contracts import PersonRef, RawDoc
from arrival.util import doc_id

pytestmark = pytest.mark.ticket("T-1")

PERSON = PersonRef(
    person_id="marisol-quennebeck",
    name="Marisol Quennebeck",
    details=["co-founder, Thornfield Loom", "Providence, Rhode Island",
             "https://thornfieldloom.example.com/"],
)


def _doc(url: str) -> RawDoc:
    return RawDoc(
        doc_id=doc_id(url),
        source_kind="self_page",
        url=url,
        title="",
        text="Some prose about the member that is long enough to be evidence.",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )


class _Overproducer(BaseConnector):
    """A source that ignores its budget entirely. The base class has to stop it."""

    kind = "self_page"

    async def _search(self, person, budget):
        return [_doc(f"https://thornfieldloom.example.com/page/{n}") for n in range(20)]


class _Repeater(BaseConnector):
    """A talkative source returning the same page over and over."""

    kind = "self_page"

    async def _search(self, person, budget):
        return [_doc("https://thornfieldloom.example.com/about")] * 12


class _Exploder(BaseConnector):
    kind = "self_page"

    async def _search(self, person, budget):
        raise RuntimeError("the API changed shape overnight")


class _ReturnsGarbage(BaseConnector):
    """A subclass with a bug, which is not the same thing as a dead source."""

    kind = "self_page"

    async def _search(self, person, budget):
        return ["not a RawDoc", None, 17]


class _ReturnsNone(BaseConnector):
    kind = "self_page"

    async def _search(self, person, budget):
        return None


def _run(connector, budget):
    return asyncio.run(connector.search(PERSON, budget))


def test_budget_is_enforced_by_the_base_class_even_when_the_subclass_ignores_it():
    docs = _run(_Overproducer(), 3)

    assert len(docs) == 3, (
        f"a connector that ignored its budget returned {len(docs)} documents. The cap in "
        "BaseConnector._finalise is the only thing between one talkative source and a "
        "person's entire research allowance (DESIGN §Budget, docs_per_connector)."
    )


def test_duplicates_are_removed_before_the_budget_is_spent_on_them():
    docs = _run(_Repeater(), 5)

    assert len(docs) == 1, (
        f"twelve copies of one page became {len(docs)} documents. De-duplication has to "
        "happen BEFORE the cap, or three copies of one page cost three budget slots."
    )
    assert docs[0].url == "https://thornfieldloom.example.com/about"


def test_a_subclass_that_raises_becomes_an_empty_list():
    assert _run(_Exploder(), 5) == [], (
        "DESIGN Decision 8: a dead source is [], because the build has to finish when "
        "half the internet is down"
    )


def test_a_subclass_that_returns_garbage_is_survivable():
    """A bug in one connector must not be able to take down a whole build either.

    `_finalise` reads `.doc_id` off whatever it is handed, so a subclass returning the
    wrong type raised AttributeError from OUTSIDE the sealed try -- past the wrapper whose
    docstring says "Never raises".
    """
    try:
        docs = _run(_ReturnsGarbage(), 5)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"search() raised {type(exc).__name__}({exc}) for a subclass returning "
            "non-RawDocs. The never-raise contract covers the wrapper, not just the "
            "subclass body."
        )
    assert docs == []


def test_none_is_treated_as_no_documents():
    assert _run(_ReturnsNone(), 5) == []


@pytest.mark.parametrize("budget", [0, -1])
def test_a_non_positive_budget_buys_nothing_and_costs_no_request(budget):
    assert _run(_Overproducer(), budget) == []


def test_a_budget_that_is_not_a_number_does_not_take_down_the_build():
    """T-6 reads the budget out of a `Budget` model, but `search` is a public surface."""
    try:
        docs = asyncio.run(_Overproducer().search(PERSON, None))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"search() raised {type(exc).__name__}({exc}) for a non-numeric budget")
    assert docs == []


# --- the citation builder -----------------------------------------------------------


def test_doc_refuses_to_build_a_citation_it_could_not_stand_behind():
    connector = _Overproducer()

    assert connector.doc("", text="prose") is None, "a citation needs an address"
    assert connector.doc("ftp://example.com/x", text="prose") is None, (
        "a citation needs a FETCHABLE address a reader can open"
    )
    assert connector.doc("mailto:someone@example.com", text="prose") is None
    assert connector.doc("https://example.com/x", text="") is None, (
        "DESIGN §Interfaces: RawDoc.text is never empty; a citation to a blank page is "
        "worse than no citation"
    )
    assert connector.doc("https://example.com/x", text="   \n  ") is None


def test_doc_stamps_the_id_and_kind_the_contract_names():
    connector = _Overproducer()
    doc = connector.doc("https://example.com/x", title="  Title  ", text="Real prose here.")

    assert doc is not None
    assert doc.doc_id == doc_id("https://example.com/x")
    assert doc.source_kind == "self_page"
    assert doc.title == "Title"
    assert doc.fetched_at is not None


def test_doc_clips_an_overlong_body_to_the_rawdoc_budget():
    connector = _Overproducer()
    doc = connector.doc("https://example.com/x", text="word " * 20_000)

    assert doc is not None
    assert 0 < len(doc.text) <= 20_000


# --- the shared detail parsers ------------------------------------------------------


def test_urls_and_hosts_are_read_out_of_free_text_details():
    details = [
        "co-founder, Thornfield Loom",
        "site: https://thornfieldloom.example.com/, blog at https://notes.example.org/m.",
        "https://thornfieldloom.example.com/",
    ]
    assert urls_in(details) == [
        "https://thornfieldloom.example.com/",
        "https://notes.example.org/m",
    ], "trailing punctuation is not part of a url, and the list is deduped in order"
    assert hosts_in(details) == ["thornfieldloom.example.com", "notes.example.org"]


def test_affiliations_strip_role_words_so_a_search_finds_the_company():
    found = affiliations(PERSON.details)

    assert "Thornfield Loom" in found
    assert "co-founder" not in found, (
        "'co-founder, Thornfield Loom' has to search for the company, not the job title"
    )
    assert not any(entry.startswith("http") for entry in found), "a url is not an employer"


def test_parse_date_refuses_to_guess():
    assert parse_date("2024-05-02").isoformat() == "2024-05-02"
    assert parse_date("2024-05-02T09:14:00Z").isoformat() == "2024-05-02"
    assert parse_date("20180614101500").isoformat() == "2018-06-14", "Wayback CDX timestamp"
    assert parse_date(1_700_000_000) is not None, "unix seconds"

    for unparseable in ("", "   ", "sometime in 2019", "not a date", None, [], {}, 42):
        assert parse_date(unparseable) is None, (
            f"parse_date({unparseable!r}) guessed. A wrong published_at propagates into "
            "the digest's recency scoring, so anything not confidently parsed is None."
        )


def test_text_block_drops_the_empty_parts_rather_than_printing_none():
    assert text_block("first", None, "", "  ", "second") == "first\nsecond"
    assert text_block(None, "") == ""
