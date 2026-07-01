"""Shared host-`claude` judge call for agents that delegate grading to it.

Both ClaudeCodeAgent and OpenCodeAgent shell out to `claude -p` on the host for grading
so judging quality stays consistent across the matrix — task arms differ, grading
should not. Centralizing the call (`run_host_judge`) and its failure check
(`raise_for_judge_cli_failure`) here means a third agent that also delegates can't
reintroduce the masking bug by forgetting either.
"""

from __future__ import annotations

import json
import subprocess


def run_host_judge(prompt: str, *, model: str, timeout: int = 300) -> str:
    """Run the host's `claude -p` judge and return its raw stdout (caller parses).

    Host-vs-VM asymmetry is intentional: grading needs the host's credentials and
    reasoning budget; the agent stays inside the per-arm sandbox.

    Raises RuntimeError on every infra-level failure so none can be laundered into
    fake assertion failures:
    - missing binary (`claude` off PATH) — FileNotFoundError normalized here;
    - nonzero exit, or 0-exit `is_error=true` envelope — via raise_for_judge_cli_failure.

    `run_eval_arm` catches the RuntimeError and surfaces it as arm-level `errored=True`,
    so an infra failure can't be mistaken for an honest 0% pass rate. A TimeoutExpired is
    deliberately left to propagate — `grade_run` records a hung judge as a graded error.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError("host claude CLI not found on PATH") from e
    raise_for_judge_cli_failure(proc)
    return proc.stdout


def raise_for_judge_cli_failure(proc: subprocess.CompletedProcess) -> None:
    """Raise RuntimeError if the host `claude -p` call failed at the CLI level.

    Two failure shapes:
    - Nonzero exit (network blip, binary crash) — stderr/stdout carries the message.
    - Zero exit but `is_error=true` in the JSON envelope — this is how `claude -p`
      reports auth, rate-limit, quota, and overload failures (the body carries a
      human-readable `result` like "Not logged in · Please run /login").

    Without raising, both get laundered through `parse_judge_json` → "no JSON object"
    ValueError → `grade_run`'s fake "JUDGE ERROR: unparseable output" mask, hiding a
    real infra problem as fake assertion failures.
    """
    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"host claude CLI exited {proc.returncode}: "
            f"{body[-1000:] or '(no output)'}"
        )
    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # 0-exit + unparseable stdout is unusual but not necessarily a CLI failure
        # (e.g. transient streaming glitch); let parse_judge_json surface it.
        return

    if isinstance(outer, dict) and outer.get("is_error"):
        msg = str(outer.get("result") or "").strip() or "(no error message)"
        raise RuntimeError(f"host claude CLI returned is_error=true: {msg[:1000]}")
