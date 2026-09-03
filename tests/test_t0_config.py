"""T-0 acceptance 7: Settings ships complete, so no later ticket has to widen it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from arrival.config import Settings, get_settings

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "GITHUB_TOKEN",
    "CONTACT_EMAIL",
    "DEBUG_VIEWS",
    "ANTHROPIC_MODEL_FAST",
    "ANTHROPIC_MODEL_SMART",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """A process environment with none of our keys set, so defaults are observable."""
    for key in (*ENV_KEYS, "CACHE_DIR"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is None
    assert s.tavily_api_key is None
    assert s.github_token is None
    assert s.contact_email == "arrival-engine@example.com"
    assert s.debug_views is False
    assert s.cache_dir == Path(".cache/http")
    assert s.anthropic_model_fast == "claude-haiku-4-5-20251001"
    assert s.anthropic_model_smart == "claude-sonnet-5"


def test_every_env_example_key_is_a_settings_field():
    """T-1, T-2 and T-8 each need a different key; none may widen config.py."""
    text = ENV_EXAMPLE.read_text()
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))
    assert documented == set(ENV_KEYS), documented
    fields = {name.upper() for name in Settings.model_fields}
    assert documented <= fields, documented - fields


def test_env_overrides_every_key(clean_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")
    monkeypatch.setenv("CONTACT_EMAIL", "host@arenahall.example")
    monkeypatch.setenv("DEBUG_VIEWS", "1")
    monkeypatch.setenv("CACHE_DIR", "/tmp/arrival-cache")
    monkeypatch.setenv("ANTHROPIC_MODEL_FAST", "fast-override")
    monkeypatch.setenv("ANTHROPIC_MODEL_SMART", "smart-override")

    s = Settings(_env_file=None)
    assert s.anthropic_api_key == "sk-test"
    assert s.tavily_api_key == "tvly-test"
    assert s.github_token == "ghp-test"
    assert s.contact_email == "host@arenahall.example"
    assert s.debug_views is True  # R15: DEBUG_VIEWS=1 opens /debug
    assert s.cache_dir == Path("/tmp/arrival-cache")
    assert s.anthropic_model_fast == "fast-override"
    assert s.anthropic_model_smart == "smart-override"


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("true", True), ("0", False), ("false", False)]
)
def test_debug_views_parsing(clean_env, monkeypatch: pytest.MonkeyPatch, raw, expected):
    monkeypatch.setenv("DEBUG_VIEWS", raw)
    assert Settings(_env_file=None).debug_views is expected


def test_user_agent_carries_the_contact_email(clean_env):
    """SPEC C5: every outbound request identifies itself."""
    s = Settings(_env_file=None, contact_email="host@arenahall.example")
    assert s.user_agent == "ArrivalEngine/0.1 (+host@arenahall.example)"


def test_get_settings_is_cached(clean_env):
    assert get_settings() is get_settings()
    get_settings.cache_clear()
    assert get_settings() is not None


def test_unknown_env_vars_are_ignored(clean_env, monkeypatch: pytest.MonkeyPatch):
    """A stray var in the operator's shell must not crash boot."""
    monkeypatch.setenv("SOME_UNRELATED_THING", "x")
    assert Settings(_env_file=None) is not None
