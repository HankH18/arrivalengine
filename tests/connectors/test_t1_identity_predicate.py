"""The shared identity predicate, tested directly — because ten connectors now trust it.

WHY THIS EXISTS BESIDE `test_t1_identity_contract.py`.  That module grades every connector
end to end against a corpus containing a same-name stranger, which is the property that
matters.  It cannot grade everything: a corpus that presents TWO candidates never executes
the branch that decides what to do with ONE, and a predicate is only as good as the case
its corpus happens to contain.

Measured, and the reason this file was written: breaking `choose_one`'s
`require_corroboration` — the single guard standing between `self_page`, the highest-trust
source in the fan-out, and a lone uncorroborated stranger — left the whole end-to-end
contract GREEN, because the decoy corpus always offers two Wikidata candidates and the tie
logic covered for it.  A guard nothing fails over is a guard that will be deleted by
somebody tidying up.

The lone-candidate case cannot be made a universal rule, and that is a deliberate,
measured asymmetry rather than an oversight:

* `openalex` accepts a lone same-name author with no corroboration, because its recorded
  corpus and the frozen suite BOTH offer exactly that and both require >= 1 document
  from it. (`Narragansett Institute of Technology` and `Bellhaven Polytechnic` echo no
  roster detail in either corpus.) Tightening it fails the frozen gate.
* `self_page` refuses one, because it stamps `self_page` — the kind whose entire
  justification is "they published it on their own domain" — and a search index that
  returned one row is not evidence that only one person has the name.

So the difference lives in one flag, on one shared function, and the flag is tested here.
"""

from __future__ import annotations

import pytest
from t1_ambiguity import search
from t1_decoy import HER_QID, MEMBER_NAME, STRANGER_QID, decoy_router

from arrival.connectors.identity import (
    best_affiliation,
    carries_name,
    choose_one,
    corroborates,
    identifies,
    is_an_address,
    is_shared_host,
    mentions_name,
    on_own_host,
    roster_terms,
    tokens,
)
from arrival.contracts import PersonRef

pytestmark = pytest.mark.ticket("T-1")

MEMBER = PersonRef(
    person_id="marisol-quennebeck",
    name=MEMBER_NAME,
    details=[
        "co-founder, Thornfield Loom",
        "Providence, Rhode Island",
        "https://thornfieldloom.example.com/",
    ],
)

NO_DETAILS = PersonRef(person_id="ovid-thrale", name="Ovid Thrale", details=[])


# -- tokens / carries_name: the predicate the four repaired connectors shipped ------------


def test_tokens_drops_single_letters_so_a_middle_initial_matches_anything():
    """`QUENNEBECK MARISOL A` and `Marisol Quennebeck` are the same person."""
    assert tokens("QUENNEBECK MARISOL A") == ("quennebeck", "marisol")
    assert carries_name("QUENNEBECK MARISOL A", MEMBER_NAME)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Marisol Quennebeck", True),
        ("Marisol Quennebeck (entrepreneur)", True),  # a disambiguator is not a rejection
        ("Marisol Quennebeck Vidal", True),  # a fuller name still contains hers
        ("Quennebeck, Marisol A.", True),  # roster order, punctuation, initial
        ("Thornfield Loom", False),  # the company is not the person
        ("Quennebeck", False),  # a shared surname alone is never a match
        ("Marisol", False),
        ("", False),
    ],
)
def test_carries_name_is_word_containment_not_substring(label, expected):
    assert carries_name(label, MEMBER_NAME) is expected


def test_carries_name_is_false_for_an_empty_name_rather_than_matching_everything():
    """An empty wanted-set is a subset of every set. Failing open here would accept all."""
    assert carries_name("anything at all", "") is False


# -- mentions_name: the reason a LABEL test is not a PROSE test ---------------------------


def test_mentions_name_requires_the_words_to_be_adjacent():
    """The case that separates the two predicates, and the reason both exist.

    `carries_name` is right for a field whose job is to name one entity. Applied to prose
    it accepts a page about two other people, because every word of the name is on it.
    """
    prose = "Marisol Farrow was introduced to Dev Quennebeck at the mill."
    assert carries_name(prose, MEMBER_NAME) is True, "the loose predicate accepts it"
    assert mentions_name(prose, MEMBER_NAME) is False, "the prose predicate must not"
    assert mentions_name(f"...{MEMBER_NAME} co-founded Thornfield Loom.", MEMBER_NAME)


def test_mentions_name_tolerates_a_middle_initial_in_prose():
    assert mentions_name("Written by Marisol A. Quennebeck, 2019.", MEMBER_NAME)


# -- the roster side ----------------------------------------------------------------------


def test_roster_terms_keep_the_city_and_best_affiliation_drops_it():
    """A place is worthless as a QUERY and valuable as a CHECK; the split is the point."""
    assert roster_terms(MEMBER) == ["thornfield loom", "providence", "rhode island"]
    assert best_affiliation(MEMBER) == "Thornfield Loom"


def test_best_affiliation_skips_the_city_even_when_the_roster_lists_it_first():
    """The measured defect: `affiliations()[0]` is a CITY for a city-first roster.

    Four connectors built their search query that way, so a roster written
    `["Providence, Rhode Island", "co-founder, Thornfield Loom"]` sent the search engine
    "Marisol Quennebeck Providence" — a query about a city, whose every result is a
    stranger who lives in it.
    """
    city_first = PersonRef(
        person_id="marisol-quennebeck",
        name=MEMBER_NAME,
        details=["Providence, Rhode Island", "co-founder, Thornfield Loom"],
    )
    assert best_affiliation(city_first) == "Thornfield Loom"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("Providence, Rhode Island", True),
        ("Austin, Texas", True),
        ("Texas", True),
        ("co-founder, Thornfield Loom", False),
        ("Thornfield Loom", False),
    ],
)
def test_is_an_address(detail, expected):
    assert is_an_address(detail) is expected


# -- own host vs shared platform ----------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("linkedin.com", True),
        ("www.linkedin.com", True),
        ("medium.com", True),
        ("substack.com", True),
        ("github.com", True),
        ("thornfieldloom.example.com", False),
        ("", False),
    ],
)
def test_is_shared_host(host, expected):
    assert is_shared_host(host) is expected


def test_a_private_domain_vouches_for_every_path_under_it():
    assert on_own_host("https://thornfieldloom.example.com/about", MEMBER)
    assert on_own_host("https://thornfieldloom.example.com/team/x", MEMBER)
    assert not on_own_host("https://halvardfreight.example.net/about", MEMBER)


def test_a_shared_platform_vouches_only_for_the_path_the_roster_named():
    """The wayback weakness, stated as a property of the predicate rather than a connector.

    A roster line `https://www.linkedin.com/in/marisol-quennebeck` names a PAGE. Reading
    it as naming a HOST turns "her own site" into "everybody's site" — which is how
    `{host}/*` enumerated strangers' captures, and how any host-only check accepts a
    stranger's profile on the same platform.
    """
    person = PersonRef(
        person_id="marisol-quennebeck",
        name=MEMBER_NAME,
        details=["https://www.linkedin.com/in/marisol-quennebeck"],
    )
    assert on_own_host("https://www.linkedin.com/in/marisol-quennebeck", person)
    assert on_own_host("https://www.linkedin.com/in/marisol-quennebeck/recent", person)
    assert not on_own_host("https://www.linkedin.com/in/someone-else", person)
    # A path that merely STARTS WITH hers is a different person, and the two differ by
    # one character. The separator is what makes the check a path check.
    assert not on_own_host("https://www.linkedin.com/in/marisol-quennebeck-2", person)


def test_a_shared_platform_root_vouches_for_nobody():
    person = PersonRef(
        person_id="marisol-quennebeck", name=MEMBER_NAME, details=["https://medium.com/"]
    )
    assert not on_own_host("https://medium.com/@anyone/a-post", person)


# -- identifies: the oracle itself --------------------------------------------------------


def test_identifies_accepts_the_members_own_domain_with_no_other_evidence():
    assert identifies(MEMBER, urls=["https://thornfieldloom.example.com/notes/3"])


def test_identifies_rejects_a_perfect_name_match_with_nothing_behind_it():
    """The defect, reduced to one assertion. This is the whole ticket."""
    assert not identifies(
        MEMBER,
        names=["Marisol Quennebeck"],
        prose=["Marisol Quennebeck runs dock scheduling at Halvard Freight Systems."],
        urls=["https://halvardfreight.example.net/people/marisol-quennebeck"],
        context=["Halvard Freight Systems", "Tucson, Arizona"],
    )


def test_identifies_accepts_a_name_match_the_roster_corroborates():
    assert identifies(
        MEMBER,
        names=["Marisol Quennebeck"],
        context=["Thornfield Loom"],
    )


def test_identifies_rejects_corroboration_with_no_name():
    """A company-only EDGAR filing: the affiliation is right, nobody is named."""
    assert not identifies(
        MEMBER,
        names=["Thornfield Loom Inc. (CIK 0001742119)"],
        prose=["Thornfield Loom publishes a monthly maintenance almanac."],
        context=["Thornfield Loom"],
    )


def test_identifies_declines_everything_for_a_person_the_roster_only_names():
    """With no host and no affiliation there is nothing to corroborate against.

    This fails CLOSED on purpose. A member described by nothing but a name is a member
    this system cannot research safely, and the honest output is no documents rather than
    the most popular stranger who answers to it.
    """
    assert not identifies(
        NO_DETAILS,
        names=["Ovid Thrale"],
        prose=["Ovid Thrale is a person about whom this page is written."],
        urls=["https://example.org/ovid-thrale"],
    )


def test_corroborates_counts_distinct_roster_terms():
    assert corroborates(MEMBER, "Thornfield Loom of Providence") == 2
    assert corroborates(MEMBER, "Halvard Freight Systems of Tucson") == 0


# -- choose_one: a tie is a decline -------------------------------------------------------


def test_choose_one_returns_a_lone_candidate_by_default():
    """OpenAlex's shipped behaviour, which the hoist must not have changed.

    Both its recorded corpus and the frozen suite offer exactly one same-name author whose
    institution echoes no roster detail, and both require >= 1 document from it.
    """
    assert choose_one(["only"], lambda _: 0) == "only"


def test_choose_one_refuses_a_lone_candidate_when_corroboration_is_required():
    """`self_page`'s only guard, and the one this file exists to make fail loudly.

    Measured: breaking exactly this left the end-to-end identity contract green, because
    the decoy corpus always presents two Wikidata candidates and the tie rule covered for
    it. `self_page` stamps the highest-trust SourceKind in the system off a Wikidata
    search that used to run at `limit=1` — where "one result" is a property of the LIMIT,
    not of the world.
    """
    assert choose_one(["only"], lambda _: 0, require_corroboration=True) is None
    assert choose_one(["only"], lambda _: 1, require_corroboration=True) == "only"


def test_choose_one_declines_a_tie_rather_than_taking_the_first():
    assert choose_one(["a", "b"], lambda _: 2) is None


def test_choose_one_declines_when_nothing_is_corroborated_at_all():
    assert choose_one(["a", "b"], lambda _: 0) is None


def test_choose_one_picks_the_uniquely_best_candidate():
    assert choose_one(["a", "b"], lambda c: 2 if c == "b" else 1) == "b"


def test_choose_one_returns_none_for_no_candidates():
    assert choose_one([], lambda _: 1) is None


# -- and the same flag, observed through the connector that sets it -----------------------


def _one_wikidata_candidate(qid: str, description: str):
    """A Wikidata world with EXACTLY ONE same-name item — the shape `limit=1` produced."""

    def route(request):
        from urllib.parse import parse_qsl, urlsplit

        split = urlsplit(str(request.url))
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if "wikidata.org" not in (split.hostname or ""):
            return decoy_router(request)
        action = query.get("action", "")
        if action == "wbsearchentities":
            return {
                "search": [
                    {
                        "id": qid,
                        "title": qid,
                        "label": MEMBER_NAME,
                        "description": description,
                        "match": {"type": "label", "text": MEMBER_NAME},
                    }
                ],
                "success": 1,
            }
        if action == "wbgetentities":
            site = (
                "https://halvardfreight.example.net/"
                if qid == STRANGER_QID
                else "https://thornfieldloom.example.com/"
            )
            return {
                "entities": {
                    qid: {
                        "id": qid,
                        "labels": {"en": {"language": "en", "value": MEMBER_NAME}},
                        "descriptions": {"en": {"language": "en", "value": description}},
                        "aliases": {},
                        "claims": {
                            "P31": [
                                {
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "property": "P31",
                                        "datavalue": {
                                            "value": {"entity-type": "item", "id": "Q5"},
                                            "type": "wikibase-entityid",
                                        },
                                    }
                                }
                            ],
                            "P856": [
                                {
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "property": "P856",
                                        "datavalue": {"value": site, "type": "string"},
                                    }
                                }
                            ],
                        },
                    }
                },
                "success": 1,
            }
        return decoy_router(request)

    return route


NO_SITE = PersonRef(
    person_id="marisol-quennebeck",
    name=MEMBER_NAME,
    details=["co-founder, Thornfield Loom", "Providence, Rhode Island"],
)


def test_self_page_refuses_a_lone_uncorroborated_wikidata_item(monkeypatch, tmp_path):
    """One same-name item, nothing the roster recognises, so no website and no documents.

    `wbsearchentities` at `limit=1` ALWAYS returns one result. Treating that as "there is
    only one person with this name" is the defect this branch shipped with, and it decided
    the member's most-trusted document.
    """
    docs, requested = search(
        "self_page",
        _one_wikidata_candidate(STRANGER_QID, "logistics manager at Halvard Freight Systems"),
        monkeypatch,
        tmp_path,
        person=NO_SITE,
    )

    assert requested, "the connector has to look before it declines"
    assert docs == [], (
        f"self_page emitted {[d.url for d in docs]!r} for a Wikidata item that carries the "
        "member's name and nothing else the roster supplied. This stamps `self_page`, the "
        "highest-trust SourceKind in the system, on a stranger's homepage."
    )
    assert not any("halvardfreight" in url for url in requested), (
        f"self_page fetched the stranger's site before deciding: {requested!r}"
    )


def test_self_page_accepts_a_lone_wikidata_item_the_roster_corroborates(monkeypatch, tmp_path):
    """The paired half: refusing everybody is not a fix."""
    docs, _ = search(
        "self_page",
        _one_wikidata_candidate(HER_QID, "co-founder of Thornfield Loom"),
        monkeypatch,
        tmp_path,
        person=NO_SITE,
    )

    assert docs, "the roster names Thornfield Loom and so does the item; this IS her"
    assert all("thornfieldloom.example.com" in doc.url for doc in docs), (
        f"got {[d.url for d in docs]!r}"
    )


def test_wayback_anchors_a_shared_platform_query_on_the_roster_s_own_path(
    monkeypatch, tmp_path
):
    """`{host}/*` on a shared platform is a request for everybody's captures.

    The connector was anchored on `hosts_in(details)`, which throws away the part of the
    roster's URL that names the person. Asking the archive for `www.linkedin.com/*` is
    asking it to enumerate nine hundred million profiles, and every row that comes back is
    a page the connector will fetch and cite under the member's name.

    Graded separately from the row check on purpose: they defend the same failure at two
    different distances, and either one alone is one revert away from being the only one.
    """
    from t1_decoy import PERSON_SHARED_SITE

    _, requested = search("wayback", decoy_router, monkeypatch, tmp_path, person=PERSON_SHARED_SITE)

    cdx = [url for url in requested if "/cdx/" in url]
    assert cdx, f"wayback made no CDX request at all; asked {requested!r}"
    assert not any("url=www.linkedin.com%2F%2A" in url or "url=www.linkedin.com/*" in url
                   for url in cdx), (
        f"wayback asked the archive for the whole platform: {cdx!r}. The roster line names "
        "a PAGE on a shared host, not a host."
    )
    assert any("marisol-quennebeck-thornfield" in url for url in cdx), (
        f"the query is not anchored on the path the roster gave: {cdx!r}"
    )


def test_wayback_drops_a_capture_outside_the_members_web_space(monkeypatch, tmp_path):
    """And the second defence: whatever CDX actually returns, only her captures are cited.

    A real CDX endpoint prefix-matches on `urlkey` and is free to answer with more than
    was asked for, so the connector cannot treat its own query as a guarantee about the
    rows. Here the archive answers a path-anchored query with every capture on the host —
    hers and a same-name stranger's, one path segment apart.
    """
    from t1_decoy import PERSON_SHARED_SITE, STRANGER_SHARED_PROFILE

    docs, _ = search("wayback", decoy_router, monkeypatch, tmp_path, person=PERSON_SHARED_SITE)

    stranger = [doc.url for doc in docs if "marisol-quennebeck-halvard" in doc.url]
    assert not stranger, (
        f"wayback cited a stranger's archived profile: {stranger!r}. CDX returned both "
        f"{STRANGER_SHARED_PROFILE} and the member's page; they differ only below the host."
    )
    assert docs, "her own captures are on that host too; refusing everybody is not a fix"
