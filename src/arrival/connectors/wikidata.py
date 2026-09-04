"""Wikidata: the identity spine.

WHY THIS ONE MATTERS MORE THAN ITS DOCUMENT COUNT SUGGESTS.  A Wikidata QID is a *strong
key* (`Resolution.strong_keys`, DESIGN §Interfaces) and it is the canonical `hub_id`
prefix — `Hub.hub_id` is `"wd:Q123"` whenever a hub resolves here and only otherwise falls
back to `"{type}:{slug(label)}"`.  Two people who both know "Bellhaven Polytechnic" join
in the graph only if both sides spell it the same way, and a QID is the one spelling that
cannot drift.  So this connector's job is less "find prose" than "find the identifiers the
rest of the pipeline will key on", and the prose it returns carries them in the text where
T-3 can quote them.

WHICH IS ALSO WHY A NAME MATCH IS NOT ENOUGH HERE (T-017).  `wbsearchentities` matches
labels, and a label is a name: search "Marisol Quennebeck" and Wikidata will happily offer
you a speed skater.  Because every other source's facts are joined onto whatever identity
this connector picked, a wrong QID does not add one wrong document — it merges two people
across the entire graph, for every person on the roster, and the merge then produces
confident matches.  So a candidate has to clear three bars before it is emitted:

1. **Its label carries the member's name.**  Necessary, never sufficient.
2. **It is a human.**  `P31` present and lacking `Q5` is a company or a song, however
   well its description happens to echo the roster's words.
3. **Something in the item corroborates a `detail` the roster supplied** — an affiliation
   named in the description or in a claim, or an official website (`P856`) on a host the
   member's own `details` already names.  TASKS T-1 acceptance 2 calls this "filtered by
   detail".  With nothing corroborated the connector returns `[]`: an unresolvable name
   yields no document, never a plausible stranger.

Three calls, in the order the API intends: `wbsearchentities` to turn a name into
candidate QIDs, `wbgetentities` to pull each candidate's label, description, claims and
English Wikipedia sitelink, and a second `wbgetentities` for the LABELS of the items those
claims point at.  Item-valued claims arrive as bare ids, so without that third call the
document reads `occupation: Q131524` — an identifier where acceptance 2 asks for a
labelled affiliation, and a string neither a resolver nor a reader can use.  An id the
lookup cannot resolve is dropped rather than displayed.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from arrival.connectors.base import BaseConnector, affiliations, hosts_in, text_block
from arrival.connectors.identity import carries_name
from arrival.contracts import PersonRef, RawDoc
from arrival.util import normalize_ws

__all__ = ["WikidataConnector"]

API = "https://www.wikidata.org/w/api.php"
ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"

#: `Q5` is "human". An item that says it is something else is not a member of the club.
HUMAN = "Q5"

#: `wbgetentities` takes at most 50 ids per call.
MAX_IDS = 50

#: Properties worth putting in the document text, by Wikidata property id.
_INTERESTING = {
    "P31": "instance of",
    "P106": "occupation",
    "P108": "employer",
    "P69": "educated at",
    "P937": "work location",
    "P856": "official website",
    "P1416": "affiliation",
    "P512": "academic degree",
    "P166": "award received",
}

_ITEM_ID = re.compile(r"^Q\d+$")

#: The name predicate now lives in `identity.py`. It was written here first and copied
#: into three more connectors before anyone hoisted it; the import is the hoist.
_carries_name = carries_name


def _snak_value(claim: Any) -> str:
    """A readable scalar out of one Wikidata statement, or "" when it is not scalar."""
    if not isinstance(claim, dict):
        return ""
    snak = claim.get("mainsnak")
    if not isinstance(snak, dict):
        return ""
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, dict):
        return ""
    value = datavalue.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # An item reference ("entity-type": "item") or a time/quantity value.
        for key in ("id", "text", "time", "amount"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def _claim_values(entity: Any, prop: str) -> list[str]:
    """Every scalar value of `prop` on `entity`, blanks dropped."""
    if not isinstance(entity, dict):
        return []
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return []
    statements = claims.get(prop)
    if not isinstance(statements, list):
        return []
    return [value for value in (_snak_value(claim) for claim in statements) if value]


def _localised(container: Any, language: str = "en") -> str:
    """`labels`/`descriptions` are `{lang: {"value": ...}}`; pull the English one."""
    if not isinstance(container, dict):
        return ""
    entry = container.get(language) or next(iter(container.values()), None)
    if isinstance(entry, dict):
        return str(entry.get("value") or "")
    return str(entry or "")


def _aliases(entity: Any, language: str = "en") -> list[str]:
    if not isinstance(entity, dict):
        return []
    container = entity.get("aliases")
    if not isinstance(container, dict):
        return []
    entries = container.get(language)
    if not isinstance(entries, list):
        return []
    return [str(entry.get("value")) for entry in entries if isinstance(entry, dict)]


class WikidataConnector(BaseConnector):
    """`kind="wikidata"` — candidate QIDs and the identifiers hanging off them."""

    kind = "wikidata"

    async def _search(self, person: PersonRef, budget: int) -> list[RawDoc]:
        payload = await self.get_json(
            API,
            params={
                "action": "wbsearchentities",
                "search": person.name,
                "language": "en",
                "uselang": "en",
                "type": "item",
                # Headroom: candidates are about to be filtered by detail, so asking for
                # exactly `budget` of them would let one same-name stranger at rank 1
                # spend the whole allowance and return nothing.
                "limit": max(5, min(budget * 3, 20)),
                "format": "json",
            },
        )
        candidates = [
            candidate
            for candidate in self._candidates(payload)
            if _carries_name(candidate["label"] or candidate["id"], person.name)
        ]
        if not candidates:
            return []

        wanted = candidates[: max(3, min(budget + 2, 10))]
        entities = await self._entities([candidate["id"] for candidate in wanted])
        labels = await self._labels(
            [
                value
                for candidate in wanted
                for prop in _INTERESTING
                for value in _claim_values(entities.get(candidate["id"]), prop)
                if _ITEM_ID.match(value)
            ]
        )

        terms = [normalize_ws(term) for term in affiliations(person.details)]
        hosts = hosts_in(person.details)

        docs: list[RawDoc] = []
        for candidate in wanted:
            entity = entities.get(candidate["id"])
            if not self._is_this_person(entity, terms, hosts, labels):
                continue
            doc = self._document(candidate["id"], candidate, entity, labels)
            if doc is not None:
                docs.append(doc)
        return docs

    @staticmethod
    def _candidates(payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            return []
        rows = payload.get("search")
        if not isinstance(rows, list):
            return []
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            qid = str(row.get("id") or row.get("title") or "")
            if not qid.startswith("Q"):
                continue
            match = row.get("match")
            out.append(
                {
                    "id": qid,
                    "label": str(
                        row.get("label")
                        or (match.get("text") if isinstance(match, dict) else "")
                        or ""
                    ),
                    "description": str(row.get("description") or ""),
                }
            )
        return out

    async def _entities(self, qids: list[str]) -> dict[str, Any]:
        if not qids:
            return {}
        payload = await self.get_json(
            API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(qids[:MAX_IDS]),
                "props": "labels|aliases|descriptions|claims|sitelinks/urls",
                "languages": "en",
                "format": "json",
            },
        )
        if isinstance(payload, dict) and isinstance(payload.get("entities"), dict):
            return payload["entities"]
        return {}

    async def _labels(self, qids: list[str]) -> dict[str, str]:
        """English labels for the items claims REFER to, so `Q131524` reads `entrepreneur`."""
        unique = [qid for qid in dict.fromkeys(qids)][:MAX_IDS]
        if not unique:
            return {}
        payload = await self.get_json(
            API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(unique),
                "props": "labels",
                "languages": "en",
                "format": "json",
            },
        )
        entities = payload.get("entities") if isinstance(payload, dict) else None
        if not isinstance(entities, dict):
            return {}
        out: dict[str, str] = {}
        for qid, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            label = _localised(entity.get("labels"))
            if label:
                out[str(qid)] = label
        return out

    def _is_this_person(
        self,
        entity: Any,
        terms: list[str],
        hosts: list[str],
        labels: dict[str, str],
    ) -> bool:
        """Is this item the member, or merely somebody with her name? (TASKS T-1 acc. 2)

        Unverifiable is treated as no. The cost of a false negative is one missing
        document; the cost of a false positive is two people's hubs merged for good.
        """
        if not isinstance(entity, dict):
            return False

        instances = _claim_values(entity, "P31")
        if instances and HUMAN not in instances:
            return False

        for site in _claim_values(entity, "P856"):
            host = (urlsplit(site).hostname or "").lower()
            if host and host in hosts:
                return True

        if not terms:
            return False

        haystack = normalize_ws(
            " ".join(
                [
                    _localised(entity.get("descriptions")),
                    _localised(entity.get("labels")),
                    *_aliases(entity),
                    *self._claim_labels(entity, labels),
                ]
            )
        )
        return any(term and term in haystack for term in terms)

    @staticmethod
    def _claim_labels(entity: Any, labels: dict[str, str]) -> list[str]:
        """Every interesting claim value, with item ids replaced by their English labels."""
        out: list[str] = []
        for prop in _INTERESTING:
            for value in _claim_values(entity, prop):
                if _ITEM_ID.match(value):
                    resolved = labels.get(value)
                    if resolved:
                        out.append(resolved)
                else:
                    out.append(value)
        return out

    def _document(
        self,
        qid: str,
        candidate: dict[str, str],
        entity: Any,
        labels: dict[str, str],
    ) -> RawDoc | None:
        label = candidate.get("label") or ""
        description = candidate.get("description") or ""
        lines: list[str] = []

        if isinstance(entity, dict):
            label = _localised(entity.get("labels")) or label
            description = _localised(entity.get("descriptions")) or description
            for prop, readable in _INTERESTING.items():
                values: list[str] = []
                for value in _claim_values(entity, prop):
                    if _ITEM_ID.match(value):
                        # An unresolved id is dropped, not printed. "occupation: Q131524"
                        # is an identifier standing where an affiliation should be, and
                        # T-3 quotes this text verbatim.
                        resolved = labels.get(value)
                        if resolved:
                            values.append(resolved)
                    else:
                        values.append(value)
                if values:
                    lines.append(f"{readable}: {', '.join(values)}")
            sitelinks = entity.get("sitelinks")
            if isinstance(sitelinks, dict):
                enwiki = sitelinks.get("enwiki")
                if isinstance(enwiki, dict) and enwiki.get("title"):
                    lines.append(f"English Wikipedia: {enwiki['title']}")

        return self.doc(
            ENTITY_URL.format(qid=qid),
            title=f"{label} ({qid})".strip(),
            text=text_block(
                f"{label} ({qid})" if label else qid,
                description,
                *lines,
            ),
        )
