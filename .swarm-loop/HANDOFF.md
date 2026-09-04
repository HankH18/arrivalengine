# HANDOFF — read this, then run `resume`

Written mid-flight against skill commit `c37a46b`. **Everything load-bearing is committed;
this file only holds what `resume` cannot reconstruct.**

## Where the run is

Cycle 0 is **frozen, measured and checkpointed**. Five build lanes are **IN FLIGHT**.

    python3 ~/.claude/skills/swarm-loop/scripts/swarmloop.py resume
    python3 ~/.claude/skills/swarm-loop/scripts/swarmloop.py escalate --open-asks   # silent = none open
    python3 ~/.claude/skills/swarm-loop/scripts/swarmloop.py dispatch --list        # the 5 lanes

Baseline (cycle 0): pass rate 12.61, collected 120, build 1, lint 0, `criteria_t1..t8`
all 0 against targets 19/13/10/14/8/10/10/14. Every scored criterion red at baseline;
the 12 green tests are T-0's contract guards, green by design and excluded from scored
counts.

## The five in-flight lanes — DO NOT RE-DISPATCH THEM

`task/T-1` … `task/T-5`, each in its own provisioned worktree under
`../arrivalengine-worktrees/<id>`, all forked from `6ab53d7d24b7`, all recorded in
`dispatch.jsonl` with their owned files. `check-wave T-1 T-2 T-3 T-4 T-5` exits 0 and
`red-check` confirmed all five gates are genuine reds before dispatch.

If a session died between dispatch and collection, the branches are still there. Check
`git log --oneline main..task/T-N` **before** assuming a lane produced nothing: a branch
with zero commits is a failed/hung task to reclaim; a branch with commits is work to
collect.

## Collecting a returning lane — the order matters

```bash
S=~/.claude/skills/swarm-loop/scripts/swarmloop.py
WT=../arrivalengine-worktrees/T-N
BASE=$(git merge-base main task/T-N)
python3 $S red-check --close --ticket T-N --base "$BASE"
python3 $S check-branch --branch task/T-N --base main --ticket T-N --worktree "$WT"
git diff -M -C --stat main...task/T-N                       # THREE dots pre-merge
git diff -M -C main...task/T-N | grep -E '^-[^-]'           # read EVERY removed line
```

Read the removed lines over the **whole** diff, not just `*test*` — a source-side
deletion is what the glob cannot see. For each one, name what now happens on the path
that line used to guard.

`check-branch` will **VETO** any branch touching `.swarm-loop/`. For a build ticket that
veto is real and final — return the branch. (The harness lanes earlier in this run were
a sanctioned pre-freeze exception; **that exception is over.** The harness is frozen now.)

Merge with `--no-ff` so `frontier --closed-from-merged` can see it, then
`dispatch --close T-N --ticket T-N --verdict accepted`.

**Write commit messages to a file and use `git commit -F <file>`.** Backticks in a
`-m "..."` argument are command substitution and silently delete the words they wrap;
this cost a merge message earlier in the run.

## After a batch merges

`measure --cycle 1` → `analyze --cycle 1` → `checkpoint --cycle 1`, one at a time,
reading each exit code — **never chained with `&&`**, which skips `checkpoint` when
`measure` exits 1. Then commit `.swarm-loop/`, then `push-gate --cycle 1`.
Do not open a measurement epoch that landed no merges: the stall counter cannot tell a
mid-flight epoch from a spinning one.

Then dispatch a **rung-2 verifier per integrated batch** (read-only, no worktree, must
not have built anything in the batch), and use the **two-dot** range for its deletion
re-read: `git diff <pre-batch-main>..main`. Three dots after the merge diffs a commit
against itself and prints nothing.

## Standing decisions made this session — do not re-litigate

1. **Three escalations are RESOLVED** (taste corpus approved as written; T-9 scope is
   code artifacts only; Tavily key deferred — proceed without it, revisit before the
   T-9 live build). `escalate --list --all` has the recorded notes.
2. **T-8's construction seam** is `create_app(dossier_dir=None, llm=None)` plus a
   module-level `app`, with `DOSSIER_DIR` naming the directory. DESIGN pins none of it;
   the owner approved it. `Settings.dossier_dir` now exists (absolute by default).
3. **The unit fixtures' `person_id` deliberately is NOT `slug(name)`.** A rename was
   tried and REVERTED — seven ticket lines that T-5 and T-8 build against name
   alpha/bravo/charlie/delta. A test now fails if anyone re-does it. The product
   invariant holds in the frozen corpus, which is what grades.
4. **Stop-hubs match hub LABELS, never type prefixes.** `investor:foundry-seed-2019` is
   legitimate and load-bearing.
5. **Ten connectors, not nine** (the tenth is `self_page`).
6. **IDF is smoothed**: `max(0, ln(N/(1+n)))`.

## Things that will bite a resumed session

- **`import arrival` breaks silently and comes back.** macOS sets `UF_HIDDEN` on `.venv`
  and CPython ≥3.12.6 skips hidden `.pth` files, so `uv sync` exits 0 and leaves the
  editable install inert. It re-hid itself twice during this session. Repair:
  `chflags -R nohidden .venv`, then **assert** by importing and printing the resolved
  path. The frozen runner is immune (it sets `PYTHONPATH` itself); ad-hoc commands are
  not — prefix them `PYTHONPATH=src`.
- **`freeze --product-namespace` rewrites frozen bytes. The owner said ASK FIRST.** It
  has not been run. Do not run it unprompted.
- **Duplicate test-module basenames are a hard collection error** (no `__init__.py`,
  `prepend` import mode). Every lane was told to prefix `test_t<N>_`. If the merged suite
  goes red with `import file mismatch`, that is this, and it is invisible to every
  per-ticket gate.
- Three worktrees refused `retire` because they hold untracked metric logs. Harmless.

## Where the rest of the state lives

`.swarm-loop/worker-preamble.md` (the invariant half of every packet — packets point at
it by path), `.swarm-loop/codebase-map.md` (§6 is the measured-hazards list),
`.swarm-loop/backlog.md`, `.swarm-loop/learnings.md`, `.swarm-loop/reports/intake.md`
(the eight intake divergences), `ESCALATIONS.md`, `findings.jsonl`, `redcheck.jsonl`,
`dispatch.jsonl`. The ticket graph is `tickets.json` at the repo root and is **frozen**
— it is an input to red-check's bootstrap exemption, so an unhashed graph is a gate a
worker could edit around.
