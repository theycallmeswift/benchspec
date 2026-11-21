"""End-to-end tests for the events one cell writes.

Each test drives the real `run_eval_arm`, the real `SandboxSession`, the real Claude Code
adapter and a real `JsonlEventSink`. Only the guest is a double: a sandbox replaying a
stream captured from the live CLI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.config.arms import Arm
from benchspec.events.schema import PHASES
from benchspec.events.sink import JsonlEventSink
from benchspec.orchestration import execution, workspace
from benchspec.orchestration.execution import run_eval_arm
from benchspec.sandbox import registry
from benchspec.specs.discovery import EvalCase
from benchspec.testing import FakeExecEvent, FakeExecOutput, FakeSandbox
from tests.support.events import of_type, read_events

CAPTURE = (
    Path(__file__).resolve().parents[1] / "agents" / "fixtures" / "claude_stream_openrouter.jsonl"
).read_text(encoding="utf-8")
CELL = "hello/greets/trial/0"


def _replaying_guest(*, exit_code: int = 0, setup_exit_code: int = 0) -> FakeSandbox:
    """A guest whose agent turn streams the capture and whose `setup.sh` exits as told."""
    stream = [FakeExecEvent("stdout", data=(line + "\n").encode()) for line in CAPTURE.splitlines()]

    return FakeSandbox(
        stream_events=[*stream, FakeExecEvent("exited", code=exit_code)],
        shell_output=FakeExecOutput(exit_code=setup_exit_code, stderr_text="no such skill"),
    )


def _boot_guest_instead_of_a_vm(monkeypatch: pytest.MonkeyPatch, guest: FakeSandbox) -> None:
    """Make the production session boot `guest`, with no snapshot build or image probe."""

    async def create_sandbox(**kwargs: object) -> FakeSandbox:
        return guest

    backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="token", version="latest")
    monkeypatch.setattr(backend, "create_sandbox", create_sandbox)
    monkeypatch.setattr(execution, "resolve_sandbox", lambda name: backend)
    monkeypatch.setattr(execution, "make_agent", lambda harness=None, provider="default": agent)
    monkeypatch.setattr(execution, "ensure_snapshot", lambda agent, **kwargs: "snap")
    monkeypatch.setattr(execution, "_capture_sandbox_provenance", lambda *args: None)


def _grade_all_pass(assertions: list[str], *args: object, **kwargs: object) -> dict:
    """A judge that passes every line."""
    return {"assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in assertions]}


def _judge_down(assertions: list[str], *args: object, **kwargs: object) -> dict:
    """A judge whose CLI fails at the transport level."""
    raise RuntimeError("judge CLI exited 1: rate limited")


def _bind_first_line_only(text: str) -> dict | None:
    """Bind the file check to a checker that fails; punt everything else to the judge."""
    if text == "missing.md exists":
        return {"type": "deterministic", "checker": "file_exists", "path": "missing.md"}

    return None


def _run_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guest: FakeSandbox,
    *,
    grade: Callable[..., dict] = _grade_all_pass,
) -> tuple[list[dict], Path]:
    """Run one cell into a real event file; return its events and its artifact dir."""
    workspace.set_current_iteration("iteration_01")
    eval_dir = tmp_path / "skills" / "hello" / "evals" / "greets"
    eval_dir.mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    sink = JsonlEventSink(workspace.events_path(tmp_path, "main"), "main")
    _boot_guest_instead_of_a_vm(monkeypatch, guest)

    try:
        run_eval_arm(
            EvalCase(
                group="hello",
                eval_dir=eval_dir,
                eval_file=eval_dir / "eval.md",
                eval={
                    "id": "greets",
                    "prompt": "Say hi to Alice.",
                    "assertions": ["missing.md exists", "the greeting is formal"],
                },
            ),
            Arm("trial", "claude-code", "sonnet"),
            workdir,
            {},
            tmp_path,
            today="2099-01-01",
            repo_root=tmp_path,
            sample=0,
            grade=grade,
            bind=_bind_first_line_only,
            events=sink,
        )
    finally:
        sink.close()

    return read_events(workspace.iteration_root(tmp_path)), workspace.arm_dir(
        tmp_path, "hello", "greets", "trial", sample=0
    )


def test_a_clean_cell_writes_start_phases_deltas_and_done_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean cell writes start phases deltas and done in order."""
    events, _run_dir = _run_cell(tmp_path, monkeypatch, _replaying_guest())

    assert all(event["cell"] == CELL for event in events)
    assert [event.get("phase", event["type"]) for event in events] == [
        "cell.start",
        "bind",
        "boot",
        "setup",
        "agent",
        *["cell.delta"] * 7,
        "grade",
        "cell.done",
    ]
    assert events[0] | {"ts_ms": 0} == {
        "format_version": 2,
        "type": "cell.start",
        "ts_ms": 0,
        "worker": "main",
        "cell": CELL,
        "skill": "hello",
        "eval_id": "greets",
        "arm": "trial",
        "sample": 0,
        "harness": "claude-code",
        "model": "sonnet",
    }
    assert [event["phase"] for event in of_type(events, "cell.phase")] == list(PHASES)
    assert {(event["kind"], event["name"]) for event in of_type(events, "cell.delta")} == {
        ("tool_call", "Skill"),
        ("tool_call", "Bash"),
        ("tool_call", "Write"),
        ("text", None),
    }


def test_done_equals_the_written_timing_plus_the_grading_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done equals the written timing plus the grading counts."""
    events, run_dir = _run_cell(tmp_path, monkeypatch, _replaying_guest())

    timing = json.loads((run_dir / "timing.json").read_text())
    done = events[-1]
    envelope = {key: done[key] for key in ("format_version", "ts_ms", "worker")}
    assert done == {
        **envelope,
        "type": "cell.done",
        "cell": CELL,
        "passed": 1,
        "total": 2,
        "pass_rate": 0.5,
        "wall_ms": done["wall_ms"],
        "stopped": False,
        **timing,
    }
    assert timing["cost_usd"] == 0.05973615
    assert isinstance(timing["first_output_ms"], int)
    assert done["wall_ms"] >= timing["first_output_ms"]


def test_an_agent_that_exits_nonzero_ends_in_one_agent_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent that exits nonzero ends in one agent error."""
    events, run_dir = _run_cell(tmp_path, monkeypatch, _replaying_guest(exit_code=1))

    error = events[-1]
    assert (error["type"], error["error_kind"], error["phase"]) == ("cell.error", "agent", "grade")
    assert error["duration_ms"] == json.loads((run_dir / "timing.json").read_text())["duration_ms"]
    assert of_type(events, "cell.done") == []


def test_a_judge_transport_failure_ends_in_one_judge_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge transport failure ends in one judge error."""
    events, _run_dir = _run_cell(tmp_path, monkeypatch, _replaying_guest(), grade=_judge_down)

    error = events[-1]
    assert (error["type"], error["error_kind"]) == ("cell.error", "judge")
    assert error["message"] == "JUDGE INFRA ERROR: judge CLI exited 1: rate limited"
    assert of_type(events, "cell.done") == []


def test_a_failing_setup_script_emits_an_exception_error_and_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing setup script emits an exception error and still raises."""
    with pytest.raises(RuntimeError, match=r"setup\.sh failed") as raised:
        _run_cell(tmp_path, monkeypatch, _replaying_guest(setup_exit_code=1))

    events = read_events(workspace.iteration_root(tmp_path))
    error = events[-1]
    assert (error["type"], error["error_kind"], error["phase"]) == (
        "cell.error",
        "exception",
        "setup",
    )
    assert error["message"] == str(raised.value)[:500]
    assert "duration_ms" not in error
    assert [event["type"] for event in events].count("cell.error") == 1
