"""A corpus that presents THE CHOICE: the member, and a same-name stranger ranked above her.

WHY THIS EXISTS.  The recorded corpus in `tests/fixtures/http/` and the frozen suite's own
inlined corpus both offer **exactly one candidate per source**, and both answer every
unrecognised URL successfully.  Against a corpus like that, a connector that treats "the
name matched" as "this is the person" is *invisible*: there is nothing for it to get
wrong.  The branch where a connector has to answer "which one is her?" is never executed
for any of the ten, which is how the same defect shipped ten times and was found six times
one at a time.

So this module serves, at the same `httpx.AsyncHTTPTransport` seam the recorded corpus and
the frozen suite use, a world containing two people with the identical name:

* **Marisol Quennebeck of Thornfield Loom, Providence** — the member.  Every roster detail
  she has (`Thornfield Loom`, `Providence`, `Rhode Island`, `thornfieldloom.example.com`)
  appears somewhere in her half of the corpus.
* **Marisol Quennebeck of Halvard Freight Systems, Tucson** — a stranger.  Not one roster
  detail appears anywhere in his half, and every source **ranks him first**, because
  "take the top hit" is the defect, and a corpus that puts the right answer at rank 1
  grades it green.

Nothing here is a real person or a real domain: the names are invented and every host is
under an RFC 2606 reserved domain.

WHAT A CORRECT CONNECTOR DOES WITH IT.  Emit documents about her, or emit nothing.
Emitting anything about the stranger is the failure this corpus exists to catch, and it is
detectable without knowing anything about a connector's internals: the stranger's half of
the world is the only place `STRANGER_MARK` and `STRANGER_HOST` occur.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from arrival.contracts import PersonRef

__all__ = [
    "HER_HOST",
    "HER_SITE",
    "MEMBER_NAME",
    "PERSON_NO_SITE",
    "PERSON_SHARED_SITE",
    "PERSON_WITH_SITE",
    "SHARED_PROFILE",
    "STRANGER_HOST",
    "STRANGER_MARK",
    "STRANGER_SITE",
    "about_the_stranger",
    "decoy_router",
]

MEMBER_NAME = "Marisol Quennebeck"

HER_COMPANY = "Thornfield Loom"
HER_CITY = "Providence"
HER_STATE = "Rhode Island"
HER_HOST = "thornfieldloom.example.com"
HER_SITE = f"https://{HER_HOST}/"
HER_PAGE = f"{HER_SITE}team/marisol-quennebeck"
HER_LOGIN = "mquennebeck"
HER_QID = "Q104882317"
HER_CIK = "0001742119"
HER_EIN = "861742119"
HER_LINE = (
    f"{MEMBER_NAME} co-founded {HER_COMPANY} in {HER_CITY} in 2017 to keep small textile "
    "mills scheduling their own looms."
)

#: The stranger. Same name, nothing else in common, and first in every result list.
STRANGER_COMPANY = "Halvard Freight Systems"
STRANGER_CITY = "Tucson"
STRANGER_STATE = "Arizona"
STRANGER_HOST = "halvardfreight.example.net"
STRANGER_SITE = f"https://{STRANGER_HOST}/"
STRANGER_PAGE = f"{STRANGER_SITE}people/marisol-quennebeck"
STRANGER_LOGIN = "mq-halvard"
STRANGER_QID = "Q555000111"
STRANGER_CIK = "0009900011"
STRANGER_EIN = "869900011"
STRANGER_LINE = (
    f"{MEMBER_NAME} runs dock scheduling at {STRANGER_COMPANY} in {STRANGER_CITY} and has "
    "never set foot in New England."
)

#: The one string that occurs ONLY in the stranger's half of the corpus. An emitted
#: document containing it, or hosted on `STRANGER_HOST`, is a stranger's document
#: regardless of which field the connector pulled it out of.
STRANGER_MARK = STRANGER_COMPANY

#: A roster line pointing at a page on a platform millions of people publish under. The
#: URL identifies a PAGE; the host identifies nobody.
SHARED_PROFILE = "https://www.linkedin.com/in/marisol-quennebeck-thornfield"
SHARED_HOST = "www.linkedin.com"
STRANGER_SHARED_PROFILE = "https://www.linkedin.com/in/marisol-quennebeck-halvard"

_DETAILS = [f"co-founder, {HER_COMPANY}", f"{HER_CITY}, {HER_STATE}"]

#: Scenario A — the roster names her own domain. The commonest shape, and the one both
#: existing corpora use.
PERSON_WITH_SITE = PersonRef(
    person_id="marisol-quennebeck", name=MEMBER_NAME, details=[*_DETAILS, HER_SITE]
)

#: Scenario B — the roster names no site at all. This is the shape that reaches
#: `self_page`'s Wikidata fallback, which the recorded fixture (which always supplies a
#: URL) never executes.
PERSON_NO_SITE = PersonRef(
    person_id="marisol-quennebeck", name=MEMBER_NAME, details=list(_DETAILS)
)

#: Scenario C — the roster names a page on a SHARED platform. A host-only check reads this
#: as "she owns linkedin.com".
PERSON_SHARED_SITE = PersonRef(
    person_id="marisol-quennebeck", name=MEMBER_NAME, details=[*_DETAILS, SHARED_PROFILE]
)


def about_the_stranger(doc: Any) -> bool:
    """True when this `RawDoc` is about the stranger rather than about the member."""
    url = str(getattr(doc, "url", ""))
    text = f"{getattr(doc, 'title', '')}\n{getattr(doc, 'text', '')}"
    host = (urlsplit(url).hostname or "").lower()
    return (
        host == STRANGER_HOST
        or STRANGER_MARK.lower() in text.lower()
        or STRANGER_CITY.lower() in text.lower()
        or STRANGER_LOGIN in url
        or STRANGER_LOGIN in text
        or STRANGER_QID in url
        or STRANGER_EIN in url
        or STRANGER_CIK in url
        or url.rstrip("/") == STRANGER_SHARED_PROFILE.rstrip("/")
    )


# -- the sources, stranger first ----------------------------------------------------------


def _tavily() -> dict[str, Any]:
    return {
        "query": MEMBER_NAME,
        "results": [
            {
                "title": f"{MEMBER_NAME} — {STRANGER_COMPANY}",
                "url": STRANGER_PAGE,
                "content": STRANGER_LINE,
                "raw_content": STRANGER_LINE,
                "score": 0.99,
                "published_date": "2024-06-01",
            },
            {
                "title": f"{MEMBER_NAME} — {HER_COMPANY}",
                "url": HER_PAGE,
                "content": HER_LINE,
                "raw_content": HER_LINE,
                "score": 0.62,
                "published_date": "2024-05-02",
            },
        ],
    }


def _duckduckgo() -> str:
    rows = "".join(
        f'<div class="result"><a class="result__a" href="{url}">{title}</a>'
        f'<div class="result__snippet">{line}</div></div>'
        for url, title, line in (
            (STRANGER_PAGE, f"{MEMBER_NAME} — {STRANGER_COMPANY}", STRANGER_LINE),
            (HER_PAGE, f"{MEMBER_NAME} — {HER_COMPANY}", HER_LINE),
        )
    )
    return f"<html><body>{rows}</body></html>"


def _wikidata_search() -> dict[str, Any]:
    def row(qid: str, description: str) -> dict[str, Any]:
        return {
            "id": qid,
            "title": qid,
            "label": MEMBER_NAME,
            "description": description,
            "concepturi": f"http://www.wikidata.org/entity/{qid}",
            "url": f"//www.wikidata.org/wiki/{qid}",
            "match": {"type": "label", "language": "en", "text": MEMBER_NAME},
        }

    return {
        "searchinfo": {"search": MEMBER_NAME},
        "search": [
            row(STRANGER_QID, f"logistics manager at {STRANGER_COMPANY}"),
            row(HER_QID, f"co-founder of {HER_COMPANY}"),
        ],
        "success": 1,
    }


_LABELS = {
    "Q5": "human",
    "Q131524": "entrepreneur",
    f"{HER_QID}-employer": HER_COMPANY,
    f"{STRANGER_QID}-employer": STRANGER_COMPANY,
    "Q90000001": HER_COMPANY,
    "Q90000002": STRANGER_COMPANY,
}


def _item_claim(prop: str, qid: str) -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datatype": "wikibase-item",
            "datavalue": {
                "value": {"entity-type": "item", "id": qid},
                "type": "wikibase-entityid",
            },
        },
        "type": "statement",
        "rank": "normal",
    }


def _string_claim(prop: str, value: str) -> dict[str, Any]:
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


def _wikidata_entity(qid: str) -> dict[str, Any]:
    stranger = qid == STRANGER_QID
    employer = "Q90000002" if stranger else "Q90000001"
    site = STRANGER_SITE if stranger else HER_SITE
    description = (
        f"logistics manager at {STRANGER_COMPANY}" if stranger else f"co-founder of {HER_COMPANY}"
    )
    return {
        "id": qid,
        "type": "item",
        "labels": {"en": {"language": "en", "value": MEMBER_NAME}},
        "descriptions": {"en": {"language": "en", "value": description}},
        "aliases": {},
        "sitelinks": {},
        "claims": {
            "P31": [_item_claim("P31", "Q5")],
            "P106": [_item_claim("P106", "Q131524")],
            "P108": [_item_claim("P108", employer)],
            "P856": [_string_claim("P856", site)],
        },
    }


def _wikidata_entities(ids: str) -> dict[str, Any]:
    wanted = [item for item in ids.split("|") if item]
    entities: dict[str, Any] = {}
    for item in wanted:
        if item in (HER_QID, STRANGER_QID):
            entities[item] = _wikidata_entity(item)
        else:
            entities[item] = {
                "id": item,
                "type": "item",
                "labels": {"en": {"language": "en", "value": _LABELS.get(item, item)}},
                "descriptions": {},
                "aliases": {},
                "claims": {},
            }
    return {"entities": entities, "success": 1}


_WIKI_HER = "Marisol Quennebeck"
_WIKI_STRANGER = "Marisol Quennebeck (logistics executive)"


def _wikipedia_search() -> dict[str, Any]:
    return {
        "batchcomplete": True,
        "query": {
            "search": [
                {"ns": 0, "title": _WIKI_STRANGER, "pageid": 55500011},
                {"ns": 0, "title": _WIKI_HER, "pageid": 74110921},
            ]
        },
    }


def _wikipedia_summary(title: str) -> dict[str, Any] | None:
    normalised = unquote(title).replace("_", " ")
    if normalised == _WIKI_STRANGER:
        return {
            "type": "standard",
            "title": _WIKI_STRANGER,
            "titles": {"normalized": _WIKI_STRANGER},
            "description": f"logistics executive at {STRANGER_COMPANY}",
            "extract": STRANGER_LINE,
            "timestamp": "2024-06-01T00:00:00Z",
            "content_urls": {
                "desktop": {"page": "https://en.wikipedia.org/wiki/Marisol_Quennebeck_(logistics_executive)"}
            },
        }
    if normalised == _WIKI_HER:
        return {
            "type": "standard",
            "title": _WIKI_HER,
            "titles": {"normalized": _WIKI_HER},
            "description": f"co-founder of {HER_COMPANY}",
            "extract": HER_LINE,
            "timestamp": "2024-03-11T00:00:00Z",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Marisol_Quennebeck"}},
        }
    return None


def _github_user(login: str) -> dict[str, Any]:
    stranger = login == STRANGER_LOGIN
    return {
        "login": login,
        "id": 90210331 if not stranger else 55500011,
        "type": "User",
        "html_url": f"https://github.com/{login}",
        "name": MEMBER_NAME,
        "company": STRANGER_COMPANY if stranger else HER_COMPANY,
        "blog": STRANGER_SITE if stranger else HER_SITE,
        "location": (
            f"{STRANGER_CITY}, {STRANGER_STATE}" if stranger else f"{HER_CITY}, {HER_STATE}"
        ),
        "bio": STRANGER_LINE if stranger else HER_LINE,
        "public_repos": 17,
        "followers": 240,
        "created_at": "2016-08-19T11:04:12Z",
    }


def _github_repos(login: str) -> list[dict[str, Any]]:
    stranger = login == STRANGER_LOGIN
    name = "dock-scheduler" if stranger else "loom-scheduler"
    return [
        {
            "name": name,
            "full_name": f"{login}/{name}",
            "html_url": f"https://github.com/{login}/{name}",
            "description": STRANGER_LINE if stranger else HER_LINE,
            "language": "Python",
            "stargazers_count": 312,
            "pushed_at": "2024-06-11T08:30:00Z",
            "fork": False,
        }
    ]


def _edgar() -> dict[str, Any]:
    def hit(index: int, cik: str, names: list[str], description: str) -> dict[str, Any]:
        return {
            "_id": f"{cik}-24-00001{index}:primary_doc.xml",
            "_source": {
                "adsh": f"{cik}-24-00001{index}",
                "ciks": [cik],
                "display_names": names,
                "form": "4",
                "root_forms": ["4"],
                "file_date": "2024-04-18",
                "file_description": description,
            },
        }

    return {
        "hits": {
            "total": {"value": 3, "relation": "eq"},
            "hits": [
                hit(
                    1,
                    STRANGER_CIK,
                    [
                        f"Quennebeck Marisol (CIK {STRANGER_CIK})",
                        f"{STRANGER_COMPANY} Inc. (CIK {STRANGER_CIK})",
                    ],
                    f"FORM 4 - {STRANGER_LINE}",
                ),
                hit(
                    2,
                    HER_CIK,
                    [f"{HER_COMPANY} Inc. (CIK {HER_CIK})"],
                    f"FORM 4 - {HER_COMPANY} Inc. filed a statement of changes.",
                ),
                hit(
                    3,
                    HER_CIK,
                    [
                        f"Quennebeck Marisol (CIK {HER_CIK})",
                        f"{HER_COMPANY} Inc. (CIK {HER_CIK})",
                    ],
                    f"FORM 4 - {HER_LINE}",
                ),
            ],
        }
    }


def _hn() -> dict[str, Any]:
    return {
        "nbHits": 3,
        "hits": [
            {
                "objectID": "55500011",
                "title": f"{STRANGER_COMPANY} rebuilt dock scheduling in a week",
                "author": STRANGER_LOGIN,
                "points": 214,
                "num_comments": 63,
                "url": f"{STRANGER_SITE}notes/dock",
                "story_text": STRANGER_LINE,
                "created_at": "2024-06-01T15:11:00.000Z",
            },
            {
                "objectID": "40112233",
                "title": f"{HER_COMPANY}: scheduling looms without a mainframe",
                "author": HER_LOGIN,
                "points": 96,
                "num_comments": 28,
                "url": f"{HER_SITE}notes/2024-scheduling",
                "story_text": HER_LINE,
                "created_at": "2024-04-02T15:11:00.000Z",
            },
        ],
    }


def _openalex_authors() -> dict[str, Any]:
    def author(identifier: str, institution: str, works: int) -> dict[str, Any]:
        return {
            "id": f"https://openalex.org/{identifier}",
            "display_name": MEMBER_NAME,
            "display_name_alternatives": [MEMBER_NAME],
            "works_count": works,
            "cited_by_count": works * 4,
            "last_known_institutions": [{"display_name": institution}],
            "affiliations": [{"institution": {"display_name": institution}}],
        }

    return {
        "results": [
            author("A5500001", STRANGER_COMPANY, 40),
            author("A5031927451", HER_COMPANY, 9),
        ],
        "meta": {"count": 2},
    }


def _openalex_works(author_id: str) -> dict[str, Any]:
    stranger = "A5500001" in author_id
    line = STRANGER_LINE if stranger else HER_LINE
    url = f"{STRANGER_SITE}papers/1" if stranger else f"{HER_SITE}papers/1"
    return {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "doi": None,
                "title": line[:60],
                "display_name": line[:60],
                "publication_date": "2023-04-01",
                "cited_by_count": 3,
                "abstract_inverted_index": {
                    word: [i] for i, word in enumerate(line.split())
                },
                "primary_location": {"landing_page_url": url, "source": {"display_name": "Notes"}},
                "authorships": [
                    {
                        "author": {"id": author_id, "display_name": MEMBER_NAME},
                        "institutions": [
                            {"display_name": STRANGER_COMPANY if stranger else HER_COMPANY}
                        ],
                    }
                ],
            }
        ],
        "meta": {"count": 1},
    }


def _propublica_search() -> dict[str, Any]:
    return {
        "organizations": [
            {
                "ein": int(STRANGER_EIN),
                "strein": STRANGER_EIN,
                "name": f"{STRANGER_COMPANY} Foundation",
                "city": STRANGER_CITY,
                "state": "AZ",
                "ntee_code": "S41",
            },
            {
                "ein": int(HER_EIN),
                "strein": HER_EIN,
                "name": "Narragansett Mill Archive",
                "city": HER_CITY,
                "state": "RI",
                "ntee_code": "A80",
            },
        ]
    }


def _propublica_org(ein: str) -> dict[str, Any]:
    stranger = ein.startswith("8699")
    return {
        "organization": {
            "ein": int(ein),
            "name": f"{STRANGER_COMPANY} Foundation" if stranger else "Narragansett Mill Archive",
            "city": STRANGER_CITY if stranger else HER_CITY,
            "state": "AZ" if stranger else "RI",
            "officers": [
                {"name": MEMBER_NAME, "title": "Chair"},
                {"name": "Ilves Tarn", "title": "Treasurer"},
            ],
        }
    }


def _wayback_cdx(target: str) -> list[list[str]]:
    """CDX rows for whatever URL PATTERN the connector asked for.

    This is the whole wayback question in one function: a query for `{host}/*` on a shared
    platform enumerates *everybody's* captures, and a query anchored on the roster's own
    path enumerates hers.  The router answers each one honestly, so the connector's choice
    of query is what decides which rows it sees.
    """
    header = [
        "urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"
    ]
    pattern = target.rstrip("*").rstrip("/")
    captures = [
        ("20180614101500", HER_SITE),
        ("20200211084500", f"{HER_SITE}about"),
        ("20190101120000", SHARED_PROFILE),
        ("20190202120000", STRANGER_SHARED_PROFILE),
        ("20200303120000", f"{STRANGER_SITE}about"),
    ]
    rows = [header]
    for index, (timestamp, url) in enumerate(captures):
        bare = url.split("://", 1)[-1].rstrip("/")
        if not bare.startswith(pattern.split("://", 1)[-1].rstrip("/")):
            continue
        rows.append(
            [
                f"key{index}",
                timestamp,
                url,
                "text/html",
                "200",
                f"DIGEST{index:026d}",
                "5120",
            ]
        )
    return rows


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{body}</p></body></html>"
    )


def _page_for(url: str) -> str:
    """The page any http(s) URL in this world serves."""
    host = (urlsplit(url).hostname or "").lower()
    if host == STRANGER_HOST or url.rstrip("/") == STRANGER_SHARED_PROFILE.rstrip("/"):
        return _page(f"{MEMBER_NAME} — {STRANGER_COMPANY}", STRANGER_LINE)
    return _page(f"{MEMBER_NAME} — {HER_COMPANY}", HER_LINE)


def decoy_router(request: Any) -> Any:
    """Answer any request with this two-people world. Never 404s a plausible endpoint."""
    split = urlsplit(str(request.url))
    host = (split.hostname or "").lower()
    path = split.path
    query = dict(parse_qsl(split.query, keep_blank_values=True))

    if "tavily" in host:
        return _tavily()
    if "duckduckgo" in host:
        return _duckduckgo()

    if "wikidata.org" in host:
        action = query.get("action", "")
        if action == "wbsearchentities":
            return _wikidata_search()
        if action == "wbgetentities":
            return _wikidata_entities(query.get("ids", ""))
        if path.startswith("/wiki/"):
            qid = path.rsplit("/", 1)[-1]
            body = STRANGER_LINE if qid == STRANGER_QID else HER_LINE
            return _page(f"{MEMBER_NAME} ({qid}) - Wikidata", body)
        return _wikidata_search()

    if "wikipedia.org" in host:
        if "/api/rest_v1/page/summary" in path:
            return _wikipedia_summary(path.rsplit("/", 1)[-1])
        if path.startswith("/wiki/"):
            return _page("Wikipedia", HER_LINE)
        return _wikipedia_search()

    if host == "api.github.com":
        if path.startswith("/search/users"):
            return {
                "total_count": 2,
                "incomplete_results": False,
                # The REAL shape: a search item carries a login and nothing that could
                # identify anybody. Rejecting here is impossible; that is the point.
                "items": [
                    {
                        "login": login,
                        "id": 1,
                        "type": "User",
                        "html_url": f"https://github.com/{login}",
                    }
                    for login in (STRANGER_LOGIN, HER_LOGIN)
                ],
            }
        if path.endswith("/repos"):
            return _github_repos(path.split("/")[2])
        if path.startswith("/users/"):
            return _github_user(path.split("/")[2])
        return None

    if "sec.gov" in host:
        if host.startswith("efts.") or "search" in path:
            return _edgar()
        if "/Archives/" in path:
            body = STRANGER_LINE if STRANGER_CIK.lstrip("0") in path else HER_LINE
            return _page("SEC filing index", body)
        return _edgar()

    if "archive.org" in host:
        if "/cdx" in path:
            return _wayback_cdx(query.get("url", ""))
        if path.startswith("/web/"):
            # `/web/{timestamp}/{original}` — serve the page the ORIGINAL would serve.
            remainder = path[len("/web/") :]
            original = remainder.split("/", 1)[-1] if "/" in remainder else ""
            if split.query:
                original = f"{original}?{split.query}"
            return _page_for(original)
        return _wayback_cdx(query.get("url", ""))

    if "propublica.org" in host:
        if "/organizations/" in path:
            return _propublica_org(path.rsplit("/", 1)[-1].removesuffix(".json"))
        return _propublica_search()

    if "algolia" in host:
        return _hn()
    if "ycombinator.com" in host:
        return _page("Hacker News", HER_LINE)

    if "openalex.org" in host:
        if "/works" in path:
            return _openalex_works(json.dumps(query))
        return _openalex_authors()

    return _page_for(str(request.url))
