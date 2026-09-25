"""Skill-activation detection primitives over an agent run.

`_tool_uses` yields the tool_use blocks in one Claude stream-json line;
`detect_skill_fired` decides whether a given skill fired anywhere in a stream;
`settled_once_dispatched` is the stop rule for a run whose every graded line is a skill
activation check, so the run can end the moment every verdict is fixed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator

from benchspec.grading.trajectory import skills_dispatched

# Whether every graded verdict of a run is fixed, read from the trajectory streamed so far.
StopRule = Callable[[list[dict]], bool]


def settled_once_dispatched(skills: Iterable[str]) -> StopRule:
    """The stop rule for a run whose every graded line is a skill activation check.

    A skill's first dispatch fixes its verdict for good (`invoked` passes and
    `not invoked` fails from then on), so the run is settled once every named skill has
    fired, and it runs on until then. A dispatch is read the way grading reads it: a
    `Skill` call naming the skill, or a tool call named for it, bare or namespaced
    (`plugin:skill`).

    Args:
        skills: The skills the run's activation lines name.

    Returns:
        A pure function of the trajectory so far.
    """
    wanted = frozenset(skills)

    def settled(trajectory: list[dict]) -> bool:
        """Whether every named skill has been dispatched."""
        return all(_dispatched(trajectory, skill) for skill in wanted)

    return settled


def _dispatched(trajectory: list[dict], skill: str) -> bool:
    """Whether `skill` was dispatched, exactly or namespaced, anywhere in `trajectory`."""
    return any(
        name == skill or name.endswith(f":{skill}")
        for name in skills_dispatched(trajectory, skill)
    )


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

