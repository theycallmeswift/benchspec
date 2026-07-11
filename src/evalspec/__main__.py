"""`python -m evalspec <command>` / the `evalspec` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evalspec import analyze, lint


def _add_root_argument(command_parser: argparse.ArgumentParser) -> None:
    """Add the shared optional `root` positional to a subcommand parser."""
    command_parser.add_argument(
        "root",
        nargs="?",
        default=Path("."),
        type=Path,
        help="repo root to scan (default: cwd)",
    )


def main(argv: list[str] | None = None) -> int:
    """Dispatch the evalspec command-line interface."""
    parser = argparse.ArgumentParser(prog="evalspec")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_root_argument(
        sub.add_parser("lint", help="static checks for eval suites (unjudgeable assertions)")
    )
    _add_root_argument(
        sub.add_parser(
            "analyze",
            help="classify assertions deterministic/activation/judge-backed before a run",
        )
    )
    args = parser.parse_args(argv)

    dispatch = {"lint": lint.run, "analyze": analyze.run}
    return dispatch[args.command](args.root.resolve())


if __name__ == "__main__":
    sys.exit(main())
