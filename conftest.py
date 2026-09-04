"""Rootdir conftest: makes the T-0 harness guarantees independent of the paths named.

pytest loads a conftest only for directories on the path from the rootdir down to each
named argument. With the harness living solely in ``tests/conftest.py``, naming anything
outside ``tests/`` skipped it — no ``--ticket`` option, and no offline block:

    $ pytest --ticket T-0 src/
    pytest: error: unrecognized arguments: --ticket          # exit 4

Nothing escaped on the day only because ``src/`` holds no tests. SPEC C7 ("no test may hit
the network") is a property of the SUITE, not of one directory, so the hooks and autouse
fixtures now come from ``tests/harness.py`` and are re-exported here, where pytest loads
them for every invocation whose rootdir is this repo. ``tests/test_t0b_harness_scope.py``
executes that guarantee, probe module and all.

The hooks are declared in exactly one place. Two conftests both defining
``pytest_addoption`` is an argparse "conflicting option string" error, and two both
running ``pytest_configure`` would install the offline block twice and undo it once.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `tests/` is not a package, so the harness is imported as a top-level module — the same
# way test modules import `doubles`. Inserting the directory here (rather than relying on
# pytest to do it when it later imports tests/conftest.py) guarantees ONE `harness` module
# object, hence one `_ORIGINALS` table and one installed block.
_TESTS_DIR = Path(__file__).resolve().parent / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from harness import (  # noqa: E402 - must follow the sys.path insertion above
    NETWORK_DISABLED_MESSAGE,
    NETWORK_MARKER,
    TICKET_MARKER,
    block_network,
    network_block_targets,
    pytest_addoption,
    pytest_collection_modifyitems,
    pytest_configure,
    pytest_unconfigure,
    reset_settings_cache,
)

__all__ = [
    "NETWORK_DISABLED_MESSAGE",
    "NETWORK_MARKER",
    "TICKET_MARKER",
    "block_network",
    "network_block_targets",
    "pytest_addoption",
    "pytest_collection_modifyitems",
    "pytest_configure",
    "pytest_unconfigure",
    "reset_settings_cache",
]
