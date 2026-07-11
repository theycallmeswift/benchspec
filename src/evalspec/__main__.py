"""`python -m evalspec <command>` / the `evalspec` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evalspec import analyze, lint, run, sandbox
from evalspec.exit_codes import ExitCode
from evalspec.schema import SchemaError


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


def _run_sandbox_build(args: argparse.Namespace) -> int:
    """Build the agent-ready snapshot for `root`, mapping host and build errors to exit codes.

    A failed host `preflight` is a usage error (exit 2) surfaced before any build. A bad
    environment config raises `SchemaError` from `cli_build`, which the `main` boundary maps
    to USAGE (2) as well — a config error, not a build failure. A genuine build or
    provisioning failure (`MicrosandboxError`, or a `RuntimeError` from the redundant internal
    preflight) is a finding (exit 1). `MicrosandboxError` is imported after a passing preflight
    (which guarantees `import microsandbox` works) so a host without microsandbox installed
    hits preflight's clean exit 2 rather than an uncaught `ModuleNotFoundError`.

    Args:
        args: The parsed `sandbox:build` namespace, with `root` a `Path`.

    Returns:
        `ExitCode.SUCCESS` on a built or already-present snapshot, `ExitCode.USAGE` on a
        preflight or config-schema failure, `ExitCode.FINDING` on a build failure.
    """
    root = args.root.resolve()

    try:
        sandbox.preflight()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.USAGE

    from microsandbox.errors import MicrosandboxError

    try:
        sandbox.cli_build(repo_root=root)
    except (RuntimeError, MicrosandboxError) as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.FINDING

    return ExitCode.SUCCESS


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
    _add_root_argument(
        sub.add_parser("sandbox:build", help="build the agent-ready sandbox snapshot")
    )

    if argv is None:
        argv = sys.argv[1:]
    head, passthrough = _split_passthrough(argv)
    args = parser.parse_args(head)
    args.passthrough = passthrough

    dispatch = {
        "lint": lambda parsed: lint.run(parsed.root.resolve()),
        "analyze": lambda parsed: analyze.run(parsed.root.resolve()),
        "run": run.run,
        "sandbox:build": _run_sandbox_build,
    }
    try:
        return dispatch[args.command](args)
    except SchemaError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.USAGE


if __name__ == "__main__":
    sys.exit(main())
