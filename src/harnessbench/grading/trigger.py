"""Skill-activation detection primitives for the sandbox routing path.

Routing runs inside a microVM (`sandbox.route_in_sandbox`); this module holds the pure
detection logic it feeds. `_tool_uses` yields the tool_use blocks in one stream-json
line; `detect_skill_fired` decides whether a given skill fired anywhere in a stream;
`dispatches_skill` spots the first skill dispatch in a single line (the sandbox router
early-stops there — routing is decided once a skill is picked, and waiting out the turn
burns minutes); `streamed_activity` tells a budget timeout that did real work (a clean
non-fire) from a launch stall (a retryable `RoutingError`). `dispatches_skill` and
`streamed_activity` are public so a `CodingAgent.detect_dispatch` impl can reuse the
Claude Code event-shape match without crossing a private boundary.
"""

from __future__ import annotations

import json


class RoutingError(RuntimeError):
    """A routing subprocess genuinely failed — a non-zero exit, or no model.

    activity at all (a budget timeout that streamed only the startup line, or nothing).
    Distinct from a clean run where the skill simply didn't fire — which includes a
    budget timeout after the agent began a turn but didn't route in time. The difference
    matters: a failed call counted as a non-fire is a false negative that looks exactly
    like a real routing result.
    """


def _tool_uses(line: str) -> object:
    """Yield each tool_use block in one stream-json line; skip empty/malformed lines.

    Names/inputs can be null in a partial event, so callers guard their own string ops —
    a stray null must not crash routing (the call site retries subprocess failures, not
    AttributeErrors, so a crash here would be fatal).
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


def detect_skill_fired(stream_lines: object, skill_name: str) -> bool:
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


def dispatches_skill(line: str, skill_name: str | None = None) -> bool:
    """True if the line shows a skill being routed to.

    Routing is decided there, so the sandbox router stops rather than wait out the skill's
    possibly minutes-long work.

    Matches the two fire shapes `detect_skill_fired` recognizes: any `Skill` tool_use
    (the agent routed to *some* skill), and — when `skill_name` is given — a tool_use
    whose name is our skill (the namespaced-tool fallback). Keeping the two detectors
    aligned means a fire via the fallback path isn't missed by the early-stop and then
    cut by the watchdog.
    """
    for block in _tool_uses(line):
        name = block.get("name")
        if name == "Skill":
            return True
        if (
            skill_name
            and isinstance(name, str)
            and (name == skill_name or name.endswith(f":{skill_name}"))
        ):
            return True
    return False


def streamed_activity(stream_lines: object) -> bool:
    """True if the model began a turn (an `assistant` event), not just the startup.

    `system`/init line. A budget timeout after real activity is a genuine non-fire (the
    agent worked but didn't route in time); a timeout that streamed only the init line
    is a launch stall worth retrying, not a routing answer.
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
