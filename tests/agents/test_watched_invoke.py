"""Tests for a trigger-only turn streamed through each adapter's `invoke` with a stop rule."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.agents.opencode import OpenCodeAgent
from benchspec.grading.trajectory import skills_dispatched
from benchspec.grading.trigger import STOP_DECIDED, STOP_TIMEOUT, TriggerWatch
from benchspec.orchestration.results import RunResult
from benchspec.testing import FakeExecEvent, FakeSandbox


@dataclass(frozen=True)
class _Harness:
    """One adapter plus real-shaped stream lines in its own output format."""

    agent: ClaudeCodeAgent | CodexAgent | OpenCodeAgent
    model: str
    startup: str
    hello_dispatch: str
    plain_tool_call: str


def _claude_tool_use(name: str, tool_input: dict) -> str:
    """A Claude Code stream-json assistant line carrying one tool_use and its usage."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": f"msg-{name}",
                "content": [{"type": "tool_use", "id": f"tu-{name}", "name": name,
                             "input": tool_input}],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5},
            },
        }
    )


def _codex_command(item_id: str, command: str) -> str:
    """A `codex exec --json` completed command_execution line."""
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": item_id, "type": "command_execution", "command": command,
                     "exit_code": 0, "status": "completed"},
        }
    )


def _opencode_tool(tool: str, tool_input: dict) -> str:
    """An OpenCode completed tool_use line."""
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1000,
            "part": {"type": "tool", "tool": tool,
                     "state": {"status": "completed", "input": tool_input}},
        }
    )


HARNESSES = {
    "claude-code": _Harness(
        agent=ClaudeCodeAgent(auth_value="token", version="latest"),
        model="sonnet",
        startup=json.dumps({"type": "system", "subtype": "init"}),
        hello_dispatch=_claude_tool_use("Skill", {"skill": "hello"}),
        plain_tool_call=_claude_tool_use("Bash", {"command": "ls"}),
    ),
    "codex": _Harness(
        agent=CodexAgent(auth_value="sk-test", auth_env="CODEX_API_KEY", version="latest"),
        model="gpt-5.5",
        startup=json.dumps({"type": "turn.started"}),
        hello_dispatch=_codex_command(
            "item_1", "/bin/bash -lc \"sed -n '1,200p' /home/benchspec/skills/hello/SKILL.md\""
        ),
        plain_tool_call=_codex_command("item_2", "/bin/bash -lc ls"),
    ),
    "opencode": _Harness(
        agent=OpenCodeAgent(auth_value="token", auth_env="OPENROUTER_API_KEY", version="v"),
        model="openrouter/anthropic/claude-sonnet-4.6",
        startup=json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        hello_dispatch=_opencode_tool("skill", {"name": "hello"}),
        plain_tool_call=_opencode_tool("bash", {"command": "ls"}),
    ),
}


def _stream(*lines: str, exit_code: int | None = None) -> list[FakeExecEvent]:
    """Stdout events for `lines`, then an exit event when `exit_code` is given."""
    events = [FakeExecEvent("stdout", data=(line + "\n").encode()) for line in lines]
    if exit_code is not None:
        events.append(FakeExecEvent("exited", code=exit_code))
    return events


def _invoke(
    harness: _Harness, guest: FakeSandbox, watch: TriggerWatch, *, timeout: int = 30
) -> RunResult:
    """Run one watched turn through the adapter's real `invoke`."""
    return asyncio.run(
        harness.agent.invoke(
            guest,
            "Say hi to Dana for me.",
            eval_id="fires",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model=harness.model,
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            timeout=timeout,
            watch=watch,
        )
    )


@pytest.mark.parametrize("harness_name", sorted(HARNESSES))
def test_watched_invoke_stops_on_the_deciding_dispatch(harness_name: str) -> None:
    """The first `hello` dispatch kills the agent; the run is graded, not errored."""
    harness = HARNESSES[harness_name]
    guest = FakeSandbox(
        stream_events=_stream(
            harness.startup, harness.hello_dispatch, harness.plain_tool_call, exit_code=137
        )
    )

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})))

    assert result.stopped == STOP_DECIDED
    assert result.is_error is False
    assert skills_dispatched(result.trajectory, "hello") == ["hello"]
    assert harness.plain_tool_call not in result.raw
    assert guest.streams[0].killed is True
    assert [call[0] for call in guest.calls] == ["exec_stream"]


@pytest.mark.parametrize("harness_name", sorted(HARNESSES))
def test_watched_invoke_lets_a_run_that_never_fires_reach_its_end(harness_name: str) -> None:
    """Tool calls that are not the watched skill never stop the run."""
    harness = HARNESSES[harness_name]
    guest = FakeSandbox(
        stream_events=_stream(
            harness.startup, *[harness.plain_tool_call] * 8, exit_code=0
        )
    )

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})))

    assert result.stopped is None
    assert len(result.trajectory) == 8
    assert skills_dispatched(result.trajectory, "hello") == []
    assert guest.streams[0].killed is False


@pytest.mark.parametrize("harness_name", sorted(HARNESSES))
def test_watched_invoke_that_ends_on_its_own_is_not_marked_stopped(harness_name: str) -> None:
    """A run that exits before any stop keeps the adapter's usual exit handling."""
    harness = HARNESSES[harness_name]
    guest = FakeSandbox(stream_events=_stream(harness.startup, exit_code=2))

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})))

    assert result.stopped is None
    assert result.is_error is True


def test_watched_invoke_reads_partial_usage_from_a_stopped_claude_stream() -> None:
    """Usage streamed before the stop is recorded; each message's usage counts once."""
    harness = HARNESSES["claude-code"]
    guest = FakeSandbox(
        stream_events=_stream(harness.startup, harness.plain_tool_call, harness.hello_dispatch)
    )

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})))

    assert result.stopped == STOP_DECIDED
    assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (20, 180, 10)
    assert result.total_tokens == 210


def test_watched_invoke_times_out_after_model_activity_as_a_graded_non_fire() -> None:
    """A run that worked but never fired before the timeout is graded, not errored."""
    harness = HARNESSES["claude-code"]
    working = json.dumps({"type": "assistant", "message": {"content": [{"type": "text",
                                                                          "text": "thinking"}]}})
    guest = FakeSandbox(stream_events=_stream(harness.startup, working), stream_stalls=True)

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})), timeout=1)

    assert result.stopped == STOP_TIMEOUT
    assert result.is_error is False


def test_watched_invoke_stall_with_no_model_activity_is_an_infra_error() -> None:
    """A run that only started up before the timeout is a launch stall: errored."""
    harness = HARNESSES["claude-code"]
    guest = FakeSandbox(stream_events=_stream(harness.startup), stream_stalls=True)

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})), timeout=1)

    assert result.is_error is True
    assert result.stopped is None
    assert "no model activity before the 1s timeout" in result.result_text


def test_unwatched_invoke_keeps_the_buffered_exec() -> None:
    """Without a stop rule the turn runs through the buffered exec, never a stream."""
    harness = HARNESSES["claude-code"]
    guest = FakeSandbox()

    asyncio.run(
        harness.agent.invoke(
            guest,
            "Greet Alice.",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert [call[0] for call in guest.calls] == ["exec"]


def test_watched_invoke_keeps_the_harness_own_duration_when_its_events_carry_one() -> None:
    """A stopped OpenCode run is timed from its event timestamps, like a full run."""
    harness = HARNESSES["opencode"]
    step_start = json.dumps(
        {"type": "step_start", "timestamp": 1000, "part": {"type": "step-start"}}
    )
    dispatch = json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1364,
            "part": {"type": "tool", "tool": "skill",
                     "state": {"status": "completed", "input": {"name": "hello"}}},
        }
    )
    guest = FakeSandbox(stream_events=_stream(step_start, dispatch))

    result = _invoke(harness, guest, TriggerWatch(frozenset({"hello"})))

    assert result.stopped == STOP_DECIDED
    assert result.duration_ms == 364
