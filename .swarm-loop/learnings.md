# Learnings — orchestration process log

Imperative, specific, curated on every append. Merge into an existing entry rather than
adding a near-duplicate; hold at roughly 30 entries. Entries are injected into task
packets **one relevant entry at a time**, never wholesale.

---

## Strip `__pycache__` and tool caches from the protected path BEFORE `freeze`

`freeze` hashes **everything** under `.swarm-loop/acceptance/`, and running the frozen
suite even once writes `__pycache__/*.pyc` and `.ruff_cache/` into that directory. Hash
those and `verify` goes red on the next cycle for a reason that has nothing to do with
tampering — and `restore-harness` cannot help, because a `.pyc` that was never in git
reports as `[ADDED]`, which exits 1 and keeps `verify` red until it is deleted by hand.

Observed here: 24 `.pyc` files and a `.ruff_cache/` appeared under the frozen directory
during authoring, from two different interpreters (3.12/pytest 9.1.1 and 3.9/pytest 6.2.4
— itself a signal that agents reached for whatever pytest was on PATH).

APPLY: delete `__pycache__` and every tool cache under the protected path immediately
before `freeze`, add them to `.gitignore`, and keep `-p no:cacheprovider` and
`PYTHONDONTWRITEBYTECODE=1` in the frozen runner so they do not come back. Re-check after
the freeze smoke-run, which itself executes every metric command.

## Run the metric runner's own invocation before trusting it — reading it is not enough

The frozen `run.py` passed `-q` twice for the collected-count metric (once from the shared
argv builder, once at the call site). Two `-q` make pytest `-qq`, which switches
`--collect-only` from node ids to a compact `<file>: <count>` line containing no `::` at
all — so the node-id counter read **0** on a healthy suite and would have failed the
measurement every single cycle, loudly and for a completely wrong reason.

Nothing about that is visible by reading the code; it took running the exact invocation.

APPLY: before the freeze, execute every metric command's real argv against a throwaway
fixture with a KNOWN pass/fail/skip/deselect mix and check the number against what you
counted by hand. Do it against a deliberately hostile config too (a root `conftest.py`
that clears collected items, an `addopts` that ignores the suite) so the hermeticity claim
is measured rather than asserted.

## The hermetic runner discards the project's `pythonpath` — make it import the product itself

`-o addopts=` plus `--rootdir` at the frozen directory means the project's
`[tool.pytest.ini_options]` is **not read at all**, so a `pythonpath = ["src"]` entry the
project relies on does not apply to the frozen suite. Here that entry is load-bearing and
non-obvious: on macOS `uv venv` sets the BSD `UF_HIDDEN` flag on the `.venv` tree, and
CPython ≥ 3.12.6's `site.addpackage` **silently skips hidden `.pth` files**, so a fresh
`uv sync` can leave the editable install's `.pth` present, healthy-looking and inert —
`import arrival` fails with no diagnostic from uv, from site, or from pip metadata.
(Confirmed on this machine by `os.lstat(...).st_flags == 0x8040`; it is intermittent, so a
passing run proves nothing about the next one.)

Neutralised correctly it would surface as N red tests that look exactly like N unbuilt
features — the one shape this loop is least able to question.

APPLY: the frozen runner must make the product importable **on its own terms** — set
`PYTHONPATH` to the repo's own `src` in the subprocess env rather than inheriting or
hoping — and must assert the import before collection, failing with
`harness: cannot import <namespace>` rather than a number. Popping an inherited
`PYTHONPATH` and setting the run's own are different acts; do the first, then the second.

## Give the frozen suite fixtures the graded ticket cannot write — check `scope`, not intent

Two of this doc set's tickets graded themselves against fixtures listed in their own
`scope` (T-4's `taste_cases.yaml`, T-2's `resolve_cases/`). No bad intent is needed for
that to fail: a worker implements, runs, sees a disagreement, and "fixes the fixture".

APPLY: at intake, cross every acceptance criterion's fixture path against the graded
ticket's own `scope` and `owns`. Any intersection means the metric measures nothing —
move the grading copy into the frozen, orchestrator-owned corpus and let the ticket keep
its own copy for its own unit tests. Brief the frozen-test authors explicitly that reading
anything under the project's `tests/` is forbidden, because it looks helpful.

- (cycle 0) **Route every finding by WHO IS PERMITTED TO FIX IT before recording it, and make
  that routing mechanical rather than remembered: a defect in the graded product belongs in
  the ticket-minting ledger, a defect in the frozen measuring apparatus belongs in an
  orchestrator-owned pre-freeze checklist, and the intake command should partition incoming
  findings by matching each recorded location against the protected-path list and refuse to
  mint across that boundary rather than trusting the operator to sort them.** — Two
  adversarial swarms returned in the same session - one reviewing the product, one reviewing
  the acceptance harness that grades it - and the orchestrator piped all of both into the
  ticket-minting ledger in a single gesture, minutes after stating the correct distinction in
  prose. The split was perfectly clean and machine-detectable after the fact: every finding
  from one swarm named a protected path and every finding from the other named product code.
  Nothing but an unrelated record-only flag stopped roughly thirty unclosable tickets being
  minted at workers whom the branch veto rejects from touching that path every time. A
  distinction an orchestrator can state correctly and still act against in the next tool call
  is a distinction that needs a mechanism, not clearer prose.

- (cycle 0) **After 'uv sync' in every worktree on macOS, run 'chflags -R nohidden .venv' and
  then ASSERT the effect by importing the project package and printing its resolved path; the
  installer exits 0 either way.** — uv venv sets UF_HIDDEN on the .venv tree and CPython >=
  3.12.6 site.addpackage silently skips hidden .pth files, so a fresh sync leaves a correct
  editable install completely inert: uv sync exited 0, the .pth held the right path, and
  'import arrival' still raised ModuleNotFoundError with no diagnostic from uv, site or pip.

- (cycle 0) **Verify a metric's hermeticity by RUNNING it against a hostile config and reading
  which configfile it reports, never by reading the flags it passes.** — A frozen pytest
  runner documented itself as sealed by -o addopts= plus --confcutdir plus --rootdir, and
  reported 'configfile: ../../pyproject.toml' when actually run: --rootdir does not stop
  configfile discovery. A two-line worker-writable pyproject took the suite from 93 collected
  to 12 and the pass rate from 15.05 to 100.0 with zero frozen bytes moved, so the integrity
  check stayed green throughout. Only -c <frozen ini> overrides discovery.

- (cycle 0) **When a gate reports OK, confirm it parsed something before believing it; a count
  of zero inputs and a count of zero violations print the same green.** — A read-edge closure
  gate exited 0 reporting '(none)' inbound and outbound because its AST extractor recognised
  only @pytest.mark.ticket(id) while the suite used pytestmark = pytest.mark.tN. The intake
  had recorded that green as proof the closure was 'mechanical rather than advisory'. The same
  mismatch would have made freeze refuse all ten tickets with two remedies in its error text
  that were both wrong for the case.

- (cycle 0) **Write every non-trivial git commit message to a file and use 'git commit -F
  <file>'; never pass one as a double-quoted shell argument.** — Backticks inside a
  double-quoted -m argument are command substitution, so a merge message documenting which
  field names a stub accepted had every one of those names silently executed as a command and
  stripped, leaving sentences like 'the stub knew only .' The commit succeeded and the loss
  was only visible on re-reading the stored message.

- (cycle 0) **Before dispatching a wave, run one test-collection probe for basename collisions
  across the tickets' test directories; a shared module basename is a hard collection error no
  per-ticket gate can see.** — Measured twice on a real repo: two sibling test modules named
  test_client.py, in a tests/ tree with no __init__.py under pytest's default prepend import
  mode, give 'import file mismatch' and 'Interrupted: 1 error during collection', exit 2. The
  design named a client.py for two different tickets, so each lane would have passed its own
  gate and the merged suite would have gone red for a reason neither lane could see.

- (cycle 0) **State a packet's factual premises as claims to be measured and require them back
  under PREMISES-FALSIFIED; a prescribed fix is a starting point, never an instruction.** —
  Across nine lanes in one wave, six falsified at least one premise of their brief and every
  one was right: a prescribed fix that would have made its defect strictly worse, a count of
  nine where the spec said ten, hub arithmetic that was simply wrong, and a fixture rename
  that bought nothing measurable while breaking seven ticket lines the dependent tickets were
  built against. Two lanes also refused a directive that would have clobbered a sibling's live
  branch.
