# Cycle 2/34 analysis

Targets met: 8/12

Harness integrity at analysis time: **intact** — every frozen file re-hashed against `.swarm-loop/manifest.json`, which is itself reconciled against the append-only `.swarm-loop/freeze-log.jsonl`.

| metric | verdict | as of | value | target | error | slope/cycle | proj. final error |
|---|---|---|---|---|---|---|---|
| criteria_t8 | stalled | cycle 2 | 0 | 14 | 14 | 0 | 14 |
| criteria_t6 | stalled | cycle 2 | 0 | 10 | 10 | 0 | 10 |
| criteria_t7 | stalled | cycle 2 | 0 | 10 | 10 | 0 | 10 |
| acceptance_pass_rate | converging_on_track | cycle 2 | 66.39 | 100 | 33.61 | -26.89 | 0 |
| acceptance_collected | at_target | cycle 2 | 120 | 120 | 0 | – | – |
| build_succeeds | at_target | cycle 2 | 1 | 1 | 0 | – | – |
| lint_errors | at_target | cycle 2 | 0 | 0 | 0 | – | – |
| criteria_t1 | at_target | cycle 2 | 19 | 19 | 0 | – | – |
| criteria_t2 | at_target | cycle 2 | 13 | 13 | 0 | – | – |
| criteria_t3 | at_target | cycle 2 | 10 | 10 | 0 | – | – |
| criteria_t4 | at_target | cycle 2 | 14 | 14 | 0 | – | – |
| criteria_t5 | at_target | cycle 2 | 8 | 8 | 0 | – | – |

Priority for next cycle (worst first): criteria_t8, criteria_t6, criteria_t7
