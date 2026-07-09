"""Host-side Codex judge runner.

Runs `codex exec --json` as a fresh host process (never inside the task sandbox) and
reads its output through the same parser the task arm uses, `agents.codex
.parse_codex_jsonl`, so the judge and the arm can never disagree on how Codex output is
turned into final text or an error signal. Only the transport differs: the arm runs in
the microVM via `sb.exec`, the judge runs on the host via `subprocess.run`.
"""

from __future__ import annotations

import json
import os
import subprocess

from evalspec.agents.codex import parse_codex_jsonl

BIN = "codex"


def run(
    prompt: str,
    *,
    model: str,
    effort: str,
    timeout: int,
    harness_args: list[str],
    env: dict,
) -> str:
    """Grade `prompt` via `codex exec --json`, returning the final agent text.

    The text is wrapped in the {"result": ...} envelope judge.py parses. Raises
    RuntimeError on an infra failure — a missing binary, a nonzero exit, or a
    harness-reported error event surfaced by parse_codex_jsonl as `is_error`.
    """
    # Codex exec has no stable effort flag; the arg is accepted for protocol parity
    # but deliberately not wired into the command.
    del effort
    command = [BIN, "exec", "--json", "-m", model, *harness_args, prompt]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=run_env)
    except FileNotFoundError as error:
        raise RuntimeError("host codex CLI not found on PATH") from error

    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"host codex CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}"
        )
    result = parse_codex_jsonl(proc.stdout, "judge", "judge", None)
    if result.is_error:
        raise RuntimeError(f"host codex CLI reported an error: {result.result_text[:1000]}")
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
