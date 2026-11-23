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
from typing import Literal

import pytest

from benchspec.config.sets import resolved_judge_config, session_run_set, validate_cell_caps
from benchspec.exit_codes import ExitCode, exit_code_for_pytest_status
from benchspec.orchestration import cases, workspace
from benchspec.runners.notices import CapacityNotice
from benchspec.sandbox.registry import resolve_sandbox
from benchspec.sandbox.sandbox import format_preflight_failure
from benchspec.specs import discovery
from benchspec.specs.schema import SchemaError
from benchspec.watch.server import WatchServer

# Curated flag (argparse dest) -> the plugin option it forwards to. Each emits a single
# `option=value` token so a value that starts with `-` is never mistaken for a flag.
_SCALAR_OPTIONS = {
    "set": "--benchspec-set",
    "config": "--benchspec-config",
    "model": "--benchspec-model",
    "models": "--benchspec-models",
    "harness": "--benchspec-harness",
    "effort": "--benchspec-effort",
    "timeout": "--benchspec-timeout",
    "eval_paths": "--benchspec-eval-paths",
    "fail_under": "--benchspec-fail-under",
    "progress": "--benchspec-progress",
    "judge_harness": "--benchspec-judge-harness",
    "judge_provider": "--benchspec-judge-provider",
    "judge_model": "--benchspec-judge-model",
    "judge_effort": "--benchspec-judge-effort",
    "judge_timeout": "--benchspec-judge-timeout",
    "binder_provider": "--benchspec-binder-provider",
    "binder_model": "--benchspec-binder-model",
}

# Repeatable flags: each collected value emits its own `option=value` token.
_APPEND_OPTIONS = {
    "env": "--benchspec-env",
}

# pytest spellings of "collect, don't run": nothing paid happens, so the environment
# preflight is skipped exactly as the plugin skips it (fixtures never set up).
_COLLECT_ONLY_FLAGS = frozenset({"--collect-only", "--co"})

# Each local cell reserves a 2 GB sandbox, so `auto` sizes against total RAM. A remote
# backend's sandboxes take none of it, so there only cores count: the host still runs
# each worker and its judge.
GIB_PER_CELL = 2

# Binder fan-out past about 12 workers trips the Gemini call's 60 s timeout.
MAX_AUTO_WORKERS = 12


@dataclass(frozen=True)
class PluginOptions:
    """Present the parsed `run` flags the way the plugin's resolvers read pytest's config.

    `config.sets` and `orchestration.cases` resolve the set, judge, and sandbox from a
    pytest `config` via `getoption(<plugin option>)` and `rootpath`. This adapter feeds
    them the CLI namespace instead, so the CLI preflight runs the plugin's own resolvers
    rather than a second copy of the config layering.

    Only the curated `run` flags are mapped. Any other plugin option — today the
    passthrough-only judge env/harness-arg, tomorrow whatever the plugin grows —
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
        pytest.UsageError: a malformed or unknown set, a malformed judge config, or an arm
            whose cell `timeout` does not exceed the judge's.
        SchemaError: an unsupported sandbox backend or a malformed eval.
    """
    options = PluginOptions.from_args(args)
    run_set = session_run_set(options)
    if run_set is not None:
        validate_cell_caps(run_set, resolved_judge_config(options))

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


def workers_value(text: str) -> int | Literal["auto"]:
    """Parse the `--workers` flag value for argparse.

    Raises:
        argparse.ArgumentTypeError: `text` is not a positive integer or `auto`.
    """
    try:
        value = int(text) if text.isdecimal() else text
        return discovery.parse_workers(value, label="--workers")
    except SchemaError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def auto_workers(cpu_count: int, total_ram_bytes: int | None) -> int:
    """Size `auto`: min(cores, RAM at `GIB_PER_CELL`), clamped to `[1, MAX_AUTO_WORKERS]`.

    Args:
        cpu_count: Usable CPU cores.
        total_ram_bytes: Total physical memory, or None when the host cannot report it,
            in which case only cores count.
    """
    limit = cpu_count
    if total_ram_bytes is not None:
        limit = min(limit, total_ram_bytes // (GIB_PER_CELL * 1024**3))

    return max(1, min(limit, MAX_AUTO_WORKERS))


def host_cpu_count() -> int:
    """Return the cores this process may use (3.13's `process_cpu_count`, else `cpu_count`)."""
    process_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
    return process_cpu_count() or 1


def host_ram_bytes() -> int | None:
    """Return total physical memory in bytes, or None where `os.sysconf` cannot report it."""
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None

    return total if total > 0 else None


@dataclass(frozen=True)
class Workers:
    """A resolved worker count and where it came from, for the run start line."""

    count: int
    source: str

    def describe(self) -> str:
        """Return the run start line, such as `workers: 4 (flag)`."""
        return f"workers: {self.count} ({self.source})"


def _auto_plan(remote_sandbox: str | None) -> Workers:
    """Resolve `auto` against this host; RAM is left out when sandboxes run remotely.

    Args:
        remote_sandbox: The run's backend when its sandboxes run off this host, else None.
    """
    cores = host_cpu_count()
    source = f"auto: {cores} cores"

    if remote_sandbox is not None:
        source += f"; RAM not counted, sandboxes run on {remote_sandbox}"

        return Workers(count=auto_workers(cores, None), source=source)

    ram_bytes = host_ram_bytes()
    if ram_bytes is not None:
        source += f", {ram_bytes / 1024**3:.1f} GB RAM"

    return Workers(count=auto_workers(cores, ram_bytes), source=source)


def _remote_sandbox(args: argparse.Namespace) -> str | None:
    """The resolved set's backend when its sandboxes run off this host; None otherwise.

    Raises:
        pytest.UsageError: a malformed or unknown set.
        SchemaError: an unsupported sandbox backend.
    """
    run_set = session_run_set(PluginOptions.from_args(args))
    if run_set is None:
        return None

    backend = resolve_sandbox(run_set.sandbox)

    return backend.id if backend.remote else None


def _passthrough_sets_workers(passthrough: list[str]) -> bool:
    """Return whether pytest args already carry xdist's `-n` or `--numprocesses`."""
    for token in passthrough:
        if token.startswith("--numprocesses") or (
            token.startswith("-n") and not token.startswith("--")
        ):
            return True

    return False


def resolve_workers(args: argparse.Namespace) -> Workers:
    """Resolve the worker count: `--workers`, then `[tool.benchspec] workers`, then `auto`.

    Raises:
        SchemaError: the project `workers` value is malformed, or the set names an
            unsupported sandbox.
        pytest.UsageError: `auto` met a malformed or unknown set.
    """
    flag = args.workers
    if flag is not None and flag != "auto":
        return Workers(count=flag, source="flag")
    if flag == "auto":
        return _auto_plan(_remote_sandbox(args))

    configured = discovery.pyproject_workers(args.root.resolve())
    if configured is None or configured == "auto":
        return _auto_plan(_remote_sandbox(args))

    return Workers(count=configured, source="config")


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

    A run that is not collect-only names its iteration itself and follows its event
    files for as long as the child runs, warning once when cells start waiting for a
    sandbox slot. `--watch` also serves the iteration's live view from a daemon thread,
    stopped when the child exits.

    Args:
        args: The parsed `run` namespace — `root` (a `Path`), the curated `--benchspec-*`
            flags, `workers` (an int, `auto`, or None), `watch`, and `passthrough`
            (verbatim pytest args after `--`).
        runner: Injectable process launcher with `subprocess.run`'s contract; called as
            `runner(argv, env=child_env)` and expected to expose `.returncode`.
        preflight: Injectable preflight with `preflight_run`'s contract.

    Returns:
        `ExitCode.NOTHING_TO_DO` when no evals are discovered, `ExitCode.USAGE` on a
        preflight failure or `--workers` given with a passthrough `-n`, otherwise the
        child pytest status mapped through `exit_code_for_pytest_status`.
    """
    root = args.root.resolve()

    eval_paths = None
    if args.eval_paths:
        eval_paths = [path.strip() for path in args.eval_paths.split(",") if path.strip()]
    if not discovery.discover_eval_cases(root, eval_paths):
        print(f"no evals discovered under {root}")
        return ExitCode.NOTHING_TO_DO

    passthrough_sets_workers = _passthrough_sets_workers(args.passthrough)
    if args.workers is not None and passthrough_sets_workers:
        print("error: --workers conflicts with -n/--numprocesses; pass one", file=sys.stderr)
        return ExitCode.USAGE

    collect_only = bool(_COLLECT_ONLY_FLAGS.intersection(args.passthrough))

    if not collect_only:
        try:
            preflight(args)
        except (RuntimeError, pytest.UsageError) as error:
            print(f"error: {error}", file=sys.stderr)
            return ExitCode.USAGE

    child_env = {**os.environ}
    child_env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)

    workers: Workers | None = None
    worker_tokens: list[str] = []
    if not collect_only and not passthrough_sets_workers:
        workers = resolve_workers(args)
        print(workers.describe(), flush=True)
        if workers.count > 1:
            worker_tokens = [f"-n={workers.count}"]

    iteration_tokens: list[str] = []
    server = None
    notice = None
    if not collect_only:
        iteration = workspace.next_iteration_name(root)
        iteration_dir = workspace.evals_root(root) / iteration
        iteration_tokens = [f"--benchspec-iteration={iteration}"]
        notice = CapacityNotice(iteration_dir, workers=workers.count if workers else None)
        notice.start()

        if args.watch:
            server = WatchServer(iteration_dir)
            server.start()
            print(f"Watching: {server.url}", flush=True)

    argv = [
        sys.executable,
        "-m",
        "pytest",
        *worker_tokens,
        *translate_run_flags(args),
        *iteration_tokens,
        *args.passthrough,
    ]
    try:
        completed = runner(argv, env=child_env)
    finally:
        if server is not None:
            server.stop()
        if notice is not None:
            notice.stop()

    return exit_code_for_pytest_status(int(completed.returncode))
