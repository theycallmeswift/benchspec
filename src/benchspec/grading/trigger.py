"""Skill-activation detection primitives and the early-stop rule for trigger-only runs.

`_tool_uses` yields the tool_use blocks in one Claude stream-json line;
`detect_skill_fired` decides whether a given skill fired anywhere in a stream;
`streamed_activity` tells a timed-out run that did real work from a launch stall.
`TriggerWatch` is the stop rule for a trigger-only run: every graded line is a skill
activation check, so the run can stop the moment every verdict is fixed, or once it has
spent its tool-call budget without fixing them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from benchspec.grading.trajectory import skills_dispatched

# Why a watched run was stopped before it ended on its own; recorded as `stopped`.
STOP_DECIDED = "decided"
STOP_BUDGET = "budget"
STOP_TIMEOUT = "timeout"

# Tool calls a trigger-only run may make with verdicts still open before it is stopped.
# Every in-repo sample that fires does so by its second call; a few calls of headroom
# keep a prompt that reads a file before routing honest.
DEFAULT_TRIGGER_BUDGET = 5

# Maps one raw stream line to the trajectory events it carries (the adapter's parser).
LineTrajectory = Callable[[str], list[dict]]
# Fed each stream line in order; returns a stop reason once the run should end.
LineWatcher = Callable[[str], "str | None"]


def _names_skill(dispatched: str, skill: str) -> bool:
    """Whether a dispatched name is `skill`, exactly or namespaced (`plugin:skill`)."""
    return dispatched == skill or dispatched.endswith(f":{skill}")


@dataclass(frozen=True)
class TriggerWatch:
    """The stop rule for a trigger-only run.

    Every open verdict belongs to one of `skills`, and each is fixed by that skill's
    first dispatch: `skill_invoked` passes and `not_skill_invoked` fails from then on.
    So the run is decided once every skill has fired. A run that makes `budget` tool
    calls with verdicts still open is stopped and graded as it stands.

    Attributes:
        skills: The skills the run's activation lines name.
        budget: The tool calls allowed before an undecided run is stopped.
    """

    skills: frozenset[str]
    budget: int = DEFAULT_TRIGGER_BUDGET

    def line_watcher(self, line_trajectory: LineTrajectory) -> LineWatcher:
        """Start a fresh watcher for one run.

        A dispatch is read from the same trajectory events grading reads, so the stop
        and the verdict can never disagree about whether a skill fired. A dispatch
        counts toward the budget, but is checked first: the call that decides the run
        stops it as decided.

        Args:
            line_trajectory: The adapter's per-line trajectory parser.

        Returns:
            A watcher that returns `STOP_DECIDED` or `STOP_BUDGET` once the run should
            stop, and None until then.
        """
        open_skills = set(self.skills)
        tool_calls_seen = 0

        def watch(line: str) -> str | None:
            """Advance the rule over one stream line."""
            nonlocal tool_calls_seen
            for event in line_trajectory(line):
                if event.get("kind") != "tool_call":
                    continue

                fired = {
                    skill
                    for skill in open_skills
                    if any(
                        _names_skill(name, skill) for name in skills_dispatched([event], skill)
                    )
                }
                open_skills.difference_update(fired)
                if not open_skills:
                    return STOP_DECIDED

                tool_calls_seen += 1
                if tool_calls_seen >= self.budget:
                    return STOP_BUDGET

            return None

        return watch


def _tool_uses(line: str) -> Iterator[dict]:
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

    A trigger-only run that times out after real activity is a genuine non-fire (the
    agent worked but never routed); one that streamed only the `system`/init line is a
    launch stall, an infra error.
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
