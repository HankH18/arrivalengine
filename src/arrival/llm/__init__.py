"""The production LLM surface.

`arrival.llm.client.AnthropicClient` is the only implementation of
`arrival.contracts.LLMClient` that talks to a real model; `tests.doubles.LLMDouble` is its
offline twin and the two are interchangeable everywhere downstream (T-2, T-3, T-4, T-6,
T-7, T-8).

Nothing is imported eagerly here: `arrival.llm.client` imports the Anthropic SDK lazily,
inside the first real call, so importing this package costs nothing and needs no API key.
"""

from __future__ import annotations

__all__ = ["AnthropicClient"]


def __getattr__(name: str) -> object:
    """Expose `arrival.llm.AnthropicClient` without importing the client eagerly."""
    if name == "AnthropicClient":
        from arrival.llm.client import AnthropicClient

        return AnthropicClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
