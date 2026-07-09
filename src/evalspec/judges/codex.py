"""Host-side Codex judge runner.

Codex `exec --json` has no native {"result": ...} envelope the way `claude -p
--output-format json` does — extract the final agent_message text from the JSONL
stream and wrap it manually. Infra-error detection is Codex-specific: a
`turn.failed`/`error` event, or a nonzero exit.
"""

from __future__ import annotations

import json
import os
import subprocess

from evalspec.trajectory import iter_events

BIN = "codex"


def _event_item(event: dict) -> dict:
    """The event's `item` dict, or an empty dict if absent/malformed."""
    item = event.get("item") if isinstance(event, dict) else None
    return item if isinstance(item, dict) else {}


def _extract_final_text(stdout: str) -> str:
    """Concatenate every completed agent_message's text from the JSONL stream."""
    parts: list[str] = []
    for event in iter_events(stdout):
        if event.get("type") != "item.completed":
            continue
        item = _event_item(event)
        if item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _raise_for_codex_infra_failure(proc: subprocess.CompletedProcess) -> None:
    """Raise RuntimeError for a nonzero exit or an error/turn.failed event."""
    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"host codex CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}"
        )
    for event in iter_events(proc.stdout):
        if event.get("type") in {"error", "turn.failed"}:
            msg = str(event.get("message") or event.get("error") or "").strip()
            raise RuntimeError(f"host codex CLI reported an error: {msg[:1000] or '(no message)'}")


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    """Grade `prompt` via `codex exec --json`, returning the final agent text.

    The text is wrapped in the {"result": ...} envelope judge.py parses. Raises
    RuntimeError on infra failure (missing binary, nonzero exit, error event).
    """
    # Codex exec has no stable effort flag; the arg is accepted for protocol parity
    # but deliberately not wired into the command.
    del effort
    cmd = [BIN, "exec", "--json", "-m", model, *harness_args, prompt]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=run_env)
    except FileNotFoundError as e:
        raise RuntimeError("host codex CLI not found on PATH") from e
    _raise_for_codex_infra_failure(proc)
    return json.dumps({"result": _extract_final_text(proc.stdout)})


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
