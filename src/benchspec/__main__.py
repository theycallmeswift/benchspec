"""`python -m benchspec <command>` / the `benchspec` console script."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from benchspec.exit_codes import ExitCode
from benchspec.grading.binder import BinderAuthError
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


def _fail(error: Exception, code: ExitCode) -> ExitCode:
    """Print `error` as the user-facing `error:` line and hand back the exit code to return."""
    print(f"error: {error}", file=sys.stderr)
    return code


def _no_finding_types() -> tuple[type[Exception], ...]:
    """The default `finding` resolver: this command has no exit-1 failure class."""
    return ()


def _at_boundary(
    action: Callable[[], int],
    *,
    usage: tuple[type[Exception], ...] = (),
    finding: Callable[[], tuple[type[Exception], ...]] = _no_finding_types,
) -> int:
    """Run `action` at the CLI boundary, mapping known failures to `error:` plus an exit code.

    The one place internals' exceptions become the documented exit contract:

    - `SchemaError` (a malformed eval or config, an unsupported backend) is a usage error
      for every command → USAGE (2).
    - `usage`: the command's own preflight/credential failures → USAGE (2).
    - `finding`: the command's genuine failure class → FINDING (1). Resolved lazily, and
      only once an exception outside `usage` arrives, so a handler can name an error type
      from a package that its preflight guarantees importable — the microsandbox error
      type must never be imported on the preflight-failed path.

    Anything else propagates as the traceback it is.

    Args:
        action: The zero-argument command body, returning its exit code.
        usage: Exception types that map to `ExitCode.USAGE`.
        finding: Resolver for the exception types that map to `ExitCode.FINDING`.

    Returns:
        `action()`'s exit code, or the mapped code for a known failure.
    """
    try:
        return action()
    except SchemaError as error:
        return _fail(error, ExitCode.USAGE)
    except usage as error:
        return _fail(error, ExitCode.USAGE)
    except Exception as error:
        if isinstance(error, finding()):
            return _fail(error, ExitCode.FINDING)
        raise


def _microsandbox_error_types() -> tuple[type[Exception], ...]:
    """Resolve microsandbox's failure type, safe only after its host preflight passed."""
    from microsandbox.errors import MicrosandboxError

    return (MicrosandboxError,)


def _run_sandbox_build(args: argparse.Namespace) -> int:
    """Build the agent-ready snapshot for `root`.

    `cli_build` resolves the selected set's backend first, then preflights exactly that
    backend once — so selected-backend diagnostics are not hidden behind a
    default-microsandbox preflight. Host preflight failures (`RuntimeError`, including
    microsandbox-not-installed) are usage errors; a genuine build/provision failure
    (`MicrosandboxError`) is a finding.

    Args:
        args: The parsed `sandbox:build` namespace, with `root` a `Path`.

    Returns:
        `ExitCode.SUCCESS` on a built or already-present snapshot, else the mapped code.
    """

    def build() -> int:
        """Build or reuse the snapshot and report success."""
        sandbox.cli_build(args.root.resolve(), set_name=args.set, config=args.config)
        return ExitCode.SUCCESS

    return _at_boundary(build, usage=(RuntimeError,), finding=_microsandbox_error_types)


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


def _run_analyze(args: argparse.Namespace) -> int:
    """Classify the suite under `root`.

    `analyze` is the only subcommand that talks to the Gemini binder directly, so the
    binder's failure taxonomy surfaces here rather than through pytest's status mapping.
    A missing key (preflight), a transport failure mid-classification (the same
    `RuntimeError` type), and a rejected credential (`BinderAuthError`) all mean no
    classification was produced and the fix is environmental, so all are usage errors.

    Args:
        args: The parsed `analyze` namespace, with `root` a `Path`.

    Returns:
        `ExitCode.SUCCESS` once the suite is classified, else the mapped code.
    """
    return _at_boundary(
        lambda: analyze.run(args.root.resolve()), usage=(RuntimeError, BinderAuthError)
    )


def _run_lint(args: argparse.Namespace) -> int:
    """Lint the suite under `root`; a malformed eval is the only mapped failure."""
    return _at_boundary(lambda: lint.run(args.root.resolve()))


def _run_run(args: argparse.Namespace) -> int:
    """Run the suite; `run.run` maps its own preflight, so only `SchemaError` is mapped here."""
    return _at_boundary(lambda: run.run(args))


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
        "lint": _run_lint,
        "analyze": _run_analyze,
        "run": _run_run,
        "sandbox:build": _run_sandbox_build,
        "sandbox:clean": _run_sandbox_clean,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
