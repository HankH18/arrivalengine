"""Who is in the building right now (R3, R5, R6; DESIGN Decision 11).

A process-local ordered set of `person_id`s. Decision 11 pins that deliberately: Render's
free tier runs one instance and a restart clearing presence is acceptable for a demo, so
Redis and a database table were both rejected.

Insertion order is kept because it is the only order that means anything here — "who walked
in most recently" is real information to a host, whereas alphabetical order is an artefact
of the id. The lock is not theatre: uvicorn serves concurrent requests, and a set mutated
from two request handlers at once is the classic way a presence list loses a person.
"""

from __future__ import annotations

import threading

__all__ = ["Presence"]


class Presence:
    """The in-memory presence set. One per application instance."""

    def __init__(self) -> None:
        # dict, not set: it preserves arrival order and still gives O(1) membership.
        self._present: dict[str, None] = {}
        self._lock = threading.Lock()

    def arrive(self, person_id: str) -> None:
        """Record an arrival. Arriving twice is a no-op, not a duplicate row."""
        with self._lock:
            self._present.pop(person_id, None)
            self._present[person_id] = None

    def leave(self, person_id: str) -> bool:
        """Remove a person. Returns whether they were actually here.

        R5 only asks that they stop being proposed, so leaving twice is not an error — the
        second call is simply a no-op and the caller still gets a 200.
        """
        with self._lock:
            if person_id not in self._present:
                return False
            del self._present[person_id]
            return True

    def present(self) -> list[str]:
        """Everyone here now, most recent arrival last."""
        with self._lock:
            return list(self._present)

    def __contains__(self, person_id: object) -> bool:
        with self._lock:
            return person_id in self._present

    def __len__(self) -> int:
        with self._lock:
            return len(self._present)
