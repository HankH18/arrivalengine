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

The suite is offline by construction. `tests/harness.py` raises
`RuntimeError("network disabled in tests")` at three layers, installed in `pytest_configure`
so it predates collection and every fixture scope. The hooks live in `harness.py` and are
re-exported by the **rootdir** `conftest.py`, because a conftest under `tests/` is loaded
only when a named path leads into `tests/` — `pytest src/` skipped the whole block, silently,
and C7 is a promise about the suite rather than about one directory:

| layer | why it is separate |
|---|---|
| `httpx` transports | T-1's connectors and `http/client.py` |
| `httpx2` transports | `anthropic` and `starlette.testclient` run on httpx2, a **different distribution** — `httpx.HTTPTransport is httpx2.HTTPTransport` is `False`, so patching one does nothing to the other |
| `socket.socket.connect` (AF_INET/AF_INET6) | the floor under `urllib`, `requests` and any vendored SDK; SPEC C7 says *no test may hit the network*, not "no httpx test" |

A supplied `MockTransport` (either stack) still works — the patch is at the transport, which
is the network boundary. `AF_UNIX` is left alone. Opt out with `@pytest.mark.network`.

```bash
uv run pytest -q                 # whole suite — the only evidence the REPO is green
uv run pytest --ticket T-0 -q    # only the tests attributed to ticket T-0
uv run ruff check src tests
```

Every test module carries `pytestmark = pytest.mark.ticket("T-N")`; `--ticket T-N` deselects
everything else, unmarked tests included. A blank `--ticket ""` is a `UsageError`, never
"run everything", and a misspelled marker is an error (`--strict-markers`) rather than a
silent deselection.

> **`--ticket T-N` green is not repo green.** A ticket's own gate cannot see a regression it
> caused in T-0's shared primitives (`util`, `contracts`, `config`) — those tests are
> deselected. Close every ticket with `pytest --ticket T-N && pytest -q`.

Test helpers live in `tests/doubles.py` and are imported as a top-level module
(`from doubles import LLMDouble`) because `tests/` is not a package.

### Conformance: `assert_conforms`, not `isinstance`

`Connector` and `LLMClient` are `runtime_checkable`, and `isinstance` against a
runtime-checkable Protocol checks only that attributes with the right **names** exist — a
class whose whole implementation is `def structured(self): return "not a BaseModel"` passes.
`issubclass` is not an option for `Connector` either: its `kind` data member makes
`issubclass` raise `TypeError`. So the `conforms_to` test each ticket owes is written as:

```python
from doubles import assert_conforms
assert_conforms(AnthropicClient(settings), LLMClient)   # TypeError listing every mismatch
assert_conforms(GithubConnector(...), Connector)
```

It compares `inspect.signature` (names, kinds, defaults, annotations, return type),
async-ness, and — for `Connector.kind` — that the value is a real `SourceKind`.

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
data/dossiers/{id}.json    # THE committed corpus — one Dossier per roster person
data/docs/{doc_id}.json    # the RawDoc every displayed quote is checked against
render.yaml                # the Render blueprint: one free web service, no database
```

## Committed dossiers, and the check that keeps them honest

There is no database. The corpus is JSON in git: one `Dossier` per person at
`data/dossiers/{person_id}.json`, and the source document behind every citation at
`data/docs/{doc_id}.json`, keyed by `sha1(url)[:16]` (`util.doc_id`).

`tests/test_t9_committed_dossiers.py` validates whatever is committed there:

```bash
uv run pytest tests/test_t9_committed_dossiers.py -q -rs
```

It loads each file as a `contracts.Dossier`, checks the identity invariants (the filename
is the `person_id`, the resolution agrees, hubs cite facts that exist), and then — for
every fact `taste.is_displayable` says may reach a screen — re-runs the citation check the
product rests on: `normalize_ws(quote)` must be a substring of `normalize_ws(doc.text)` for
the RawDoc it names, with the url, source kind and `doc_id` matching that document too.
Excluded, low-confidence and never-displayable-kind facts are skipped deliberately: they
never reach a screen, so C8 says nothing about their quotes.

**On an empty `data/dossiers/` it SKIPS, loudly, and never passes.** Building the corpus is
a human gate — the live-network build of the ten real people plus a fact-by-fact review of
every displayed fact at its source URL — and a validator that went green on an empty
directory would let that gate be skipped in silence. A skip here means *nothing was
checked*, not *everything was fine*.

## Hours log

Per-ticket hours are logged in [`HOURS.md`](HOURS.md) — one row appended as each ticket
closes, which is why that file, not this section, is the source of truth while the build is
running; the totalled table is folded in here at submission.

Snapshot at the time this section was written: **1.5 h** across one closed ticket (T-0 —
contracts, util, config, the CLI skeleton, the ticket-selecting hard-offline test harness,
the doubles, and four designed fixture dossiers).

## Deploy URL

**Live URL:** _TBD — filled in when the Render deploy runs. That deploy is a human gate; the
blueprint and start command it needs are committed and checked._

The app deploys to **Render** from [`render.yaml`](render.yaml): one free-tier web service,
no database, booting straight from the dossier JSON committed in this repo. Point Render at
the repository (Dashboard → New → Blueprint) and it reads the blueprint.

| | |
|---|---|
| Build | `pip install uv && uv sync --frozen --no-dev` |
| Start | `uv run --no-sync uvicorn arrival.web.app:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /building` |
| Corpus | `data/dossiers/` — `DOSSIER_DIR` is left unset on purpose (see below) |
| Secrets | `ANTHROPIC_API_KEY`, `CONTACT_EMAIL` — `sync: false`, set in the dashboard |

`DOSSIER_DIR` is deliberately absent from the blueprint. `Settings.dossier_dir` defaults to
`<repo>/data/dossiers` resolved from `arrival/config.py`'s own `__file__`, and `uv sync`
installs the project editable, so the default is right wherever Render checks the repo out.
A hardcoded absolute path would be right only until that path changed — and a `DOSSIER_DIR`
pointing somewhere that does not exist does not fail the boot, it serves an **empty
building**, which is the failure mode that looks exactly like a working demo. Set it only to
point a running instance at a different corpus.

`$PORT` is Render's, not ours: a service that binds a fixed port never passes the health
check and the deploy hangs "in progress" forever. One worker is deliberate — presence and
issued digests live in process memory, so a second worker would answer `/building` from a
different building. If `uv` is ever unavailable on the instance, `pip install .` plus
`uvicorn arrival.web.app:app --host 0.0.0.0 --port $PORT` is an equivalent pair; every
runtime dependency is pinned with `==` in `pyproject.toml`.

### Warm it up before the demo — the free tier sleeps

Render's free web services **spin down after roughly 15 minutes without traffic**, and the
next request pays a **cold start**: about 30–60 seconds to reboot the container and re-read
the corpus at import. A demo that opens on a sleeping instance looks broken, because the
first click simply hangs with no feedback.

So wake it a few minutes before presenting, and keep it awake:

```bash
# one wake-up call, timed
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://<your-app>.onrender.com/building

# keep it warm while you set up
while :; do curl -s -o /dev/null https://<your-app>.onrender.com/building; sleep 240; done
```

The first response is the slow one; everything after it is warm. Note that a restart empties
the building — dossiers are on disk, presence is not — so re-`POST /arrive` after any wake-up
before demoing.

### A bad committed dossier fails the whole import, on purpose

`arrival/web/app.py` ends with `app = create_app()`, so **the corpus is read at import
time**. A file in `data/dossiers/` that does not validate raises `DossierLoadError` naming
the offending path — and because that happens at import, it takes down `import
arrival.web.app` everywhere: Render refuses to boot the service, and `pytest` reports
collection errors across `tests/web/` rather than one tidy red test. That is the design
(T-8 acceptance 1): a corpus that silently drops a person is worse than a deploy that will
not start. When a deploy dies on boot, read the path in the traceback — it names the file to
fix — and run `uv run pytest tests/test_t9_committed_dossiers.py -q` before pushing again.

## Exclusion policy

Verbatim `arrival.taste.EXCLUSION_POLICY` — the same paragraph rendered under every digest
(R13), so what a member is told and what this README says cannot drift apart:

> This digest deliberately withholds six kinds of information about a member, however
> easily a public source gives them up: their home address, property records or where they
> live; their family, spouse, children or personal relationships; their health and medical
> history; their litigation, criminal, divorce or other personal court records; their net
> worth, compensation, salary or personal wealth; and their political donations, party
> affiliations or campaign giving. The line is who the fact is about and who made it
> public: a member's own published professional work stays in even when its topic is
> sensitive, and a company's business events are the company's, not the member's. Anything
> the sources leave genuinely unresolved is withheld rather than guessed.

Withheld facts are not deleted. They are kept in the dossier with `excluded=True` and an
`exclusion_reason`, and are visible only on the operator-only `/debug/{person_id}` view,
which is 404 unless `DEBUG_VIEWS` is on. Displayability is decided in exactly one place —
`taste.is_displayable` — over three independent clauses: the fact survived the taste
filter, its provenance confidence is at least 0.7, and its source kind is on
`DISPLAYABLE_KINDS`. The complement, `NEVER_DISPLAYABLE_KINDS`, is derived by subtraction
from the contract, so a source kind added without a display ruling lands on the safe side
by construction.

## What I'd build next, with a month and real data

**Close the loop with the hosts.** The single highest-value missing signal is whether an
introduction actually happened. A one-tap "they met / they didn't" on the digest turns
every arrival into a labelled example, and the matching score stops being a hand-tuned
IDF × recency × type-boost product and starts being fitted to the only outcome the club
cares about. Nothing else on this list beats having ground truth.

**Then, in rough order of value per hour:**

- **Freshness as a first-class property.** Dossiers are built once and committed; a month
  in, "current work" is a claim about the past. A nightly incremental re-crawl of only the
  cheap, high-churn sources (GitHub, HN, the member's own site via `If-Modified-Since`) with
  a per-fact `last_seen`, and a visible staleness marker on anything older than ~60 days.
- **The decoy problem at scale.** The resolver is graded today against one same-name decoy.
  With real people it needs an actively adversarial evaluation set — common names, married
  names, people who share an employer — and a calibrated "unresolved" that shows a member
  with no dossier rather than the wrong one. Being confidently wrong about who someone is
  is the failure mode that ends the product.
- **Second-order matches.** The graph joins people who share a hub. It cannot yet say "you
  should meet Dana, because you both know Priya" — a two-hop path with a much steeper decay
  and a hard cap on how much of a digest may come from it.
- **A taste corpus that grows.** Every fact a host reports as creepy or wrong becomes a case
  in `taste_cases.yaml`, and the rule layer is re-measured against the whole set on every
  change. The fixture set is the asset; the classifier is replaceable.
- **Observability worth the name.** Per-build `zero_result_sources` are already reported;
  what is missing is the trend. A connector that quietly stops returning results is
  indistinguishable from a person with a small footprint until you can see the rate move.
- **Operationally:** dossiers move out of git once the corpus outgrows hand review, `/arrive`
  gets an HMAC on the webhook, and presence moves to Redis so more than one worker can serve
  the same building.

## Open questions (SPEC Q1–Q5)

SPEC resolved five hidden assumptions by default. Here is what was actually built against
each, and where the answer bites.

**Q1 — Roster identity keys.** `person_id = slug(name)`, with `slug(details[0])` appended
only on a name collision; the ten roster people have no duplicate names, so no suffix is in
use. `slug()` has exactly one implementation (`arrival/util.py`) on purpose: two spellings
of a slug means two spellings of every `hub_id`, and the graph silently stops joining people
who should join. `tests/test_t9_committed_dossiers.py` enforces the key on the committed
corpus. Two notes. The four synthetic unit fixtures in `tests/fixtures/dossiers/` are named
`alpha`…`delta` and deliberately *violate* the rule, because a dozen ticket criteria name
them that way — do not infer the convention from them. And the ten are NY / Boulder /
Philadelphia / SF / Sydney, so city is a resolver disambiguator, not a shared hub; the
known same-name decoy (Nabeel Qureshi the writer/researcher vs. the author who died in
2017) is a required resolver case rather than a hypothetical.

**Q2 — Search provider.** Tavily when `TAVILY_API_KEY` is set, **DuckDuckGo-lite**
(`html.duckduckgo.com`, no account, no key) when it is not. Both live behind one connector
with the fallback inside it, so the pipeline does not change shape when the key is missing —
only coverage drops. Creating the Tavily account is the operator's call and is required by
nothing here.

**Q3 — Fast refresh (GitHub + news) at arrival time.** **Cut, as the spec planned.** It was
the documented first thing to drop and it is not in `arrival/research.py`. Arrival stays a
pure read of the committed corpus, which is what actually holds the latency bound: the
arrival path never researches, it only renders.

**Q4 — FEC and CourtListener connectors.** **Not built.** R11 excludes everything they
return from display anyway, so building them would have served only the debug demo of "we
found it and withheld it". USPTO, YouTube and Podcast Index are likewise not built — low
yield for a roster of investors and writers. The same judgment is shown more cheaply: the
demo pairs a Wayback/990 find that is *kept* with a home-or-family fact that is *withheld*,
reason attached, on `/debug`.

**Q5 — "Say out loud" generation.** One LLM call at arrival time with a cached prefix, and
a hard template fallback: with no `ANTHROPIC_API_KEY`, on an `LLMError`, or when the
generated line fails the speakability check, the digest falls back to the highest-confidence
hook fact phrased by template (`digest._fallback_opener`). So the demo runs with no key at
all — but see the defect note below before demoing that path.

> **Known defect on the no-key path (tracked separately as T-029).** The template is
> `"Ask about {text}"` and it splices the fact sentence in verbatim, so a fact that begins
> "Argues that…" renders as *"Ask about Argues that developer-tools pricing should be
> published…"* — a fragment, read aloud, in the demo's default configuration. Set
> `ANTHROPIC_API_KEY` and the generated line is used instead and the fallback never
> appears. The fix belongs to that ticket, not here.
