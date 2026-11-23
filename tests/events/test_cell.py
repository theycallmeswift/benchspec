"""Tests for the per-cell event emitter."""

from __future__ import annotations

from collections.abc import Mapping

from benchspec.agents.base import StreamDelta
from benchspec.events.cell import CellEvents
from benchspec.events.schema import DELTA_TEXT_LIMIT, ERROR_MESSAGE_LIMIT, PHASES, Timing

TIMING: Timing = {
    "duration_ms": 4740,
    "judge_ms": 1450,
    "total_tokens": 61234,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "input_tokens": 61000,
    "output_tokens": 234,
    "first_output_ms": 2000,
    "cost_usd": 0.0597,
}


class _Clock:
    """A hand-driven monotonic clock, in seconds."""

    def __init__(self) -> None:
        """Start at 100 s so no test depends on a zero origin."""
        self.now = 100.0

    def __call__(self) -> float:
        """The current reading."""
        return self.now


class _RecordingSink:
    """Keeps every event it is given."""

    def __init__(self) -> None:
        """Start empty."""
        self.events: list[Mapping[str, object]] = []

    def emit(self, event: Mapping[str, object]) -> None:
        """Record the event."""
        self.events.append(event)


def _started_cell() -> tuple[CellEvents, _RecordingSink, _Clock]:
    """A cell that has emitted `cell.start`, with its sink and clock."""
    sink, clock = _RecordingSink(), _Clock()
    cell = CellEvents(sink, "hello/greets/trial/0", now=clock)
    cell.start(
        skill="hello", eval_id="greets", arm="trial", sample=0, harness="codex", model="gpt-5.5"
    )

    return cell, sink, clock


def test_first_output_is_measured_from_the_agent_phase_to_the_first_delta() -> None:
    """First output is measured from the agent phase to the first delta."""
    cell, _sink, clock = _started_cell()
    clock.now += 3
    cell.phase("agent")
    clock.now += 2.37
    cell.delta(StreamDelta("text", None, "Hi Dana!"))
    clock.now += 5

    cell.delta(StreamDelta("tool_call", "Bash", "ls"))

    assert cell.first_output_ms == 2370


def test_first_output_is_null_without_a_delta() -> None:
    """First output is null without a delta."""
    cell, _sink, _clock = _started_cell()

    cell.phase("agent")

    assert cell.first_output_ms is None


def test_done_carries_wall_time_counts_and_the_timing_verbatim() -> None:
    """Done carries wall time counts and the timing verbatim."""
    cell, sink, clock = _started_cell()
    clock.now += 8.59

    cell.done(passed=1, total=2, timing=TIMING)

    assert sink.events[-1] == {
        "type": "cell.done",
        "cell": "hello/greets/trial/0",
        "passed": 1,
        "total": 2,
        "pass_rate": 0.5,
        "wall_ms": 8590,
        "stopped": False,
        **TIMING,
    }


def test_done_keeps_a_stopped_run_stopped_and_has_no_rate_without_graded_lines() -> None:
    """Done keeps a stopped run stopped and has no rate without graded lines."""
    cell, sink, _clock = _started_cell()

    cell.done(passed=0, total=0, timing={**TIMING, "stopped": True})

    assert sink.events[-1]["stopped"] is True
    assert sink.events[-1]["pass_rate"] is None


def test_phases_emit_in_order_and_a_repeat_is_silent() -> None:
    """Phases emit in order and a repeat is silent."""
    cell, sink, _clock = _started_cell()

    for phase in (*PHASES, "grade"):
        cell.phase(phase)

    assert [event["phase"] for event in sink.events[1:]] == list(PHASES)


def test_error_names_the_running_phase_and_carries_no_timing_it_never_reached() -> None:
    """Error names the running phase and carries no timing it never reached."""
    cell, sink, clock = _started_cell()
    cell.phase("bind")
    cell.phase("boot")
    cell.phase("setup")
    clock.now += 1.5

    cell.error(error_kind="exception", message="x" * 900, timing=None)

    error = sink.events[-1]
    assert error["phase"] == "setup"
    assert error["error_kind"] == "exception"
    assert error["wall_ms"] == 1500
    assert error["message"] == "x" * ERROR_MESSAGE_LIMIT
    assert error.keys().isdisjoint(TIMING)


def test_error_before_any_phase_has_a_null_phase() -> None:
    """Error before any phase has a null phase."""
    cell, sink, _clock = _started_cell()

    cell.error(error_kind="exception", message="snapshot build failed", timing=None)

    assert sink.events[-1]["phase"] is None


def test_delta_text_is_cut_to_the_limit() -> None:
    """Delta text is cut to the limit."""
    cell, sink, _clock = _started_cell()

    cell.delta(StreamDelta("tool_call", "Bash", "y" * 400))

    assert sink.events[-1] == {
        "type": "cell.delta",
        "cell": "hello/greets/trial/0",
        "kind": "tool_call",
        "name": "Bash",
        "text": "y" * DELTA_TEXT_LIMIT,
    }


def test_wait_names_the_backend_and_its_account_limit() -> None:
    """Wait names the backend at capacity and the limit it reported."""
    cell, sink, _clock = _started_cell()

    cell.wait(sandbox="e2b", limit=10)

    assert sink.events[-1] == {
        "type": "cell.wait",
        "cell": "hello/greets/trial/0",
        "sandbox": "e2b",
        "limit": 10,
    }
