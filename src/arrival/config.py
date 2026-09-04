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

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- credentials (all optional; a missing key disables its capability, never crashes)
    anthropic_api_key: str | None = None
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
    """
    return Settings()
