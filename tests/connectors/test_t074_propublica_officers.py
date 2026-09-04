"""T-074: the 990 roster moved off the JSON API, and the recorded tests could not see it.

WHAT WAS MEASURED (live, 2026-09-04).  `organizations/{ein}.json` answers 200 and carries
NO officer list anywhere: `organization` is the IRS Business Master File record and
`filings_with_data` is the SOI financial extract, sixty-eight numeric columns whose only
officer-shaped field is `compnsatncurrofcr`, an aggregate compensation FIGURE that T-4
excludes outright.  The one rule this connector exists to enforce — *she is emitted only
if the 990 names her* — therefore could not fire at all, so the connector returned zero
documents for every person on every live run.  Every recorded test stayed green, because
the recording carries an `officers` array the API does not send.

That is the shape of the defect worth writing down: a corpus recorded from an endpoint's
DOCUMENTED shape grades the connector against a world, and the world moved.  The tests
below therefore serve BOTH worlds from one router — a JSON payload with a roster and a
JSON payload without one — and require the connector to find the member either way.

AND THE PART THAT IS NOT A DEFECT.  `search.json` answers **404 with a well-formed
zero-results body** when nothing matches (`{"total_results": 0, "organizations": []}`) and
200 when something does, multi-word queries included.  A 404 there means "no such
charity"; it was mistaken for "no such endpoint", which is why the last test in this
module pins that a 404 from search is a quiet empty rather than anything louder.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search

from arrival.connectors.propublica import MAX_PAGE_LOOKUPS, officers_on_page

pytestmark = pytest.mark.ticket("T-1")

EIN = 814402257
ARCHIVE = "NARRAGANSETT MILL ARCHIVE"
ORG_URL = f"https://projects.propublica.org/nonprofits/organizations/{EIN}"

SEARCH_HIT = {
    "total_results": 1,
    "organizations": [
        {
            "ein": EIN,
            "strein": "81-4402257",
            "name": ARCHIVE,
            "city": "PROVIDENCE",
            "state": "RI",
            "ntee_code": "A80",
        }
    ],
}

#: The live shape: a Business Master File record and financial extracts, no roster.
ORG_JSON_WITHOUT_OFFICERS = {
    "api_version": 2,
    "organization": {
        "ein": EIN,
        "name": ARCHIVE,
        "city": "PROVIDENCE",
        "state": "RI",
        "ntee_code": "A80",
        "asset_amount": 412000,
    },
    "filings_with_data": [
        {"tax_prd_yr": 2023, "totrevenue": 250000, "compnsatncurrofcr": 91000},
    ],
    "filings_without_data": [],
}

#: The shape the recorded corpus carries, and the one the API documented.
ORG_JSON_WITH_OFFICERS = {
    "organization": {
        "ein": EIN,
        "name": ARCHIVE,
        "officers": [
            {"name": "QUENNEBECK MARISOL A", "title": "Board chair", "compensation": 0},
            {"name": "Teodora Ilves", "title": "Treasurer", "compensation": 0},
        ],
    },
}

#: Nonprofit Explorer's organisation page, in the markup the live site serves: the roster
#: sits in the FIRST cell of each `tr.employee-row` and three salary columns sit beside it.
ORG_PAGE_HTML = f"""<!doctype html><html><head><title>{ARCHIVE} - Nonprofit Explorer</title>
</head><body>
<section class="padded-box">
  <table class="financials table--small"><tbody>
    <tr class=""><td class="padded-right">Total Assets</td>
      <td class="table__td--numeric padded-right">$412,000 </td><td></td></tr>
  </tbody></table>
</section>
<section class="padded-box">
  <div class="table-header"><h5 class="table-header__hed">Compensation</h5></div>
  <table class="employees table--small">
    <thead><tr><th>Key Employees and Officers</th><th class="right">Compensation</th>
      <th class="right">Related</th><th class="right">Other</th></tr></thead>
    <tbody>
      <tr class="employee-row shortlist">
        <td class="padded-right">
            QUENNEBECK MARISOL A
          <span>(Board Chair)</span>
        </td>
        <td class="table__td--numeric padded-right">$118,402</td>
        <td class="table__td--numeric padded-right">$0</td>
        <td class="table__td--numeric">$9,140</td>
      </tr>
      <tr class="employee-row shortlist">
        <td class="padded-right">
            Teodora Ilves
          <span>(Treasurer)</span>
        </td>
        <td class="table__td--numeric padded-right">$0</td>
        <td class="table__td--numeric padded-right">$0</td>
        <td class="table__td--numeric">$0</td>
      </tr>
      <tr class="employee-row">
        <td class="padded-right">
            Abelard Nkemdirim
        </td>
        <td class="table__td--numeric padded-right">$0</td>
        <td class="table__td--numeric padded-right">$0</td>
        <td class="table__td--numeric">$0</td>
      </tr>
    </tbody>
  </table>
</section>
</body></html>"""


def _router(org_json, *, page=ORG_PAGE_HTML):
    def router(request):
        path, _ = parts(request)
        if "/api/v2/search" in path:
            return SEARCH_HIT
        if "/api/v2/organizations/" in path:
            return org_json
        if "/nonprofits/organizations/" in path:
            return page
        return None

    return router


# --- the parser, on the markup the live site serves --------------------------------


def test_the_officer_table_yields_names_and_titles_and_never_a_salary():
    found = officers_on_page(ORG_PAGE_HTML)

    assert found == [
        "QUENNEBECK MARISOL A (Board Chair)",
        "Teodora Ilves (Treasurer)",
        "Abelard Nkemdirim",
    ], f"got {found!r}"
    assert not any("$" in line for line in found), (
        "the page puts three salary columns beside every name. T-4 excludes `wealth` "
        "outright and R11 never displays it, so a figure that reaches RawDoc.text is one "
        "T-3 can quote and T-7 can print"
    )


def test_a_page_with_no_officer_table_names_nobody():
    assert officers_on_page("<html><body><p>No filings on record.</p></body></html>") == []
    assert officers_on_page("") == []
    assert officers_on_page("<table><tr><td>unclosed") == []


# --- the connector, in both worlds -------------------------------------------------


def test_the_member_is_found_when_the_roster_is_on_the_page_and_not_in_the_json(
    monkeypatch, tmp_path
):
    docs, requested = search(
        "propublica", _router(ORG_JSON_WITHOUT_OFFICERS), monkeypatch, tmp_path
    )

    assert any("/api/v2/organizations/" in url for url in requested), (
        f"the JSON detail call is still the first read; asked {requested!r}"
    )
    assert any(url.rstrip("/") == ORG_URL for url in requested), (
        "the JSON payload carried no roster -- which is what the live API sends -- and "
        f"the connector never read the organisation page that does. Asked {requested!r}"
    )
    assert len(docs) == 1, f"the board seat the page states was not emitted: {docs!r}"
    assert docs[0].url == ORG_URL
    assert "Marisol Quennebeck is listed as" in docs[0].text
    assert "Board Chair" in docs[0].text
    assert "Teodora Ilves" in docs[0].text, "the rest of the board is the hub"
    assert "$" not in docs[0].text, "a compensation figure reached the citation"


def test_a_json_payload_that_still_carries_a_roster_costs_no_page_read(monkeypatch, tmp_path):
    docs, requested = search("propublica", _router(ORG_JSON_WITH_OFFICERS), monkeypatch, tmp_path)

    assert len(docs) == 1, f"the recorded JSON shape must keep working: {docs!r}"
    assert not any(url.rstrip("/") == ORG_URL for url in requested), (
        "the JSON already named the board; reading a third of a megabyte of HTML to learn "
        f"the same thing is a request nobody needed. Asked {requested!r}"
    )


def test_an_organisation_that_does_not_name_her_is_still_refused(monkeypatch, tmp_path):
    stranger = ORG_PAGE_HTML.replace("QUENNEBECK MARISOL A", "Halvard Brenninkmeyer")

    docs, requested = search(
        "propublica",
        _router(ORG_JSON_WITHOUT_OFFICERS, page=stranger),
        monkeypatch,
        tmp_path,
    )

    assert any(url.rstrip("/") == ORG_URL for url in requested), (
        "the connector has to READ the roster before it can decline on it"
    )
    assert docs == [], (
        "the page names three people and none of them is the member. An organisation that "
        "does not name her is one that exists near her, and a document saying otherwise is "
        "false attribution with a real url and a real quote attached"
    )


def test_the_page_fallback_is_bounded_however_many_candidates_came_back(monkeypatch, tmp_path):
    many = {
        "total_results": 12,
        "organizations": [
            {
                "ein": EIN + index,
                "strein": f"81-440225{index}",
                "name": f"{ARCHIVE} {index}",
                "city": "PROVIDENCE",
                "state": "RI",
                "ntee_code": "A80",
            }
            for index in range(12)
        ],
    }

    def router(request):
        path, _ = parts(request)
        if "/api/v2/search" in path:
            return many
        if "/api/v2/organizations/" in path:
            return ORG_JSON_WITHOUT_OFFICERS
        if "/nonprofits/organizations/" in path:
            return "<html><body><p>No filings on record.</p></body></html>"
        return None

    _docs, requested = search("propublica", router, monkeypatch, tmp_path)

    pages = [url for url in requested if "/api/" not in url]
    assert len(pages) <= MAX_PAGE_LOOKUPS, (
        f"the organisation page is a third of a megabyte of HTML and the connector read "
        f"{len(pages)} of them for one person: {pages!r}"
    )


def test_a_search_that_matches_nothing_answers_404_and_that_is_an_empty_not_an_error(
    monkeypatch, tmp_path
):
    # Live behaviour, measured: `search.json?q=Melanie%20Perkins` -> 404 with
    # `{"total_results": 0, "organizations": []}`. It reads like a dead endpoint and is
    # not one; the connector must degrade to `[]` and must not go looking for an org.
    def router(request):
        path, _ = parts(request)
        if "/api/v2/search" in path:
            return None  # the harness serves `None` as a 404
        pytest.fail(
            f"a search that matched nothing was followed by {path!r}; there is no "
            "organisation to look up"
        )

    docs, requested = search("propublica", router, monkeypatch, tmp_path)

    assert requested, "the connector has to ask before it can decline"
    assert docs == []


def test_the_connector_still_never_searches_for_an_organisation_named_after_her_city(
    monkeypatch, tmp_path
):
    _docs, asked = search("propublica", _router(ORG_JSON_WITHOUT_OFFICERS), monkeypatch, tmp_path)

    searches = [url for url in asked if "/api/v2/search" in url]
    assert searches, f"no search at all; asked {asked!r}"
    assert not any("q=Rhode" in url or "q=Providence" in url for url in searches), (
        f"a place is where she lives, never an organisation name; asked {searches!r}"
    )
    assert any("q=Marisol" in url for url in searches), f"asked {searches!r}"
    assert any("q=Thornfield" in url for url in searches), (
        "the employer is the one affiliation worth a charity search, and after T-073 it is "
        f"what `affiliations` yields first rather than the job title. Asked {searches!r}"
    )
