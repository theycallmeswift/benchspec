"""`python -m evalspec <command>` / the `evalspec` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evalspec import lint


def main(argv: list[str] | None = None) -> int:
    """Dispatch the evalspec command-line interface."""
    parser = argparse.ArgumentParser(prog="evalspec")
    sub = parser.add_subparsers(dest="command", required=True)
    lint_parser = sub.add_parser(
        "lint", help="static checks for eval suites (unjudgeable assertions)"
    )
    lint_parser.add_argument(
        "root",
        nargs="?",
        default=Path("."),
        type=Path,
        help="repo root to scan (default: cwd)",
    )
    args = parser.parse_args(argv)
    return lint.run(args.root.resolve())


if __name__ == "__main__":
    sys.exit(main())
