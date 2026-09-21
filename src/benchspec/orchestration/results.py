"""Pure helpers for eval arm execution: prompt/assertion substitution and result parsing.

`substitute_prompt` and `substitute_assertions` fill in the {TODAY} placeholder;
paths are cwd-relative (the agent runs with cwd = the workdir mount).
`parse_run_json` and `parse_stream_run` turn raw `claude -p` output into `RunResult`.
`parse_stream_run` also detects whether the skill-under-test actually fired.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import overload

from benchspec.grading.trajectory import extract_trajectory
from benchspec.grading.trigger import detect_skill_fired

_TOKEN_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)

_PLACEHOLDER = re.compile(r"\{[A-Z_]+\}")


@dataclass(frozen=True)
class RunResult:
    """Store run result data."""

    eval_id: str
    config: str  # the arm name this run belongs to
    result_text: str  # the claude -p `result` field (agent's final message)
    duration_ms: int
    total_tokens: int
    is_error: bool
    session_id: str = ""  # claude -p session id, for resuming across turns
    fired: bool = False  # did the skill-under-test get invoked? (graded per arm, symmetric)
    # Full raw stdout when the parser had a stream; "" when there is none.
    raw: str = ""
    # structured tool_call/tool_result events (Claude stream); [] otherwise
    trajectory: list[dict] = field(default_factory=list)
    cache_read_tokens: int = 0  # usage.cache_read_input_tokens (Claude prompt-cache hit)
    cache_creation_tokens: int = 0  # usage.cache_creation_input_tokens
    input_tokens: int = 0  # usage.input_tokens — uncached input only
    output_tokens: int = 0  # usage.output_tokens
    # CLI result event subtype such as "success" or "error_max_turns".
    result_subtype: str = ""
    # {display-path: content} for authored skill files outside the workdir mount.
    artifacts: dict[str, str] = field(default_factory=dict)


def utc_today(now: datetime.datetime | None = None) -> str:
    """Return today's date in UTC as YYYY-MM-DD."""
    now = now or datetime.datetime.now(datetime.UTC)
    return now.astimezone(datetime.UTC).date().isoformat()


def sum_tokens(usage: dict) -> int:
    """Sum token counts across agent run results."""
    return sum(int(usage.get(field, 0) or 0) for field in _TOKEN_FIELDS)


def substitute_today(text: str, today: str | None = None) -> str:
    """Substitute {TODAY} without rejecting other placeholder-shaped text."""
    return text.replace("{TODAY}", today) if today is not None else text


def substitute_prompt(prompt: str, today: str | None = None) -> str:
    """Substitute {TODAY} in a prompt and reject unknown placeholders."""
    result = substitute_today(prompt, today)
    residual = _PLACEHOLDER.findall(result)
    if residual:
        raise ValueError(
            f"prompt contains unresolved placeholder(s) {sorted(set(residual))}. "
            "Eval prompts must be spec-free for honest baselines: the only "
            "placeholder the runner substitutes is {TODAY}; paths are cwd-relative."
        )
    return result


@overload
def substitute_assertions(assertions: Sequence[str], today: str) -> list[str]: ...


@overload
def substitute_assertions(assertions: Sequence[str | dict], today: str) -> list[str | dict]: ...


def substitute_assertions(assertions: Sequence[str | dict], today: str) -> Sequence[str | dict]:
    """Apply date placeholders to assertions and reject unknown placeholders.

    Assertions are prose strings; a typed checker object is substituted field by field
    so a dated `path` resolves the same way a dated prose assertion does.
    """

    def substitute_assertion(assertion: str | dict) -> str | dict:
        """Replace placeholders inside one assertion value."""
        if isinstance(assertion, str):
            return assertion.replace("{TODAY}", today)
        return {
            key: value.replace("{TODAY}", today) if isinstance(value, str) else value
            for key, value in assertion.items()
        }

    result = [substitute_assertion(assertion) for assertion in assertions]
    residual = sorted(
        {
            placeholder
            for assertion in result
            for text in (
                [assertion]
                if isinstance(assertion, str)
                else [value for value in assertion.values() if isinstance(value, str)]
            )
            for placeholder in _PLACEHOLDER.findall(text)
        }
    )
    if residual:
        raise ValueError(
            f"assertion contains unresolved placeholder(s) {residual}; the only "
            "placeholder the runner substitutes is {TODAY}."
        )
    return result


def parse_run_json(raw: str, eval_id: str, config: str) -> RunResult:
    """Parse one agent run JSON record from stdout."""
    data = json.loads(raw)
    usage = data.get("usage", {})

    return RunResult(
        eval_id=eval_id,
        config=config,
        result_text=data.get("result", ""),
        duration_ms=int(data.get("duration_ms", 0)),
        total_tokens=sum_tokens(usage),
        is_error=bool(data.get("is_error", False)),
        session_id=str(data.get("session_id", "")),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        result_subtype=str(data.get("subtype", "")),
    )


def mark_errored_by_nonzero_exit(result: RunResult, stderr: str) -> RunResult:
    """Mark a parsed run errored because its harness exited non-zero.

    A non-zero exit is a crash even when the stream looks complete, so the run is errored
    regardless of what the parser concluded. The parsed raw stream, trajectory, tokens,
    and duration stay as evidence of what the agent did before the harness died. The
    diagnostic lives on stderr, which headlines the result when it has content; otherwise
    the parser's own text stands.

    Args:
        result: The run parsed from the harness stream.
        stderr: Everything the harness wrote to stderr.

    Returns:
        `result` with `is_error` forced and `result_text` swapped for the stderr tail.
    """
    text = stderr[-2000:].strip() or result.result_text
    return replace(result, result_text=text, is_error=True)


def parse_stream_run(stdout: str, eval_id: str, config: str, skill_name: str | None) -> RunResult:
    """Parse a streamed JSON run and preserve raw trajectory evidence."""
    lines = stdout.splitlines()
    fired = bool(skill_name) and detect_skill_fired(lines, skill_name)
    for line in reversed(lines):
        text = line.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return replace(
                parse_run_json(text, eval_id, config),
                fired=fired,
                raw=stdout,
                trajectory=extract_trajectory(stdout),
            )
    return RunResult(
        eval_id,
        config,
        stdout[-2000:],
        0,
        0,
        is_error=True,
        fired=fired,
        raw=stdout,
        trajectory=extract_trajectory(stdout),
    )
