"""`python -m evalspec <command>` / the `evalspec` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evalspec import analyze, lint, run


def _add_root_argument(command_parser: argparse.ArgumentParser) -> None:
    """Add the shared optional `root` positional to a subcommand parser."""
    command_parser.add_argument(
        "root",
        nargs="?",
        default=Path("."),
        type=Path,
        help="repo root to scan (default: cwd)",
    )


def _add_run_flags(run_parser: argparse.ArgumentParser) -> None:
    """Add the curated `run` flag vocabulary that forwards to the plugin's options."""
    run_parser.add_argument("--set", help="eval set to run (--evalspec-set)")
    run_parser.add_argument("--config", help="config file to load (--evalspec-config)")
    run_parser.add_argument("--model", help="model for the single arm (--evalspec-model)")
    run_parser.add_argument("--models", help="comma-separated model sweep (--evalspec-models)")
    run_parser.add_argument("--harness", help="harness for the single arm (--evalspec-harness)")
    run_parser.add_argument("--effort", help="reasoning effort (--evalspec-effort)")
    run_parser.add_argument(
        "--eval-paths", help="comma-separated search paths (--evalspec-eval-paths)"
    )
    run_parser.add_argument("--fail-under", help="pass-rate gate (--evalspec-fail-under)")
    run_parser.add_argument(
        "--judge-harness", help="judge harness (--evalspec-judge-harness)"
    )
    run_parser.add_argument("--judge-model", help="judge model (--evalspec-judge-model)")
    run_parser.add_argument("--judge-effort", help="judge effort (--evalspec-judge-effort)")
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="repeatable env var for arms (--evalspec-env); pass --env KEY=VAL per var",
    )


def _split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first standalone `--`, returning the head and verbatim tail.

    Args:
        argv: The command-line arguments to split.

    Returns:
        A `(head, tail)` pair — everything before the first standalone `--` and
        everything after it. The tail is empty when no `--` is present.
    """
    if "--" not in argv:
        return argv, []
    separator = argv.index("--")
    return argv[:separator], argv[separator + 1 :]


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
    run_parser = sub.add_parser(
        "run", help="run the eval suite (pytest); pass args after `--` verbatim to pytest"
    )
    _add_root_argument(run_parser)
    _add_run_flags(run_parser)

    if argv is None:
        argv = sys.argv[1:]
    head, passthrough = _split_passthrough(argv)
    args = parser.parse_args(head)
    args.passthrough = passthrough

    dispatch = {
        "lint": lambda parsed: lint.run(parsed.root.resolve()),
        "analyze": lambda parsed: analyze.run(parsed.root.resolve()),
        "run": run.run,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
