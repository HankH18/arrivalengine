# Cycle 3/34 analysis

Targets met: 10/12

Harness integrity at analysis time: **intact** — every frozen file re-hashed against `.swarm-loop/manifest.json`, which is itself reconciled against the append-only `.swarm-loop/freeze-log.jsonl`.

> **SELECTION TRACKING IS UNAVAILABLE for 12 metric(s): `acceptance_pass_rate`, `acceptance_collected`, `build_succeeds`, `lint_errors`, `criteria_t1`, `criteria_t2`, `criteria_t3`, `criteria_t4`, `criteria_t5`, `criteria_t6`, `criteria_t7`, `criteria_t8`.** Read every 'no selection regression' above as NOT MEASURED, never as clean.
> `measure` records the selected/deselected columns only when the metric command's own stdout is PYTEST-SHAPED, and a correctly-authored frozen wrapper prints a BARE NUMBER as its last stdout line — so the tokens are hidden and both columns are written empty. Measured on a real frozen cycle-0 baseline: all 12 metrics blank, so the regression comparison can never fire for them at any cycle.
> This is not the same as `0 deselected`: a missing token means zero and IS a baseline; an un-parseable output means UNKNOWN and is not. The deselection backstop is the only one of the three enforcement points that can see a filter added after the freeze — this run has two. Restore it by having the wrapper report selection out of band (a sidecar beside its metric log, or stderr) rather than by printing raw pytest output to stdout, which would break the bare-number contract every metric depends on.

> **GOALPOSTS MOVED 1x — this trend is not against a single fixed target.** Last amendment 2026-09-03T23:19:36: "ESC-005 Option A, granted by the goal owner 2026-09-03. test_t2_resolver.py's sabotage companion for the namesake decoy flipped every 'no' verdict to 'yes' while KEEPING its contradicting evidence, and then required those documents to be ACCEPTED. That made a document explicitly naming a different employer AND a different city acceptable purely because the model attached 'yes' to it — contradicting DESIGN's 'negative evidence hard-rejects... a single contradiction must veto', which is a claim about the evidence rather than about which token the model emitted. It also could not distinguish 'polarity caused the rejection' from 'the contradiction caused it', because it moved the one dimension carrying both. AMENDED to flip the DISAMBIGUATOR instead: each decoy verdict becomes a 'yes' on 'role' carrying a different verbatim span from the same document, verified to assert no employer and no work location. Documents, confidences and source kinds are untouched, so the sabotage still proves the earlier rejection was not incidental. Verified: 14/14 t2 pass; and the amended test is NOT vacuous — a resolver sabotaged to drop those three doc ids from accepted_doc_ids makes it fail, with the sabotage confirmed present in the loaded source.". Full record: `.swarm-loop/freeze-log.jsonl`.

| metric | verdict | as of | value | target | error | slope/cycle | proj. final error |
|---|---|---|---|---|---|---|---|
| criteria_t8 | stalled | cycle 3 | 0 | 14 | 14 | 0 | 14 |
| acceptance_pass_rate | converging_on_track | cycle 3 | 83.19 | 100 | 16.81 | -25.04 | 0 |
| acceptance_collected | at_target | cycle 3 | 120 | 120 | 0 | – | – |
| build_succeeds | at_target | cycle 3 | 1 | 1 | 0 | – | – |
| lint_errors | at_target | cycle 3 | 0 | 0 | 0 | – | – |
| criteria_t1 | at_target | cycle 3 | 19 | 19 | 0 | – | – |
| criteria_t2 | at_target | cycle 3 | 13 | 13 | 0 | – | – |
| criteria_t3 | at_target | cycle 3 | 10 | 10 | 0 | – | – |
| criteria_t4 | at_target | cycle 3 | 14 | 14 | 0 | – | – |
| criteria_t5 | at_target | cycle 3 | 8 | 8 | 0 | – | – |
| criteria_t6 | at_target | cycle 3 | 10 | 10 | 0 | – | – |
| criteria_t7 | at_target | cycle 3 | 10 | 10 | 0 | – | – |

Priority for next cycle (worst first): criteria_t8
