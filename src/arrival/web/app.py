"""The FastAPI application: DESIGN's route table, R3-R8, R10, R13, R15.

    uvicorn arrival.web.app:app

**The construction seam.** DESIGN names this module and a route table and pins nothing
about how the app object is built, so the shape is fixed here and stated once:

    create_app(dossier_dir: Path | None = None, llm: LLMClient | None = None) -> FastAPI

plus a module-level ``app = create_app()`` for the uvicorn command above. Both halves are
required. The factory is what makes R4 observable at all: "an off-roster arrival triggers
no live research" is only checkable by counting calls on a client the CALLER owns, and an
app that reaches for its own `ANTHROPIC_API_KEY` cannot be asked that question. The
module-level instance is what the README's deploy line and Render's start command need.

**Settings are read at factory time, never at import time.** `create_app` calls
`get_settings()` inside its body, so `DOSSIER_DIR` and `DEBUG_VIEWS` are whatever the
environment says when the app is BUILT. A module that snapshots settings into a constant at
import passes every test that builds one app and fails the moment a second app is built
against a different directory in the same process — which is exactly what a corrupt-boot
test does.

**Nothing on the arrival path researches** (DESIGN Decision 2). `POST /arrive` is a
dictionary lookup, a graph query over a graph built at boot, and one bounded `llm.structured`
call inside `make_digest`. An unknown name is refused before any of that happens, so the
404 path makes zero LLM calls by construction rather than by care.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from arrival.config import get_settings
from arrival.contracts import Digest, Dossier, LLMClient
from arrival.digest import make_digest
from arrival.graph import match as match_present
from arrival.taste import EXCLUSION_POLICY
from arrival.web.presence import Presence
from arrival.web.render import debug_view, digest_view, render
from arrival.web.store import DossierLoadError, DossierStore

#: Re-exported so a caller of `create_app` can catch a bad corpus without importing the
#: store module: T-8 acceptance 1 makes this the one exception a deploy must handle.
__all__ = ["DossierLoadError", "app", "create_app"]

#: How many digests to keep addressable. Presence is process-local (DESIGN Decision 11) and
#: so is this; the cap exists so a long-running demo cannot grow without bound.
DIGEST_HISTORY = 200


def _not_on_roster() -> JSONResponse:
    """R4 / DESIGN's route table: `404 {"error": "not on roster"}`."""
    return JSONResponse(status_code=404, content={"error": "not on roster"})


async def _payload(request: Request) -> dict[str, Any]:
    """The request body as a flat dict, whether it arrived as JSON or as a form.

    Both shapes are real and neither is optional. DESIGN's route table pins the JSON body
    (`{"name": ...}`), and TASKS T-8 acceptance 6 requires `GET /` to carry plain-HTML forms
    that POST to these same routes — a browser sends those as
    `application/x-www-form-urlencoded`. Parsing the body here rather than declaring a
    Pydantic request model is what lets one route answer both without a second endpoint.

    Nothing here can raise: a malformed body yields `{}`, which resolves to no person, which
    is a 404. An arrival that cannot name anybody is off-roster by definition.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    body = await request.body()

    if content_type.startswith("multipart/"):
        form = await request.form()
        return {key: str(value) for key, value in form.multi_items()}

    if content_type == "application/x-www-form-urlencoded":
        return _from_urlencoded(body)

    if not body:
        return {}

    try:
        parsed = json.loads(body)
    except ValueError:
        return _from_urlencoded(body)
    return parsed if isinstance(parsed, dict) else {}


def _from_urlencoded(body: bytes) -> dict[str, Any]:
    decoded = body.decode("utf-8", errors="replace")
    return {
        key: values[-1]
        for key, values in parse_qs(decoded, keep_blank_values=True).items()
        if values
    }


def _wants_json(request: Request) -> bool:
    """True when the caller asked for JSON rather than a page.

    `GET /building` serves both (DESIGN's route table: "HTML list of present people (JSON if
    `Accept: application/json`)"), and a form POST wants to land on a page while an API
    client wants a body it can parse.
    """
    return "application/json" in (request.headers.get("accept") or "").lower()


def _is_form_post(request: Request) -> bool:
    content_type = (request.headers.get("content-type") or "").lower()
    return "form" in content_type or "urlencoded" in content_type


def create_app(
    dossier_dir: Path | str | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    """Build the application, pointed at a dossier corpus and (optionally) an LLM client.

    Args:
        dossier_dir: where the committed dossiers live. `None` reads `Settings.dossier_dir`,
            i.e. the `DOSSIER_DIR` environment variable or the repo's `data/dossiers`.
        llm: the client every request-path LLM call goes through. `None` builds an
            `AnthropicClient` LAZILY, on the first call that needs one — so an app can be
            constructed, booted and served with no API key present, which SPEC C4 requires
            and the offline test suite depends on.

    Raises:
        DossierLoadError: a dossier file exists and is invalid. The message names the path.
    """
    settings = get_settings()
    directory = Path(dossier_dir) if dossier_dir is not None else settings.dossier_dir

    app = FastAPI(
        title="Arrival Engine",
        description="Staff-facing arrival digests. Server-rendered, no auth, one instance.",
        version="0.1.0",
    )
    app.state.store = DossierStore.load(directory)
    app.state.presence = Presence()
    app.state.digests = {}
    app.state.digest_order = []
    app.state.llm = llm
    # Captured at FACTORY time, from the environment this app was built in. R15 calls it a
    # switch rather than auth, and a switch that could flip under a running process would be
    # a worse contract than one that is read once and reported honestly.
    app.state.debug_views = bool(settings.debug_views)

    _register_routes(app)
    return app


def _llm_for(app: FastAPI) -> LLMClient:
    """The injected client, or a lazily built default.

    Lazy on purpose. `AnthropicClient` never touches the network at construction, but
    building one during `create_app` would still put the SDK on the boot path of a service
    whose whole point (SPEC C4) is that it boots from committed JSON with no network.
    """
    client = app.state.llm
    if client is None:
        from arrival.llm.client import AnthropicClient

        client = AnthropicClient()
        app.state.llm = client
    return client


def _remember(app: FastAPI, digest: Digest) -> None:
    app.state.digests[digest.digest_id] = digest
    app.state.digest_order.append(digest.digest_id)
    while len(app.state.digest_order) > DIGEST_HISTORY:
        app.state.digests.pop(app.state.digest_order.pop(0), None)


def _present_people(app: FastAPI) -> list[dict[str, str]]:
    store: DossierStore = app.state.store
    people = []
    for person_id in app.state.presence.present():
        dossier = store.get(person_id)
        name = dossier.person.name if dossier is not None else person_id
        people.append({"person_id": person_id, "name": name})
    return people


def _register_routes(app: FastAPI) -> None:
    """Attach DESIGN's route table to `app`.

    Every handler closes over `app` rather than reaching for a module global, because the
    frozen harness builds several apps in one process against different corpora and a
    module-level store would hand the second app the first one's data.
    """

    # ---------------------------------------------------------------- R3 / R4: arrival
    @app.post("/arrive")
    async def arrive(request: Request) -> Response:
        store: DossierStore = app.state.store
        payload = await _payload(request)
        token = str(payload.get("name") or payload.get("person_id") or "")

        person_id = store.resolve(token)
        if person_id is None:
            # R4. Refused BEFORE any matching or LLM work, so "no live research" is a
            # property of the control flow rather than of a check somewhere downstream.
            return _not_on_roster()

        dossier = store.get(person_id)
        if dossier is None:  # pragma: no cover - resolve() only returns ids it holds
            return _not_on_roster()

        # R3: presence first, then match. `graph.match` never returns the arriving person in
        # their own result, so adding them before matching is safe and is what makes the
        # presence set correct for the NEXT arrival.
        app.state.presence.arrive(person_id)
        matches = match_present(store.graph, person_id, app.state.presence.present())
        digest = await make_digest(dossier, matches, _llm_for(app))
        _remember(app, digest)

        digest_url = f"/digest/{digest.digest_id}"
        if _is_form_post(request) and not _wants_json(request):
            # The demo driver posted this. Land the host on the page they came for.
            return RedirectResponse(digest_url, status_code=303)
        return JSONResponse(
            {
                "digest_id": digest.digest_id,
                "person_id": person_id,
                "digest_url": digest_url,
            }
        )

    # ---------------------------------------------------------------- R5: departure
    @app.post("/leave")
    async def leave(request: Request) -> Response:
        store: DossierStore = app.state.store
        payload = await _payload(request)
        token = str(payload.get("person_id") or payload.get("name") or "")

        person_id = store.resolve(token)
        if person_id is None:
            return _not_on_roster()

        # Idempotent: leaving twice is not an error. R5 asks that they stop being proposed,
        # and they already have.
        app.state.presence.leave(person_id)

        if _is_form_post(request) and not _wants_json(request):
            return RedirectResponse("/", status_code=303)
        return JSONResponse({"present": _present_people(app)})

    # ---------------------------------------------------------------- R6: the building
    @app.get("/building")
    async def building(request: Request) -> Response:
        people = _present_people(app)
        if _wants_json(request):
            return JSONResponse({"present": people, "count": len(people)})
        return HTMLResponse(render("building.html", present=people))

    # ---------------------------------------------------------------- R7-R10, R13: digest
    @app.get("/digest/{digest_id}")
    async def digest_page(digest_id: str) -> Response:
        digest = app.state.digests.get(digest_id)
        if digest is None:
            return HTMLResponse(
                render("not_found.html", what=f"digest {digest_id}"), status_code=404
            )
        store: DossierStore = app.state.store
        dossier: Dossier | None = store.get(digest.person.person_id)
        return HTMLResponse(render("digest.html", **digest_view(digest, dossier)))

    # ---------------------------------------------------------------- R15: operator view
    @app.get("/debug/{person_id}")
    async def debug_page(person_id: str) -> Response:
        if not app.state.debug_views:
            # R15: "It is a switch, not auth." Off means the route does not exist, which is
            # a 404 rather than a 403 — a 403 would confirm that a dossier is there to see.
            return HTMLResponse(render("not_found.html", what="that page"), status_code=404)
        store: DossierStore = app.state.store
        resolved = store.resolve(person_id)
        dossier = store.get(resolved) if resolved else None
        if dossier is None:
            return HTMLResponse(
                render("not_found.html", what=f"dossier {person_id}"), status_code=404
            )
        return HTMLResponse(render("debug.html", **debug_view(dossier)))

    # ---------------------------------------------------------------- the demo driver
    @app.get("/")
    async def index() -> Response:
        store: DossierStore = app.state.store
        presence: Presence = app.state.presence
        roster = [
            {"person": person, "present": person.person_id in presence}
            for person in store.people()
        ]
        return HTMLResponse(
            render(
                "index.html",
                roster=roster,
                present_count=len(presence),
                dossier_dir=str(store.dossier_dir),
                exclusion_policy=EXCLUSION_POLICY,
            )
        )


#: The instance `uvicorn arrival.web.app:app` and Render's start command bind to.
#: Built from the environment, so `DOSSIER_DIR` decides which corpus it serves. A missing
#: directory yields an empty roster rather than a boot failure; a directory holding a broken
#: dossier fails the import loudly, with the path (`DossierLoadError`), which is the whole
#: of T-8 acceptance 1 applied to the deploy that actually ships.
app = create_app()
