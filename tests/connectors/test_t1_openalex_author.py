"""T-019: the first search result carrying a `display_name` is not "the author".

OpenAlex `/authors?search=` is a fuzzy search over a corpus with hundreds of thousands of
duplicate and near-duplicate names.  The connector took the first result with a
`display_name` and then pulled every work filtered by THAT author id, so a stranger's
papers arrive attributed to the member — each with a DOI.

That is the worst shape this whole class of defect can take.  A DOI resolves, the abstract
really does contain the sentence the extractor quoted, and T-3's citation check therefore
passes: the digest says "she wrote the 2020 ice-sheet paper", displays it as sourced, and
a host reads it out loud.

The recorded corpus and the frozen `_OA_AUTHORS` both return exactly ONE author, so the
selection branch is never exercised by either suite.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search

pytestmark = pytest.mark.ticket("T-1")

MEMBER_ID = "A5099184423"
DECOY_ID = "A5000000017"
OTHER_ID = "A5000000042"
STRANGER_ID = "A5000000099"


def _author(identifier: str, name: str, institution: str, concepts: list[str]) -> dict:
    return {
        "id": f"https://openalex.org/{identifier}",
        "orcid": None,
        "display_name": name,
        "display_name_alternatives": [name],
        "works_count": 9,
        "cited_by_count": 63,
        "last_known_institutions": [
            {"id": "https://openalex.org/I4210000001", "display_name": institution}
        ],
        "x_concepts": [{"display_name": concept, "score": 60.0} for concept in concepts],
    }


def _work(identifier: str, author_id: str, name: str, doi: str, title: str, line: str) -> dict:
    return {
        "id": f"https://openalex.org/{identifier}",
        "doi": doi,
        "title": title,
        "display_name": title,
        "publication_year": 2024,
        "publication_date": "2024-04-02",
        "cited_by_count": 11,
        "abstract": line,
        "primary_location": {
            "landing_page_url": f"https://papers.example.org/{identifier}",
            "source": {"display_name": "Working Papers"},
        },
        "authorships": [
            {
                "author": {"id": f"https://openalex.org/{author_id}", "display_name": name},
                "institutions": [],
            }
        ],
    }


MEMBER_AUTHOR = _author(
    MEMBER_ID,
    "Marisol Quennebeck",
    "Thornfield Loom Research Group",
    ["Textile manufacturing", "Production scheduling"],
)
# Same name, real researcher, nothing to do with a textile company in Providence.
DECOY_AUTHOR = _author(
    DECOY_ID, "Marisol Quennebeck", "University of Otago", ["Glaciology", "Ice dynamics"]
)
OTHER_AUTHOR = _author(
    OTHER_ID, "Marisol Quennebeck", "Institut Polaire Bertaud", ["Palaeoclimatology"]
)
# Not her at all: a fuzzy search returns near-misses, and `display_name` is always set.
STRANGER_AUTHOR = _author(
    STRANGER_ID, "Teodora Ilves", "Narragansett Institute of Technology", ["Scheduling"]
)

MEMBER_DOI = "https://doi.org/10.5555/thornfield.2024.1"
DECOY_DOI = "https://doi.org/10.5555/otago.2024.9"
STRANGER_DOI = "https://doi.org/10.5555/narragansett.2024.4"

WORKS = {
    MEMBER_ID: [
        _work(
            "W1",
            MEMBER_ID,
            "Marisol Quennebeck",
            MEMBER_DOI,
            "Self-scheduling on small textile floors",
            "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep "
            "small textile mills scheduling their own looms.",
        )
    ],
    DECOY_ID: [
        _work(
            "W2",
            DECOY_ID,
            "Marisol Quennebeck",
            DECOY_DOI,
            "Basal melt rates beneath the Ross Ice Shelf",
            "Basal melt rates beneath the Ross Ice Shelf are revised downward by a "
            "decade of phase-sensitive radar returns.",
        )
    ],
    STRANGER_ID: [
        _work(
            "W3",
            STRANGER_ID,
            "Teodora Ilves",
            STRANGER_DOI,
            "Queueing on the mill floor",
            "Queueing on the mill floor is a scheduling problem with a human in every "
            "loop, which is what makes it hard.",
        )
    ],
}


def router(authors: list[dict]):
    """`/authors?search=` -> `authors`; `/works?filter=author.id:X` -> X's papers only."""

    def route(request):
        path, query = parts(request)
        if path.endswith("/authors"):
            return {"meta": {"count": len(authors)}, "results": authors}
        if path.endswith("/works"):
            wanted = ""
            for clause in query.get("filter", "").split(","):
                if clause.startswith("author.id:"):
                    wanted = clause.split(":", 1)[1]
            results = WORKS.get(wanted, [])
            return {"meta": {"count": len(results)}, "results": results}
        return None

    return route


def test_openalex_returns_nothing_when_the_only_hit_is_not_the_member(monkeypatch, tmp_path):
    """A fuzzy search always returns SOMETHING. "Something" is not "her"."""
    docs, requested = search("openalex", router([STRANGER_AUTHOR]), monkeypatch, tmp_path)

    assert requested, "the connector has to ask before it can decline"
    assert docs == [], (
        f"openalex returned {[doc.url for doc in docs]} built on an author profile whose "
        "display_name is 'Teodora Ilves'. The connector accepted the first result "
        "carrying any display_name at all, so a near-miss from a fuzzy search became the "
        "member's publication record -- with a resolving DOI on every paper."
    )
    assert not any(doc.url == STRANGER_DOI for doc in docs)


def test_openalex_picks_the_same_name_author_the_members_details_corroborate(
    monkeypatch, tmp_path
):
    """Two Marisol Quennebecks. One works on looms in Providence; one studies ice."""
    docs, _ = search(
        "openalex", router([DECOY_AUTHOR, MEMBER_AUTHOR]), monkeypatch, tmp_path
    )

    urls = [doc.url for doc in docs]
    assert f"https://openalex.org/{MEMBER_ID}" in urls, (
        "the member's own OpenAlex profile is the one corroborated by details: her "
        f"institution names Thornfield Loom. Got {urls!r}"
    )
    assert f"https://openalex.org/{DECOY_ID}" not in urls, (
        f"the glaciologist's profile was returned as the member's. Got {urls!r}"
    )
    assert DECOY_DOI not in urls, (
        "a paper on basal melt rates beneath the Ross Ice Shelf was attributed to a "
        "textile-software founder, with a citable DOI. This is the shape the citation "
        f"guard cannot catch: the quote really is in the document. Got {urls!r}"
    )
    assert MEMBER_DOI in urls, "her actual paper still has to come back"


def test_openalex_refuses_to_choose_between_two_indistinguishable_same_name_authors(
    monkeypatch, tmp_path
):
    """Nothing separates them, so any pick is a coin flip presented as a fact."""
    docs, _ = search(
        "openalex", router([DECOY_AUTHOR, OTHER_AUTHOR]), monkeypatch, tmp_path
    )

    assert docs == [], (
        f"openalex returned {[doc.url for doc in docs]} by picking one of two same-name "
        "authors that no detail in the roster distinguishes -- a glaciologist in Otago "
        "and a palaeoclimatologist in Bertaud. Fewer documents is the safe direction; a "
        "coin flip wearing a DOI is not."
    )


def test_openalex_still_accepts_the_only_author_who_carries_the_members_name(
    monkeypatch, tmp_path
):
    """The filter must reject strangers, not the member. `[]` for everyone is not a fix."""
    docs, _ = search("openalex", router([MEMBER_AUTHOR]), monkeypatch, tmp_path)

    urls = [doc.url for doc in docs]
    assert f"https://openalex.org/{MEMBER_ID}" in urls, urls
    assert MEMBER_DOI in urls, urls
