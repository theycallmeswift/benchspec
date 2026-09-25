"""Skill-activation detection primitives over a Claude Code stream-json run.

`_tool_uses` yields the tool_use blocks in one stream-json line; `detect_skill_fired`
decides whether a given skill fired anywhere in a stream; `streamed_activity` tells a
timed-out run that did real work from a launch stall. `streamed_activity` is public so a
`CodingAgent.streamed_activity` impl can reuse the Claude Code event-shape match without
crossing a private boundary.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator


def _tool_uses(line: str) -> Iterator[dict]:
    """Yield each tool_use block in one stream-json line; skip empty/malformed lines.

    Names/inputs can be null in a partial event, so callers guard their own string ops —
    a stray null must not crash the parse of a run that already happened.
    """
    line = line.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return  # valid JSON but a non-object line (bare scalar/array)
    message = event.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def detect_skill_fired(stream_lines: Iterable[str], skill_name: str) -> bool:
    """Return whether stream events show the requested skill firing."""
    for line in stream_lines:
        for block in _tool_uses(line):
            name = block.get("name")
            inp = block.get("input")
            inp = inp if isinstance(inp, dict) else {}
            # Primary path: Skill tool with input["skill"] containing the skill name.
            # Matches exact ("my-skill") or namespaced ("my-plugin:my-skill").
            if name == "Skill":
                skill_value = inp.get("skill")
                if isinstance(skill_value, str) and (
                    skill_value == skill_name or skill_value.endswith(f":{skill_name}")
                ):
                    return True
            # Fallback: tool_use whose name itself is the skill (exact or namespaced).
            elif isinstance(name, str) and (name == skill_name or name.endswith(f":{skill_name}")):
                return True
    return False


def streamed_activity(stream_lines: Iterable[str]) -> bool:
    """True if the model began a turn (an `assistant` event), not just the startup line.

    A timeout after real activity is a genuine non-fire (the agent worked but never
    fired); a timeout that streamed only the `system`/init line is a launch stall.
    """
    for line in stream_lines:
        text = line.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "assistant":
            return True
    return False
