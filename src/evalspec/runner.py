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
from dataclasses import dataclass, field, replace

from evalspec.trajectory import extract_trajectory
from evalspec.trigger import detect_skill_fired

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
    trajectory: list = field(
        default_factory=list
    )  # structured tool_call/tool_result events (Claude stream); [] otherwise
    cache_read_tokens: int = 0  # usage.cache_read_input_tokens (Claude prompt-cache hit)
    cache_creation_tokens: int = 0  # usage.cache_creation_input_tokens
    input_tokens: int = 0  # usage.input_tokens — uncached input only
    output_tokens: int = 0  # usage.output_tokens
    # the CLI result event's `subtype` (e.g. "success", "error_max_turns") — NOT the API stop_reason
    result_subtype: str = ""
    # {display-path: content} for files the agent authored in its skills dir (outside the
    # workdir mount), already diffed against the staged baseline. Empty unless the
    # session captured any. Merged into the turn's facts so the judge can grade them.
    artifacts: dict = field(default_factory=dict)


def utc_today(now: datetime.datetime | None = None) -> str:
    """Today's date (YYYY-MM-DD) in UTC.

    The substituted {TODAY} (host side) and the agent's in-VM `date` (guest runs TZ=UTC)
    must name the same day, or dated-path assertions fail spuriously. A host-local date
    skews by a day from an evening timezone behind UTC, so anchor both sides to UTC.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).date().isoformat()


def sum_tokens(usage: dict) -> int:
    """Sum token counts across agent run results."""
    return sum(int(usage.get(f, 0) or 0) for f in _TOKEN_FIELDS)


def substitute_prompt(prompt: str, today: str | None = None) -> str:
    """Substitute {TODAY} in a prompt. Paths are cwd-relative (the agent runs with.

    cwd = the workdir mount), so there is no path placeholder to substitute. Any
    residual `{UPPER_CASE}` placeholder is rejected, so a stray legacy path token is
    caught rather than passed through to the agent verbatim.
    """
    result = prompt
    if today is not None:
        result = result.replace("{TODAY}", today)
    residual = _PLACEHOLDER.findall(result)
    if residual:
        raise ValueError(
            f"prompt contains unresolved placeholder(s) {sorted(set(residual))}. "
            "Eval prompts must be spec-free for honest baselines: the only "
            "placeholder the runner substitutes is {TODAY}; paths are cwd-relative."
        )
    return result


def substitute_assertions(assertions: list, today: str) -> list:
    """Apply date placeholders to assertions and reject unknown placeholders."""

    def _sub(a: object) -> object:
        """Replace placeholders inside one assertion value."""
        if isinstance(a, str):
            return a.replace("{TODAY}", today)
        return {k: v.replace("{TODAY}", today) if isinstance(v, str) else v for k, v in a.items()}

    result = [_sub(a) for a in assertions]
    residual = sorted(
        {
            m
            for a in result
            for s in ([a] if isinstance(a, str) else [v for v in a.values() if isinstance(v, str)])
            for m in _PLACEHOLDER.findall(s)
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


def parse_stream_run(stdout: str, eval_id: str, config: str, skill_name: str | None) -> RunResult:
    """Parse a `--output-format stream-json` run.

    Every arm streams, so `raw` and the.     structured trajectory are always captured.

    `skill_name` gates fired-detection: the suite's skill is passed on every arm to
    report whether it was invoked (activation is graded symmetrically — a
    `skill_invoked` assertion the trial arm passes and the baseline arm fails), or None
    to skip detection and leave `fired` False. The final `type: "result"` event carries
    the same fields as the single-object `--output-format json` payload, so
    `parse_run_json` reads it.
    """
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
