"""A deliberately UNMARKED test module.

Decision 10 says `--ticket T-N` deselects tests with no `ticket` marker as well as tests
marked for another ticket. This module is the evidence for the "as well as" half: it
carries no `pytestmark`, so `pytest --ticket T-0` must not collect it, while a plain
`pytest` run must. `tests/test_t0_harness.py` asserts both directions.

Do not add a marker here — that would silently make
`test_unmarked_tests_are_deselected_too` vacuous.
"""

from __future__ import annotations


def test_t0_unmarked_sentinel():
    """Passes when the whole suite runs; never selected by --ticket."""
    assert True
