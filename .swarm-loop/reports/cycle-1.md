# Swarm-loop epoch report — cycle 1

## Outcome

Targets met 5/12. Two tickets (T-3, T-5) merged clean this epoch. Three more
(T-1, T-2, T-4) are in flight on their own branches, salvage-committed after a
session token cap killed the epoch mid-build, and already measured passing
their full frozen target in-worktree — they were re-dispatched to finish, not
rebuild. Three (T-6, T-7, T-8) remain correctly undispatched, blocked on
upstream tickets. Zero open escalations. Harness integrity intact at both
measure and analyze time.

## What landed

- **T-3** — the extractor: cited facts, canonical hubs, mechanical citation
  check, recency bands.
- **T-5** — matching: IDF-weighted shared hubs with exposed score components
  and a graph path.

Both merged `--no-ff` (`5440169`, `0a28926`). Both purely additive: 11 files,
2887 insertions, **zero deletions** across the combined range — the
removed-lines audit was clean by construction, nothing to hunt for there.

## Metrics (cycle 0 -> cycle 1)

| metric | value | target | verdict |
|---|---|---|---|
| acceptance_pass_rate | 12.61 -> 27.73 | 100 | converging_on_track, slope -15.12 error/cycle |
| acceptance_collected | 120 | 120 | at_target |
| build_succeeds | 1 | 1 | at_target |
| lint_errors | 0 | 0 | at_target |
| criteria_t3 | 0 -> 10 | 10 | at_target |
| criteria_t5 | 0 -> 8 | 8 | at_target |
| criteria_t1, t2, t4, t6, t7, t8 | 0 | — | stalled (see below) |

Project test suite: 248 -> 343 passed. Tracked files: 130. Codebase stall
0/3, goal-progress stall 0/3.

## How to read the stalled verdicts

`analyze` marks criteria_t1, t2, t4, t6, t7, t8 as `stalled`. **This is an
epoch-boundary artifact, not a product signal — do not diff-hunt a cause.**

- **t1, t2, t4** have work committed on `task/T-1`, `task/T-2`, `task/T-4`
  but not yet merged to main, so the frozen suite on main sees zero. Measured
  directly in their own worktrees, all three already pass their full frozen
  target (19/19, 13/13, 14/14 respectively).
- **t6, t7, t8** are blocked tickets that have never been dispatched: T-6
  waits on T-1/T-2/T-3/T-4, T-7 on T-4/T-5, T-8 on T-5/T-7. Their zero is
  simply "not started yet."

## The token-cap interruption and how it was handled

A session token cap killed all five build lanes (T-1, T-2, T-3, T-4, T-5)
mid-flight this epoch. External limit, not a task fault. Handling:

1. T-3 and T-5 had already committed their work and were functionally
   complete — collected and merged as normal.
2. T-1, T-2, T-4 died with work **uncommitted** in their worktrees, where the
   next tree-touching operation would have destroyed it. The orchestrator
   salvage-committed all three on their own branches, explicitly marked as
   unverified and not inherited as complete.
3. All five lanes were then **re-measured from scratch** rather than
   believed on the salvage commit's say-so: every one already passed its
   full frozen target.
4. T-1, T-2, T-4 were re-dispatched to **finish**, not rebuild. Remaining
   gaps going into next epoch:
   - T-1: zero tests of its own — `pytest --ticket T-1` selects nothing —
     which its acceptance criteria require.
   - T-4: same gap — `pytest --ticket T-4` selects nothing.
   - T-2: ruff is not clean, so its own verify step does not pass.

## Gates run (not assumed)

For both merged tickets (T-3, T-5):

- `red-check --close` against the branch's own merge parent reported a real
  gate with no SWALLOWED / DESELECTED / MISSING finding.
- `check-branch --ticket --worktree` returned OK with every changed file
  inside the ticket's ownership map.
- Both branches carried a stale-base advisory only, because `main` had moved
  two commits ahead — both `.swarm-loop/` bookkeeping, no semantic effect.

## Defects the lanes caught in themselves

Both found and fixed before the token cap hit, by verifiers the lanes spawned
on their own work:

- **T-5**: an adversarial verifier found two order-dependence defects in its
  own matching module — a scoring function whose result depended on
  dictionary iteration order, passing every fixture assertion while giving
  different answers in production. Fixed in `b496de5` (`make build_graph
  independent of the order dossiers arrive in`).
- **T-3**: found its own ticket spec says "the same label across two docs
  yields ONE Hub" while its merge logic was also keying on hub type;
  committed the fix (`789bfc8`, ExtractionStats counters accumulate so one
  object can span a roster, plus the merge-key correction in the same
  extractor work).

## Open at close

A rung-2 adversarial verifier for the T-3+T-5 batch is running now
(read-only, dispatched concurrently with this report). **The push gate is
expected to decline until it returns — that is the gate working as
designed, not a failure to chase.**

Escalations: zero open. Three resolved earlier by the owner: taste corpus
approved as written; T-9 scoped to code artifacts only; Tavily key deferred
(proceed without it — it only bites at the T-9 live build).

## Harness hermeticity — partial, by design

`verify` answers "did the frozen bytes move" — intact at both measure and
analyze time, re-hashed against `.swarm-loop/manifest.json`, itself
reconciled against the append-only `.swarm-loop/freeze-log.jsonl`. This is
narrower than "can this measurement be gamed": product code imported by the
frozen suite runs in the scorer's own process and can tamper with in-process
state. That residual is inherent to any in-process black-box suite; it is
moved, not removed, by subprocess isolation this design does not pay for.

## Next epoch

Priority order (worst first, per analysis): criteria_t1, t4, t8, t2, t6, t7.
Concretely:

1. Land T-1 and T-4's own test coverage (`pytest --ticket T-1`/`T-4` select
   nothing today) and merge both.
2. Clean T-2's ruff findings and merge.
3. Wait for the T-3+T-5 rung-2 verifier result before trusting that batch
   past the push gate.
4. Once T-1/T-2/T-4 land, T-6 becomes dispatchable; once T-5 clears rung-2,
   T-7 becomes dispatchable (pending T-4 too); T-8 stays blocked until T-5
   and T-7 are both in.

## Push gate — DECLINED, and this is the gate working

`push-gate --cycle 1` scored its five mechanical conditions and refused on exactly one:

| condition | result |
|---|---|
| `verify` passed this epoch | PASS — frozen manifest re-hashed and authenticated, intact |
| `analyze` did not exit 1, no metric stale | PASS — cycle-1.json records cycle 1, harness_integrity intact |
| tracked-file count is not an unexplained drop | PASS — 115 -> 130 since cycle 0, no sharp drop |
| **every rung-2 verifier for this epoch has returned** | **FAIL — `verify-batch-1` still open** |
| everything `.swarm-loop/` holds is committed | PASS — clean at HEAD |

**Nothing is pushed this epoch.** The adversarial verifier on the T-3 + T-5 batch was
dispatched concurrently with the measurement, by design, so that verification costs no
wall-clock — and the consequence is that it was still running when the gate ran. A
verdict still outstanding is an unmet condition, not a pass: the push carries to cycle
2's gate rather than racing the verification it exists to wait for. The finding that
most often arrives late from rung 2 is resolution provenance — greens that executed some
other tree's source — which is precisely the class no per-branch check can see.

Two further conditions the command deliberately refuses to score, and which are the
operator's:
- *this epoch's integrations landed green on `main`* — they did (suite 343 passed, ruff
  clean, frozen metrics re-measured on `main` after both merges), but that ran in the
  primary checkout and left no artifact the command can read, and re-reading a green out
  of a log is not the same claim as having watched it.
- *no rung-2 finding invalidates a merged branch* — unanswerable until the verifier
  returns, which is the failing condition above.

No remote has been pushed at any point in this run. Nothing is lost by the delay: `main`
is committed locally and the next epoch's gate re-scores everything from scratch.
