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
