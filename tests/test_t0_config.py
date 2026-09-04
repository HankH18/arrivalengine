"""T-0 acceptance 7: Settings ships complete, so no later ticket has to widen it."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from arrival.config import ENV_FILE, Settings, SettingsError, env_file_path, get_settings

pytestmark = pytest.mark.ticket("T-0")

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    # Only an IDENTITY-LINKED key needs this; the API answers every request without it
    # `400 anthropic-workspace-id is required`. A key that is not identity-linked ignores
    # the header, so it is always safe to send when set. Measured against the live API.
    "ANTHROPIC_WORKSPACE_ID",
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


# --- T-065: a `.env` that cannot be decoded ---------------------------------
#
# THE DEFECT, measured before the fix.  `env_file_encoding="utf-8"` is STRICT, so a `.env`
# holding one latin-1 byte -- an accented character in a contact address is enough -- made
# `Settings()` raise a bare `UnicodeDecodeError` four frames inside `python-dotenv`,
# naming no path, with no handler anywhere in this process:
#
#     File ".../dotenv/parser.py", line 73, in __init__
#       self.string = stream.read().removeprefix("﻿")
#     UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 17
#
# `arrival.web.app` ends with `app = create_app()`, so config is read at IMPORT and this
# is the FIRST thing the process does. `uvicorn arrival.web.app:app` died with that
# traceback on a host where the traceback is the only diagnosis anyone gets.
#
# These grade against the CPython exception hierarchy and pydantic's own error type --
# never against anything in `arrival.config`.

BAD_ENV_BYTES = "CONTACT_EMAIL=caf\xe9@example.com\n".encode("latin-1")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def in_tmp_cwd(clean_env, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A working directory of our own, because `env_file=".env"` resolves against the CWD.

    That relativity is the point of the ticket and not an accident: it is why the error
    has to name an absolute path.
    """
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_a_unicode_decode_error_is_a_value_error_and_not_an_os_error():
    """The answer key for the whole ticket, straight out of CPython.

    `except OSError` -- the shape every other file-reading guard in this repo started
    with -- cannot catch this, and `except ValueError` can. Nothing about `arrival` is
    consulted here; if this ever fails, the language changed.
    """
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, OSError)


def test_settings_error_stays_catchable_as_a_value_error():
    """A CROSS-MODULE CONTRACT, and the reason this assertion exists rather than being
    obvious: `research.py`'s CLI turns an unreadable `.env` into exit 2 with
    `except ValueError` around `get_settings()`, because the error it was written against
    was `UnicodeDecodeError`. Naming the error must NARROW that type, never leave it.

    Making `SettingsError` a `RuntimeError` -- the shape `DossierLoadError` uses -- was
    tried and silently downgraded that CLI to exit 1 with a traceback at the operator.
    `tests/research/test_t059_roster_encoding.py` catches it; this says why.
    """
    assert issubclass(SettingsError, ValueError)
    assert not issubclass(SettingsError, UnicodeDecodeError), (
        "a caller must be able to tell 'we diagnosed this' from 'the codec did'"
    )


def test_a_non_utf8_env_file_raises_a_named_error_naming_the_path(in_tmp_cwd):
    """The headline. What comes out must say WHICH file and be catchable by name."""
    (in_tmp_cwd / ENV_FILE).write_bytes(BAD_ENV_BYTES)

    with pytest.raises(SettingsError) as excinfo:
        get_settings()

    message = str(excinfo.value)
    assert str(in_tmp_cwd / ENV_FILE) in message, (
        "the error does not name the file that could not be read, which is the entire "
        f"reason it exists -- `.env` is CWD-relative, so the path is not guessable: {message}"
    )
    assert isinstance(excinfo.value.__cause__, UnicodeDecodeError), (
        "the original decode error must survive as __cause__; a diagnosis that discards "
        "the byte offset is worse than the traceback it replaced"
    )
    assert not isinstance(excinfo.value, UnicodeDecodeError), (
        "still the raw dotenv error, merely re-raised"
    )


def test_the_path_the_error_names_is_absolute(in_tmp_cwd):
    """A relative `.env` in a traceback tells an operator nothing about which one it was."""
    (in_tmp_cwd / ENV_FILE).write_bytes(BAD_ENV_BYTES)

    with pytest.raises(SettingsError) as excinfo:
        get_settings()

    named = Path(str(excinfo.value).split(":", 1)[0])
    assert named.is_absolute(), f"the error named a relative path: {named}"
    assert env_file_path().is_absolute()


def test_a_readable_env_file_is_still_read(in_tmp_cwd):
    """POSITIVE CONTROL. A guard that made every `.env` unreadable would pass the tests
    above and break the product; this is the assertion that says the door still opens."""
    (in_tmp_cwd / ENV_FILE).write_text(
        "CONTACT_EMAIL=host@arenahall.example\n", encoding="utf-8"
    )

    settings = get_settings()

    assert settings.contact_email == "host@arenahall.example"
    assert settings.user_agent == "ArrivalEngine/0.1 (+host@arenahall.example)"


def test_no_env_file_at_all_is_not_an_error(in_tmp_cwd):
    """The ordinary deployment: no `.env`, everything from the real environment."""
    assert not (in_tmp_cwd / ENV_FILE).exists()
    assert get_settings().contact_email == "arrival-engine@example.com"


def test_a_bad_debug_views_value_turns_the_switch_off_instead_of_killing_the_service(
    in_tmp_cwd, caplog
):
    """T-090: an unreadable `DEBUG_VIEWS` fails CLOSED, and the diagnosis is a warning.

    JUSTIFIED TEST EDIT — T-090. This test previously read:

        (in_tmp_cwd / ENV_FILE).write_text("DEBUG_VIEWS=notabool\\n", encoding="utf-8")
        with pytest.raises(ValidationError) as excinfo:
            get_settings()
        assert "debug_views" in str(excinfo.value).lower()
        assert not isinstance(excinfo.value, SettingsError)

    i.e. it required an unreadable `DEBUG_VIEWS` to raise out of `get_settings`. That
    encoded a **total production outage as the contract**, and it is wrong independently of
    any implementation, on evidence measured against the deployed service:
    `arrival/web/app.py` ends with a module-level `app = create_app()`, so `Settings()` is
    read at IMPORT — which means `DEBUG_VIEWS` set to `""`, `"2"`, `"-1"`, `"maybe"`,
    `"1.0"`, `"null"` or `"False "` with a trailing space did not turn a debug page off, it
    killed the whole service before a single route existed. `export DEBUG_VIEWS=` and a bare
    `DEBUG_VIEWS=` line in a `.env` both produce the empty string, and pasting a value with
    a trailing space into a hosting dashboard produces the last one. R15 calls `/debug` a
    switch, and a switch has a safe position; taking a live product down for every user over
    one operator page is not it.

    The property the old test actually cared about — that `get_settings` does not swallow a
    `ValidationError` into the vaguer `SettingsError`, because `ValidationError` subclasses
    `ValueError` — is NOT dropped. It is worth keeping and it is now pinned directly, on the
    handler rather than on a field, by
    `test_get_settings_never_swallows_a_validation_error_into_settings_error` below. That is
    also the stronger test: `debug_views` was the only field on `Settings` that could fail
    to parse from an environment variable at all (every other field is `str`, `Path` or
    `str | None`), so this property was one field-type change away from being untestable.
    """
    (in_tmp_cwd / ENV_FILE).write_text("DEBUG_VIEWS=notabool\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="arrival.config"):
        settings = get_settings()

    assert settings.debug_views is False
    assert "DEBUG_VIEWS" in caplog.text
    assert "notabool" in caplog.text


def test_get_settings_never_swallows_a_validation_error_into_settings_error(in_tmp_cwd):
    """The property the edit above preserves, pinned on the handler that implements it.

    `get_settings` catches `ValidationError` FIRST and re-raises it untouched, precisely
    because `ValidationError` subclasses `ValueError` and the `except (OSError, ValueError)`
    clause below it would otherwise rewrite pydantic's own field-and-value diagnosis into
    the vaguer "could not be read". Asserted by making `Settings()` raise one, so the guard
    is tested even though no environment variable can currently produce one.
    """
    import arrival.config as config_module

    error = ValidationError.from_exception_data("Settings", [])

    class Exploding:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise error

    original = config_module.Settings
    config_module.Settings = Exploding  # type: ignore[misc]
    try:
        get_settings.cache_clear()
        with pytest.raises(ValidationError) as excinfo:
            get_settings()
    finally:
        config_module.Settings = original  # type: ignore[misc]
        get_settings.cache_clear()

    assert excinfo.value is error
    assert not isinstance(excinfo.value, SettingsError)


def test_booting_the_web_app_over_a_bad_env_gives_the_diagnosis_not_a_decode_error(
    tmp_path,
):
    """The blast radius, out of process because `arrival.config` is already imported here.

    This is literally what `uvicorn arrival.web.app:app` does on Render: `app =
    create_app()` at module scope reads config at import. Before the fix the terminating
    line was `UnicodeDecodeError` out of `dotenv/parser.py`. The import failing at all is
    correct -- an unreadable config is not something to boot past -- and is not what this
    asserts; WHICH exception reaches the operator is.
    """
    (tmp_path / ENV_FILE).write_bytes(BAD_ENV_BYTES)

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); import arrival.web.app",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )

    assert result.returncode != 0, f"a `.env` that cannot be read must not boot:\n{result.stdout}"
    stderr = result.stderr
    assert "SettingsError" in stderr, f"boot failed without the diagnosis:\n{stderr}"
    assert str(tmp_path / ENV_FILE) in stderr, f"the failure did not name the file:\n{stderr}"
    final_line = stderr.strip().splitlines()[-1]
    assert not final_line.startswith("UnicodeDecodeError"), (
        f"the raw dotenv error is still what reaches the operator:\n{stderr}"
    )
