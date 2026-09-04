# Intake report — Arrival Engine build-docs doc set

Skill commit this was written against: see the run's final report; regenerate this stamp
with `git -C ~/.claude/skills/swarm-loop rev-parse --short HEAD`.

The doc set is high quality and the planning half is genuinely done: SPEC pins R1–R18 /
C1–C8 / S1–S8, DESIGN pins the `contracts.py` interfaces verbatim and records twelve
decisions with rationale, TASKS gives ten tickets with acceptance criteria, verify
commands, scopes and a dependency graph, and EXECUTION reads almost exactly like this
skill's own orchestration protocol. **Nothing below is a complaint about the plan.** These
are the properties of the ticket *set as a whole*, and of the ticket set against a real
tree and a real harness, which a planning pass structurally cannot see.

Precedence applied throughout: the user's words → this skill's orchestration protocol →
`EXECUTION.md` where compatible → the doc set for *what* to build.

---

## A. The SPEC "Open questions" gate — judged, not skipped

The intake rule is that a non-empty *Open questions* section stops everything until the
user answers. SPEC §"Open questions" is non-empty — Q1–Q5 — **but every one of them
carries an explicit resolution** ("**Resolved.**", "**Default: create the Tavily
account.**", "**Default accepted**"), and the section header itself reads *"resolved by
default; override before T-1 starts"*. The planning skill did not leave these open; it
closed them and recorded the defaults.

So the gate is satisfied, with one carve-out that is not a question about *what to build*
but about *who does an action*: **Q2 requires a human to create a Tavily account.** That
is filed as **ESC-002** rather than assumed, because a default the loop cannot execute is
not a resolved question from the loop's point of view.

Q1's decoy (Nabeel Qureshi, writer/researcher, vs. the late author of the same name) is
carried into the frozen resolver corpus as a **fictional analogue** — SPEC's non-goals
forbid research on the ten real subjects outside the T-9 build run, and forbid real people
in fixtures.

---

## B. Divergences this intake introduced

Each of these changes something the doc set specified. They are listed so the checkpoint
approves them explicitly rather than discovering them at cycle 4.

### B1 — The graded taste fixture moved out of T-4's own scope · **the most important one**

`TASKS.md` T-4 grades `apply_taste` against `tests/fixtures/taste_cases.yaml`, and the same
file is listed in T-4's `Scope`. **The gradee can therefore write its own answer key.** It
needs no bad intent: a worker implements the filter, runs it, sees a disagreement, and
"fixes the fixture" — and the metric that is supposed to be external ground truth becomes a
transcript of whatever the implementation does. DESIGN says as much itself ("external
ground truth, not the filter's own table") and then puts the file inside the ticket.

**Resolution.** The **grading** corpus is
`.swarm-loop/acceptance/fixtures/taste_cases_frozen.yaml` — orchestrator-authored, inside
the frozen manifest, inside no ticket's scope, and vetoed by `check-branch` if a lane
touches it. T-4 still ships `tests/fixtures/taste_cases.yaml` for its own unit tests and as
the human-approval artifact TASKS.md asks for. The frozen T-4 tests are forbidden to read
anything under `tests/`.

The same reasoning moved **T-2's `resolve_cases/`** (also inside T-2's own scope) and the
**matching fixtures** T-5 is graded on. The frozen corpus is independent of T-0's
`tests/fixtures/dossiers/` — different people, different hubs — so T-5 cannot pass by
hard-coding to a fixture any ticket can edit.

### B2 — Every ticket's `verify` could not go red as written

`red-check` requires a ticket's verify to FAIL, with tests actually selected, in a tree
without that ticket's work. The doc set's verify is `pytest --ticket T-N && ruff check …`,
and DESIGN's discrimination rule argues it goes red on `ImportError`. It does not: **the
ticket's own test module is inside the ticket's own scope**, so before the ticket is built
there is no test to import anything, `pytest --ticket T-N` selects zero tests and exits 5,
and `red-check --close` scores that "collected NONE" and refuses to dispatch.

**Resolution.** Every ticket's verify now runs the **frozen acceptance selection first**:

```
uv run pytest .swarm-loop/acceptance -q -o addopts= -p no:cacheprovider \
    --confcutdir=.swarm-loop/acceptance --rootdir=.swarm-loop/acceptance -m tN \
 && uv run pytest --ticket T-N -q \
 && uv run ruff check <the ticket's paths>
```

The frozen tests exist from cycle 0, import their product module lazily inside the test
body, and therefore fail individually with `ModuleNotFoundError` — a real red with real
tests selected. Then the ticket's own suite, then lint. T-0 keeps the doc set's original
form and is the sole `bootstrap: true` exemption.

*Probed before freezing, against a deliberately hostile tree:* a root `conftest.py` whose
`pytest_collection_modifyitems` cleared every item, plus a `pyproject.toml` carrying
`addopts = "--ignore=acc"`. The hermetic invocation above collected and ran all three
probe tests anyway; marker selection and `not guard` exclusion both behaved. That is the
one measurement standing behind every ticket gate in this run.

### B3 — `HOURS.md` is orchestrator-owned

`EXECUTION.md` §8 declares `HOURS.md` append-only and **exempt from per-file ownership
checks**, with every ticket appending a line as it closes. Under a swarm that is a shared
write surface every concurrent lane touches — the exact collision the per-file predicate
exists to prevent, colliding on every wave rather than occasionally.

**Resolution.** No worker writes it; it is in no ticket's `owns`. The orchestrator appends
one line per ticket at merge time. **And it records agent wall-clock, labelled as such** —
R16/S8 want an hours log, and a swarm's elapsed time is not a human's hours. Reporting
agent minutes as if they were engineering hours would be the dishonest reading of a
criterion the client scores.

### B4 — `parallel_safe` is recorded, never dispatched on

`TASKS.md` and `EXECUTION.md` §3 both mention parallel-safety, and EXECUTION §3 already
says the right thing ("Dispatch on per-file ownership, not on the advisory flag"). Recorded
here so nobody re-litigates it: the dispatch predicate is the per-file `owns` map, checked
by `swarmloop.py check-wave`. Verified mechanically for the opening frontier —
`check-wave T-1 T-2 T-3 T-4 T-5` exits 0 and reports `owns(owns)`, meaning the per-file map
is in use rather than the coarse glob fallback.

### B5 — T-9 carries no metric

T-9's acceptance is four human gates (live network build on ten real people, fact-by-fact
review at source URLs, Render deploy, live URL at submission). A frozen metric for it would
sit at zero for the whole run and trip the goal-progress kill switch on a goal the swarm is
not permitted to move. Its **code artifacts** (`render.yaml`, the committed-dossier
validator, the README) are still graded — they are frozen tests inside
`acceptance_pass_rate`. The human half is **ESC-001**, and S7/S8 are tracked backlog notes
the final report must report on explicitly.

### B6 — `topic:ai` cannot be a hub · a genuine internal inconsistency in the doc set

`TASKS.md` T-0 criterion 6 requires the fixture dossiers to share the generic hubs
`city:austin` **and `topic:ai`**. DESIGN Decision 3's stop-hub list is
`{texas, startup, founder, ai, technology, business, ceo, investor}` after lowercasing —
so **a correct extractor can never emit `topic:ai`**, and a grading fixture must not depend
on a hub the pipeline is forbidden to produce. It is harmless inside T-0's own fixtures
(matching does not re-apply the stop list), which is presumably why it survived planning.

**Resolution.** T-0's fixtures keep the doc set's wording; the **frozen** corpus uses
`city:austin` + `topic:remote-work`, neither of which is a stop-hub. The frozen T-3 test
uses "AI", "Startup", "Texas" and "founder" as the stop-hub negative case — with a
non-stop label in the same batch as the positive control, so an extractor that drops
everything does not pass.

### B7 — Metric hermeticity: two config surfaces closed before the freeze

A metric whose result depends on any file outside the frozen manifest is not a frozen
metric, and this project has two such surfaces:

* **pytest** reads `addopts`, `testpaths` and the root `conftest.py` from
  worker-writable files. Closed by the explicit frozen path, `-o addopts=`, `--confcutdir`
  and `--rootdir`, all inside the frozen `run.py` (probed in B2).
* **ruff** reads `[tool.ruff]` — including `per-file-ignores` and `exclude` — out of
  `pyproject.toml`, which no protected path covers and which no `*test*` diff glob would
  ever match. Closed by a frozen `.swarm-loop/acceptance/ruff.toml` plus
  `ruff check src tests --config <that file>`, and by explicit `src tests` paths rather
  than a root walk.

**Stated as partial, and it stays partial.** `verify` answers *did the frozen bytes move*,
which is narrower than *can this measurement be gamed*. Product code imported by the frozen
suite runs in the scorer's own process and can tamper with in-process state; that is
inherent to any in-process black-box suite and is moved, not removed, by subprocess
isolation this design does not pay for. Every cycle report says PARTIAL and names this
residual, because a harness described as sealed stops being audited.

### B8 — A defect found in the orchestrator's own runner, before it could cost anything

`run.py --collected-count` passed `-q` twice (once from the shared argv builder, once from
the collect-only call). Two `-q` make pytest `-qq`, which switches `--collect-only` from
node ids to a compact `<file>: <count>` line containing no `::` at all — so the node-id
counter would have read **0** on a perfectly healthy suite and `_die()`d the measurement
every cycle. Found by running the invocation rather than reading it. Fixed, with the
reason recorded at the call site.

---

## C. The four intake gates

* **Gate A — `[verified]` must mean EXECUTED.** DESIGN's Decision 3 is the one decision
  carrying executable artifacts, and it is honestly tagged: the IDF/REF arithmetic is
  `[executed: python3.12 networkx 3.6.1 …]` and `[executed: math.log(10/3)*1.5 = 1.806]`,
  while Decision 10 (the `--ticket` selector) is explicitly `[reasoned — not executed]`.
  The doc set names the un-executed one and makes executing it T-0's job — which is the
  correct handling and rare. Both are re-executed at intake rather than quoted: the hub
  arithmetic is recomputed from the frozen fixtures as committed
  (`.swarm-loop/acceptance/fixtures/CORPUS-PROOF.md`), and the selector is proven by
  execution by a dedicated adversarial lens on T-0, including that it deselects an
  **unmarked** test and not merely a wrongly-marked one.

* **Gate B — cross-ticket `reads` closure.** `reads` is populated on every ticket from the
  doc set's own `Reads` lines. `swarmloop.py reads` is run before `freeze`, and `freeze`
  refuses on a read violation, so this is mechanical rather than advisory. The frozen tests
  were briefed to import only within each ticket's dependency closure and never from
  `tests/` at all.

* **Gate C — shared primitives invented twice.** The doc set already does this well:
  DESIGN names `contracts.py` and `util.py` as shared, EXECUTION §4 says a ticket that
  finds itself writing a slug/whitespace/hash helper must import instead, and T-1's
  non-goals repeat it. The residual risk is not a *primitive* but a *spec*: no RFC-8785-class
  wire format is in play here, so the classic three-canonicalizers failure has no purchase.
  What is in play is the `SourceKind` literal and `config.Settings` — two surfaces three
  tickets each need and none may widen. Both are recorded as scheduling constraints, and
  the frozen T-4 test asserts `DISPLAYABLE_KINDS` **set equality** against the DESIGN
  whitelist so a widening is caught rather than absorbed.

* **Gate D — conform-or-replace.** Two `conforms_to` obligations exist and both get a
  frozen conformance test rather than packet prose: T-1's connectors satisfy
  `contracts.Connector`, and T-2's `AnthropicClient` satisfies `contracts.LLMClient` **and**
  matches the `LLMDouble` the other tickets script. Prose in a packet is read once; a
  frozen test is checked every cycle.

---

## D. Blast radius beyond the globs

Recorded as scheduling constraints in `.swarm-loop/backlog.md` §Scheduling constraints:
`__main__.py` (T-0 → T-6), `README.md` (T-0 → T-9), the `tests/fixtures/http/` filename
split (T-0 vs T-1), the `SourceKind` literal, and `config.Settings`. Every one of them is
a T-0 → later-ticket handoff rather than a concurrent collision, because T-0 closes before
any dispatch — which is precisely why the scaffold is orchestrator-built and off the
frontier.

## E. What was NOT changed

The ticket decomposition, the dependency edges, the acceptance criteria, the cut list, the
architecture, and every design decision stand as written. No ticket was split or merged:
all ten pass the atomicity check (one objective, a done-condition tied to named tests, one
file cluster). The ~14 h budget and the Friday date in `SPEC.md`/`EXECUTION.md` §8 are
**context, not constraints** — scope here is set by what the work requires, and the cut
list is recorded in `tickets.json` for the user to invoke, not applied pre-emptively by the
loop.
