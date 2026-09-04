# Backlog — Arrival Engine

**The authoritative ticket graph is `tickets.json` at the repo root** (build-docs intake:
the doc set's machine-readable graph owns the tickets; this file is its human view).
Seeded from `tickets.json`, which was derived verbatim from
`arrival-engine-build-docs/TASKS.md`.

Re-seed this file on **every** amendment that changes the graph. Seeding is one-way and
nothing re-seeds it automatically, so from the first un-mirrored amendment onward this
view is stale in exactly the way nobody checks.

- Tickets in graph: **10** (T-0 … T-9)
- Closed: **1** (T-0)
- Open: **9**
- Graph roots: T-0 only
- Initial ready frontier once T-0 closes: **T-1, T-2, T-3, T-4, T-5** (five-wide)

---

## Closed

### T-0 — Repo skeleton, contracts, test harness and fixtures · `bootstrap: true`
Built by the **orchestrator** in Phase 1, per protocol: the scaffold is never dispatched to
a worker and never appears on a frontier, because the cycle-0 baseline is not measurable
until it exists. Resolution commit recorded at merge.
Ships the surface every other ticket imports: `contracts.py` (all models + Protocols),
`util.py` (slug / normalize_ws / doc_id), `config.py` (the complete Settings surface),
`tests/conftest.py` (the `--ticket` selector, DESIGN Decision 10, **executed** rather than
reasoned), `tests/doubles.py`, four fixture dossiers, and the repo furniture.

---

## Open

| id | depends on | unblocks | owns (per-file dispatch predicate) | est |
|---|---|---|---|---|
| **T-1** connectors + HTTP core | T-0 | T-6 | `src/arrival/http/**`, `src/arrival/connectors/**`, `tests/connectors/**`, `tests/fixtures/http/{kind}_*.json` | 3.0 h |
| **T-2** LLM client + resolver | T-0 | T-6 | `src/arrival/llm/**`, `src/arrival/resolve.py`, `tests/llm/**`, `tests/resolve/**`, `tests/fixtures/resolve_cases/**` | 2.0 h |
| **T-3** extractor + citation check | T-0 | T-6 | `src/arrival/extract.py`, `tests/extract/**` | 2.0 h |
| **T-4** taste filter | T-0 | T-6, T-7 | `src/arrival/taste.py`, `tests/taste/**`, `tests/fixtures/taste_cases.yaml` | 1.5 h |
| **T-5** graph + matching | T-0 | T-7, T-8 | `src/arrival/graph.py`, `tests/graph/**` | 1.5 h |
| **T-6** research pipeline + build CLI | T-1, T-2, T-3, T-4 | T-9 | `src/arrival/research.py`, `src/arrival/__main__.py`, `tests/research/**`, `tests/fixtures/roster_synthetic.yaml` | 1.5 h |
| **T-7** digest builder | T-4, T-5 | T-8 | `src/arrival/digest.py`, `tests/digest/**` | 1.0 h |
| **T-8** web app + demo driver | T-5, T-7 | T-9 | `src/arrival/web/**`, `tests/web/**` | 1.5 h |
| **T-9** ship artifacts | T-6, T-8 | — | `data/**`, `render.yaml`, `README.md`, `tests/test_t9_committed_dossiers.py` | 1.0 h |

Dependency depth: T-1…T-5 at depth 1; T-6, T-7 at depth 2; T-8 at depth 3; T-9 at depth 4.
`unblocks` (transitive): T-4 → 4; T-5 → 3; T-1/T-2/T-3 → 2; T-6/T-7 → 2; T-8 → 1; T-9 → 0.

---

## Scheduling constraints

These are the collisions the `scope` globs cannot see. Each stays here until the last
ticket it names is closed.

1. **`HOURS.md` is orchestrator-owned — intake divergence from `EXECUTION.md` §8.**
   EXECUTION declares it append-only and *exempt* from per-file ownership, which makes it a
   shared write surface every ticket touches — precisely the collision the per-file
   predicate exists to prevent, and one that would collide on every wave. The orchestrator
   appends one line per ticket at merge time. **No worker writes it**, and it appears in no
   ticket's `owns`.

2. **`src/arrival/__main__.py`: T-0 (stub) → T-6 (fills `build`).** T-0 closes before any
   dispatch, so the two are never in flight together. T-6 must preserve the
   `main(argv, *, connectors=None, llm=None) -> int` signature — the frozen T-6 test calls
   it in-process with injected doubles, and a signature change breaks the offline rule C7.

3. **`README.md`: T-0 (skeleton) → T-9 (final).** Same reasoning.

4. **`tests/fixtures/http/` is split by filename prefix**: T-0 owns
   `fixture_dossier_docs_*.json`, T-1 owns `{kind}_*.json`. Disjoint, and T-0 closes first.

5. **`contracts.py` / `util.py` / `config.py` are frozen by convention for the run.**
   No ticket but T-0 may edit them. A ticket that believes a contract is wrong **escalates**
   (`swarmloop.py escalate --kind other --subject src/arrival/contracts.py`); it does not
   fork the model. Forking is how three tickets end up with three incompatible spellings of
   the same primitive.

6. **Shared surface beyond globs — the `SourceKind` literal.** Defined once in
   `contracts.py` (T-0), consumed by T-1's connector `kind` fields and by T-4's
   `DISPLAYABLE_KINDS`. Neither may widen it. The frozen T-4 test asserts set equality
   against the DESIGN whitelist so a widening is caught rather than absorbed.

7. **Shared surface beyond globs — `config.Settings`.** Must ship **complete** at T-0
   (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN`, `CONTACT_EMAIL`, `DEBUG_VIEWS`,
   both model ids, cache dir). If it does not, T-1, T-2 and T-8 each need to widen the same
   file and collide. This is why T-0's acceptance criterion 7 is not optional furniture.

8. **T-6 and T-7 are both depth-2 and their scopes are disjoint** (`research.py` +
   `__main__.py` vs `digest.py`), so they may run concurrently — but T-6 depends on
   T-1…T-4 and T-7 only on T-4 and T-5, so T-7 will usually be ready first. Dispatch on
   readiness, not on depth.

---

## Human gates (tracked, not schedulable)

The swarm cannot close these. They are surfaced as escalations rather than left to rot in
the ledger.

- **Taste fixture approval (T-4, SPEC S3).** The frozen
  `.swarm-loop/acceptance/fixtures/taste_cases_frozen.yaml` is external ground truth and
  DESIGN requires the *owner* to approve it. Presented at the plan checkpoint, which is the
  one moment in the run when the owner is present.
- **Tavily account (SPEC Q2).** Requires a human signup. Without it the search connector
  falls back to DuckDuckGo-lite and coverage drops. Does not block T-1: the connector is
  built and tested against recorded fixtures either way (C7 forbids network in tests).
- **Anthropic model-id confirmation (T-2 note, DESIGN Decision 9).** Model ids are
  settings, not constants; the defaults are recorded in `.env.example` and `config.py`.
- **T-9 live build + hand review + Render deploy (S7, S8).** No API keys, no network
  research on real people, and no human reviewer are available to the swarm. The swarm
  builds T-9's code artifacts (`render.yaml`, the committed-dossier validator, the README)
  and stops. Escalated at the checkpoint.
