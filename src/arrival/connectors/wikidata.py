"""Wikidata: the identity spine.

WHY THIS ONE MATTERS MORE THAN ITS DOCUMENT COUNT SUGGESTS.  A Wikidata QID is a *strong
key* (`Resolution.strong_keys`, DESIGN §Interfaces) and it is the canonical `hub_id`
prefix — `Hub.hub_id` is `"wd:Q123"` whenever a hub resolves here and only otherwise falls
back to `"{type}:{slug(label)}"`.  Two people who both know "Bellhaven Polytechnic" join
in the graph only if both sides spell it the same way, and a QID is the one spelling that
cannot drift.  So this connector's job is less "find prose" than "find the identifiers the
rest of the pipeline will key on", and the prose it returns carries them in the text where
T-3 can quote them.

Two calls, in the order the API intends: `wbsearchentities` to turn a name into candidate
QIDs, then `wbgetentities` to pull each candidate's label, description, official website
(P856), employer/affiliation and English Wikipedia sitelink.  The description is what a
resolver disambiguates on, which is why it is the first line of the document text.
"""

from __future__ import annotations

from typing import Any

from arrival.connectors.base import BaseConnector, text_block
from arrival.contracts import PersonRef, RawDoc

__all__ = ["WikidataConnector"]

API = "https://www.wikidata.org/w/api.php"
ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"

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


def _localised(container: Any, language: str = "en") -> str:
    """`labels`/`descriptions` are `{lang: {"value": ...}}`; pull the English one."""
    if not isinstance(container, dict):
        return ""
    entry = container.get(language) or next(iter(container.values()), None)
    if isinstance(entry, dict):
        return str(entry.get("value") or "")
    return str(entry or "")


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
                "limit": max(1, min(budget * 2, 10)),
                "format": "json",
            },
        )
        candidates = self._candidates(payload)
        if not candidates:
            return []

        wanted = candidates[:budget]
        entities = await self._entities([c["id"] for c in wanted])

        docs: list[RawDoc] = []
        for candidate in wanted:
            qid = candidate["id"]
            doc = self._document(qid, candidate, entities.get(qid))
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
            out.append(
                {
                    "id": qid,
                    "label": str(row.get("label") or ""),
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
                "ids": "|".join(qids),
                "props": "labels|descriptions|claims|sitelinks/urls",
                "languages": "en",
                "format": "json",
            },
        )
        if isinstance(payload, dict) and isinstance(payload.get("entities"), dict):
            return payload["entities"]
        return {}

    def _document(self, qid: str, candidate: dict[str, str], entity: Any) -> RawDoc | None:
        label = candidate.get("label") or ""
        description = candidate.get("description") or ""
        lines: list[str] = []

        if isinstance(entity, dict):
            label = _localised(entity.get("labels")) or label
            description = _localised(entity.get("descriptions")) or description
            claims = entity.get("claims")
            if isinstance(claims, dict):
                for prop, readable in _INTERESTING.items():
                    values = [
                        _snak_value(claim) for claim in claims.get(prop, []) if claim is not None
                    ]
                    values = [value for value in values if value]
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
