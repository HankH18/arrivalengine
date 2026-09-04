"""Environment-backed settings.

This ships COMPLETE at T-0. T-1, T-2, T-6 and T-8 each need a different subset of these
keys and none of them is allowed to widen this file — if a key is missing, escalate rather
than adding a second settings object.

Every field maps to the same-named upper-case env var (`contact_email` -> `CONTACT_EMAIL`);
`.env.example` is the documented list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ENV_FILE", "Settings", "SettingsError", "env_file_path", "get_settings"]

#: The environment file every `Settings()` reads, RELATIVE to the process working
#: directory — which is the opposite of `dossier_dir`'s anchoring below, deliberately and
#: with a cost worth knowing about.
#:
#: `dossier_dir` resolves against `__file__` (see `_repo_root`) so the app finds the same
#: committed corpus whatever directory it was started from. `.env` cannot work that way:
#: an operator's secrets are a property of the DEPLOYMENT, not of the checkout, and
#: `pydantic-settings` looks a relative `env_file` up from the CWD. So `uvicorn
#: arrival.web.app:app` started from a subdirectory silently reads NO `.env` while the
#: same command from the repo root reads one, and neither says so.
#:
#: That asymmetry is why `SettingsError` names an absolute path: "could not be read" is
#: useless without saying which file, precisely because which file is not obvious.
ENV_FILE = ".env"


def env_file_path() -> Path:
    """The absolute path `ENV_FILE` resolves to for THIS process, right now.

    Computed at call time rather than cached: it is a function of the working directory,
    which a process may change. Used only to name the file in a `SettingsError`, so it is
    correct for a file that does not exist.
    """
    return Path.cwd() / ENV_FILE


class SettingsError(ValueError):
    """The environment file exists but cannot be read. The message names the path.

    A `ValueError` ON PURPOSE, and not the `RuntimeError` that `web/store.py`'s
    `DossierLoadError` uses. The exception this replaces is `UnicodeDecodeError`, which
    IS a `ValueError`, and callers outside this module already guard on that fact --
    `research.py`'s CLI wraps `get_settings()` in `except ValueError` to turn an
    unreadable `.env` into exit 2 with a sentence instead of a traceback. Subclassing
    `ValueError` therefore NARROWS the raised type (raw decode error -> named error
    carrying the path) without changing which handlers catch it, so every existing guard
    keeps working and nothing outside this file has to be touched. Widening it to
    `RuntimeError` was tried first and silently downgraded that CLI to exit 1; the
    measured failure was `tests/research/test_t059_roster_encoding.py`.
    """


def _repo_root() -> Path:
    """The directory holding `pyproject.toml`, found from this file rather than the CWD.

    Used only for `dossier_dir`'s default. Anchoring on `__file__` is what makes that
    default independent of where the process was started: `python -m arrival` from a
    subdirectory, `uvicorn` from anywhere, and a test that `chdir`s all resolve to the
    same committed corpus. The walk (rather than a fixed `parents[2]`) keeps that true if
    the package is ever imported from an installed copy inside the tree.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]  # pragma: no cover - src/arrival/config.py -> repo root


class Settings(BaseSettings):
    """Runtime configuration. Secrets default to None so the app boots without them."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,  # relative to the CWD on purpose; see ENV_FILE above
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- credentials (all optional; a missing key disables its capability, never crashes)
    anthropic_api_key: str | None = None
    #: Required by the API only for an IDENTITY-LINKED key, which answers 400
    #: `anthropic-workspace-id is required` to every request without it. A key that is not
    #: identity-linked ignores the header, so sending it when set is always safe.
    anthropic_workspace_id: str | None = None
    tavily_api_key: str | None = None
    github_token: str | None = None

    # --- politeness / operations
    contact_email: str = "arrival-engine@example.com"  # SPEC C5: advertised in the User-Agent
    cache_dir: Path = Path(".cache/http")  # DESIGN §Data models: the HTTP disk cache

    # --- surfaces
    debug_views: bool = False  # R15: /debug/{person_id} is 404 unless this is on

    # Where the committed dossiers live: DESIGN §Data models pins
    # `data/dossiers/{person_id}.json`, which is what T-6's build writes and what T-9
    # ships. The web app (T-8) boots from this directory, and the acceptance harness
    # points it at its own corpus with the `DOSSIER_DIR` env var, so the key has to exist
    # on Settings rather than being read straight from os.environ by one module.
    #
    # ABSOLUTE ON PURPOSE, unlike `cache_dir` above: a relative default is resolved
    # against the process working directory by every consumer, so `python -m arrival`
    # from a subdirectory, or a server started from elsewhere, silently reads a different
    # (usually empty) corpus and the digest surface just has nobody in it. Setting
    # DOSSIER_DIR to a relative path is still allowed — that is then the operator's
    # explicit choice, not a default that changes underfoot.
    dossier_dir: Path = _repo_root() / "data" / "dossiers"

    # --- model ids ------------------------------------------------------------------
    # These are SETTINGS, not constants (DESIGN Decision 9): the cheap model does
    # extraction and taste classification, the smart model does resolution verdicts and
    # the say-out-loud line. Override per environment; never hard-code an id at a call
    # site.
    anthropic_model_fast: str = "claude-haiku-4-5-20251001"
    anthropic_model_smart: str = "claude-sonnet-5"

    @property
    def user_agent(self) -> str:
        """SPEC C5: every outbound request identifies itself with a contact address."""
        return f"ArrivalEngine/0.1 (+{self.contact_email})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance.

    Cached, so `.env` is read once. Tests that change the environment must call
    `get_settings.cache_clear()` first.

    Raises `SettingsError`, naming the file, when `.env` cannot be read (T-065).

    WHY THIS GUARD EXISTS, and why HERE.  `env_file_encoding="utf-8"` is strict, so a
    `.env` saved as latin-1 — one accented character in a contact address is enough —
    makes `python-dotenv` raise a bare `UnicodeDecodeError` from four frames inside a
    third-party package, naming no path. Nothing in this process caught it, and this is
    the FIRST thing the process does: `arrival.web.app` ends with `app = create_app()`,
    so config is read at IMPORT. `uvicorn arrival.web.app:app` therefore died with a raw
    dotenv traceback rather than a named error saying which file, on a host where the
    only diagnosis anyone gets is that traceback. Reproduced by execution.

    This is the same ruling `web/store.py` already applies to a dossier
    (`DossierLoadError`) and `research.py` to a roster: a file we cannot read fails
    loudly, with its path. What changes is only WHICH exception comes out.

    `get_settings` rather than `Settings.__init__`: the direct constructor is how tests
    and callers build a settings object with explicit values (`Settings(_env_file=None)`),
    and it should keep pydantic's own errors. This is the process-wide entry point, and
    the one an operator's traceback comes out of.
    """
    try:
        return Settings()
    except ValidationError:
        # Deliberately NOT wrapped, and it must be caught first because pydantic's
        # ValidationError subclasses ValueError. It already IS the diagnosis this guard
        # exists to supply — it names the offending field and what was wrong with its
        # value — so wrapping it would replace a good message with a vaguer one.
        raise
    except (OSError, ValueError) as exc:
        # ValueError is the encoding failure: UnicodeDecodeError subclasses UnicodeError
        # subclasses ValueError, and is NOT an OSError. OSError is the I/O failure — a
        # `.env` that is a directory, or one whose permissions deny a read.
        raise SettingsError(f"{env_file_path()}: could not be read ({exc})") from exc
