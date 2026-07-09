"""Host-side OpenCode judge runner.

Runs `opencode run --format json` as a fresh host process (never inside the task
sandbox) and reads its output through the same parser the task arm uses,
`agents.opencode.parse_opencode_jsonl`. That parser already treats a run that spent
zero tokens as an error (the harness never reached the model — an auth, quota, or
launch failure), so the judge detects infra failures with the same structured signal
as the arm instead of scanning stderr. Only the transport differs: the arm runs in the
microVM via `sb.exec`, the judge runs on the host via `subprocess.run`.
"""

from __future__ import annotations

import json
import os
import subprocess

from evalspec.agents.opencode import parse_opencode_jsonl

BIN = "opencode"

_EFFORT_TO_VARIANT = {"low": "fast", "medium": "default", "high": "thorough"}


def run(
    prompt: str,
    *,
    model: str,
    effort: str,
    timeout: int,
    harness_args: list[str],
    env: dict,
) -> str:
    """Grade `prompt` via `opencode run --format json`, returning the final text.

    The text is wrapped in the {"result": ...} envelope judge.py parses. Raises
    RuntimeError on an infra failure — a missing binary, a nonzero exit, or a run that
    reached the model zero times (surfaced by parse_opencode_jsonl as `is_error`).
    """
    variant = _EFFORT_TO_VARIANT.get(effort, "default")
    command = [
        BIN, "run", "--format", "json", "--variant", variant, "-m", model,
        *harness_args, prompt,
    ]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            stdin=subprocess.DEVNULL,  # opencode blocks reading stdin forever without this
        )
    except FileNotFoundError as error:
        raise RuntimeError("host opencode CLI not found on PATH") from error

    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"host opencode CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}"
        )
    result = parse_opencode_jsonl(proc.stdout, "judge", "judge", None)
    if result.is_error:
        detail = (proc.stderr or "").strip()[-1000:] or result.result_text[:1000]
        raise RuntimeError(f"host opencode CLI made no successful model call: {detail}")
    return json.dumps({"result": result.result_text})


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
