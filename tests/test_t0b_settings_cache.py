"""T-0b regression for D3: `get_settings()` used to leak its cache across tests.

`arrival.config.get_settings` is `lru_cache`'d process-wide — correct for production,
where `.env` should be read once — and the shared harness installed no autouse
`cache_clear`. Only `tests/test_t0_config.py`'s local `clean_env` fixture cleared it, and
only for the tests that requested it.

The consequence is a test that sets an env var and then reads a Settings object built
BEFORE it. T-8's debug gate is exactly that shape (`DEBUG_VIEWS=1`, then `get_settings()`,
then expect `/debug` to open), and it fails or, worse, succeeds and poisons the cache for
every test that runs after it. Because it depends on what ran first, it can be green
locally and red under a different collection order.

The three tests below run in definition order, which is what makes the leak reproducible:
the first does what any ordinary earlier test does, and the second is the victim.
"""

from __future__ import annotations

import pytest

from arrival.config import get_settings

pytestmark = pytest.mark.ticket("T-0")


def test_an_earlier_test_populates_the_process_wide_cache():
    """Deliberately first, and deliberately ordinary: this is the poisoning step."""
    settings = get_settings()
    assert settings is not None
    assert get_settings() is settings, "get_settings is cached; that is the premise of D3"


def test_a_later_test_sees_the_environment_it_just_set(monkeypatch: pytest.MonkeyPatch):
    """D3: the failing half. Without an autouse `cache_clear` this reads a stale object."""
    monkeypatch.setenv("DEBUG_VIEWS", "true")
    assert get_settings().debug_views is True, (
        "get_settings() returned a Settings built before DEBUG_VIEWS was set — the "
        "process-wide lru_cache leaked in from an earlier test"
    )


def test_the_environment_of_the_previous_test_does_not_leak_forward():
    """The other direction: a test that sets an env var must not poison the next one."""
    assert get_settings().debug_views is False, (
        "DEBUG_VIEWS from the previous test is still visible through the cached Settings"
    )
