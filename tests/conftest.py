"""Constants for tests that import them; the harness itself lives one level up.

The per-ticket selector, the three-layer offline block (SPEC C7) and the autouse
settings-cache reset are implemented in ``tests/harness.py`` and installed by the ROOTDIR
``conftest.py``. They moved there because a conftest under ``tests/`` is loaded only when
a named path leads into ``tests/``: ``pytest src/`` skipped the whole harness, silently,
and C7 is a promise about the suite rather than about one directory. See the module
docstrings in ``tests/harness.py`` and ``../conftest.py``.

This file therefore declares NO hooks and NO fixtures — a second ``pytest_addoption`` is
an argparse conflict and a second ``pytest_configure`` would install the block twice and
undo it once. It exists so ``from conftest import NETWORK_DISABLED_MESSAGE,
network_block_targets`` keeps working for tests under ``tests/`` (that import resolves to
this module), and so the constants have one obvious home for anyone who looks here first.

``tests/`` is deliberately NOT a package, so this directory is on ``sys.path`` and helpers
import as top-level modules: ``from doubles import LLMDouble``.
"""

from __future__ import annotations

from harness import (
    NETWORK_DISABLED_MESSAGE,
    NETWORK_MARKER,
    TICKET_MARKER,
    network_block_targets,
)

__all__ = [
    "NETWORK_DISABLED_MESSAGE",
    "NETWORK_MARKER",
    "TICKET_MARKER",
    "network_block_targets",
]
