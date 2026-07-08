"""Structured per-turn agent trajectory: tool calls + results derived from a.

Claude Code stream-json run, for post-hoc debugging and judge process-facts.

The raw session.jsonl is the lossless source; this module derives the queryable
projection the judge and reports reason over — which tools the agent called and which
sub-skills it dispatched mid-turn (the process facts a final-message judge can't
otherwise see). Same Claude event-shape match as `trigger.py`; OpenCode's stream differs
and is handled in `agents/opencode.py`.

Event schema (one ordered list per turn):     {"kind": "tool_call",   "id": <str>,
"name": <str>, "arguments": <dict>}     {"kind": "tool_result", "call_id": <str>,
"is_error": <bool>, "content": <str>}

Field names map onto the OpenTelemetry GenAI semantic conventions so the artifact is
portable: name -> gen_ai.tool.name, id/call_id -> gen_ai.tool.call.id, arguments ->
gen_ai.tool.call.arguments, content -> gen_ai.tool.call.result.
"""

from __future__ import annotations

import json

# tool results can be huge (file reads); the raw .jsonl keeps the full bytes, the
# structured projection keeps a readable prefix.
_RESULT_CONTENT_LIMIT = 2000

# Per-turn key, shared with execution.py: a delimiter LINE in the consolidated
# session.jsonl, and a per-event FIELD in the derived trajectory. One definition so the
# writer (execution.py) and the reader (split_session) can't drift.
TURN_DELIM = "turn"


def iter_events(text: str) -> object:
    """Yield each JSON-object event from a stream-json/JSONL text, skipping blank.

    and malformed lines and non-object lines. Single source of the whole-text parse
    shared by the Claude trajectory extractor and the OpenCode parser.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _content_blocks(event: object) -> object:
    """Return message content blocks from a trajectory event."""
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def _result_text(content: object) -> str:
    """tool_result content is either a string or a list of {type:text,text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _claude_trajectory(events: list[dict]) -> list[dict]:
    """Ordered tool_call/tool_result events from already-parsed Claude stream events.

    The events-in counterpart to extract_trajectory, mirroring _opencode_trajectory so a
    caller holding parsed events needn't re-parse the stream.
    """
    traj: list[dict] = []
    for event in events:
        for block in _content_blocks(event):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                inp = block.get("input")
                traj.append(
                    {
                        "kind": "tool_call",
                        "id": block.get("id") or "",
                        "name": block.get("name") or "",
                        "arguments": inp if isinstance(inp, dict) else {},
                    }
                )
            elif btype == "tool_result":
                traj.append(
                    {
                        "kind": "tool_result",
                        "call_id": block.get("tool_use_id") or "",
                        "is_error": bool(block.get("is_error", False)),
                        "content": _result_text(block.get("content"))[:_RESULT_CONTENT_LIMIT],
                    }
                )
    return traj


def extract_trajectory(stdout: str) -> list[dict]:
    """Ordered tool_call/tool_result events for one turn's stream-json stdout."""
    return _claude_trajectory(list(iter_events(stdout)))


def skills_dispatched(trajectory: list[dict], skill_name: str | None = None) -> list[str]:
    """Ordered names of sub-skills the agent dispatched (with repeats).

    A `Skill` tool call carries the target in arguments["skill"]. When `skill_name` is
    given, the namespaced-tool fallback is also collected: a `tool_call` whose name IS
    the skill (exact `ingest` or namespaced `plugin:ingest`) — the same second fire
    shape `detect_skill_fired` recognizes. Without this, a skill that fires via the
    fallback sets `fired=True` but never lands in the dispatched set, so the
    `skill_invoked` checker grades it a false negative. Keeping the two detectors
    aligned means activation grades True wherever the skill demonstrably fired.
    """
    names: list[str] = []
    for ev in trajectory:
        if ev.get("kind") != "tool_call":
            continue
        name = ev.get("name")
        if name == "Skill":
            skill = ev.get("arguments", {}).get("skill")
            if isinstance(skill, str) and skill:
                names.append(skill)
        elif (
            skill_name
            and isinstance(name, str)
            and (name == skill_name or name.endswith(f":{skill_name}"))
        ):
            names.append(name)
    return names


def render_process_facts(trajectories: list[list[dict]], start: int = 1) -> str:
    """Compact, judge-readable summary of hidden tool activity across turns.

    Includes tool calls and sub-skill dispatches a final-message judge cannot see. Empty
    string when no turn carried tool activity. Turns with no tool calls are omitted.
    """
    lines: list[str] = []
    for i, traj in enumerate(trajectories, start):
        parts: list[str] = []
        for ev in traj:
            if ev.get("kind") != "tool_call":
                continue
            name = ev.get("name") or "?"
            if name == "Skill":
                skill = ev.get("arguments", {}).get("skill")
                parts.append(f"Skill({skill})" if skill else "Skill")
            else:
                parts.append(name)
        if parts:
            lines.append(f"Turn {i}: " + ", ".join(parts))
    return "\n".join(lines)


def split_session(session_text: str) -> list[tuple[int, str]]:
    """Split a consolidated session.jsonl back into (turn_idx, raw_stdout) blocks.

    Turns are delimited by a standalone `{"turn": N}` line; the lines between delimiters
    are that turn's verbatim stream-json. Inverse of how execution.py writes the file,
    so the structured trajectory regenerates deterministically. The recovered raw omits
    the trailing newline; consumers that call .splitlines() (i.e. extract_trajectory)
    are unaffected.
    """
    turns: list[tuple[int, str]] = []
    cur_idx: int | None = None
    cur_lines: list[str] = []
    for line in session_text.splitlines():
        stripped = line.strip()
        if stripped:
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict) and list(obj.keys()) == [TURN_DELIM]:
                if cur_idx is not None:
                    turns.append((cur_idx, "\n".join(cur_lines)))
                cur_idx = obj[TURN_DELIM]
                cur_lines = []
                continue
        cur_lines.append(line)
    if cur_idx is not None:
        turns.append((cur_idx, "\n".join(cur_lines)))
    return turns


def _looks_like_opencode(events: list[dict]) -> bool:
    """Route a regenerated turn to the right extractor.

    OpenCode events nest content under a top-level `part`; Claude events nest it under
    `message`. session.jsonl carries no agent marker, so we sniff the shape from the events
    themselves.
    """
    for ev in events:
        if isinstance(ev.get("part"), dict):
            return True
        if isinstance(ev.get("message"), dict):
            return False
    return False


def trajectory_from_session(session_text: str) -> list[dict]:
    """Deterministically regenerate the structured trajectory from a session.jsonl:.

    one self-contained event per element, each tagged with its `turn`. Auto-detects the
    agent's stream shape per turn (Claude vs OpenCode) so regeneration works for either
    agent's session.jsonl.
    """
    out: list[dict] = []
    for turn_idx, raw in split_session(session_text):
        events = list(iter_events(raw))
        if _looks_like_opencode(events):
            # Lazy import breaks the cycle: opencode.py imports iter_events from here.
            from evalspec.agents.opencode import _opencode_trajectory

            turn_traj = _opencode_trajectory(events)
        else:
            turn_traj = _claude_trajectory(events)
        for ev in turn_traj:
            out.append({TURN_DELIM: turn_idx, **ev})
    return out
