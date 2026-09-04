#!/usr/bin/env python3
"""Frozen metric runner for the Arrival Engine swarm-loop run.

Every metric in goals.json shells to THIS file. It is inside the frozen manifest, so a
worker cannot edit what a metric measures without `verify` going red.

Design rules it implements, each from references/goal-setting.md:

* HERMETIC.  pytest is invoked with an explicit path, `-o addopts=` and
  `--confcutdir` at the frozen directory, so neither `pyproject.toml` `addopts`, nor
  `testpaths`, nor the project's root `conftest.py` can change what runs.  The
  measured failure this defends against: a two-line `pyproject.toml` carrying
  `addopts = "--ignore=..."` drove a frozen metric from 50 to 100 with the frozen test
  still present and still failing.

* SELECTS ONLY WHAT IT GRADES.  `--ticket T-N` runs `-m tN` at the runner, not the
  whole suite filtered afterwards.  Twelve metrics that each run everything and filter
  are eleven wasted full runs.

* NO CACHED ARTIFACT BETWEEN A METRIC AND ITS MEASUREMENT.  Every invocation runs
  pytest itself.  The junit XML it parses is written into a fresh `mkdtemp()` and
  deleted in the same call; nothing a worker can write ever stands between a metric and
  the thing it measures.

* ASSERTS ITS OWN PRECONDITION.  `-o addopts=` discards any `pythonpath` the project
  set, so before collecting anything the runner imports the product namespace and, if
  that fails, exits non-zero with `harness: cannot import arrival` — never a number.
  Without this, a mis-rooted runner reports N red tests that look exactly like N
  unbuilt features.

* DISTINGUISHES "everything failed" (prints 0) from "could not run at all" (prints
  NOTHING and exits non-zero, so `measure` fails loudly).

* LEAVES AN ARTIFACT FOR EVERY ZERO.  Diagnostics go to
  `.swarm-loop/reports/metric-<id>.log`, never to /dev/null, so a zero is something you
  open rather than something you reproduce.

Usage:
    run.py --pass-rate                     -> percentage of frozen tests passing (0-100)
    run.py --collected-count               -> number of frozen tests collected
    run.py --ticket T-4 --count-passing    -> number of T-4-attributed frozen tests passing
    run.py --build                         -> 1 if the package imports and the CLI contract holds, else 0
    run.py --lint                          -> count of ruff diagnostics under a FROZEN ruff config
    run.py --selfcheck                     -> freeze-time sanity checks; not a metric
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ACC_DIR = Path(__file__).resolve().parent
REPO_ROOT = ACC_DIR.parent.parent
REPORTS = REPO_ROOT / ".swarm-loop" / "reports"

# Product namespace the frozen suite exercises. Asserted importable before any run.
PRODUCT_NAMESPACE = "arrival"

# Tests that are green at baseline by design (contract guards). They are collected and
# they run, but they are excluded from the scored per-ticket counts so they cannot
# contribute free points to a metric the run steers by.
GUARD_MARK = "guard"
# A criterion the SWARM structurally cannot close: it needs a human action outside the
# loop (a live network build, an account, a deploy). Such a test can only ever SKIP, and
# run.py deliberately keeps skips in the denominator -- so leaving one in the scored set
# makes acceptance_pass_rate = 100 unreachable BY CONSTRUCTION (measured ceiling: 92/93 =
# 98.92). It stays collected and stays reported; it is excluded only from the rate the
# swarm is steered by, so a goal nobody can reach is never mistaken for a goal nobody hit.
HUMAN_GATE_MARK = "human_gate"


def _log(metric_id: str, text: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"metric-{metric_id}.log").write_text(text)


def _die(metric_id: str, message: str, detail: str = "") -> None:
    """Could not run at all: print NOTHING on stdout, exit non-zero, leave an artifact."""
    _log(metric_id, f"HARNESS FAILURE\n{message}\n\n{detail}")
    print(f"harness: {message}", file=sys.stderr)
    if detail:
        print(detail[:4000], file=sys.stderr)
    raise SystemExit(3)


def _hermetic_env() -> dict[str, str]:
    """The ONE environment every subprocess in this runner uses.

    Two jobs, and both were previously done wrong in different places.

    1. PYTHONPATH is SET, not inherited and not stripped.  Stripping it made every
       metric depend entirely on the editable install, and on macOS `uv venv` sets
       UF_HIDDEN on the .venv tree while CPython >= 3.12.6's site.addpackage silently
       SKIPS hidden .pth files -- so a clean `uv sync` that exits 0 leaves a correct
       editable install completely inert and every metric _die()s.  Setting it pins
       the value (hermetic) AND survives an inert install (robust).
    2. It is applied at EVERY call site.  `_assert_product_importable` used to inherit
       the ambient env while `_run_pytest` stripped it, so the precondition could pass
       while the measurement it guards failed.

    PYTHONDONTWRITEBYTECODE is set here rather than at one call site: three of the four
    subprocesses used to omit it and wrote .pyc files into the frozen, hash-protected
    acceptance directory, so merely MEASURING mutated the manifest.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _assert_product_importable(metric_id: str) -> None:
    """A mis-rooted runner must never be reportable as a product failure."""
    proc = subprocess.run(
        [sys.executable, "-c", f"import {PRODUCT_NAMESPACE}; print({PRODUCT_NAMESPACE}.__file__)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_hermetic_env(),
    )
    if proc.returncode != 0:
        _die(
            metric_id,
            f"cannot import {PRODUCT_NAMESPACE} from {REPO_ROOT}",
            proc.stderr,
        )
    resolved = Path(proc.stdout.strip()).resolve()
    if REPO_ROOT not in resolved.parents:
        _die(
            metric_id,
            f"{PRODUCT_NAMESPACE} resolved OUTSIDE the tree under measurement: {resolved}",
            "A green measured against another tree's source is evidence about the wrong code.",
        )


def _pytest_argv(extra: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        str(ACC_DIR),
        "-q",
        "--tb=no",
        "-o",
        "addopts=",
        "-c",
        str(ACC_DIR / "pytest.ini"),
        "-p",
        "no:cacheprovider",
        "--confcutdir",
        str(ACC_DIR),
        "--rootdir",
        str(ACC_DIR),
        *extra,
    ]


def _run_pytest(metric_id: str, extra: list[str]) -> tuple[dict, str]:
    """Run the frozen suite and return (counts, raw_output).

    counts: {'total','passed','failed','error','skipped'} derived from junit XML, which
    is far more robust than scraping a terminal summary line whose wording changes
    between pytest versions.
    """
    tmp = tempfile.mkdtemp(prefix="swarm-acc-")
    xml_path = Path(tmp) / "report.xml"
    env = _hermetic_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            _pytest_argv([*extra, f"--junit-xml={xml_path}"]),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        raw = f"$ {' '.join(_pytest_argv([*extra, '--junit-xml=<tmp>']))}\n\nexit={proc.returncode}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        if not xml_path.exists():
            # pytest could not produce a report at all: missing runner, crashed plugin,
            # a conftest that raised. That is a broken measuring stick, not a zero.
            _die(metric_id, "pytest produced no junit report", raw)
        tree = ET.parse(xml_path)
        root = tree.getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            _die(metric_id, "junit report contained no testsuite element", raw)
        total = int(suite.get("tests", 0))
        failures = int(suite.get("failures", 0))
        errors = int(suite.get("errors", 0))
        skipped = int(suite.get("skipped", 0))
        passed = total - failures - errors - skipped
        counts = {
            "total": total,
            "passed": passed,
            "failed": failures,
            "error": errors,
            "skipped": skipped,
            "exit": proc.returncode,
        }
        # A COLLECTION error is a broken measuring stick, not a low score. Measured: with
        # one third-party module-scope import shadowed, pytest reported "Interrupted: 2
        # errors during collection", junit said tests=2 errors=2, and --pass-rate printed
        # 0.0 with exit 0 -- a silently DEFLATED denominator wearing a real number. Two
        # frozen modules still import yaml at module scope, so this is reachable today.
        if errors:
            _die(
                metric_id,
                f"{errors} COLLECTION/SETUP error(s): the suite could not be assembled, "
                "so this number is about the harness, not the product",
                raw,
            )
        # exit 2 = INTERNALERROR / usage error / interrupted. Never a score.
        if proc.returncode == 2:
            _die(metric_id, "pytest exited 2 (internal error, usage error, or interrupted)", raw)
        return counts, raw
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _collected(metric_id: str, extra: list[str]) -> tuple[int, str]:
    env = _hermetic_env()
    # NOTE: exactly ONE -q, supplied by _pytest_argv. A second -q makes pytest -qq, which
    # switches collect-only from node ids to a compact "<file>: <count>" summary with no
    # "::" in it at all — the node-id counter below then reads 0 and _die()s on a healthy
    # suite. Measured on this project's pytest before the freeze.
    proc = subprocess.run(
        _pytest_argv([*extra, "--collect-only"]),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    raw = f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    # Count the listed node ids rather than the footer: the footer counts items
    # collected BEFORE pytest_collection_modifyitems, so a filter hook would shrink the
    # real denominator while the footer held steady.
    n = sum(1 for line in proc.stdout.splitlines() if "::" in line)
    if n == 0:
        _die(metric_id, "frozen suite collected ZERO tests", raw)
    return n, raw


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass-rate", action="store_true")
    ap.add_argument("--collected-count", action="store_true")
    ap.add_argument("--count-passing", action="store_true")
    ap.add_argument("--ticket")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args(argv)

    if a.selfcheck:
        return _selfcheck()

    if a.build:
        return _build()

    if a.lint:
        return _lint()

    if a.collected_count:
        _assert_product_importable("acceptance_collected")
        n, raw = _collected("acceptance_collected", [])
        _log("acceptance_collected", raw)
        print(n)
        return 0

    if a.pass_rate:
        _assert_product_importable("acceptance_pass_rate")
        counts, raw = _run_pytest("acceptance_pass_rate", ["-m", f"not {HUMAN_GATE_MARK}"])
        _log("acceptance_pass_rate", f"{counts}\n\n{raw}")
        if counts["total"] == 0:
            _die("acceptance_pass_rate", "frozen suite ran ZERO tests", raw)
        if counts["skipped"]:
            # A skipped test is not a passed test. Skips stay in the denominator.
            pass
        print(round(100.0 * counts["passed"] / counts["total"], 2))
        return 0

    if a.count_passing:
        if not a.ticket:
            print("--count-passing requires --ticket", file=sys.stderr)
            return 3
        mark = "t" + a.ticket.replace("T-", "").strip()
        metric_id = f"criteria_{mark}"
        _assert_product_importable(metric_id)
        counts, raw = _run_pytest(metric_id, ["-m", f"{mark} and not {GUARD_MARK}"])
        _log(metric_id, f"{counts}\n\n{raw}")
        if counts["total"] == 0:
            # The selector matched nothing. That is a broken measuring stick, never a 0.
            _die(metric_id, f"selector -m '{mark}' selected ZERO frozen tests", raw)
        print(counts["passed"])
        return 0

    ap.print_help()
    return 3


def _build() -> int:
    """0/1: the package imports and __main__.main honours its contract.

    Deliberately more than `import arrival`: an importable package whose CLI entry
    point is broken is not a build that succeeds.
    """
    probe = (
        "import arrival, arrival.contracts, arrival.util, arrival.config;"
        "from arrival.__main__ import main;"
        "rc = main(['definitely-not-a-command']);"
        "assert rc == 2, f'unknown command returned {rc}, expected 2';"
        # POSITIVE CONTROL. Without it `def main(argv): return 2` satisfies this whole
        # metric: the negative probe alone cannot tell a working CLI from a stub that
        # returns 2 for everything. --help is the one command every argparse CLI has.
        "rc0 = main(['--help']) if False else None;"
        "import contextlib, io;"
        "buf = io.StringIO();"
        "ok0 = None;"
        "\ntry:\n"
        "    with contextlib.redirect_stdout(buf):\n"
        "        ok0 = main(['--help'])\n"
        "except SystemExit as e:\n"
        "    ok0 = e.code if e.code is not None else 0\n"
        "assert ok0 == 0, f'--help returned {ok0}, expected 0 (a CLI that returns the "
        "same code for every command is a stub, not a build)'\n"
        "print('ok')"
    )
    env = _hermetic_env()
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, env=env
    )
    _log(
        "build_succeeds",
        f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
    )
    print(1 if proc.returncode == 0 else 0)
    return 0


def _lint() -> int:
    """Count ruff diagnostics under a FROZEN ruff config.

    `ruff check src tests` on its own is NOT a frozen metric: ruff reads `[tool.ruff]` out
    of `pyproject.toml`, which is not a protected path, so a worker could add a
    per-file-ignore or an `exclude` and drive this number to zero without touching a
    frozen byte and without the change appearing in any test diff.  `--config` at a file
    inside the frozen manifest closes that: ruff uses the named file INSTEAD of any
    discovered configuration.

    The explicit `src tests` paths are the other half.  A bare root walk would descend
    into whatever the run parks in the tree, and a metric whose value moves when a worker
    is dispatched is measuring the swarm, not the product.
    """
    cfg = ACC_DIR / "ruff.toml"
    if not cfg.exists():
        _die("lint_errors", f"frozen ruff config missing at {cfg}")
    targets = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")]
    present = [t for t in targets if Path(t).exists()]
    if not present:
        _die("lint_errors", "neither src/ nor tests/ exists - nothing to lint")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            *present,
            "--config",
            str(cfg),
            "--output-format",
            "json",
            "--no-cache",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_hermetic_env(),
    )
    raw = f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    # ruff exits 0 with no findings, 1 with findings, 2 on a usage/config error. A usage
    # error must never read as a clean zero.
    if proc.returncode not in (0, 1):
        _die("lint_errors", f"ruff failed to run (exit {proc.returncode})", raw)
    # ...and neither must a MISSING linter. `python -m ruff` with ruff absent exits 1
    # with ZERO bytes of stdout, which is inside the window above, and json.loads("[]")
    # then prints a perfect 0. Measured: a venv without ruff returns exactly that.
    # ruff ALWAYS emits a JSON document under --output-format json when it actually
    # ran, so empty stdout means it did not run, at any exit code.
    if not proc.stdout.strip():
        _die(
            "lint_errors",
            "ruff produced NO output - it did not run (is it installed in "
            f"{sys.executable}?). An uninstalled linter reporting zero errors is the "
            "exact false green this check exists to refuse.",
            raw,
        )
    try:
        diagnostics = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        _die("lint_errors", "ruff did not emit parseable JSON", raw)
    _log("lint_errors", f"{len(diagnostics)} diagnostic(s)\n\n{raw}")
    print(len(diagnostics))
    return 0


def _selfcheck() -> int:
    """Freeze-time checks. NOT a metric: it must never contribute a scored point.

    Checks the two properties that make the pre-implementation exception coherent:
    every frozen test imports product code LAZILY (inside a function body), and the
    frozen conftest imports nothing from the product.
    """
    problems: list[str] = []
    # Imports that are ALWAYS safe at module scope: the standard library plus pytest
    # itself, which the runner cannot function without anyway. Everything else -- the
    # product AND any third-party wheel -- must be imported inside a function body.
    _STDLIB_OK = set(sys.stdlib_module_names) | {"pytest", "__future__"}
    for path in sorted(ACC_DIR.glob("test_*.py")) + [ACC_DIR / "conftest.py"]:
        if not path.exists():
            continue
        text = path.read_text()
        # AST, not line prefixes: a docstring sentence beginning "from ..." is prose,
        # and a line-based scan reported two of them as module-scope imports.
        tree = ast.parse(text)
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative: resolves inside the acceptance package
                roots = [(node.module or "").split(".")[0]]
            else:
                roots = [a.name.split(".")[0] for a in node.names]
            for root in roots:
                if not root or root in _STDLIB_OK:
                    continue
                src = ast.get_source_segment(text, node) or root
                if root == PRODUCT_NAMESPACE:
                    problems.append(
                        f"{path.name}:{node.lineno}: MODULE-SCOPE product import turns an "
                        f"unbuilt feature into a collection error, which is "
                        f"indistinguishable from a broken measuring stick -> {src}"
                    )
                else:
                    # Measured: with one such wheel absent, pytest reported "2 errors
                    # during collection", junit said tests=2 errors=2, and --pass-rate
                    # printed 0.0 at exit 0 -- a silently DEFLATED denominator wearing a
                    # real number. This check was blind to it because it only ever
                    # looked for the product namespace.
                    problems.append(
                        f"{path.name}:{node.lineno}: MODULE-SCOPE THIRD-PARTY import "
                        f"({root}) -- a missing wheel aborts collection of this module "
                        f"while the others still collect, so every metric silently loses "
                        f"this module's tests from its denominator -> {src}"
                    )
    marked = 0
    # T-0 is the ONLY module allowed to be entirely `guard`. Its tests are contract
    # regressions that are green at baseline by design, so it carries no criteria_t0
    # metric and a scored selector would legitimately match zero. Every other ticket
    # is graded by `-m "tN and not guard"`, and a module that is all-guard makes that
    # selector match nothing -- which _die()s the metric rather than scoring it. Three
    # separate review lenses each had to find that by hand; it is three lines here.
    ALL_GUARD_OK = {"test_t0_contracts.py"}
    for path in sorted(ACC_DIR.glob("test_t*.py")):
        text = path.read_text()
        if "pytestmark" not in text:
            problems.append(f"{path.name}: no pytestmark -> its tests belong to no ticket metric")
        else:
            marked += 1
        if path.name in ALL_GUARD_OK:
            continue
        tree = ast.parse(text)
        scored = 0
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            # node.decorator_list, NOT get_source_segment: a FunctionDef's lineno
            # points at the `def`, so the source segment EXCLUDES its decorators and
            # a guard-mark scan over it can never match. Caught by a positive control
            # that failed to fire on a deliberately all-guard module.
            is_guard = False
            for dec in node.decorator_list:
                fn = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(fn, ast.Attribute) and fn.attr == GUARD_MARK:
                    is_guard = True
            if not is_guard:
                scored += 1
        if scored == 0:
            problems.append(
                f"{path.name}: EVERY test is @pytest.mark.{GUARD_MARK}, so the scored "
                f"selector -m '<ticket> and not {GUARD_MARK}' matches nothing and the "
                f"metric _die()s instead of returning a number"
            )
    if problems:
        print("SELFCHECK FAILED", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print(f"selfcheck ok: {marked} frozen test modules, all lazily importing, all attributed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
