"""T-023: EDGAR asks for the form classes the acceptance criterion names, and renders them.

TWO DEFECTS, ONE MODULE.

**The form list.**  TASKS T-1 acceptance 2 says "Form D/4/13F hits as text"; `FORMS` read
`"3,4,5,D"`.  The module docstring argued 13F away as holdings data excluded by R11, which
is a defensible-sounding reading and is wrong in a specific way: it confuses the FILING with
the FIELDS THIS CONNECTOR READS.  Nothing here fetches a 13F's information table — the
connector reads `adsh`, `display_names`, `form`, `file_date` and `file_description` out of a
search hit, and none of those is a dollar amount.  What a 13F contributes is the same thing
Form 3/4/5/D contribute: a role.  R11 governs DISPLAY and T-4's exclusion filter is where a
wealth-shaped fact is stopped, with the whole corpus in front of it; a connector declining
a form class on R11's behalf applies the policy in the wrong place and loses the evidence
before anything can weigh it.  The deviation is now resolved in the direction of the
criterion rather than argued in a docstring.

**The rendered label.**  `edgar.py` derived the form as
`str(source.get("form") or source.get("root_forms") or "")`.  EDGAR returns `form` as a
string and `root_forms` as a LIST, so every hit whose `form` was absent produced the title
`SEC Form ['4']` and the body line `, form ['4']` — with `_first_str`, the helper that
handles exactly this, defined nine lines above and applied only to `ciks`.  Neither the
recorded corpus nor the frozen one contains such a hit, which is why nothing showed it.

THE ROUTER HONOURS `forms`, as the real endpoint does.  That is what makes the first defect
visible at all: a router that returned every hit regardless would grade a connector that
never asked for 13F exactly as it grades one that did.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import parts, search

pytestmark = pytest.mark.ticket("T-1")

CIK = "0001742119"
HER_LINE = (
    "Marisol Quennebeck co-founded Thornfield Loom in Providence in 2017 to keep small "
    "textile mills scheduling their own looms."
)


def _hit(index: int, form: str, roots: list[str], description: str, *, omit_form: bool = False):
    source = {
        "adsh": f"{CIK}-24-00002{index}",
        "ciks": [CIK],
        "display_names": [
            f"Quennebeck Marisol (CIK {CIK})",
            f"Thornfield Loom Inc. (CIK {CIK})",
        ],
        "root_forms": roots,
        "file_date": "2024-04-18",
        "file_description": description,
    }
    if not omit_form:
        source["form"] = form
    return {"_id": f"{CIK}-24-00002{index}:primary_doc.xml", "_source": source, "_form": form}


HITS = [
    _hit(1, "4", ["4"], f"FORM 4 - statement of changes in beneficial ownership. {HER_LINE}"),
    _hit(2, "D", ["D"], f"FORM D - notice of exempt offering of securities. {HER_LINE}"),
    _hit(
        3,
        "13F-HR",
        ["13F-HR"],
        "FORM 13F-HR - quarterly report of an institutional investment manager. "
        f"{HER_LINE}",
    ),
    # A hit EDGAR returned without a top-level `form`, which its API does for some
    # filings and neither recorded corpus contains.
    _hit(4, "4", ["4"], f"FORM 4 - a second statement of changes. {HER_LINE}", omit_form=True),
]


def router(request):
    """Full-text search that FILTERS on `forms`, exactly as the documented endpoint does."""
    path, query = parts(request)
    if "sec.gov" in str(request.url) and "search" in path:
        wanted = {value.strip() for value in query.get("forms", "").split(",") if value.strip()}
        rows = [
            {"_id": hit["_id"], "_source": hit["_source"]}
            for hit in HITS
            if not wanted or hit["_form"] in wanted
        ]
        return {"hits": {"total": {"value": len(rows), "relation": "eq"}, "hits": rows}}
    if "/Archives/" in path:
        return f"<html><body><p>{HER_LINE}</p></body></html>"
    return None


def test_edgar_asks_for_the_form_classes_the_acceptance_criterion_names(monkeypatch, tmp_path):
    """The reproduction. `forms=3,4,5,D` names neither of the two real 13F root forms."""
    _, requested = search("edgar", router, monkeypatch, tmp_path)

    searches = [url for url in requested if "search" in url]
    assert searches, f"no full-text search was made at all: {requested!r}"
    asked = searches[0]
    assert "13F" in asked, (
        "TASKS T-1 acceptance 2 names 'Form D/4/13F hits as text' and the connector asked "
        f"for {asked!r}. A form class that is never requested is a criterion the code does "
        "not meet, whatever a docstring says about it."
    )
    for required in ("D", "4"):
        assert required in asked, f"{required} is named by the criterion too; asked {asked!r}"


def test_edgar_returns_the_13f_filing_that_names_the_member(monkeypatch, tmp_path):
    """Asking is half of it; the hit has to survive the identity check and be emitted."""
    docs, _ = search("edgar", router, monkeypatch, tmp_path)

    corpus = "\n".join(f"{doc.title}\n{doc.text}" for doc in docs)
    assert "13F" in corpus, (
        f"the 13F hit was requested and then dropped; got {[d.title for d in docs]!r}"
    )
    filing = next(doc for doc in docs if "13F" in doc.text)
    assert filing.source_kind == "edgar"
    assert filing.url.startswith("https://www.sec.gov/Archives/edgar/data/1742119/")
    assert "Quennebeck Marisol" in filing.text, "display_names is who the filing names"
    assert filing.published_at is not None and filing.published_at.year == 2024


def test_edgar_does_not_render_a_list_where_a_form_type_belongs(monkeypatch, tmp_path):
    """`str(['4'])` is `"['4']"`. `_first_str` was defined for this and not used for it."""
    docs, _ = search("edgar", router, monkeypatch, tmp_path)

    fallback = [doc for doc in docs if doc.url.endswith("-24-000024-index.htm")]
    assert fallback, (
        "the hit whose `form` key EDGAR omitted produced no document at all; got "
        f"{[d.url for d in docs]!r}"
    )
    doc = fallback[0]
    assert "['" not in doc.title and "['" not in doc.text, (
        f"the form type rendered as a Python list repr: title={doc.title!r}, "
        f"text={doc.text[:120]!r}. EDGAR returns `form` as a string and `root_forms` as a "
        "list; `_first_str` handles both and was applied only to `ciks`."
    )
    assert "form 4" in doc.text, (
        f"the form type is the one fact that says what KIND of filing this is: {doc.text!r}"
    )
    assert doc.title.startswith("SEC Form 4"), f"got {doc.title!r}"


def test_edgar_still_declines_a_filing_that_names_nobody_the_roster_knows(
    monkeypatch, tmp_path
):
    """Widening the form list must not widen who a filing may be about.

    A 13F is filed BY an institutional manager, and its `display_names` is very often the
    firm alone. That is a company-only filing, and emitting it as a document about a person
    is the defect `_names_the_person` exists to stop — unchanged by which forms are asked
    for.
    """

    def company_only(request):
        path, _ = parts(request)
        if "sec.gov" in str(request.url) and "search" in path:
            return {
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_id": f"{CIK}-24-000099:primary_doc.xml",
                            "_source": {
                                "adsh": f"{CIK}-24-000099",
                                "ciks": [CIK],
                                "display_names": ["Halvard Freight Systems Inc. (CIK 9900011)"],
                                "form": "13F-HR",
                                "root_forms": ["13F-HR"],
                                "file_date": "2024-04-18",
                                "file_description": (
                                    "FORM 13F-HR - quarterly report of an institutional "
                                    "investment manager."
                                ),
                            },
                        }
                    ],
                }
            }
        return None

    docs, requested = search("edgar", company_only, monkeypatch, tmp_path)

    assert requested, "the connector has to look before it declines"
    assert docs == [], (
        "a 13F naming only a company the roster has never heard of was emitted as a "
        f"document about Marisol Quennebeck: {[d.url for d in docs]!r}"
    )
