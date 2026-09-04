"""Frozen acceptance-suite conftest.

RULES THIS FILE OBEYS, and why (references/goal-setting.md):

1. It imports the STANDARD LIBRARY and `pytest` and NOTHING ELSE. A single product
   import here is executed before any test runs, so one half-built module would fail
   collection for the entire suite and take every metric to "could not measure" at
   once, with no failing test to name a culprit.

2. It registers the per-ticket markers `t0`..`t9`. These are marker NAMES, not marker
   arguments, so plain `pytest -m t4` selects them with no custom option and no
   dependency on the project's own conftest. (The project's own `--ticket T-N`
   selector, DESIGN Decision 10, is a separate mechanism owned by ticket T-0; the
   frozen suite deliberately does not depend on it.)

3. It is run with `--confcutdir` pointed at this directory, so the project's root
   `conftest.py` is never loaded and no worker fixture can reach the goals.
"""

import pytest

TICKET_MARKERS = [f"t{n}" for n in range(10)]


def pytest_configure(config):
    for m in TICKET_MARKERS:
        config.addinivalue_line(
            "markers", f"{m}: frozen acceptance criteria attributed to ticket T-{m[1:]}"
        )
    config.addinivalue_line(
        "markers", "guard: contract guard - green at baseline by design, excluded from scored counts"
    )
    # The ARGUMENT form, carried alongside the tN names. Scored selection never uses it;
    # the freeze-time coverage gate and the read-edge closure gate parse it out of the
    # AST, and without it both see a fully-marked suite as entirely unattributed.
    config.addinivalue_line(
        "markers", "ticket(id): the ticket this frozen acceptance criterion grades"
    )
    # Excluded from acceptance_pass_rate only. See run.py HUMAN_GATE_MARK: a criterion
    # that needs a human action outside the loop can only SKIP, and skips stay in the
    # denominator, so leaving one scored makes a 100 target unreachable by construction.
    config.addinivalue_line(
        "markers",
        "human_gate: needs a human action outside the swarm; collected and reported, "
        "never scored in acceptance_pass_rate",
    )


@pytest.fixture(scope="session")
def repo_root():
    """The repository root, resolved from THIS file's location.

    Never from cwd: a test that resolves paths from cwd measures wherever the runner
    happened to be started, and `measure` and a worker's `verify` start it in two
    different trees.
    """
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def frozen_fixtures():
    """Directory holding the ORCHESTRATOR-OWNED fixture corpus.

    These fixtures are inside the frozen manifest and inside no ticket's scope, which
    is the whole point: a metric graded against a file the gradee can write measures
    nothing.
    """
    from pathlib import Path

    return Path(__file__).resolve().parent / "fixtures"
