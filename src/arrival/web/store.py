"""The committed dossier corpus, loaded and validated once at boot (C4, R3).

SPEC C4 says the app "must boot from committed JSON dossiers with no build-time network
access", and DESIGN §Data models says a dossier "that fails validation aborts boot with the
path in the error". Both properties live here:

* **Every file is read and validated at boot, not lazily on the request that needs it.**
  A malformed dossier discovered on the arrival path is a 500 in front of a host in a lobby;
  discovered at boot it is a deploy that never went out. :class:`DossierLoadError` always
  names the offending path, because "some dossier is invalid" is not an error an operator
  can act on.
* **Nothing here touches the network or the LLM.** DESIGN Decision 2 — the arrival path
  never researches — starts by making the boot path a pure read of the repository.

The interest graph is built once, here, for the same reason: it is a pure function of the
corpus (:func:`arrival.graph.build_graph`), so rebuilding it per arrival would spend R3's
three-second budget re-deriving a constant.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import networkx as nx

from arrival.contracts import Dossier, PersonRef
from arrival.graph import build_graph
from arrival.util import slug

__all__ = ["DossierLoadError", "DossierStore"]


class DossierLoadError(RuntimeError):
    """A dossier file is unreadable, is not JSON, or does not satisfy `Dossier`.

    The message always begins with the offending path. This is the whole point of the
    exception type: T-8 acceptance 1 is not "boot fails", it is "boot fails and the operator
    is told which file to look at".
    """


class DossierStore:
    """The corpus behind every route: dossiers by id, a name index, and the graph.

    Immutable after construction. The app never writes back into `dossier_dir` — the frozen
    acceptance harness hands the app a *copy* of its corpus precisely because a service that
    rewrote its own data store would silently rewrite the answer key, and an implementation
    that never writes is the reason that copy is not needed.
    """

    def __init__(self, dossier_dir: Path, dossiers: Iterable[Dossier]) -> None:
        self.dossier_dir = dossier_dir
        self.dossiers: dict[str, Dossier] = {d.person.person_id: d for d in dossiers}

        # Standing ruling 1: the PRODUCT contract is `person_id == slug(name)`, and lookup is
        # implemented against `slug(name)` rather than inferred from any fixture directory.
        # Both spellings are indexed so `POST /arrive {"name": "Runa Okonkwo"}` and a form
        # posting `person_id=runa-okonkwo` reach the same person.
        index: dict[str, str] = {}
        for person_id, dossier in self.dossiers.items():
            index[person_id] = person_id
            index[slug(person_id)] = person_id
            index[slug(dossier.person.name)] = person_id
        self._index = index

        # An UNRESOLVED dossier is deliberately kept out of the graph population.
        # `graph.build_graph`'s own docstring: "an unresolved dossier must be left out by the
        # caller, or it perturbs N for everyone" — N is the IDF denominator, so one
        # unresolved person silently moves every score on every digest. They stay on the
        # roster and can still arrive; they simply match nobody, which is the honest answer
        # for a person the resolver could not identify.
        self.graph: nx.Graph = build_graph(
            d for d in self.dossiers.values() if d.resolution.status == "resolved"
        )

    # -- construction ---------------------------------------------------------

    @classmethod
    def load(cls, dossier_dir: Path | str) -> DossierStore:
        """Read and validate every `*.json` under `dossier_dir`.

        A missing directory is an EMPTY corpus, not an error: `data/dossiers` does not exist
        until T-9 commits it, and `uvicorn arrival.web.app:app` has to come up and serve an
        empty roster rather than refusing to start on a fresh checkout. A file that exists
        and is broken is a different thing entirely, and raises.
        """
        directory = Path(dossier_dir)
        dossiers: list[Dossier] = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                dossiers.append(cls._read_one(path))
        return cls(directory, dossiers)

    @staticmethod
    def _read_one(path: Path) -> Dossier:
        try:
            raw = path.read_text(encoding="utf-8")
        # BOTH arms are load-bearing, and the second one is the one an earlier version of
        # this comment claimed the first covered. `OSError` is the I/O failure -- an
        # unreadable file, a directory named `*.json`, a dangling symlink. A file that reads
        # fine but is not UTF-8 raises `UnicodeDecodeError`, which subclasses `ValueError`
        # and NOT `OSError`, so `except OSError` let it escape `DossierLoadError` entirely.
        # That is not a cosmetic escape: `arrival.web.app` ends with `app = create_app()`,
        # so the corpus loads at IMPORT. One latin-1 dossier in `data/dossiers/` therefore
        # turned `import arrival.web.app` into a raw traceback -- measured as
        # `Interrupted: 1 error during collection`, pytest exit 2, ZERO of the project's
        # 1329 tests run -- and a Render boot into a stack trace instead of the diagnosis
        # this exception exists to give. `research.py:_existing_row` already had the right
        # shape (`except (OSError, ValidationError, ValueError)`); this matches it.
        except (OSError, ValueError) as exc:
            raise DossierLoadError(f"{path}: could not be read ({exc})") from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise DossierLoadError(f"{path}: is not valid JSON ({exc})") from exc
        try:
            return Dossier.model_validate(payload)
        except Exception as exc:
            raise DossierLoadError(f"{path}: does not validate as a Dossier ({exc})") from exc

    # -- reads ----------------------------------------------------------------

    def resolve(self, token: str) -> str | None:
        """The `person_id` a roster name or id refers to, or `None` when off-roster.

        R4's 404 hangs off this returning `None`, and R4 also forbids the arrival path from
        doing any research — so this is a dictionary lookup and nothing else.
        """
        candidate = (token or "").strip()
        if not candidate:
            return None
        return self._index.get(candidate) or self._index.get(slug(candidate))

    def get(self, person_id: str) -> Dossier | None:
        return self.dossiers.get(person_id)

    def people(self) -> list[PersonRef]:
        """The whole roster, by display name, for `GET /`."""
        return sorted((d.person for d in self.dossiers.values()), key=lambda p: p.name.lower())

    def __len__(self) -> int:
        return len(self.dossiers)
