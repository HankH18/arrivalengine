"""CLI entry point: ``python -m arrival <command>``.

T-0 ships the dispatch skeleton; T-6 fills the ``build`` subcommand at the marked point
below and MUST keep this signature. `connectors` and `llm` are injected so the CLI is
testable in-process and offline (`None` means "build the real ones from settings") —
T-6's `test_cli_build` calls `main([...], connectors=[...], llm=LLMDouble())` directly,
with no subprocess and no network.
"""

from __future__ import annotations

import sys

__all__ = ["USAGE", "main"]

USAGE = """usage: python -m arrival <command> [options]

commands:
  build   Research the roster and write dossier JSON.
          python -m arrival build --roster data/roster.yaml --out data/dossiers
                                  [--force] [--only PERSON_ID]

  -h, --help   Show this message.
"""


def main(argv: list[str], *, connectors=None, llm=None) -> int:
    """Run one CLI command. Returns the process exit code.

    Args:
        argv: arguments AFTER the program name, i.e. ``sys.argv[1:]``.
        connectors: injected connectors; ``None`` means build the real set from settings.
        llm: injected LLM client; ``None`` means build the real one from settings.

    Returns:
        0 on success, 2 for a missing or unknown command (and for any subcommand that is
        not implemented yet).
    """
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2

    command, args = argv[0], argv[1:]

    if command in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    if command == "build":
        # T-6 DISPATCH POINT. The verb itself lives in `arrival.research.build_command`,
        # imported HERE rather than at module scope: `python -m arrival --help` must not
        # pay for yaml, httpx and ten connector modules, and T-0's CLI tests import this
        # module before any of those necessarily exist.
        from .research import build_command

        return build_command(args, connectors=connectors, llm=llm)

    print(f"arrival: unknown command {command!r}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
