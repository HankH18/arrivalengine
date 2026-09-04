"""T-020: a page about the member's COMPANY is not a page about the member.

Three defects in one connector, all of them invisible to the two existing suites:

* No relevance filter.  Every title the search API returned was summarised and emitted,
  so the recorded corpus already produces two documents and one of them is
  `/wiki/Thornfield_Loom`.  The frozen suite hides it because every `page/summary` path
  there returns the same `content_urls`, so three off-topic documents collapse into one by
  `doc_id` and the count looks right.
* `srlimit=budget` with no headroom.  Search rank is not relevance rank: with `budget=1`
  the connector asks for one title, and if that title is the company it spends the budget
  on the wrong page and returns nothing usable.
* `quote()` leaves `/` safe.  Wikipedia titles can contain a slash, and the un-encoded
  form addresses a different REST path that 404s.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search, search_recorded

pytestmark = pytest.mark.ticket("T-1")

MEMBER_PAGE = "https://en.wikipedia.org/wiki/Marisol_Quennebeck"
COMPANY_PAGE = "https://en.wikipedia.org/wiki/Thornfield_Loom"

LEAD = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)
COMPANY_LEAD = (
    "Thornfield Loom publishes a monthly maintenance almanac that mill-floor supervisors "
    "read before every shift."
)
FREIGHT_LEAD = (
    "Freight scheduling is the assignment of loads to vehicles over time, and it is the "
    "problem most mill software solves badly."
)


def _summary(title: str, description: str, extract: str) -> dict:
    return {
        "type": "standard",
        "title": title,
        "displaytitle": title,
        "titles": {"canonical": title.replace(" ", "_"), "normalized": title},
        "description": description,
        "extract": extract,
        "timestamp": "2024-03-11T09:22:41Z",
        "content_urls": {
            "desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}
        },
    }


SUMMARIES = {
    "Marisol_Quennebeck": _summary(
        "Marisol Quennebeck", "American textile-software co-founder", LEAD
    ),
    "Thornfield_Loom": _summary("Thornfield Loom", "textile scheduling company", COMPANY_LEAD),
    "Freight_scheduling": _summary("Freight scheduling", "logistics concept", FREIGHT_LEAD),
    "Marisol_Quennebeck%2FArchive": _summary(
        "Marisol Quennebeck/Archive", "archived biography", LEAD
    ),
}


def router(titles: list[str]):
    """The search API HONOURS `srlimit`, exactly as the real one does.

    That is what makes the headroom defect visible: a connector that asks for `budget`
    titles gets `budget` titles, and if the relevant one is not among them it has nothing
    left to fall back on.
    """

    def route(request):
        path, query = parts(request)
        if path.endswith("/w/api.php"):
            limit = int(query.get("srlimit", "10"))
            return {
                "batchcomplete": True,
                "query": {
                    "search": [
                        {"ns": 0, "title": title, "pageid": 74110900 + index}
                        for index, title in enumerate(titles[:limit])
                    ]
                },
            }
        marker = "/api/rest_v1/page/summary/"
        if marker in path:
            return SUMMARIES.get(path.split(marker, 1)[1])
        return None

    return route


def test_wikipedia_does_not_emit_the_companys_article_as_a_document_about_the_member(
    monkeypatch, tmp_path
):
    """Measured on the recorded corpus today: 2 docs, one of them Thornfield_Loom."""
    docs, _ = search_recorded("wikipedia", monkeypatch, tmp_path)

    urls = [doc.url for doc in docs]
    assert MEMBER_PAGE in urls, f"her own article must still come back; got {urls!r}"
    assert COMPANY_PAGE not in urls, (
        "the article about Thornfield Loom was emitted with source_kind='wikipedia' as a "
        "document about Marisol Quennebeck. R11 displays a wikipedia document as an "
        "encyclopedic biography of the person, and T-3 extracts facts about the SUBJECT "
        f"from whatever text arrives, so the company's lead section becomes hers. {urls!r}"
    )


def test_wikipedia_finds_the_member_when_search_ranks_her_page_below_the_budget(
    monkeypatch, tmp_path
):
    """Search rank is not relevance rank, so `srlimit=budget` is a coin flip."""
    docs, _ = search(
        "wikipedia",
        router(["Thornfield Loom", "Freight scheduling", "Marisol Quennebeck"]),
        monkeypatch,
        tmp_path,
        budget=1,
    )

    assert [doc.url for doc in docs] == [MEMBER_PAGE], (
        f"with budget=1 the connector returned {[doc.url for doc in docs]!r}. It asked "
        "the search API for exactly one title, got the company, and had no headroom to "
        "reach the member's own article three results down. A budget caps the DOCUMENTS "
        "returned, not the candidates considered."
    )


def test_wikipedia_percent_encodes_a_slash_in_a_page_title(monkeypatch, tmp_path):
    """`quote()` keeps `/` safe by default, which addresses a different REST resource."""
    docs, requested = search(
        "wikipedia", router(["Marisol Quennebeck/Archive"]), monkeypatch, tmp_path
    )

    summaries = [url for url in requested if "/page/summary/" in url]
    assert summaries, f"no summary was fetched at all; asked {requested!r}"
    assert all("%2F" in url for url in summaries), (
        f"the connector asked for {summaries!r}. `quote(title)` leaves '/' safe, so a "
        "title containing one builds .../page/summary/Marisol_Quennebeck/Archive, which "
        "is a different path and 404s. The title is one path SEGMENT: quote it with "
        "safe=''."
    )
    assert [doc.url for doc in docs] == [
        "https://en.wikipedia.org/wiki/Marisol_Quennebeck/Archive"
    ], f"got {[doc.url for doc in docs]!r}"


def test_wikipedia_keeps_the_page_that_is_actually_about_her(monkeypatch, tmp_path):
    """The filter has to reject the company, not the person. `[]` for everyone is no fix."""
    docs, _ = search(
        "wikipedia",
        router(["Marisol Quennebeck", "Thornfield Loom", "Freight scheduling"]),
        monkeypatch,
        tmp_path,
    )

    assert [doc.url for doc in docs] == [MEMBER_PAGE], (
        f"got {[doc.url for doc in docs]!r}"
    )
    assert LEAD in docs[0].text, "the lead section is the whole reason to call summary"
    assert docs[0].published_at is not None, "the summary carries a timestamp; use it"
