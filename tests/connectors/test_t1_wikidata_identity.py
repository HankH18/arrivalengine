"""T-017: the Wikidata connector must not turn a NAME match into an IDENTITY match.

WHY THIS ONE IS WORSE THAN ITS DOCUMENT COUNT.  A QID is a `Resolution.strong_key` and
the canonical `Hub.hub_id` prefix (`wd:Q123`).  Every other source's facts are joined onto
whatever identity this connector picked, for EVERY person on the roster, so a wrong QID
here does not merely add one wrong document — it silently merges two people's hubs and the
graph goes on producing confident matches off the merge.

The recorded corpus in `tests/fixtures/http/wikidata_*.json` offers exactly ONE candidate,
and so does the frozen suite's `_WIKIDATA_API`.  Neither can see this defect: there is no
choice to get wrong.  The corpora below present the choice.

Also graded here: TASKS T-1 acceptance 2 asks for LABELLED affiliations as text.  Wikidata
returns item-valued claims as bare ids, so the connector's own measured output read
`instance of: Q5 | occupation: Q131524` — a "fact" no reader, and no extractor quoting it,
can do anything with.
"""

from __future__ import annotations

import re

import pytest
from t1_ambiguity import MEMBER, parts, search

pytestmark = pytest.mark.ticket("T-1")

QID_MEMBER = "Q104882317"
QID_SKATER = "Q900014"
QID_COMPANY = "Q900022"

PAGE = "https://www.wikidata.org/wiki/{qid}"

#: Labels for every item the claims below reference, served by the second `wbgetentities`
#: call a connector has to make if it wants to render `occupation: entrepreneur`.
LABELS = {
    "Q5": "human",
    "Q4830453": "business",
    "Q131524": "entrepreneur",
    "Q13381863": "speed skater",
    "Q99903010": "Thornfield Loom",
    "Q99903011": "Bellhaven Skating Club",
}


def _item_claim(prop: str, qid: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datatype": "wikibase-item",
            "datavalue": {
                "value": {"entity-type": "item", "numeric-id": int(qid[1:]), "id": qid},
                "type": "wikibase-entityid",
            },
        },
        "type": "statement",
        "rank": "normal",
    }


def _string_claim(prop: str, value: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datatype": "url",
            "datavalue": {"value": value, "type": "string"},
        },
        "type": "statement",
        "rank": "normal",
    }


def _row(qid: str, description: str, label: str = "Marisol Quennebeck") -> dict:
    """One `wbsearchentities` hit. Every one of these matches the member BY NAME."""
    return {
        "id": qid,
        "title": qid,
        "pageid": 10_000 + int(qid[1:]) % 1000,
        "concepturi": f"http://www.wikidata.org/entity/{qid}",
        "url": f"//www.wikidata.org/wiki/{qid}",
        "label": label,
        "description": description,
        "match": {"type": "label", "language": "en", "text": label},
    }


def _entity(qid: str, description: str, claims: dict, sitelink: str | None = None) -> dict:
    entity = {
        "type": "item",
        "id": qid,
        "labels": {"en": {"language": "en", "value": "Marisol Quennebeck"}},
        "descriptions": {"en": {"language": "en", "value": description}},
        "claims": claims,
        "sitelinks": {},
    }
    if sitelink:
        entity["sitelinks"] = {
            "enwiki": {
                "site": "enwiki",
                "title": sitelink,
                "url": f"https://en.wikipedia.org/wiki/{sitelink.replace(' ', '_')}",
            }
        }
    return entity


# The member: her own homepage is the P856 claim, and it is the url the roster already
# supplied in `details`. That is the corroboration a detail filter is supposed to find.
MEMBER_DESC = "American textile-software co-founder"
MEMBER_ENTITY = _entity(
    QID_MEMBER,
    MEMBER_DESC,
    {
        "P31": [_item_claim("P31", "Q5")],
        "P106": [_item_claim("P106", "Q131524")],
        "P108": [_item_claim("P108", "Q99903010")],
        "P856": [_string_claim("P856", "https://thornfieldloom.example.com/")],
    },
    sitelink="Marisol Quennebeck",
)

# The stranger: same name, a human, a perfectly good Wikidata item — and nothing in it
# touches a single thing the roster says about the member.
SKATER_ENTITY = _entity(
    QID_SKATER,
    "Canadian speed skater",
    {
        "P31": [_item_claim("P31", "Q5")],
        "P106": [_item_claim("P106", "Q13381863")],
        "P1416": [_item_claim("P1416", "Q99903011")],
    },
    sitelink="Marisol Quennebeck (speed skater)",
)

# Not a person at all. Its description quotes the member's own city, so a detail filter
# that only looks for a matching word accepts it; `instance of: business` is what says no.
COMPANY_ENTITY = _entity(
    QID_COMPANY,
    "textile scheduling company based in Providence, Rhode Island",
    {
        "P31": [_item_claim("P31", "Q4830453")],
        "P856": [_string_claim("P856", "https://quennebeck-studio.example.net/")],
    },
)


def router(rows: list[dict], entities: dict[str, dict]):
    """`wbsearchentities` -> `rows`; `wbgetentities` -> full items, or labels only."""

    def route(request):
        path, query = parts(request)
        if not path.endswith("/w/api.php"):
            return None
        action = query.get("action")
        if action == "wbsearchentities":
            return {"searchinfo": {"search": query.get("search", "")}, "search": rows, "success": 1}
        if action == "wbgetentities":
            ids = [value for value in query.get("ids", "").split("|") if value]
            if "claims" in query.get("props", ""):
                return {
                    "entities": {qid: entities[qid] for qid in ids if qid in entities},
                    "success": 1,
                }
            # The label-only lookup. Serving it separately is the whole point: a connector
            # that never asks cannot render anything but the raw ids.
            return {
                "entities": {
                    qid: {
                        "type": "item",
                        "id": qid,
                        "labels": {"en": {"language": "en", "value": LABELS[qid]}},
                    }
                    for qid in ids
                    if qid in LABELS
                },
                "success": 1,
            }
        return None

    return route


def test_wikidata_does_not_return_the_same_name_stranger_beside_the_member(
    monkeypatch, tmp_path
):
    """Two humans called Marisol Quennebeck. One of them is on this club's roster."""
    docs, _ = search(
        "wikidata",
        router(
            [_row(QID_SKATER, "Canadian speed skater"), _row(QID_MEMBER, MEMBER_DESC)],
            {QID_SKATER: SKATER_ENTITY, QID_MEMBER: MEMBER_ENTITY},
        ),
        monkeypatch,
        tmp_path,
    )

    assert [doc.url for doc in docs] == [PAGE.format(qid=QID_MEMBER)], (
        "wikidata returned "
        f"{[doc.url for doc in docs]}. Only one of these two same-name items is the "
        "member: hers carries P856 https://thornfieldloom.example.com/, which is the url "
        "the roster itself supplied in details. The other is a speed skater. A QID is the "
        "hub_id prefix every source is joined on, so emitting both merges two people."
    )
    assert "speed skater" not in docs[0].text.lower()
    assert QID_SKATER not in docs[0].text


def test_wikidata_returns_nothing_when_no_candidate_matches_a_single_detail(
    monkeypatch, tmp_path
):
    """An unresolvable name yields [], not a plausible stranger (DESIGN Decision 8)."""
    docs, requested = search(
        "wikidata",
        router([_row(QID_SKATER, "Canadian speed skater")], {QID_SKATER: SKATER_ENTITY}),
        monkeypatch,
        tmp_path,
    )

    assert requested, "the connector has to actually ask before it can decline"
    assert docs == [], (
        f"wikidata returned {[doc.url for doc in docs]} for a candidate that shares only "
        "the member's NAME: nothing in it touches Thornfield Loom, Providence, Rhode "
        "Island or thornfieldloom.example.com. Acceptance 2 says the connector is "
        "'filtered by detail'; with no detail matched the honest answer is no document."
    )


def test_wikidata_never_returns_an_item_that_is_not_a_person(monkeypatch, tmp_path):
    """`instance of: business` is not a member of the club, however well the words match."""
    docs, _ = search(
        "wikidata",
        router(
            [_row(QID_COMPANY, "textile scheduling company based in Providence")],
            {QID_COMPANY: COMPANY_ENTITY},
        ),
        monkeypatch,
        tmp_path,
    )

    assert docs == [], (
        f"wikidata returned {[doc.url for doc in docs]} for an item whose P31 is "
        "Q4830453 (business). Its description quotes the member's own city, so a filter "
        "that only counts matching words says yes; the identity spine has to check that "
        "the thing it keyed on is a human."
    )


def test_wikidata_renders_item_claims_as_labels_and_never_as_bare_qids(monkeypatch, tmp_path):
    """TASKS T-1 acceptance 2 asks for LABELLED affiliations as text, not `Q131524`."""
    docs, _ = search(
        "wikidata",
        router([_row(QID_MEMBER, MEMBER_DESC)], {QID_MEMBER: MEMBER_ENTITY}),
        monkeypatch,
        tmp_path,
    )

    assert docs, "the member's own item must still come back"
    text = docs[0].text

    assert "occupation: entrepreneur" in text, (
        f"the occupation claim reads {text!r}. P106 points at Q131524; a second "
        "wbgetentities call resolves that to 'entrepreneur'. Without it the document "
        "carries an identifier where the affiliation should be, and T-3 quotes this text "
        "verbatim into something a host reads out loud."
    )
    assert "employer: Thornfield Loom" in text, (
        "P108 is the employer claim and it is the affiliation this whole connector exists "
        f"to hand the hub graph; got {text!r}"
    )
    assert not re.findall(r":\s*Q\d+\b", text), (
        f"a claim in {text!r} still renders as a bare QID. Any id the label lookup cannot "
        "resolve should be dropped rather than displayed: an unlabelled Q-number is noise "
        "that a resolver cannot use and an extractor can still quote."
    )
    assert QID_MEMBER in text, (
        "the item's OWN QID stays quotable out of the text -- it is the strong key, and "
        "dropping it would trade one defect for another"
    )


def test_wikidata_asks_its_search_with_the_members_name(monkeypatch, tmp_path):
    """Guard on the sweep above: filtering must not be achieved by never searching."""
    _, requested = search(
        "wikidata",
        router([_row(QID_MEMBER, MEMBER_DESC)], {QID_MEMBER: MEMBER_ENTITY}),
        monkeypatch,
        tmp_path,
    )

    assert any("wbsearchentities" in url for url in requested), requested
    assert any(MEMBER.name.split()[-1] in url for url in requested), (
        f"no request named the member; the connector asked {requested!r}"
    )
