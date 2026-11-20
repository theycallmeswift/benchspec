"""Tests for the live deltas and the cost each adapter reads from its own stream."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from benchspec.agents import CodingAgent
from benchspec.agents.base import StreamDelta, text_delta, tool_call_delta
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent, parse_codex_jsonl
from benchspec.agents.opencode import OpenCodeAgent, parse_opencode_jsonl
from benchspec.orchestration.results import RunResult, parse_stream_run
from benchspec.testing import FakeExecEvent, FakeSandbox

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_CAPTURE = (FIXTURES / "claude_stream_openrouter.jsonl").read_text(encoding="utf-8")
CODEX_CAPTURE = (FIXTURES / "codex_exec_openrouter.jsonl").read_text(encoding="utf-8")
OPENCODE_CAPTURE = (FIXTURES / "opencode_run_openrouter.jsonl").read_text(encoding="utf-8")

ECHO_STYLE = (
    'echo "GREETING_STYLE=${GREETING_STYLE:-casual}" && '
    'echo "GREETING_LOCALE=${GREETING_LOCALE:-en-US}"'
)

CAPTURES = {
    "claude-code": (
        ClaudeCodeAgent(auth_value="token", version="latest"),
        CLAUDE_CAPTURE,
        [
            StreamDelta("tool_call", "Skill", "hello"),
            StreamDelta(
                "text",
                None,
                "I'll check the environment variables and write Alice's greeting file.",
            ),
            StreamDelta("tool_call", "Bash", ECHO_STYLE),
            StreamDelta(
                "text",
                None,
                "`GREETING_STYLE=formal`, `GREETING_LOCALE=en-US` — "
                "using the formal en-US greeting.",
            ),
            StreamDelta("tool_call", "Bash", "mkdir -p /workspace/Greetings"),
            StreamDelta("tool_call", "Write", "/workspace/Greetings/Alice.md"),
            StreamDelta(
                "text",
                None,
                "Greeting written to `./Greetings/Alice.md` using the formal en-US style.",
            ),
        ],
    ),
    "codex": (
        CodexAgent(auth_value="sk-test", auth_env="CODEX_API_KEY", version="latest"),
        CODEX_CAPTURE,
        [
            StreamDelta("tool_call", "command_execution", "/bin/bash -lc pwd"),
            StreamDelta("text", None, "Hello, Alice!"),
        ],
    ),
    "opencode": (
        OpenCodeAgent(auth_value="token", auth_env="OPENROUTER_API_KEY", version="latest"),
        OPENCODE_CAPTURE,
        [
            StreamDelta("tool_call", "bash", "pwd"),
            StreamDelta("text", None, "Hello, Alice!"),
        ],
    ),
}


def _invoke(
    agent: CodingAgent,
    guest: FakeSandbox,
    *,
    heard: list[StreamDelta] | None = None,
    timeout: float = 30,
) -> RunResult:
    """Run one turn through the adapter's real `invoke`, collecting deltas into `heard`."""
    return asyncio.run(
        agent.invoke(
            guest,
            "Say hi to Alice.",
            eval_id="greets",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            timeout=timeout,
            on_delta=None if heard is None else heard.append,
        )
    )


def _replay(capture: str, *, exit_code: int | None = 0, stalls: bool = False) -> FakeSandbox:
    """A guest that streams `capture` line by line, then exits or stalls."""
    events = [FakeExecEvent("stdout", data=(line + "\n").encode()) for line in capture.splitlines()]
    if exit_code is not None:
        events.append(FakeExecEvent("exited", code=exit_code))

    return FakeSandbox(stream_events=events, stream_stalls=stalls)


@pytest.mark.parametrize("harness", sorted(CAPTURES))
def test_invoke_reports_one_delta_per_tool_call_and_text_block(harness: str) -> None:
    """Invoke reports one delta per tool call and text block."""
    agent, capture, deltas = CAPTURES[harness]
    heard: list[StreamDelta] = []

    _invoke(agent, _replay(capture), heard=heard)

    assert heard == deltas


@pytest.mark.parametrize("harness", sorted(CAPTURES))
def test_listening_for_deltas_does_not_change_the_run(harness: str) -> None:
    """Listening for deltas does not change the run."""
    agent, capture, _deltas = CAPTURES[harness]

    listened = _invoke(agent, _replay(capture), heard=[])
    unheard = _invoke(agent, _replay(capture))

    assert listened.is_error is False
    assert listened.result_text == unheard.result_text
    assert listened.raw == unheard.raw
    assert listened.trajectory == unheard.trajectory
    assert listened.total_tokens == unheard.total_tokens
    assert listened.cost_usd == unheard.cost_usd


@pytest.mark.parametrize("harness", sorted(CAPTURES))
def test_a_nonzero_exit_is_still_errored_while_listening(harness: str) -> None:
    """A nonzero exit is still errored while listening."""
    agent, capture, deltas = CAPTURES[harness]
    heard: list[StreamDelta] = []

    result = _invoke(agent, _replay(capture, exit_code=3), heard=heard)

    assert result.is_error is True
    assert heard == deltas


@pytest.mark.parametrize("harness", sorted(CAPTURES))
def test_a_timeout_after_activity_is_timed_out_and_one_with_none_is_errored(harness: str) -> None:
    """A timeout after activity is timed out and one with none is errored."""
    agent, capture, deltas = CAPTURES[harness]
    heard: list[StreamDelta] = []

    stalled = _replay(capture, exit_code=None, stalls=True)

    active = _invoke(agent, stalled, heard=heard, timeout=0.01)
    silent = _invoke(agent, FakeSandbox(stream_stalls=True), heard=[], timeout=0.01)

    assert (active.timed_out, active.is_error) == (True, False)
    assert heard == deltas
    assert (silent.timed_out, silent.is_error) == (False, True)


@pytest.mark.parametrize(
    ("arguments", "summary"),
    [
        ({"command": "ls -la", "path": "/workspace"}, "ls -la"),
        ({"file_path": "/workspace/a.md", "content": "hi"}, "/workspace/a.md"),
        ({"pattern": "TODO", "glob": "*.py"}, "TODO"),
        ({"skill": "hello"}, "hello"),
        ({"command": "echo one\necho two"}, "echo one echo two"),
        ({"url": "https://example.com", "depth": 2}, '{"url":"https://example.com","depth":2}'),
        ({}, "{}"),
    ],
)
def test_tool_call_summary_prefers_the_telling_argument_then_compact_json(
    arguments: dict, summary: str
) -> None:
    """Tool call summary prefers the telling argument then compact json."""
    call = {"kind": "tool_call", "id": "t1", "name": "Tool", "arguments": arguments}

    assert tool_call_delta(call) == StreamDelta("tool_call", "Tool", summary)


@pytest.mark.parametrize(
    ("block", "delta"),
    [
        ("\n\n  Hi Dana!  \nSwift says hello.", StreamDelta("text", None, "Hi Dana!")),
        ("  \n\n", None),
    ],
)
def test_text_delta_is_the_first_non_empty_line(block: str, delta: StreamDelta | None) -> None:
    """Text delta is the first non empty line."""
    assert text_delta(block) == delta


def test_claude_cost_is_the_result_events_total() -> None:
    """Claude cost is the result events total."""
    assert parse_stream_run(CLAUDE_CAPTURE, "greets", "trial", None).cost_usd == 0.05973615


def test_claude_stream_cut_before_its_result_has_no_cost() -> None:
    """Claude stream cut before its result has no cost."""
    truncated = "\n".join(CLAUDE_CAPTURE.splitlines()[:-1])

    assert parse_stream_run(truncated, "greets", "trial", None).cost_usd is None


def test_opencode_cost_sums_every_finished_step() -> None:
    """Opencode cost sums every finished step."""
    result = parse_opencode_jsonl(OPENCODE_CAPTURE, "greets", "trial", None)

    assert result.cost_usd == pytest.approx(0.04133025 + 0.00377505)


def test_opencode_stream_with_no_finished_step_has_no_cost() -> None:
    """Opencode stream with no finished step has no cost."""
    first_line = OPENCODE_CAPTURE.splitlines()[0]

    assert parse_opencode_jsonl(first_line, "greets", "trial", None).cost_usd is None


def test_codex_reports_no_cost() -> None:
    """Codex reports no cost."""
    assert parse_codex_jsonl(CODEX_CAPTURE, "greets", "trial", None).cost_usd is None
