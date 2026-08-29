"""`python -m harnessbench <command>` / the `harnessbench` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harnessbench.exit_codes import ExitCode
from harnessbench.reporting import analyze
from harnessbench.runners import run
from harnessbench.sandbox import sandbox
from harnessbench.specs import lint
from harnessbench.specs.schema import SchemaError


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
    run_parser.add_argument("--set", help="eval set to run (--harnessbench-set)")
    run_parser.add_argument("--config", help="config file to load (--harnessbench-config)")
    run_parser.add_argument("--model", help="model for the single arm (--harnessbench-model)")
    run_parser.add_argument("--models", help="comma-separated model sweep (--harnessbench-models)")
    run_parser.add_argument("--harness", help="harness for the single arm (--harnessbench-harness)")
    run_parser.add_argument("--effort", help="reasoning effort (--harnessbench-effort)")
    run_parser.add_argument(
        "--eval-paths", help="comma-separated search paths (--harnessbench-eval-paths)"
    )
    run_parser.add_argument("--fail-under", help="pass-rate gate (--harnessbench-fail-under)")
    run_parser.add_argument(
        "--judge-harness", help="judge harness (--harnessbench-judge-harness)"
    )
    run_parser.add_argument("--judge-model", help="judge model (--harnessbench-judge-model)")
    run_parser.add_argument("--judge-effort", help="judge effort (--harnessbench-judge-effort)")
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="repeatable env var for arms (--harnessbench-env); pass --env KEY=VAL per var",
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

    `cli_build` resolves the selected set's backend first, then preflights exactly that backend
    once — so selected-backend diagnostics are not hidden behind a default-microsandbox preflight,
    and a future backend can build independently. Error mapping:

    - `SchemaError` (bad config, or a `docker`/unsupported set): propagates to the `main` boundary
      → USAGE (2). Config error, not a build failure.
    - `RuntimeError` (host preflight, including microsandbox-not-installed): USAGE (2), surfaced
      before any provisioning. This branch imports no microsandbox, so a host without the package
      still exits 2 cleanly rather than raising `ModuleNotFoundError`.
    - `MicrosandboxError` (a genuine build/provision failure): FINDING (1). Only reachable after
      preflight passed, which guarantees `import microsandbox` works, so importing the error type
      here is safe.

    Args:
        args: The parsed `sandbox:build` namespace, with `root` a `Path`.

    Returns:
        `ExitCode.SUCCESS` on a built or already-present snapshot, `ExitCode.USAGE` on a
        preflight or config-schema failure, `ExitCode.FINDING` on a build failure.
    """
    root = args.root.resolve()

    try:
        sandbox.cli_build(root, set_name=args.set, config=args.config)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.USAGE
    except SchemaError:
        raise  # a bad config / unsupported backend is a usage error; let `main` map it to 2
    except Exception as error:
        # Reached only after preflight passed, so microsandbox is importable here.
        from microsandbox.errors import MicrosandboxError

        if isinstance(error, MicrosandboxError):
            print(f"error: {error}", file=sys.stderr)
            return ExitCode.FINDING
        raise

    return ExitCode.SUCCESS


def main(argv: list[str] | None = None) -> int:
    """Dispatch the harnessbench command-line interface."""
    parser = argparse.ArgumentParser(prog="harnessbench")
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
    sandbox_build_parser = sub.add_parser(
        "sandbox:build", help="build the agent-ready sandbox snapshot"
    )
    _add_root_argument(sandbox_build_parser)
    sandbox_build_parser.add_argument(
        "--set", help="eval set whose sandbox backend + env drive the build"
    )
    sandbox_build_parser.add_argument(
        "--config", help="config file layered over pyproject for set resolution"
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
