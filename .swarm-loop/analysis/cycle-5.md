# Cycle 5/34 analysis

Targets met: 12/12

Harness integrity at analysis time: **intact** — every frozen file re-hashed against `.swarm-loop/manifest.json`, which is itself reconciled against the append-only `.swarm-loop/freeze-log.jsonl`.

> **SELECTION TRACKING IS UNAVAILABLE for 12 metric(s): `acceptance_pass_rate`, `acceptance_collected`, `build_succeeds`, `lint_errors`, `criteria_t1`, `criteria_t2`, `criteria_t3`, `criteria_t4`, `criteria_t5`, `criteria_t6`, `criteria_t7`, `criteria_t8`.** Read every 'no selection regression' above as NOT MEASURED, never as clean.
> `measure` records the selected/deselected columns only when the metric command's own stdout is PYTEST-SHAPED, and a correctly-authored frozen wrapper prints a BARE NUMBER as its last stdout line — so the tokens are hidden and both columns are written empty. Measured on a real frozen cycle-0 baseline: all 12 metrics blank, so the regression comparison can never fire for them at any cycle.
> This is not the same as `0 deselected`: a missing token means zero and IS a baseline; an un-parseable output means UNKNOWN and is not. The deselection backstop is the only one of the three enforcement points that can see a filter added after the freeze — this run has two. Restore it by having the wrapper report selection out of band (a sidecar beside its metric log, or stderr) rather than by printing raw pytest output to stdout, which would break the bare-number contract every metric depends on.

> **GOALPOSTS MOVED 3x — this trend is not against a single fixed target.** Last amendment 2026-09-04T00:23:43: 'Re-derive the two targets the ESC-006 amendment moved: acceptance_collected 120 -> 122 and criteria_t6 10 -> 12, both measured against the amended suite rather than assumed.'. Full record: `.swarm-loop/freeze-log.jsonl`.

| metric | verdict | as of | value | target | error | slope/cycle | proj. final error |
|---|---|---|---|---|---|---|---|
| acceptance_pass_rate | at_target | cycle 5 | 100 | 100 | 0 | – | – |
| acceptance_collected | at_target | cycle 5 | 122 | 122 | 0 | – | – |
| build_succeeds | at_target | cycle 5 | 1 | 1 | 0 | – | – |
| lint_errors | at_target | cycle 5 | 0 | 0 | 0 | – | – |
| criteria_t1 | at_target | cycle 5 | 19 | 19 | 0 | – | – |
| criteria_t2 | at_target | cycle 5 | 13 | 13 | 0 | – | – |
| criteria_t3 | at_target | cycle 5 | 10 | 10 | 0 | – | – |
| criteria_t4 | at_target | cycle 5 | 14 | 14 | 0 | – | – |
| criteria_t5 | at_target | cycle 5 | 8 | 8 | 0 | – | – |
| criteria_t6 | at_target | cycle 5 | 12 | 12 | 0 | – | – |
| criteria_t7 | at_target | cycle 5 | 10 | 10 | 0 | – | – |
| criteria_t8 | at_target | cycle 5 | 14 | 14 | 0 | – | – |

**RE-MEASURE OWED — cycle(s) 3. A `freeze --amend` moved at least one target and the baseline it moved has not been re-measured since. All-targets-met is WITHHELD until 'measure --cycle <n>' has run for each: an amendment invalidates its own baseline, and a hand-entered 'record' does not satisfy it.**

_Note: the stored `error` column disagreed with the current target for acceptance_collected, criteria_t6; every error above is recomputed from `value` against the target in force now, never read from history._

