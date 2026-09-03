# Arrival Engine — Build Doc Set

Read in this order. An orchestrator reads `EXECUTION.md` then pulls tickets from `TASKS.md`; it loads `SPEC.md` / `DESIGN.md` sections only as a ticket's Refs name them.

| File | Tier | What it is |
|---|---|---|
| `EXECUTION.md` | header | Rules for the orchestrator: one ticket at a time, verify-gated, parallel only on disjoint scopes, cut list discipline, hours logging. |
| `SPEC.md` | 1 — intent | Requirements R1–R18, constraints C1–C8, non-goals, success criteria S1–S8, resolved open questions Q1–Q5. |
| `DESIGN.md` | 2 — contracts | Architecture, the pinned `contracts.py` models and function signatures every ticket imports, routes, data files, decisions (executed vs reasoned), verification strategy and test-attribution convention. |
| `TASKS.md` | 3 — task graph | Ten tickets T-0…T-9 with objective, acceptance, verify command, scope, reads, provides, depends-on, non-goals; dependency graph; ordered cut list. |
| `data/roster.yaml` | input | The ten stand-ins with resolver disambiguators. Used only by T-9's build run. |
| `RESEARCH.md` | reference | The sourcing/architecture research this plan was derived from. Not loaded by agents. |

Human gates you own: approve `tests/fixtures/taste_cases.yaml` (T-4), hand-check every displayed fact against its source (T-9), create the Tavily account (Q2), confirm current Anthropic model IDs (T-2 note).

Known not-executed decision: the `pytest --ticket T-N` selector (DESIGN Decision 10). T-0 must execute and prove it before any other ticket trusts it.
