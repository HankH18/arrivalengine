"""T-018: a charity NEAR the member is not a charity the member is involved with.

Two defects, one consequence.  `affiliations(details)` returns
`['Thornfield Loom', 'Providence', 'Rhode Island']` — the member's CITY and STATE arrive
looking exactly like organisation names, and ProPublica indexes organisation names, so a
query on `Providence` returns Providence-area charities.  Nothing then requires the member
to appear on the roster of the organisation before it becomes a `RawDoc` stamped
`source_kind="propublica"` and handed downstream as a document about her.

The extractor pulls cited facts out of these and the citation guard passes, because the
quote really is in the document and the url really does resolve: "Marisol Quennebeck,
Providence River Conservancy" arrives fully sourced and completely false.

The recorded corpus already contains the wrong-entity response — the lane that wrote it
recorded PROVIDENCE RIVER CONSERVANCY on purpose and noted in
`test_propublica_says_plainly_whether_the_member_is_on_the_board` that the connector emits
it anyway.  That test pins the weaker boundary (the document must not CLAIM she is
listed).  These pin the real one: it must not be emitted at all.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search, search_recorded

pytestmark = pytest.mark.ticket("T-1")

ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/{ein}"

ARCHIVE_EIN = "814402257"
CONSERVANCY_EIN = "50912344"


def _org_row(ein: str, name: str, city: str = "PROVIDENCE", state: str = "RI") -> dict:
    return {
        "ein": int(ein),
        "strein": f"{ein[:2]}-{ein[2:]}",
        "name": name,
        "sub_name": "",
        "city": city,
        "state": state,
        "ntee_code": "A80",
        "subseccd": 3,
        "have_filings": True,
        "score": 12.0,
    }


def _org_detail(ein: str, name: str, officers: list[tuple[str, str]]) -> dict:
    return {
        "organization": {
            "id": int(ein),
            "ein": int(ein),
            "name": name,
            "city": "PROVIDENCE",
            "state": "RI",
            "officers": [
                {"name": person, "title": title, "compensation": 0}
                for person, title in officers
            ],
        },
        "filings_with_data": [],
    }


def router(hits: dict[str, list[dict]], details: dict[str, dict]):
    """`search.json?q=` -> whatever that exact query was recorded to return."""

    def route(request):
        path, query = parts(request)
        if path.endswith("/search.json"):
            rows = hits.get(query.get("q", ""), [])
            return {"total_results": len(rows), "organizations": rows}
        if "/organizations/" in path:
            ein = path.rsplit("/", 1)[-1].removesuffix(".json")
            return details.get(ein)
        return None

    return route


def test_propublica_never_emits_an_organisation_the_member_has_no_role_in(
    monkeypatch, tmp_path
):
    """The recorded corpus already contains the wrong entity. Nothing asserted on it."""
    docs, _ = search_recorded("propublica", monkeypatch, tmp_path)

    urls = [doc.url for doc in docs]
    assert ORG_PAGE.format(ein=ARCHIVE_EIN) in urls, (
        "the org the member actually chairs must survive the filter; the fix is to emit "
        f"fewer documents, not none. Got {urls!r}"
    )
    assert ORG_PAGE.format(ein=CONSERVANCY_EIN) not in urls, (
        "PROVIDENCE RIVER CONSERVANCY came back only because the member's CITY was used "
        "as an organisation-name query, and its 990 roster names Hollis Barrowman and "
        "Ines Sarrazin -- not her. Emitted as a propublica document it is false "
        f"attribution with a real url and a real quote. Got {urls!r}"
    )


def test_propublica_does_not_search_for_organisations_named_after_the_members_city(
    monkeypatch, tmp_path
):
    """`Providence` is where she lives, not a nonprofit she is part of."""
    _, requested = search_recorded("propublica", monkeypatch, tmp_path)

    assert any("q=Marisol" in url for url in requested), (
        f"the member's own name has to be one of the queries; asked {requested!r}"
    )
    assert not any("q=Providence" in url or "q=Rhode" in url for url in requested), (
        "the connector queried Nonprofit Explorer for organisations called 'Providence'. "
        "`affiliations()` cannot tell a city from an employer -- it is documented as "
        "deliberately generous -- so the connector that uses those strings as "
        f"ORGANISATION names is the one that has to. Asked: {requested!r}"
    )


def test_propublica_returns_nothing_when_no_roster_names_the_member(monkeypatch, tmp_path):
    """Two real charities, two real 990s, and she is on neither board."""
    docs, requested = search(
        "propublica",
        router(
            {
                "Marisol Quennebeck": [_org_row("112233445", "QUENNEBECK FAMILY TRUST FUND")],
                "Thornfield Loom": [_org_row("998877665", "LOOM WORKS COMMUNITY SHOP")],
            },
            {
                "112233445": _org_detail(
                    "112233445",
                    "QUENNEBECK FAMILY TRUST FUND",
                    [("Aurelio Quennebeck", "President"), ("Ines Sarrazin", "Treasurer")],
                ),
                "998877665": _org_detail(
                    "998877665",
                    "LOOM WORKS COMMUNITY SHOP",
                    [("Hollis Barrowman", "Executive director")],
                ),
            },
        ),
        monkeypatch,
        tmp_path,
    )

    assert requested, "the connector has to look before it declines"
    assert docs == [], (
        f"propublica returned {[doc.url for doc in docs]}. A shared SURNAME and a shared "
        "word in the org name are not a board seat. Form 990 Part VII is the only thing "
        "in this source that connects a person to an organisation; with no match in it "
        "the honest answer is no document."
    )


def test_propublica_recognises_the_member_when_the_990_lists_her_surname_first(
    monkeypatch, tmp_path
):
    """Real 990 rosters are inconsistent; `name.lower() in line.lower()` is not a match."""
    docs, _ = search(
        "propublica",
        router(
            {"Marisol Quennebeck": [_org_row(ARCHIVE_EIN, "NARRAGANSETT MILL ARCHIVE")]},
            {
                ARCHIVE_EIN: _org_detail(
                    ARCHIVE_EIN,
                    "NARRAGANSETT MILL ARCHIVE",
                    [
                        ("QUENNEBECK MARISOL A", "Board chair"),
                        ("ILVES TEODORA", "Treasurer"),
                    ],
                )
            },
        ),
        monkeypatch,
        tmp_path,
    )

    assert len(docs) == 1, f"the member IS on this board; got {[d.url for d in docs]!r}"
    text = docs[0].text
    assert "Marisol Quennebeck is listed as" in text, (
        f"the roster reads 'QUENNEBECK MARISOL A' and the document says {text!r}. A "
        "substring test on the full name in roster order misses the form half the IRS "
        "filings actually use, so the one fact this connector exists to state -- 'she "
        "chairs this' -- silently degrades to 'this exists near her'."
    )
    assert "Board chair" in text
    assert "Teodora" in text or "ILVES" in text, "the rest of the board is the hub"
