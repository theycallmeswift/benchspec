"""The events of one sample, emitted to whatever sink the run was given."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from benchspec.agents.base import StreamDelta
from benchspec.events.schema import (
    DELTA_TEXT_LIMIT,
    ERROR_MESSAGE_LIMIT,
)
from benchspec.events.sink import EventSink
from benchspec.orchestration.deadline import PHASE_AGENT


class CellEvents:
    """Emits one sample's events and times them on a monotonic clock."""

    def __init__(
        self, sink: EventSink, cell: str, *, now: Callable[[], float] = time.monotonic
    ) -> None:
        """Bind to a sink and a cell key.

        Args:
            sink: Where the events go.
            cell: The sample's `cell_key`.
            now: A monotonic clock in seconds, injectable so tests never wait.
        """
        self._sink = sink
        self._cell = cell
        self._now = now
        self._started = now()
        self._phase: str | None = None
        self._agent_started: float | None = None
        self._first_delta: float | None = None

    @property
    def first_output_ms(self) -> int | None:
        """Milliseconds from the agent phase's start to its first delta; None without one."""
        if self._agent_started is None or self._first_delta is None:
            return None

        return _milliseconds(self._first_delta - self._agent_started)

    def start(
        self, *, skill: str, eval_id: str, arm: str, sample: int, harness: str, model: str
    ) -> None:
        """Emit `cell.start` and start the cell's wall clock."""
        self._started = self._now()
        self._emit(
            "cell.start",
            {
                "skill": skill,
                "eval_id": eval_id,
                "arm": arm,
                "sample": sample,
                "harness": harness,
                "model": model,
            },
        )

    def phase(self, phase: str) -> None:
        """Emit `cell.phase` on entering `phase`; re-entering the current phase is silent."""
        if phase == self._phase:
            return

        self._phase = phase
        if phase == PHASE_AGENT:
            self._agent_started = self._now()

        self._emit("cell.phase", {"phase": phase})

    def wait(self, *, sandbox: str, limit: int | None) -> None:
        """Emit `cell.wait`: the boot is waiting for `sandbox` to free a slot.

        Args:
            sandbox: The backend at capacity, such as `e2b`.
            limit: The account's concurrent-sandbox limit; None when the backend did not
                name it.
        """
        self._emit("cell.wait", {"sandbox": sandbox, "limit": limit})

    def delta(self, delta: StreamDelta) -> None:
        """Emit `cell.delta`, its text cut to `DELTA_TEXT_LIMIT`."""
        if self._first_delta is None:
            self._first_delta = self._now()

        self._emit(
            "cell.delta",
            {"kind": delta.kind, "name": delta.name, "text": delta.text[:DELTA_TEXT_LIMIT]},
        )

    def done(self, *, passed: int, total: int, timing: Mapping[str, object]) -> None:
        """Emit `cell.done` for a graded sample, copying its `timing.json`."""
        self._emit(
            "cell.done",
            {
                "passed": passed,
                "total": total,
                "pass_rate": passed / total if total else None,
                "wall_ms": self._wall_ms(),
                "stopped": False,
                **timing,
            },
        )

    def error(
        self, *, error_kind: str, message: str, timing: Mapping[str, object] | None
    ) -> None:
        """Emit `cell.error`, naming the phase that was running.

        Args:
            error_kind: `agent`, `judge` or `exception`.
            message: The failure, cut to `ERROR_MESSAGE_LIMIT`.
            timing: The sample's `timing.json`; None when it was never written.
        """
        self._emit(
            "cell.error",
            {
                "phase": self._phase,
                "error_kind": error_kind,
                "message": message[:ERROR_MESSAGE_LIMIT],
                "wall_ms": self._wall_ms(),
                **(timing or {}),
            },
        )

    def _wall_ms(self) -> int:
        """Milliseconds since `cell.start`."""
        return _milliseconds(self._now() - self._started)

    def _emit(self, event_type: str, fields: Mapping[str, object]) -> None:
        """Send one of this cell's events to the sink."""
        self._sink.emit({"type": event_type, "cell": self._cell, **fields})


def _milliseconds(seconds: float) -> int:
    """Whole milliseconds in `seconds`."""
    return int(seconds * 1000)
