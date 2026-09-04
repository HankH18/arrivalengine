"""T-6: when two sources return the SAME url, which source gets the credit.

`doc_id` is `sha1(url)[:16]`, so two connectors that surface the same page hand the
pipeline two `RawDoc`s with one identity. Exactly one of them survives `_interleave`, and
the survivor's `source_kind` is a load-bearing claim, not a label:

* `resolve._sec_cik` reads `if doc.source_kind != "edgar": continue`, so a sec.gov filing
  stamped `search` can never earn the `sec_cik` strong key;
* `digest.NON_OBVIOUS_KINDS` excludes `search`, so the same document is dropped from R7's
  "Not on the first page" slot.

Both of those are decided here. The question these tests pin is *who decides*: the order
two remote APIs happened to answer in, or `arrival.connectors.DISPLAY_PRIORITY`, whose own
docstring says it is "the order a reader should meet them in".

The answer key is `DISPLAY_PRIORITY` and `digest.NON_OBVIOUS_KINDS` — both outside T-6's
write scope, so nothing here grades against a constant this ticket could edit into
agreement.
"""

from __future__ import annotations

import datetime as dt

import pytest
from t6_corpus import CITY, EMPLOYER, FETCHED_AT, OPEN_SOURCE, PERSON, PUBLISHED_AT

from arrival.connectors import DISPLAY_PRIORITY
from arrival.contracts import Budget, RawDoc, SourceKind
from arrival.digest import NON_OBVIOUS_KINDS
from arrival.research import BuildTrace, _interleave, build_dossier
from arrival.resolve import strong_keys_for
from arrival.util import doc_id as url_doc_id
from doubles import ConnectorDouble, LLMDouble

pytestmark = pytest.mark.ticket("T-6")

#: The one url both connectors return. A real instance of this: an EDGAR index page that a
#: web search also has in its index.
SHARED_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001654321"


def _doc(kind: SourceKind, url: str, *, body: str, title: str = "") -> RawDoc:
    """A `RawDoc` whose identity is its url and whose text says which source rendered it."""
    return RawDoc(
        doc_id=url_doc_id(url),
        source_kind=kind,
        url=url,
        title=title or f"{PERSON.name} — {kind}",
        text=(
            f"{PERSON.name} {EMPLOYER}. {PERSON.name} {CITY}. {OPEN_SOURCE}. {body}"
        ),
        published_at=PUBLISHED_AT,
        fetched_at=FETCHED_AT,
    )


def _edgar_copy() -> RawDoc:
    """The filing itself: the whole record, including the CIK a strong key is made of."""
    return _doc(
        "edgar",
        SHARED_URL,
        body="CIK 0001654321 lists Marisol Trevino as a reporting person of Quarrystone Labs.",
        title="SEC EDGAR filing index — Quarrystone Labs",
    )


def _search_copy() -> RawDoc:
    """A search engine's rendering of the same url: a snippet, and no CIK anywhere in it."""
    return _doc(
        "search",
        SHARED_URL,
        body="Quarrystone Labs filings and company information.",
        title="Quarrystone Labs - SEC.gov",
    )


def _priority_of(kind: str) -> int:
    return DISPLAY_PRIORITY.index(kind)


# --------------------------------------------------------------------------
# the premise: these two documents really are one identity
# --------------------------------------------------------------------------


def test_the_two_copies_share_one_doc_id_and_edgar_outranks_search():
    """Without this the rest of the module is testing two unrelated documents."""
    edgar, search = _edgar_copy(), _search_copy()
    assert edgar.doc_id == search.doc_id, "same url must mean same doc_id, or there is no clash"
    assert edgar.source_kind != search.source_kind
    assert _priority_of("edgar") < _priority_of("search"), (
        "DISPLAY_PRIORITY is the answer key for this whole module; if edgar no longer "
        "outranks search these expectations are the wrong way round"
    )


# --------------------------------------------------------------------------
# _interleave
# --------------------------------------------------------------------------


@pytest.mark.parametrize("edgar_first", [True, False], ids=["edgar-first", "search-first"])
def test_the_same_url_from_two_sources_keeps_the_priority_kind_either_way(edgar_first):
    """The survivor's kind is DISPLAY_PRIORITY's answer, not the answering order's."""
    edgar, search = _edgar_copy(), _search_copy()
    batches = [[edgar], [search]] if edgar_first else [[search], [edgar]]

    kept = _interleave(batches, 10)

    assert len(kept) == 1, "one url is one document; the cap and every join key say so"
    assert kept[0].source_kind == "edgar", (
        f"a sec.gov page returned by both connectors was stamped {kept[0].source_kind!r} "
        f"because {'edgar' if edgar_first else 'search'} answered first. "
        "DISPLAY_PRIORITY, not a remote API's ranking, decides this."
    )


def test_the_surviving_document_is_the_priority_source_s_own_rendering():
    """Not merely a restamped snippet: the kind and the text have to agree.

    Stamping `edgar` onto a search engine's snippet would satisfy the kind check and still
    leave `resolve._sec_cik` empty-handed, because the CIK is not in the snippet. The
    higher-priority connector's document is what survives, whole.
    """
    edgar, search = _edgar_copy(), _search_copy()

    for batches in ([[search], [edgar]], [[edgar], [search]]):
        kept = _interleave(batches, 10)[0]
        assert kept.text == edgar.text, "the survivor's text is not the filing's text"
        assert kept.title == edgar.title
        assert "CIK 0001654321" in kept.text, (
            "the kind says edgar but the text is the search snippet, so the strong key "
            "still cannot be earned — the stamp moved and the evidence did not"
        )


def test_a_kind_with_no_connector_never_outranks_one_that_has_one():
    """`uspto` is a SourceKind with no connector, so it is absent from DISPLAY_PRIORITY.

    An unranked kind must lose to a ranked one and must not raise. `.index()` on a missing
    member is a `ValueError`, which would turn an unusual document into a build failure.
    """
    assert "uspto" not in DISPLAY_PRIORITY, "premise: uspto has no connector, so no rank"
    ranked = _edgar_copy()
    unranked = _doc("uspto", SHARED_URL, body="Patent record.")

    for batches in ([[unranked], [ranked]], [[ranked], [unranked]]):
        kept = _interleave(batches, 10)
        assert len(kept) == 1
        assert kept[0].source_kind == "edgar"


def test_two_unranked_kinds_collide_deterministically_on_first_arrival():
    """With no priority to appeal to, the tie-break is stable rather than arbitrary."""
    first = _doc("uspto", SHARED_URL, body="Patent record.")
    second = _doc("podcast", SHARED_URL, body="Episode transcript.")

    assert _interleave([[first], [second]], 10)[0].source_kind == "uspto"
    assert _interleave([[second], [first]], 10)[0].source_kind == "podcast"


def test_deduplication_does_not_narrow_the_fan_out():
    """A duplicate must cost one slot, not one source.

    The round-robin merge is what keeps every source represented under a tight cap; a
    dedupe that collapsed a whole column would starve a source exactly the way flat
    connector-order truncation does.
    """
    edgar_batch = [_edgar_copy(), _doc("edgar", "https://www.sec.gov/other", body="Filing.")]
    search_batch = [_search_copy(), _doc("search", "https://example.test/s1", body="Page.")]
    github_batch = [_doc("github", "https://github.test/g0", body="Repository.")]

    kept = _interleave([search_batch, edgar_batch, github_batch], 3)

    assert len(kept) == 3
    assert {d.source_kind for d in kept} == {"edgar", "search", "github"}, (
        f"three slots over three sources left {sorted({d.source_kind for d in kept})} "
        "represented; deduplication must not cost a source its place"
    )


def test_the_collision_survivor_is_eligible_where_the_first_answer_was_not():
    """The two consequences, asserted against constants T-6 does not own."""
    kept = _interleave([[_search_copy()], [_edgar_copy()]], 10)[0]

    assert kept.source_kind in NON_OBVIOUS_KINDS, (
        "R7's 'Not on the first page' slot only accepts kinds a first page would not have "
        f"surfaced; {kept.source_kind!r} is not one of them"
    )
    assert kept.source_kind == "edgar", "resolve._sec_cik skips every doc that is not edgar"


def test_the_survivor_earns_the_sec_cik_strong_key_the_loser_could_not():
    """The end of the causal chain, run for real through `resolve.strong_keys_for`.

    A `sec_cik` is a DURABLE identifier: it is written into the dossier and joined on
    later. Before this fix, whether the club earned one for a member depended on whether a
    search engine happened to rank a sec.gov page above EDGAR's own listing of it.
    """
    survivor = _interleave([[_search_copy()], [_edgar_copy()]], 10)

    assert strong_keys_for(PERSON, survivor).get("sec_cik") == "0001654321", (
        "the surviving document did not earn the CIK, so the whole point of preferring "
        "the edgar copy was not achieved"
    )
    # Control: the copy first-wins would have kept earns nothing, and it is the SAME url.
    assert "sec_cik" not in strong_keys_for(PERSON, [_search_copy()]), (
        "the search copy earns the key anyway, so this test would pass either way"
    )


# --------------------------------------------------------------------------
# through the pipeline
# --------------------------------------------------------------------------


@pytest.mark.parametrize("edgar_first", [True, False], ids=["edgar-first", "search-first"])
async def test_build_dossier_sees_the_priority_kind_whichever_connector_answers_first(
    edgar_first,
):
    """`_fan_out` is where a real build meets this, and connector order is not priority."""
    edgar, search = _edgar_copy(), _search_copy()
    edgar_connector = ConnectorDouble(kind="edgar", docs=[edgar])
    search_connector = ConnectorDouble(kind="search", docs=[search])
    connectors = (
        [edgar_connector, search_connector] if edgar_first else [search_connector, edgar_connector]
    )

    trace = BuildTrace()
    llm = LLMDouble()
    await build_dossier(
        PERSON, connectors, llm, Budget(docs_per_connector=4, max_docs_total=8), trace=trace
    )

    assert [d.source_kind for d in trace.documents] == ["edgar"], (
        "the pipeline received "
        f"{[d.source_kind for d in trace.documents]} — the surviving kind moved with the "
        "connector order"
    )
    assert trace.docs_by_source == {"edgar": 1, "search": 1}, (
        "both sources really did return the document; the per-source counts describe what "
        "was fetched, not what survived deduplication"
    )
    assert trace.zero_result_sources == [], (
        "a source whose document lost a collision still looked and still found something"
    )


async def test_the_duplicate_is_not_paid_for_twice_at_the_llm_seam():
    """One url is one document, so it costs one document's worth of budget."""
    trace = BuildTrace()
    llm = LLMDouble()
    await build_dossier(
        PERSON,
        [
            ConnectorDouble(kind="search", docs=[_search_copy()]),
            ConnectorDouble(kind="edgar", docs=[_edgar_copy()]),
        ],
        llm,
        Budget(docs_per_connector=4, max_docs_total=8),
        trace=trace,
    )

    judged = {
        doc.doc_id
        for doc in trace.documents
    }
    assert len(judged) == 1
    assert len(trace.documents) == 1, (
        "the same url reached the model twice; deduplication is what stops a page indexed "
        "by several sources from costing several verdicts"
    )


def test_fetched_at_and_published_at_come_from_the_surviving_document():
    """A merge that mixed fields from both copies would invent a document neither returned."""
    stale = _doc("search", SHARED_URL, body="Snippet.")
    stale = stale.model_copy(
        update={"fetched_at": dt.datetime(2020, 1, 1, tzinfo=dt.UTC), "published_at": None}
    )
    fresh = _edgar_copy()

    kept = _interleave([[stale], [fresh]], 10)[0]

    assert kept.fetched_at == fresh.fetched_at
    assert kept.published_at == fresh.published_at
    assert kept == fresh, "the survivor must be a document a connector actually returned"
