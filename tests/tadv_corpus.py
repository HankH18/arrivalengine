"""Helpers shared by the ``test_tadv_*`` adversarial modules.

Two jobs, and both exist so the assertions in those modules grade against something the
author of this file cannot write:

* :func:`committed_dossiers` loads the REAL corpus at ``data/dossiers/`` — ten living
  public figures, produced by a human-gated build, outside this lane's ownership.
* :func:`synthetic_corpus` builds a Dossier the long way, through
  ``arrival.contracts``' own validators, so a schema drift breaks these tests loudly
  instead of letting them assert against a shape the product no longer has. Nothing here
  is a golden file: every assertion in the calling modules compares a rendered page
  against a literal the test itself planted, or against a value read out of the committed
  corpus.

``tests/`` is not a package, so this imports as a top-level module: ``import
tadv_corpus``. The ``tadv_`` prefix keeps the basename unique across the whole tree —
two same-named test modules anywhere under ``tests/`` are a hard collection error.
"""

from __future__ import annotations

import json
from pathlib import Path

from arrival.contracts import Dossier, LLMError

#: ``tests/`` sits directly under the repository root. Resolved from ``__file__`` rather
#: than the working directory, for the same reason ``tests/test_t9_committed_dossiers.py``
#: does it: a cwd-relative path silently validates a different (usually empty) corpus.
REPO_ROOT = Path(__file__).resolve().parents[1]
DOSSIER_DIR = REPO_ROOT / "data" / "dossiers"


def committed_dossiers() -> list[Dossier]:
    """Every dossier committed at ``data/dossiers/``, validated, sorted by person id."""
    return sorted(
        (Dossier.model_validate_json(path.read_text(encoding="utf-8"))
         for path in DOSSIER_DIR.glob("*.json")),
        key=lambda d: d.person.person_id,
    )


class DeadLLM:
    """An ``LLMClient`` whose every call fails.

    Deliberate: ``digest._say_out_loud`` converges on its documented template for every
    failure mode, so a dead client makes the whole page deterministic without scripting a
    single response. Conforms to the ``LLMClient`` protocol by signature.
    """

    async def structured(self, *, system, user, schema, max_tokens=None):  # noqa: ANN001
        raise LLMError("offline: TESTADVERSARY DeadLLM")


def _fact(
    person_id: str,
    index: int,
    text: str,
    *,
    excluded: bool = False,
    exclusion_reason: str | None = None,
) -> dict[str, object]:
    return {
        "fact_id": f"{person_id}-f{index}",
        "person_id": person_id,
        "text": text,
        "category": "affiliation",
        "excluded": excluded,
        "exclusion_reason": exclusion_reason,
        "provenance": {
            "doc_id": f"{person_id}-d{index}",
            "url": f"https://example.invalid/{person_id}/{index}",
            "source_kind": "self_page",
            "quote": text,
            "confidence": 0.9,
            "retrieved_at": "2026-09-01T00:00:00Z",
        },
    }


def synthetic_person(
    person_id: str,
    name: str,
    facts: list[tuple[str, bool, str | None]],
    hubs: list[tuple[str, str, str, int]],
) -> dict[str, object]:
    """One dossier payload: ``facts`` as ``(text, excluded, reason)``, ``hubs`` as
    ``(hub_id, type, label, index of the backing fact)``."""
    payload = {
        "person": {"person_id": person_id, "name": name, "details": []},
        "resolution": {
            "person_id": person_id,
            "status": "resolved",
            "strong_keys": {},
            "accepted_doc_ids": [f"{person_id}-d0"],
            "rejected": [],
            "confidence": 0.9,
        },
        "facts": [
            _fact(person_id, i, text, excluded=excluded, exclusion_reason=reason)
            for i, (text, excluded, reason) in enumerate(facts)
        ],
        "hubs": [
            {
                "hub_id": hub_id,
                "type": hub_type,
                "label": label,
                "recency": 0.9,
                "evidence_fact_ids": [f"{person_id}-f{index}"],
            }
            for hub_id, hub_type, label, index in hubs
        ],
        "built_at": "2026-09-01T00:00:00Z",
        "schema_version": 1,
    }
    # Validate through the product's own contract, so a schema change fails here rather
    # than turning these assertions into statements about a shape nothing uses.
    Dossier.model_validate(payload)
    return payload


def write_corpus(directory: Path, payloads: list[dict[str, object]]) -> Path:
    for payload in payloads:
        person_id = payload["person"]["person_id"]  # type: ignore[index]
        (directory / f"{person_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory
