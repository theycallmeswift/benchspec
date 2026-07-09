"""Host-side OpenCode judge runner.

OpenCode's JSONL stream has no terminal result event and no documented explicit
error-event type (see agents/opencode.py's comments) — a nonzero exit or an
auth/quota/rate-limit-shaped stderr message are the locally-detectable infra-failure
signals. Final text is every non-empty `text` event's `part.text`, matching
agents/opencode.py's own text-collection rule.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from evalspec.trajectory import iter_events

BIN = "opencode"

_EFFORT_TO_VARIANT = {"low": "fast", "medium": "default", "high": "thorough"}
# Explicit phrases / word-boundary patterns for genuine host-side infra failures
# (auth, quota, rate-limit, overload). Deliberately avoids bare substrings like
# "auth" that also occur in ordinary, non-error stderr text (e.g.
# "authenticated as user", "author: ..."). \bquota\b and \bauthoriz\w* are safe
# bare-word matches: "quota" and "authoriz*" do not appear inside those
# innocuous phrases, but do cover reordered provider messages like "exceeded
# your current quota" and phrasings like "Authorization header missing".
_INFRA_PATTERN = re.compile(
    r"\b(401|403|429)\b"
    r"|unauthorized"
    r"|authentication failed"
    r"|invalid api key"
    r"|rate[\s-]?limit"
    r"|too many requests"
    r"|\bquota\b"
    r"|\bauthoriz\w*"
    r"|overloaded",
    re.IGNORECASE,
)


def _looks_like_infra_failure(text: str) -> bool:
    """True if stderr text matches an auth/quota/rate-limit/overload infra signal."""
    return bool(_INFRA_PATTERN.search(text))


def _extract_final_text(stdout: str) -> str:
    """Concatenate every non-empty `text` event's `part.text` from the JSONL stream."""
    parts: list[str] = []
    for event in iter_events(stdout):
        if event.get("type") != "text":
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _raise_for_opencode_infra_failure(proc: subprocess.CompletedProcess) -> None:
    """Raise RuntimeError for a nonzero exit or infra-shaped stderr."""
    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"host opencode CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}"
        )
    stderr = (proc.stderr or "").strip()
    if stderr and _looks_like_infra_failure(stderr):
        raise RuntimeError(f"host opencode CLI reported an error: {stderr[-1000:]}")


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    """Grade `prompt` via `opencode run --format json`, returning the final text.

    The text is wrapped in the {"result": ...} envelope judge.py parses. Raises
    RuntimeError on infra failure (missing binary, nonzero exit, infra-shaped stderr).
    """
    variant = _EFFORT_TO_VARIANT.get(effort, "default")
    cmd = [BIN, "run", "--format", "json", "--variant", variant, "-m", model,
           *harness_args, prompt]

    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=run_env,
            stdin=subprocess.DEVNULL,  # opencode blocks reading stdin forever without this
        )
    except FileNotFoundError as e:
        raise RuntimeError("host opencode CLI not found on PATH") from e
    _raise_for_opencode_infra_failure(proc)
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
