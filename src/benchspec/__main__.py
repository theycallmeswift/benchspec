"""`python -m benchspec <command>` / the `benchspec` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from benchspec.exit_codes import ExitCode
from benchspec.reporting import analyze
from benchspec.runners import run
from benchspec.sandbox import sandbox
from benchspec.specs import lint
from benchspec.specs.schema import SchemaError


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
    run_parser.add_argument("--set", help="eval set to run (--benchspec-set)")
    run_parser.add_argument("--config", help="config file to load (--benchspec-config)")
    run_parser.add_argument("--model", help="model for the single arm (--benchspec-model)")
    run_parser.add_argument("--models", help="comma-separated model sweep (--benchspec-models)")
    run_parser.add_argument("--harness", help="harness for the single arm (--benchspec-harness)")
    run_parser.add_argument("--effort", help="reasoning effort (--benchspec-effort)")
    run_parser.add_argument(
        "--eval-paths", help="comma-separated search paths (--benchspec-eval-paths)"
    )
    run_parser.add_argument("--fail-under", help="pass-rate gate (--benchspec-fail-under)")
    run_parser.add_argument(
        "--judge-harness", help="judge harness (--benchspec-judge-harness)"
    )
    run_parser.add_argument("--judge-model", help="judge model (--benchspec-judge-model)")
    run_parser.add_argument("--judge-effort", help="judge effort (--benchspec-judge-effort)")
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="repeatable env var for arms (--benchspec-env); pass --env KEY=VAL per var",
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


def _run_sandbox_clean(args: argparse.Namespace) -> int:
    """Prune benchspec sandboxes and snapshots for `root`, always exiting 0 once parsed.

    `cli_clean` tolerates a missing microsandbox runtime (nothing to prune) and `msb` failures
    on individual entries, so there is no error mapping here: the only exits are 0 (success,
    including a host with nothing to prune) and 2 (an argparse usage error, raised by `main`
    before this handler runs).

    Args:
        args: The parsed `sandbox:clean` namespace, with `root` a `Path`.

    Returns:
        `ExitCode.SUCCESS`.
    """
    root = args.root.resolve()

    sandbox.cli_clean(root)

    return ExitCode.SUCCESS


def _load_repo_dotenv() -> None:
    """Load a repo-root `.env` into the environment before any subcommand runs.

    `usecwd=True` walks up from the invocation directory — the same rule the pytest
    plugin applies — so `lint`, `analyze`, `sandbox:build`, `sandbox:clean`, and `run` all
    see the same credentials. Variables already exported win over `.env` values.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the benchspec command-line interface."""
    _load_repo_dotenv()
    parser = argparse.ArgumentParser(prog="benchspec")
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
    sandbox_clean_help = (
        "prune benchspec sandboxes and snapshots; stops running eval sandboxes, "
        "so do not use mid-run"
    )
    _add_root_argument(
        sub.add_parser(
            "sandbox:clean", help=sandbox_clean_help, description=sandbox_clean_help
        )
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
        "sandbox:clean": _run_sandbox_clean,
    }
    try:
        return dispatch[args.command](args)
    except SchemaError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.USAGE


if __name__ == "__main__":
    sys.exit(main())
