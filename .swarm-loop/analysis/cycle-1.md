# Cycle 1/34 analysis

Targets met: 5/12

Harness integrity at analysis time: **intact** — every frozen file re-hashed against `.swarm-loop/manifest.json`, which is itself reconciled against the append-only `.swarm-loop/freeze-log.jsonl`.

| metric | verdict | as of | value | target | error | slope/cycle | proj. final error |
|---|---|---|---|---|---|---|---|
| criteria_t1 | stalled | cycle 1 | 0 | 19 | 19 | 0 | 19 |
| criteria_t4 | stalled | cycle 1 | 0 | 14 | 14 | 0 | 14 |
| criteria_t8 | stalled | cycle 1 | 0 | 14 | 14 | 0 | 14 |
| criteria_t2 | stalled | cycle 1 | 0 | 13 | 13 | 0 | 13 |
| criteria_t6 | stalled | cycle 1 | 0 | 10 | 10 | 0 | 10 |
| criteria_t7 | stalled | cycle 1 | 0 | 10 | 10 | 0 | 10 |
| acceptance_pass_rate | converging_on_track | cycle 1 | 27.73 | 100 | 72.27 | -15.12 | 0 |
| acceptance_collected | at_target | cycle 1 | 120 | 120 | 0 | – | – |
| build_succeeds | at_target | cycle 1 | 1 | 1 | 0 | – | – |
| lint_errors | at_target | cycle 1 | 0 | 0 | 0 | – | – |
| criteria_t3 | at_target | cycle 1 | 10 | 10 | 0 | – | – |
| criteria_t5 | at_target | cycle 1 | 8 | 8 | 0 | – | – |

Priority for next cycle (worst first): criteria_t1, criteria_t4, criteria_t8, criteria_t2, criteria_t6, criteria_t7
