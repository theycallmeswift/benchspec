"""Translate curated `benchspec run` flags into the plugin's `--benchspec-*` options.

`run` exposes one curated flag vocabulary and forwards it to a pytest subprocess that
loads the benchspec plugin via its entry point. The translation is a pure function so
the whole mapping is unit-tested without a live pytest; the subprocess launch that
consumes its output is layered on separately.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from benchspec.exit_codes import ExitCode, exit_code_for_pytest_status
from benchspec.orchestration import cases
from benchspec.sandbox.sandbox import format_preflight_failure
from benchspec.specs import discovery

# Curated flag (argparse dest) -> the plugin option it forwards to. Each emits a single
# `option=value` token so a value that starts with `-` is never mistaken for a flag.
_SCALAR_OPTIONS = {
    "set": "--benchspec-set",
    "config": "--benchspec-config",
    "model": "--benchspec-model",
    "models": "--benchspec-models",
    "harness": "--benchspec-harness",
    "effort": "--benchspec-effort",
    "eval_paths": "--benchspec-eval-paths",
    "fail_under": "--benchspec-fail-under",
    "judge_harness": "--benchspec-judge-harness",
    "judge_provider": "--benchspec-judge-provider",
    "judge_model": "--benchspec-judge-model",
    "judge_effort": "--benchspec-judge-effort",
    "binder_provider": "--benchspec-binder-provider",
}

# Repeatable flags: each collected value emits its own `option=value` token.
_APPEND_OPTIONS = {
    "env": "--benchspec-env",
}

# pytest spellings of "collect, don't run": nothing paid happens, so the environment
# preflight is skipped exactly as the plugin skips it (fixtures never set up).
_COLLECT_ONLY_FLAGS = frozenset({"--collect-only", "--co"})


@dataclass(frozen=True)
class PluginOptions:
    """Present the parsed `run` flags the way the plugin's resolvers read pytest's config.

    `config.sets` and `orchestration.cases` resolve the set, judge, and sandbox from a
    pytest `config` via `getoption(<plugin option>)` and `rootpath`. This adapter feeds
    them the CLI namespace instead, so the CLI preflight runs the plugin's own resolvers
    rather than a second copy of the config layering.

    Only the curated `run` flags are mapped. Any other plugin option — today the
    passthrough-only judge timeout/env/harness-arg, tomorrow whatever the plugin grows —
    reads as unset, exactly as when the user omits it, so a new plugin option never
    needs a matching line here to keep the preflight working.
    """

    values: dict[str, object]
    rootpath: Path

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> PluginOptions:
        """Build the adapter from a parsed `run` namespace with `root` resolved."""
        root = args.root.resolve()
        values: dict[str, object] = {"benchspec_repo_root": str(root)}

        for dest in _SCALAR_OPTIONS:
            values[f"benchspec_{dest}"] = getattr(args, dest, None)

        for dest in _APPEND_OPTIONS:
            values[f"benchspec_{dest}"] = list(getattr(args, dest, None) or [])

        return cls(values=values, rootpath=root)

    def getoption(self: PluginOptions, name: str) -> object:
        """Return the plugin option `name`, or None for any option `run` does not curate."""
        return self.values.get(name)


def preflight_run(args: argparse.Namespace) -> None:
    """Refuse a run, before pytest spawns, on anything the plugin would refuse later.

    Drives the plugin's own preflights through `PluginOptions`: grading (Gemini
    credential, judge config, judge binary and credential), then the resolved set's
    sandbox (set resolution, host readiness, agent credential). Every environment
    failure is reported in one message, so a fresh host learns everything it is missing
    from a single run. Nothing is duplicated — a run that passes here passes the same
    checks again inside pytest as a backstop for raw `pytest -p benchspec.runners.pytest`
    invocations.

    Args:
        args: The parsed `run` namespace, with `root` a `Path`.

    Raises:
        RuntimeError: a missing credential, an absent binary, or an unready host; every
            such failure listed.
        pytest.UsageError: a malformed or unknown set, or a malformed judge config.
        SchemaError: an unsupported sandbox backend or a malformed eval.
    """
    options = PluginOptions.from_args(args)

    errors = cases.grading_preflight_errors(options) + cases.sandbox_preflight_errors(options)
    if errors:
        raise RuntimeError(format_preflight_failure("benchspec preflight failed", errors))


def translate_run_flags(args: argparse.Namespace) -> list[str]:
    """Translate parsed `run` flags into plugin option tokens.

    Always emits `--benchspec-repo-root=<resolved root>` first so the subprocess
    resolves the same repo root the CLI did. Each curated scalar flag whose value is
    set emits one `option=value` token; each value of a repeatable flag emits its own
    token. Absent flags emit nothing. The pytest launcher prefix, `-p benchspec.runners.pytest`,
    and any `--` passthrough are the caller's job — they are NOT emitted here.

    Args:
        args: The parsed `run` namespace, with `root` a `Path` and the curated flags
            as `dest` attributes (`None`/empty when the user omitted them).

    Returns:
        The plugin option tokens, repo-root first.
    """
    tokens = [f"--benchspec-repo-root={args.root.resolve()}"]

    for dest, option in _SCALAR_OPTIONS.items():
        value = getattr(args, dest, None)
        if value is not None:
            tokens.append(f"{option}={value}")

    for dest, option in _APPEND_OPTIONS.items():
        for value in getattr(args, dest, None) or []:
            tokens.append(f"{option}={value}")

    return tokens


def run(
    args: argparse.Namespace,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    preflight: Callable[[argparse.Namespace], None] = preflight_run,
) -> int:
    """Run the eval suite by spawning a pytest subprocess, mapping its status to an exit code.

    Emptiness is detected up front by reusing the plugin's own discovery: with no evals
    under `root`, pytest would still self-register `cases.py` and report SUCCESS on an
    empty parametrization, so relying on its status is impossible. When discovery finds
    nothing, the runner is never spawned. A populated root is then preflighted in full
    (credentials, binaries, host, set, judge) so a missing piece is one `error:` line and
    exit 2 instead of a pytest error per cell; a collect-only passthrough skips that, as
    nothing paid follows. Otherwise the child inherits the environment with
    `PYTEST_DISABLE_PLUGIN_AUTOLOAD` scrubbed so the entry-point plugin loads exactly
    once (no `-p benchspec.runners.pytest`, which would double-register it), and the
    child's return code maps through the shared exit-code contract.

    Args:
        args: The parsed `run` namespace — `root` (a `Path`), the curated `--benchspec-*`
            flags, and `passthrough` (verbatim pytest args after `--`).
        runner: Injectable process launcher with `subprocess.run`'s contract; called as
            `runner(argv, env=child_env)` and expected to expose `.returncode`.
        preflight: Injectable preflight with `preflight_run`'s contract.

    Returns:
        `ExitCode.NOTHING_TO_DO` when no evals are discovered, `ExitCode.USAGE` on a
        preflight failure, otherwise the child pytest status mapped through
        `exit_code_for_pytest_status`.
    """
    root = args.root.resolve()

    eval_paths = None
    if args.eval_paths:
        eval_paths = [path.strip() for path in args.eval_paths.split(",") if path.strip()]
    if not discovery.discover_eval_cases(root, eval_paths):
        print(f"no evals discovered under {root}")
        return ExitCode.NOTHING_TO_DO

    if not _COLLECT_ONLY_FLAGS.intersection(args.passthrough):
        try:
            preflight(args)
        except (RuntimeError, pytest.UsageError) as error:
            print(f"error: {error}", file=sys.stderr)
            return ExitCode.USAGE

    child_env = {**os.environ}
    child_env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)

    argv = [sys.executable, "-m", "pytest", *translate_run_flags(args), *args.passthrough]
    completed = runner(argv, env=child_env)
    return exit_code_for_pytest_status(int(completed.returncode))
