# Worker preamble — the contract every packet in this run is written against

Read this in full before you touch anything. It is mandatory reading, not background.
**Read it; never edit it.** It is orchestrator-owned state under `.swarm-loop/`, and a
branch that modifies anything there is vetoed by `check-branch` before a human reads it.

## Changelog
- **2026-09-03, epoch 0** — first version, written before the T-1..T-5 build wave.

---

## What this project is

**Arrival Engine** — a staff-facing arrival digest for a private club. A webhook fires
when a member arrives; staff get a digest naming who walked in, who else in the building
they should meet and *why*, and a short dossier with a conversation opener. It is scored
on speed-to-working, creative open-source data sourcing, signal over noise, and **taste**
— the "seen vs. dossiered" line the product dies on the wrong side of.

Stack: Python 3.12, FastAPI, httpx, Pydantic v2, NetworkX, Jinja2, the Anthropic SDK,
pytest + ruff, `uv` throughout. No database — dossiers are JSON committed to the repo.
**Tests never touch the network** (constraint C7); a three-layer block (httpx, httpx2 and
raw `socket.connect`) enforces it.

## The grader is not your green

Your ticket's `verify` command is a convenience. The thing that actually scores this build
is the **frozen acceptance suite** at `.swarm-loop/acceptance/`, which is orchestrator-owned,
outside every ticket's scope, and hash-locked. **Treat your own green as vacuous until the
frozen suite agrees.** A suite can be green while the product is fully broken; the question
to ask of every green is what it is evidence *of*.

Run the frozen tests for your ticket as often as you like:

```
uv run pytest .swarm-loop/acceptance -q -o addopts= -c .swarm-loop/acceptance/pytest.ini \
    -p no:cacheprovider --confcutdir=.swarm-loop/acceptance \
    --rootdir=.swarm-loop/acceptance -m t<N>
```

You may **never edit them**. See CONCERNS in the report format for what to do when you
believe one is wrong — that channel is real, and it is the only one you have.

## Your discretion — decide alone, do not stall

The GOALS are frozen; your ROUTE to them is not. Three tiers:

- **Tier 1 — free, no permission needed.** Your implementation approach and design inside
  your owned files, including abandoning the plan this packet sketches when evidence says
  it is wrong (the decision goes in DECISIONS, the disproved claim in PREMISES-FALSIFIED).
  New files within your scope. Adding tests and assertions — always free. Fixing any bug
  you find INSIDE your ownership even when it is not part of this task: commit that fix
  separately and list it in BUGS-OBSERVED marked `[fixed]`.
- **Tier 2 — proceed, then report.** Anything surprising that crosses no boundary: a
  convention the map got wrong, a dependency that behaves differently than described.
  Handle it; record it in DECISIONS/CONCERNS.
- **Tier 3 — stop that thread and report. The only stop tier.** Boundary crossings: files
  outside your ownership, dependency changes, anything under `.swarm-loop/`, and editing an
  existing assertion without the recorded `justify-test-edit` ritual.

Stopping is for boundaries, never for approach. A worker that stalls awaiting permission to
change its own route has misread its packet.

## Boundaries — violations cause your branch to be rejected

- **Do not modify any file outside your ownership list.** If correctness truly requires an
  outside change, STOP that thread and state exactly what change is needed and why.
- **Fix inside, report everywhere.** You may never FIX a defect outside your ownership, but
  you must ALWAYS report one you noticed, in BUGS-OBSERVED, whether or not it relates to
  your task. There is no penalty for reporting, only for silence.
- **Do not touch anything under `.swarm-loop/`** — goals, acceptance tests, state. This
  boundary has no exception and no ritual that unlocks it. It is also not a dead end: a
  frozen test you believe is WRONG (not merely hard) gets reported in CONCERNS and the
  orchestrator carries it to the human who owns the goals. You may not change it; you are
  not stuck with it. Report it and keep working the parts that do not depend on it.
- **Shared surfaces are additive-only.** Add a new file and import/register it; never
  restructure a shared file.
- **Do not add, remove, or upgrade dependencies**, and never touch `pyproject.toml`'s
  dependency list or `uv.lock`. A failing install is a report, never an install you commit.
- **Do not run deploy, publish, or release commands.**
- **NEVER run a repo-global git op.** `git stash` (any form), `git reset --hard/--merge/
  --keep`, `git checkout -- <path>`, `git checkout HEAD -- <dir>`, `git checkout .`,
  `git checkout -f`, `git switch -f`, `git restore <path>`, `git clean -f/-d/-x`. Each
  reverts or deletes uncommitted work across the ENTIRE tree, and in a swarm you are never
  alone in that tree — `refs/stash` is one ref shared by every worktree. A `stash`+`pop`
  observed live swapped two agents' trees mid-wave and nearly lost one's work.

  To compare against the committed baseline use the READ-ONLY forms:
  `git show HEAD:<file>` for the baseline, `git diff HEAD -- <file>` for your diff.
  There is exactly one worker-side restore, the redirect form:

      git show HEAD:<file> > <file>       # restores ONE path, blind overwrite

  Use it only on a file you own, inside your worktree, knowing what you discard.

  `git checkout <branch>`, `git switch <branch>`, `git merge`, `git rebase`,
  `git cherry-pick` and `git revert` are **allowed by design** — git aborts each rather
  than clobbering a modified file. Reading from git is never blocked. A `PreToolUse`
  git-guard blocks the destructive set; if you hit a block **the guard is working** —
  switch to a read-only form or report, never re-spell the command to get past it.
- **YOUR OWN SUB-AGENTS ARE READ-ONLY IN YOUR WORKTREE.** Nothing you spawn writes a file
  there. The moment a second agent holds a Write tool in your tree, your tree is a shared
  working tree and every reason the swarm gives each worker its own applies again one level
  down. Measured: a lane's sub-agents mutated the tree it was building in; one watched
  `if False:` appear in a source file between its own restore and its next read and lost an
  entire pass. An agent that must MUTATE in order to test works on a copy under **your own
  scratch subdirectory**, never inside the worktree and never anywhere under the measured
  repo. Every rule here binds at every depth of the spawn tree, and **you own what your
  sub-agents write**: their violation rejects YOUR branch.
- **TESTS ARE APPEND-ONLY.** Adding tests and assertions is free and encouraged. Modifying
  or deleting an EXISTING assertion requires the `justify-test-edit` ritual — and that
  includes every disguise: changing an expected value, widening a tolerance, relaxing a
  matcher, trimming a parametrize case, adding skip/xfail, regenerating a snapshot you have
  not read. First answer, in your report: **"would this test still fail if I reverted my
  change?"** If no, YOUR CODE IS WRONG — fix the code, not the test. If you do edit, record
  the justification (the requirement it encodes, and why it is wrong independent of your
  change) both as a comment at the assertion and in the commit body. The orchestrator reads
  the REMOVED lines of every diff; an unjustified assertion change rejects the branch even
  when every metric is green.

## Working method

**First, prove the environment is yours — before any real work, sixty seconds.**
A worktree carries only TRACKED files, so every gitignored dependency starts missing, and a
missing dependency does not always fail loudly.

1. **Print what the runtime actually loads**, for the module your ticket CHANGES and for
   every module your verify imports — never whichever package is handy:
   ```
   uv run python -c "import arrival.<yourmodule> as m, os; print(os.path.realpath(m.__file__))"
   ```
   Every path must be INSIDE your worktree. Canonicalize both sides before comparing
   (`os.path.realpath`) — a symlinked checkout makes raw strings differ for a path that is
   genuinely inside your tree. Measured on this project: from one worktree, twelve of
   thirteen packages resolved to the PRIMARY checkout while one resolved locally, so a
   spot-check that happens to pick the lucky one passes while the module you were sent to
   change is main's.
2. **Flip a value in a file you own** — a return value, a constant — re-run your verify, and
   **confirm the result moves.** Restore the flip.

If it does not move, your commands are resolving outside your worktree: report it as a
blocked environment, do not work around it. Nothing you measure before this check passes is
about your code.

**This project's specific trap, already measured — expect it.** On macOS `uv venv` sets the
`UF_HIDDEN` flag on the whole `.venv` tree, and CPython >= 3.12.6's `site.addpackage`
silently SKIPS hidden `.pth` files. So a clean `uv sync` exits 0, writes a correct editable
install, and leaves `import arrival` raising `ModuleNotFoundError` with no diagnostic from
uv, site, or pip. Your worktree has already been provisioned and unhidden for you. If you
ever re-run `uv sync` and imports break, the repair is:

    chflags -R nohidden .venv

and then **assert the effect** by importing the module and printing its resolved path.
Never trust the installer's exit code.

- **Write every command relative to the worktree root.** Never an absolute path into the
  primary checkout's `.venv/bin/python`, and never with an inherited `VIRTUAL_ENV` or
  `PYTHONPATH` pointing there.
- **Never commit a provisioned directory** (`.venv/`, `__pycache__/`, `dist/`) or a
  lockfile. `git add -A` after provisioning is exactly how those land on a branch.
- **There is no code index in your worktree.** `.codegraph/` is gitignored, so it is absent
  here — and a query does not fail, it walks UP and answers from an unrelated index.
  Measured on this machine: a query from a worktree resolved to an index of 7,946 files
  across nine unrelated projects, with **zero** files of this project. **Navigate with
  `git grep` and direct reads.** If you run an index CLI at all, verify every path it
  returns is inside your own tree before believing a word of it. The general rule, because
  the next such tool will not be called CodeGraph: any capability living in a gitignored
  directory is ABSENT inside a worktree, and the ones that answer anyway are the expensive
  ones.
- **A repair or provisioning step must ASSERT ITS EFFECT.** Do the thing, then OBSERVE the
  state you claimed to create. Never write `|| true` on a step whose success you will
  report, and make the step's last line a READ of the thing that exits non-zero when it is
  absent. A step that cannot fail has not passed.
- **Before you report `complete`, run the wiring check on every symbol you added:**
  ```
  python3 ~/.claude/skills/swarm-loop/scripts/swarmloop.py reachable \
      --symbol <name> [--symbol <name>...] --from <the real entry point>
  ```
  A definition with zero call sites is **unfinished work wearing a green suite** — a suite
  cannot fail over code nothing runs, which is why your own green does not settle it.
  `TESTS ONLY` is the same defect with a passing test on top. Read the verdicts, not just
  the exit code: `DECORATOR-REGISTERED` does not fail the run (it is how routes and CLI
  commands are wired) but is not an all-clear either — confirm by eye that the table is
  imported and mounted, and say so in DECISIONS. Exit 3 is the tool refusing your
  arguments, never a finding about your code.
- **The packet is a hypothesis, not a set of facts — measure a premise before you build on
  it.** Every file:line in your packet may have drifted; a named test or target may be the
  wrong one; a prescribed fix is a starting point, not a solution. This has already
  happened repeatedly in this run: one lane was told a fix that would have made its defect
  strictly worse, another was told there were nine connectors when the spec names ten, and
  a third was given hub arithmetic that was simply wrong. **All three measured the premise
  and were right to.** List every premise that did not survive contact under
  PREMISES-FALSIFIED. You are the only agent standing where this brief can be falsified,
  and a lane that merely follows its brief builds the wrong thing and reports the failure
  as its own.
- **Commit in coherent increments** with clear messages. Uncommitted work is lost work.
  The orchestrator merges branches and runs all measurement — **you do neither.**

## Standing rulings for this run

These are settled. Do not re-litigate them; build against them.

1. **`person_id == slug(person.name)`.** The frozen grading corpus satisfies this for all
   five of its people. T-0's own unit fixtures under `tests/fixtures/dossiers/` historically
   did NOT (they used the filename stem), which is a known trap being repaired separately.
   **The contract is the frozen one.**
2. **The stop-hub list matches hub LABELS, not type prefixes.** The list is
   `{texas, startup, founder, ai, technology, business, ceo, investor}` after lowercasing.
   `investor:foundry-seed-2019` is a legitimate hub and is the load-bearing rare hub the
   entire matching-score design rests on — **stripping it because the word "investor"
   appears in the stop list would silently destroy the scoring design.**
3. **Hub IDF is smoothed:** `idf = max(0, ln(N / (1 + n)))`. With N=5, a hub on 2 people
   gives `ln(5/3) = 0.5108`; a hub held by all 5 clamps to 0.
4. **TASKS T-1 names TEN connectors**, not nine: search, wikidata, wikipedia, github, edgar,
   wayback, propublica, hn, openalex, self_page.
5. **T-0 is already built and merged.** Contracts, config, util, the CLI, the test doubles
   and fixtures all exist. A frozen test that exercises only T-0 legitimately passes today;
   those carry `@pytest.mark.guard` and are excluded from scored counts.
6. **T-8's construction seam:** the app is built by `create_app(dossier_dir=None, llm=None)`
   in `arrival.web.app`, with `app = create_app()` also exposed, and the dossier directory
   named by the `DOSSIER_DIR` environment variable. The frozen harness accepts several
   spellings, but a factory that can receive an injected `llm=` client is **mandatory** —
   12 of 14 T-8 criteria are unobservable without it.
7. **`assert_conforms` in `tests/doubles.py`, not `isinstance`,** is how protocol conformance
   is checked: `isinstance` on a `runtime_checkable` Protocol tests attribute presence only.

## Report format — return exactly this structure

```
STATUS: complete | partial | blocked
ACCEPTANCE: <which named tests now pass / still fail>
CHANGED: <file list>
DECISIONS: <choices another agent or the orchestrator must know about — interfaces you
  defined, assumptions you made>
NEEDS: <outside-ownership changes or dependencies you need. Also where to say it if a
  frozen test you objected to in CONCERNS is what is actually blocking you.>
CONFIG-TOUCHED: <every build- or test-config file this ticket expects to change, and WHY —
  pyproject.toml, pytest.ini, conftest.py, a Makefile, a coverage config. Name them even
  when the change is obviously innocent. check-branch tags exactly these files with an
  advisory `?` because they are the one class of edit that can move a frozen metric without
  touching a frozen byte, and it computes that tag AFTER your report exists. A `?` your
  report already explains is a five-second confirm; a `?` with nothing beside it stalls the
  merge on a question only you could have answered, and by then you are gone.
  Write "none" if none.>
BUGS-OBSERVED: <every defect you noticed ANYWHERE — in your files, in files you only read,
  in behaviour you observed — whether or not it relates to this task. Prefix [open], or
  [fixed] if you repaired it in-ownership (committed separately). Per item: file:line or a
  reproduction, the evidence, suspected cause. "none" if none.>
PREMISES-FALSIFIED: <every factual claim in YOUR PACKET you found to be false — a file:line
  that had drifted, a named test that was the wrong one, a prescribed fix that did not cover
  the reproduction. Quote the packet's claim and give what you measured instead. This is
  about the packet's assertions, not the code (BUGS-OBSERVED), not a shared document
  (SHARED-DOC-OBSERVATIONS), not a frozen goal (CONCERNS). Nothing else in this loop ever
  checks the orchestrator's own text. "none" if none.>
SHARED-DOC-OBSERVATIONS: <anything in an orchestrator-owned SHARED document that looks wrong
  or stale — backlog.md, learnings.md, the codebase map, this preamble. These go here and
  NOT in BUGS-OBSERVED, because your copy is a snapshot at your branch point and the
  orchestrator may already have fixed it on main. Quote the line and say what you expected.
  "none" if none.>
CONCERNS: <anything smelling wrong, including tests you believe are incorrect. For a FROZEN
  acceptance test you believe is wrong, make the objection actionable or it dies in this
  report instead of reaching the human who can change it. Give four things: (1) the test
  file and the exact assertion, quoted; (2) what it demands; (3) why that is impossible or
  wrong INDEPENDENT of your implementation — it contradicts stated behaviour, it contradicts
  another acceptance test, it needs a fixture that cannot exist; (4) what it should assert
  instead. "This test is hard to pass" is not an objection — that is the job. "This test is
  wrong, and here is the evidence" is one, and the orchestrator escalates it to the user.>
```
