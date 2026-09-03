# Arrival Engine

A staff-facing arrival engine for Arena Hall. When a member arrives, a webhook posts their
name plus an identifying detail or two, and the host gets a tight, speakable digest: who
walked in, who else in the building they should meet and *why*, a short cited dossier, and
one conversation opener. Hospitality, not surveillance.

The research half runs **offline** (`python -m arrival build`) and writes cited dossier JSON
to `data/dossiers/`. The arrival half is a FastAPI app that boots from those JSON files,
holds presence in memory, and serves digests — it never researches on the arrival path.

## Run it

```bash
uv sync                                  # provisions .venv from the committed uv.lock
cp .env.example .env                     # fill in whatever keys you have
uv run python -m arrival build --roster data/roster.yaml --out data/dossiers
uv run uvicorn arrival.web.app:app --reload
```

> The `build` subcommand lands in T-6 and the web app in T-8; on the T-0 scaffold
> `python -m arrival` prints usage and exits 2.

## Tests

The suite is offline by construction: `tests/conftest.py` installs an httpx transport that
raises `RuntimeError("network disabled in tests")` on any request.

```bash
uv run pytest -q                 # whole suite
uv run pytest --ticket T-0 -q    # only the tests attributed to ticket T-0
uv run ruff check src tests
```

Every test module carries `pytestmark = pytest.mark.ticket("T-N")`; `--ticket T-N` deselects
everything else, unmarked tests included. Test helpers live in `tests/doubles.py` and are
imported as a top-level module (`from doubles import LLMDouble`) because `tests/` is not a
package.

### If `import arrival` fails outside pytest (macOS)

`uv venv` marks the whole `.venv` tree with the macOS `UF_HIDDEN` file flag, and CPython
≥ 3.12.6's `site.addpackage` **silently skips hidden `.pth` files** — so the editable
install can be dead with no error anywhere, and `uv run python -m arrival` fails with
`ModuleNotFoundError: No module named 'arrival'`. The test suite is immune because
`[tool.pytest.ini_options] pythonpath = ["src"]` puts `src/` on the path directly. To fix
the CLI:

```bash
chflags nohidden .venv/lib/python3.12/site-packages/*.pth   # check with: ls -lO
```

Re-run it after any `uv venv` / `uv sync` that recreates the environment.

## Layout

```
src/arrival/contracts.py   # ALL shared models + Protocols. Frozen: import, never redefine.
src/arrival/util.py        # slug(), normalize_ws(), doc_id() — the only copies in the repo.
src/arrival/config.py      # Settings from env
src/arrival/__main__.py    # CLI dispatch
tests/fixtures/dossiers/   # four synthetic dossiers with designed hub overlaps
tests/fixtures/http/       # RawDocs the fixture dossiers cite
```

## Hours log

<!-- Folded in from HOURS.md at T-9. -->

_TBD_

## Deploy URL

_TBD_

## Exclusion policy

The digest never surfaces home or property records, family members or relationships,
health or medical information, litigation or court records, wealth or compensation
figures, or political donations and affiliations. Facts in those categories are retained
internally, flagged with a reason, and are visible only on the operator-only `/debug` view.

<!-- Replaced verbatim with taste.EXCLUSION_POLICY at T-4/T-9. -->

## What I'd build next, with a month and real data

_TBD_

## Open questions (SPEC Q1–Q5)

_TBD — answered at T-9._
