"""Translate curated `evalspec run` flags into the plugin's `--evalspec-*` options.

`run` exposes one curated flag vocabulary and forwards it to a pytest subprocess that
loads the evalspec plugin via its entry point. The translation is a pure function so
the whole mapping is unit-tested without a live pytest; the subprocess launch that
consumes its output is layered on separately.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable

from evalspec.exit_codes import ExitCode, exit_code_for_pytest_status
from evalspec.specs import discovery

# Curated flag (argparse dest) -> the plugin option it forwards to. Each emits a single
# `option=value` token so a value that starts with `-` is never mistaken for a flag.
_SCALAR_OPTIONS = {
    "set": "--evalspec-set",
    "config": "--evalspec-config",
    "model": "--evalspec-model",
    "models": "--evalspec-models",
    "harness": "--evalspec-harness",
    "effort": "--evalspec-effort",
    "eval_paths": "--evalspec-eval-paths",
    "fail_under": "--evalspec-fail-under",
    "judge_harness": "--evalspec-judge-harness",
    "judge_model": "--evalspec-judge-model",
    "judge_effort": "--evalspec-judge-effort",
}

# Repeatable flags: each collected value emits its own `option=value` token.
_APPEND_OPTIONS = {
    "env": "--evalspec-env",
}


def translate_run_flags(args: argparse.Namespace) -> list[str]:
    """Translate parsed `run` flags into plugin option tokens.

    Always emits `--evalspec-repo-root=<resolved root>` first so the subprocess
    resolves the same repo root the CLI did. Each curated scalar flag whose value is
    set emits one `option=value` token; each value of a repeatable flag emits its own
    token. Absent flags emit nothing. The pytest launcher prefix, `-p evalspec.runners.pytest`,
    and any `--` passthrough are the caller's job — they are NOT emitted here.

    Args:
        args: The parsed `run` namespace, with `root` a `Path` and the curated flags
            as `dest` attributes (`None`/empty when the user omitted them).

    Returns:
        The plugin option tokens, repo-root first.
    """
    tokens = [f"--evalspec-repo-root={args.root.resolve()}"]

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
) -> int:
    """Run the eval suite by spawning a pytest subprocess, mapping its status to an exit code.

    Emptiness is detected up front by reusing the plugin's own discovery: with no evals
    under `root`, pytest would still self-register `cases.py` and report SUCCESS on an
    empty parametrization, so relying on its status is impossible. When discovery finds
    nothing, the runner is never spawned. Otherwise the child inherits the environment
    with `PYTEST_DISABLE_PLUGIN_AUTOLOAD` scrubbed so the entry-point plugin loads exactly
    once (no `-p evalspec.runners.pytest`, which would double-register it), and the child's return
    code maps through the shared exit-code contract.

    Args:
        args: The parsed `run` namespace — `root` (a `Path`), the curated `--evalspec-*`
            flags, and `passthrough` (verbatim pytest args after `--`).
        runner: Injectable process launcher with `subprocess.run`'s contract; called as
            `runner(argv, env=child_env)` and expected to expose `.returncode`.

    Returns:
        `ExitCode.NOTHING_TO_DO` when no evals are discovered, otherwise the child pytest
        status mapped through `exit_code_for_pytest_status`.
    """
    root = args.root.resolve()

    eval_paths = None
    if args.eval_paths:
        eval_paths = [path.strip() for path in args.eval_paths.split(",") if path.strip()]
    if not discovery.discover_eval_cases(root, eval_paths):
        print(f"no evals discovered under {root}")
        return ExitCode.NOTHING_TO_DO

    child_env = {**os.environ}
    child_env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)

    argv = [sys.executable, "-m", "pytest", *translate_run_flags(args), *args.passthrough]
    completed = runner(argv, env=child_env)
    return exit_code_for_pytest_status(int(completed.returncode))
