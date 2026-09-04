"""The FastAPI surface (T-8): presence, digests, the debug view and the demo driver.

`arrival.web.app.create_app` is the one construction seam. Import it — never build a
second `FastAPI()` elsewhere — so that presence, the dossier corpus and the injected LLM
client all belong to one object with one lifetime (DESIGN Decision 11).
"""

from __future__ import annotations

__all__: list[str] = []
