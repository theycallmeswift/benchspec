"""Host-side Claude Code judge runner.

Reuses agents/judge_cli.raise_for_judge_cli_failure for the same infra-error contract
binder.py relies on (auth/quota/rate-limit/nonzero-exit) so the two call sites can
never disagree on what counts as an infra failure — but builds its own command so
effort/harness_args/env are configurable per JudgeConfig. binder.py's run_host_judge
stays untouched with its own narrower model+timeout call shape; nothing here imports
or calls it.
"""

from __future__ import annotations

import os
import subprocess

from evalspec.agents.judge_cli import raise_for_judge_cli_failure

BIN = "claude"


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    """`claude -p --output-format json` already emits {"result": ..., "is_error": ...}.

    That is the native envelope judge.py expects, no wrapping needed.
    """
    command = [BIN, "-p", prompt, "--output-format", "json", "--model", model,
               "--effort", effort, *harness_args]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=run_env)
    except FileNotFoundError as error:
        raise RuntimeError("host claude CLI not found on PATH") from error
    raise_for_judge_cli_failure(proc)
    return proc.stdout


def probe_version() -> str | None:
    """Best-effort version probe — never raises, never fails the run."""
    try:
        proc = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None
