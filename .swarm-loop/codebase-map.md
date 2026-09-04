# Arrival Engine — codebase map

State: **T-0 (scaffold) + T-0b (remediation) built and merged. T-1…T-9 unbuilt** — their
modules do not exist. Whole project suite: `248 passed`. Frozen acceptance: 120 collected,
only the 12 `t0` guard tests green.

## 1. What it is

A staff-facing arrival digest for a private club. A webhook (`POST /arrive`) fires when a
member walks in; the host gets one speakable page: who arrived, up to three people also in
the building they should meet **and why** (named shared hubs with exposed score
components), up to three recent cited facts, one "not on the first page" fact, and one
conversation opener. Two halves: an **offline research pipeline** (`python -m arrival
build`) that fans out over free/no-card sources, resolves the right person, extracts
quote-cited facts, applies a taste filter, and writes `data/dossiers/{person_id}.json`; and
a **FastAPI app** that boots from that committed JSON, holds presence in a process-local
set, and never researches on the arrival path. No database. Hospitality, not surveillance —
the taste filter is the scored differentiator.

## 2. Components and entry points

Shipped today (all of `src/arrival/`):

| file | what it is |
|---|---|
| `contracts.py` (273 ln) | **All** shared Pydantic models + the two Protocols. Frozen by convention — import, never redefine, never edit. Escalate instead. |
| `util.py` (60 ln) | The only copies of `slug`, `normalize_ws`, `doc_id`. |
| `config.py` (94 ln) | `Settings` (pydantic-settings) + `get_settings()` (`lru_cache`). Ships complete; no ticket may widen it. |
| `__main__.py` (68 ln) | CLI dispatch. `main(argv, *, connectors=None, llm=None) -> int`. `build` is a stub returning 2 — the **T-6 dispatch point**. |
| `__init__.py` | `__version__` only, deliberately import-free. |

Entry points: `python -m arrival <command>` and, from T-8, `uvicorn arrival.web.app:app`.

Modules the unbuilt tickets ADD (`owns` in `tickets.json`; signatures in DESIGN §Interfaces):

| ticket | owns (product) | public surface the frozen suite imports |
|---|---|---|
| T-1 | `src/arrival/http/**`, `src/arrival/connectors/**`, `tests/connectors/**`, `tests/fixtures/http/{kind}_*.json` | `arrival.http.client.fetch_text(url) -> RawDoc \| None`; `arrival.connectors.all_connectors(settings) -> list[Connector]`. **Ten** connectors: search, wikidata, wikipedia, github, edgar, wayback, propublica, hn, openalex, self_page. |
| T-2 | `src/arrival/llm/**`, `src/arrival/resolve.py`, `tests/llm/**`, `tests/resolve/**`, `tests/fixtures/resolve_cases/**` | `arrival.llm.client.AnthropicClient`; `async arrival.resolve.resolve(person, docs, llm) -> Resolution` |
| T-3 | `src/arrival/extract.py`, `tests/extract/**` | `async extract(person, resolution, docs, llm) -> tuple[list[Fact], list[Hub]]` |
| T-4 | `src/arrival/taste.py`, `tests/taste/**`, `tests/fixtures/taste_cases.yaml` | `apply_taste_rules(facts)`, `async apply_taste(facts, llm)`, `is_displayable(fact)`, `DISPLAYABLE_KINDS`, `EXCLUSION_POLICY` |
| T-5 | `src/arrival/graph.py`, `tests/graph/**` | `build_graph(dossiers) -> nx.Graph`; `match(graph, a, present) -> list[Match]` |
| T-6 | `src/arrival/research.py`, **`src/arrival/__main__.py`**, `tests/research/**`, `tests/fixtures/roster_synthetic.yaml` | `async build_dossier(...)`, `async build_all(roster_path, out_dir, *, connectors, llm, budget, force=False, only=None) -> BuildReport` |
| T-7 | `src/arrival/digest.py`, `tests/digest/**` | `async make_digest(dossier, matches, llm) -> Digest` |
| T-8 | `src/arrival/web/**`, `tests/web/**` | `arrival.web.app.create_app(dossier_dir=None, llm=None)` **plus** module-level `app = create_app()`. Routes: `POST /arrive`, `POST /leave`, `GET /building`, `GET /digest/{id}`, `GET /debug/{person_id}`, `GET /`. |
| T-9 | `data/**`, `render.yaml`, **`README.md`**, `tests/test_t9_committed_dossiers.py` | — |

`HOURS.md` is **orchestrator-owned** and in no ticket's `owns`. `contracts.py` / `util.py` /
`config.py` are frozen for the whole run.

## 3. The data model — `src/arrival/contracts.py`

Every model is `pydantic.BaseModel`; both Protocols are `@runtime_checkable`.
`from __future__ import annotations` is on. `__all__` is the exhaustive list.

| name | fields (verbatim) | produced by → consumed by |
|---|---|---|
| `LLMError(Exception)` | — | raised by T-2's client and by `LLMDouble` on an unscripted call |
| `PersonRef` | `person_id: str`, `name: str`, `details: list[str] = []` | roster/T-6 → everything. Rule: `person_id == slug(name)` |
| `SourceKind` | `Literal[self_page, search, wikidata, wikipedia, github, edgar, uspto, propublica, wayback, hn, openalex, youtube, podcast, fec, courtlistener]` | T-0 → T-1 `Connector.kind`, T-4 `DISPLAYABLE_KINDS`. **Neither may widen it.** |
| `RawDoc` | `doc_id: str` (`sha1(url)[:16]`), `source_kind`, `url: str`, `title: str = ""`, `text: str` (≤20k, never empty), `published_at: date \| None`, `fetched_at: datetime` | T-1 → T-2, T-3, T-6, T-9 |
| `Connector` (Protocol) | `kind: SourceKind`; `async search(self, person: PersonRef, budget: int) -> list[RawDoc]`. **Must never raise** — log and return `[]`. | T-1 implements → T-6 consumes |
| `Verdict` | `doc_id`, `match: Literal["yes","no","unsure"]`, `confidence: float`, `evidence: str` (verbatim span), `disambiguator: str` | T-2 → `Resolution.rejected`, `/debug` |
| `Resolution` | `person_id`, `status: Literal["resolved","unresolved"]`, `strong_keys: dict[str,str] = {}`, `accepted_doc_ids: list[str]`, `rejected: list[Verdict]`, `confidence: float` | T-2 → T-3, T-6, T-8 |
| `FactCategory` | `Literal[current_work, collaborator, interest, recent_activity, hook, affiliation, non_obvious]` | T-3 assigns |
| `ExclusionReason` | `Literal[home_or_property, family, health, legal, wealth, political, low_confidence, source_kind_not_displayable]` | T-4 assigns |
| `Provenance` | `doc_id`, `url`, `source_kind`, `quote: str`, `published_at`, `retrieved_at`, `confidence: float` | T-3 → T-7, T-8. `quote` MUST satisfy `normalize_ws(quote) in normalize_ws(doc.text)`. |
| `Fact` | `fact_id`, `text: str` (≤200 chars), `category`, `provenance`, `excluded: bool = False`, `exclusion_reason: ExclusionReason \| None = None` | T-3 creates; T-4 sets exclusion; T-7/T-8 display |
| `HubType` | `Literal[company, investor, school, board, topic, city, technology, event, cause, person]` | T-3 |
| `Hub` | `hub_id: str` (`"wd:Q123"` if Wikidata-resolved else `"{type}:{slug(label)}"`), `label`, `type`, `recency: float = 1.0`, `evidence_fact_ids: list[str] = []` | T-3 → T-5 |
| `Dossier` | `person`, `resolution`, `facts` (**includes excluded**), `hubs`, `built_at`, `schema_version: int = 1` | T-6 writes → T-5, T-7, T-8, T-9 |
| `HubContribution` | `hub: Hub` (the **arriving** person's), `idf_weight`, `recency` (min of the two edges), `type_boost`, `contribution` (= product) | T-5 → T-7, T-8 (R10 exposed reasoning) |
| `Match` | `other: PersonRef`, `score: float` (0..100), `contributions` (sorted desc), `path: list[str]`, `why: str` | T-5 → T-7, T-8 |
| `Digest` | `digest_id`, `person`, `who_line`, `meet` (≤3), `lately` (≤3, displayable only), `non_obvious: Fact \| None`, `say_out_loud`, `sources` (deduped by `doc_id`, **numbered in order**), `exclusion_policy`, `created_at` | T-7 → T-8 |
| `Budget` | `docs_per_connector: int = 8`, `max_docs_total: int = 40`, `max_llm_calls: int = 80` | T-6 |
| `BuildReport` | `people: list[dict]`, `started_at`, `finished_at`. Each dict: `{person_id, status, confidence, facts_kept, facts_excluded, hubs, zero_result_sources}` — **`list[dict]` validates nothing** | T-6 → CLI table, T-9 |
| `LLMClient` (Protocol) | `async structured(self, *, system, user, schema: type[BaseModel], max_tokens: int = 2000, cache_prefix: bool = True) -> BaseModel` — **all keyword-only**. temperature 0; returns an instance of `schema`; raises `LLMError` on invalid JSON after one retry. | T-2 implements → T-2,3,4,6,7,8 |

`tests/test_t0_contract_fields.py` holds an independent transcription of DESIGN §Interfaces
and grades field names, order, annotations, requiredness and defaults. If it goes red,
`contracts.py` drifted — fix the code or escalate; **never edit that table**.

## 4. Conventions in force

**Protocol conformance: `assert_conforms`, never `isinstance`.** `tests/doubles.py`.
`isinstance` against a `runtime_checkable` Protocol checks only that attributes with the
right *names* exist — measured: a class whose entire implementation is
`def structured(self): return "not a BaseModel"` is `isinstance(..., LLMClient) is True`.
`issubclass` is not an option for `Connector` (non-method member `kind` → `TypeError`).
`assert_conforms` compares full `inspect.signature`, async-ness, and range-checks
`Connector.kind` against the `SourceKind` Literal. It handles the ordinary
`from __future__ import annotations` + `if TYPE_CHECKING:` idiom — an earlier version
rejected every correct implementation written that way.

```python
from doubles import assert_conforms
assert_conforms(AnthropicClient(settings), LLMClient)
```

**Shared primitives — import, never reinvent** (`src/arrival/util.py`):
`slug(s)` (NFKD, strip marks, **delete** apostrophes, non-alnum → `-`;
`slug("Jane O'Neil-Ruiz") == "jane-oneil-ruiz"`), `normalize_ws(s)` (collapse whitespace,
strip, **casefold**), `doc_id(url)` (`sha1(url)[:16]`). Two spellings of `slug` means two
spellings of every `hub_id` and the graph stops joining.

**Settings.** Each field maps to the same-named uppercase env var. `anthropic_api_key`,
`tavily_api_key`, `github_token` (all `str | None = None` — a missing key disables a
capability, never crashes), `contact_email`, `cache_dir` (`Path(".cache/http")`,
**relative**), `debug_views: bool = False`, `dossier_dir` (**absolute**,
`<repo>/data/dossiers`), `anthropic_model_fast`, `anthropic_model_smart`, and the
`user_agent` property (`ArrivalEngine/0.1 (+{contact_email})`). Model ids are settings,
never hard-coded at a call site. `get_settings()` is `lru_cache(maxsize=1)`.

**Per-ticket test selection.** Implemented in `tests/harness.py`, re-exported by the
**rootdir** `conftest.py`. Every project test module carries
`pytestmark = pytest.mark.ticket("T-N")`; `--ticket T-N` keeps only items whose closest
`ticket` marker names `T-N` and deselects everything else, **unmarked tests included**.
`--ticket ""` is a `UsageError` (exit 4), never "run everything".
`addopts = "--strict-markers"` makes a misspelled marker an error, not a silent deselection.

**Async tests.** `asyncio_mode = "auto"` under `tests/` — a bare `async def test_...` works.
The frozen suite runs with `-o addopts=` and its own ini and does **not** get auto mode;
frozen tests drive async with `asyncio.run(...)`.

**Ruff.** `line-length = 100`, `src = ["src","tests"]`, `select = ["E","F","I","UP","B"]`,
no per-file-ignores. The scored metric uses the frozen mirror at
`.swarm-loop/acceptance/ruff.toml`; the two must agree (its `src` key is load-bearing —
without it isort calls `arrival` third-party and reports I001 on correct imports).

## 5. Test layout

`tests/` is **not a package** (no `__init__.py`) and its directory is on `sys.path`, so
helpers import as top-level modules: `from doubles import LLMDouble`.

- **`conftest.py` (repo root)** — the only place the hooks live, because a conftest under
  `tests/` is loaded only when a named path leads into `tests/`. Measured:
  `pytest --ticket T-0 src/` exited 4 with all three offline layers unpatched.
- **`tests/conftest.py`** — constants only. Declares no hooks on purpose: two
  `pytest_addoption`s are an argparse conflict.
- **`tests/harness.py`** — ticket selection, the offline block, and an autouse
  `reset_settings_cache` that clears `get_settings`'s cache before and after every test.
- **`tests/doubles.py`** — `assert_conforms`, `LLMDouble`, `ConnectorDouble`, `LLMCall`.
- **`tests/fixtures/dossiers/{alpha,bravo,charlie,delta}.json`** — four dossiers with
  designed hub overlaps. **`tests/fixtures/http/fixture_dossier_docs_*.json`** — JSON
  **arrays** of `RawDoc` dumps.

**`LLMDouble`**: scripted by `(schema.__name__, substring-of-user-prompt)` via
`.when(...)`, or `.queue(...)` for a sequence (queue consumed **before** rules; rules match
in registration order). A response may be a `BaseModel` (must be an instance of the
requested `schema` or it raises `LLMError`), a mapping, a JSON string, or an exception. An
**unscripted call raises `LLMError`** — never a plausible default. `delay=` awaits before
responding. Calls recorded in `.calls` as
`LLMCall(schema_name, user, max_tokens=2000, cache_prefix=True, *, system="")` —
`schema_name` and `user` positional, **`system` keyword-only** and every field type-checked,
because both prompts are `str` and the swapped construction used to store them reversed in
silence. `.calls_for(schema_name)` and `.call_count` are the assertion helpers.

**`ConnectorDouble`**: `kind` (validated against `SourceKind`), `docs`, `raises`, `delay`.
`search` records `(person, budget)`, awaits `delay`, **then** raises, then returns
`docs[:max(0, budget)]`. Delay-before-raise is deliberate: a dying source hangs first.

**The three-layer offline block (SPEC C7).** Installed in `pytest_configure` — before
collection, so import-time HTTP and session-scoped fixtures are covered:
1. `httpx.HTTPTransport.handle_request` / `AsyncHTTPTransport.handle_async_request`
2. `httpx2.*` — **a separate distribution with separate classes**
   (`httpx.HTTPTransport is httpx2.HTTPTransport` is `False`); `anthropic` and
   `starlette.testclient` run on it. With only `httpx` patched, a real round trip to
   api.anthropic.com completed from inside the suite.
3. `socket.socket.connect` / `connect_ex`, refusing only `AF_INET`/`AF_INET6`.

A real request raises `RuntimeError("network disabled in tests")`. Patching at the
**transport**, not `Client.send`, means a supplied `httpx.MockTransport` still works — that
is the seam T-1's tests use. Opt out with `@pytest.mark.network`; no frozen test uses it.

## 6. Known hazards

1. **macOS `UF_HIDDEN` kills `import arrival` with no error.** `uv venv` sets the BSD hidden
   flag on `.venv`, and CPython ≥ 3.12.6's `site.addpackage` silently skips hidden `.pth`
   files — so a clean `uv sync` exits 0, writes a correct editable install, and leaves it
   inert. The project suite is immune (`pythonpath = ["src"]`); the CLI and any plain
   `python -c` are not. Repair: `chflags -R nohidden .venv`, then **assert the effect** by
   importing and printing the resolved path. Never trust the installer's exit code.
2. **No code index inside a git worktree.** `.codegraph/` is gitignored, so it is absent
   from every worktree — and a query does not fail, it walks **up** and answers from an
   unrelated index (measured: 7,946 files across nine unrelated projects, zero from this
   one). Navigate with `git grep`. General rule: any tool living in a gitignored directory
   is absent in a worktree, and the ones that answer anyway are the expensive ones.
3. **Duplicate test-module basenames are a hard collection ERROR and cross ticket
   boundaries.** No `__init__.py` anywhere + `prepend` import mode. Measured twice:
   `tests/connectors/test_client.py` + `tests/llm/test_client.py` →
   `import file mismatch`, `Interrupted: 1 error during collection`, exit 2. DESIGN names a
   `client.py` for BOTH T-1 and T-2, so this is the collision that will happen. It is
   invisible to every per-ticket gate and appears only at merge. **Prefix every test module
   with its ticket: `test_t1_client.py`.**
4. **The unit fixtures deliberately violate `person_id == slug(name)`.**
   `tests/fixtures/dossiers/alpha.json` has `person_id="alpha"`, `name="Teodoro Vance"`.
   Intentional and pinned by `tests/test_t0b_fixture_conventions.py`, because T-5's and
   T-8's criteria name `alpha`…`delta`. The product invariant holds where it is graded: all
   five frozen dossiers satisfy it. **Implement `slug(name)` lookup; do not infer the rule
   from `tests/fixtures/`.**
5. **The stop-hub list matches hub LABELS, never a type prefix.**
   `{texas, startup, founder, ai, technology, business, ceo, investor}`. `investor` is also
   a `HubType`. Matching against `hub.type` or the `type:` prefix deletes
   `investor:foundry-seed-2019`, the rare hub the whole scoring design rests on.
6. **`cache_dir` is CWD-relative; `dossier_dir` is not.** A CLI started from a subdirectory
   silently uses a different cache root. Do not copy `cache_dir`'s shape for anything new.
7. **`data/` does not exist, and the SWARM MUST NOT CREATE IT.**
    `Settings().dossier_dir.exists()` is `False`. Under ESC-001 the swarm builds T-9's code
    artifacts only — `data/roster.yaml`, `data/dossiers/` and `data/docs/` come from the
    human-gated live build and its fact-by-fact review at source URLs. Do not read this as
    licence to fabricate a corpus. T-8 boots sanely against a missing/empty directory.
8. **The frozen suite gets NO `get_settings` cache reset** — that autouse fixture lives in
   `tests/harness.py`, which `--confcutdir` excludes. **Read `get_settings()` at call or
   factory time, never at module import time**, or 12 of T-8's 14 criteria are unobservable.
9. **A green `--ticket T-N` is not a green repo.** Your gate deselects every T-0 test, so a
   regression in `util`/`contracts`/`config` is invisible to it. Close with
   `pytest --ticket T-N -q && pytest -q`.
10. **`tests/` is on `sys.path`.** A helper named like a stdlib module (`tests/typing.py`)
    shadows it for the whole suite.
11. **Two T-0 modules shell out to real pytest subprocesses** and one writes a probe file
    into the repo root, deleting it in a `finally`. A killed run leaves it behind and the
    next `pytest -q` collects it. If you see an uncommitted `_t0b_offline_probe_test.py`,
    delete it — do not commit it.
12. **`BuildReport.people: list[dict]` validates nothing** — the one place in the contract
    with no schema, and where T-6 reports `zero_result_sources`. Validate on the way in.
    **HANDLED as of cycle 5** — `research.py:91` declares `_SOURCE_KINDS` and `_fan_out`
    applies it. Left in the list because the contract itself is still schemaless; do not
    re-fix the T-6 side.
13. **Repo furniture is load-bearing and cross-owned.** T-0 asserts `.gitignore` contains
    `.cache/` and `.env`, that `README.md` still contains `uv sync`,
    `python -m arrival build` and `pytest`, and that `HOURS.md` keeps its table. T-9 owns
    `README.md` and will break the first if it rewrites freely.
14. **`__main__.py`'s signature is pinned.** `main(argv, *, connectors=None, llm=None) -> int`,
    returning 2 for unknown commands and 0 for `--help`. The `build_succeeds` metric probes
    both, so a stub returning 2 for everything scores 0.
15. **T-1's network seam is `httpx`'s own async transport.** The frozen T-1 suite intercepts
    `httpx.AsyncHTTPTransport.handle_async_request`. Build on `httpx.AsyncClient` with the
    default transport — `httpx2`, `urllib` or `requests` will not be seen by those stubs and
    will hit the socket floor, which reads as "my connector is broken", not "wrong library".
16. **Fixture formats differ by directory.** `tests/fixtures/http/*.json` are JSON **arrays**
    of `RawDoc`s; `tests/fixtures/dossiers/*.json` are single `Dossier`s;
    `.swarm-loop/acceptance/fixtures/docs/*.json` are single `RawDoc`s named `{doc_id}.json`;
    `resolve_cases/*.json` are `{case_id, person, docs, scripted_verdicts, expect}`.
17. **A stale `.pyc` can execute code that is not in the tree.** A same-length edit made
    within the same second as the last one leaves a cache CPython accepts as valid (mtime
    at 1s resolution + size both match). Reproduced twice: source reading `return 9`
    executed as `return 2`. Neither an empty `git diff` nor re-running pytest clears it;
    `find src tests -name __pycache__ -exec rm -rf {} +` does. This silently invalidates
    any sabotage or revert-the-fix check, in both directions.
18. **Tests are append-only.** The `test_t0b_*` modules are regression tripwires for eight
    already-repaired scaffold defects — a failure there means a repair was undone.

## 7. The frozen measurement harness

`.swarm-loop/acceptance/` is orchestrator-owned, hash-locked, and outside every ticket's
scope. Read it, run it, never edit it — a branch touching anything under `.swarm-loop/` is
vetoed. If you believe a frozen test is *wrong* (not merely hard), say so in CONCERNS with
the quoted assertion and why it is wrong independent of your implementation.

```bash
uv run pytest .swarm-loop/acceptance -q -o addopts= -c .swarm-loop/acceptance/pytest.ini \
    -p no:cacheprovider --confcutdir=.swarm-loop/acceptance \
    --rootdir=.swarm-loop/acceptance -m t<N>
```

Markers are the short `tN` **names** (`-m t4`), not the project's `--ticket T-N` option.
`-c <frozen ini>` is what actually seals it: `--rootdir` alone does **not** stop configfile
discovery — a two-line `pyproject.toml` took the suite from 93 collected to 12 and the pass
rate from 15.05 to 100.0. Collection today: **122 total** — t0 12 (all `guard`), t1 19, t2 14,
t3 10, t4 14, t5 8, **t6 13**, t7 10, t8 14, t9 8. (Was 120/t6 11 before freeze
amendment #2 added two spread assertions; re-measured 2026-09-04 by the FIX-TASTE lane.) Scored per-ticket targets exclude
`@pytest.mark.guard` and `acceptance_pass_rate` excludes `@pytest.mark.human_gate`:
t1 19, t2 13, t3 10, t4 14, t5 8, **t6 12**, t7 10, t8 14. Ground truth lives only in
`.swarm-loop/acceptance/fixtures/` — the frozen suite never reads `tests/fixtures/`,
because a gradee that can write the answer key is not being graded.
