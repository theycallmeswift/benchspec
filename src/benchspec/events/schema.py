"""The shape of the run event stream, one `TypedDict` per event type.

Every event carries the `Envelope`; `EVENT_TYPES` maps each `type` to its shape. A
breaking change bumps `FORMAT_VERSION`, and readers reject any other version.
"""

from __future__ import annotations

from typing import TypedDict

from benchspec.orchestration.deadline import (
    PHASE_AGENT,
    PHASE_BIND,
    PHASE_BOOT,
    PHASE_GRADE,
    PHASE_SETUP,
)

FORMAT_VERSION = 2

PHASES = (PHASE_BIND, PHASE_BOOT, PHASE_SETUP, PHASE_AGENT, PHASE_GRADE)

DELTA_TEXT_LIMIT = 160
ERROR_MESSAGE_LIMIT = 500


class Envelope(TypedDict):
    """The fields every event carries."""

    format_version: int
    type: str
    ts_ms: int
    worker: str


class ArmInfo(TypedDict):
    """One configured arm, as `run.start` lists it."""

    name: str
    harness: str
    model: str
    effort: str
    timeout: int


class PlannedCell(TypedDict):
    """One planned sample; `applies` flags each assertion line graded in its arm."""

    cell: str
    skill: str
    eval_id: str
    arm: str
    sample: int
    applies: list[bool]


class RunStart(Envelope):
    """The run's plan, written once by the controller after collection."""

    iteration: str
    run_id: str
    set: str
    baseline: str | None
    arms: list[ArmInfo]
    judge_timeout: int
    cells: list[PlannedCell]
    cell_count: int


class CellStart(Envelope):
    """A sample began."""

    cell: str
    skill: str
    eval_id: str
    arm: str
    sample: int
    harness: str
    model: str


class CellPhase(Envelope):
    """A sample entered `phase`, which lasts until its next phase or closing event."""

    cell: str
    phase: str


class CellDelta(Envelope):
    """One tool call or assistant text block from the agent's turn."""

    cell: str
    kind: str
    name: str | None
    text: str


class Timing(TypedDict, total=False):
    """A sample's `timing.json`. `stopped`, `timed_out` and `timeout_phase` appear only when set."""

    duration_ms: int
    judge_ms: int
    total_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    first_output_ms: int | None
    cost_usd: float | None
    stopped: bool
    timed_out: bool
    timeout_phase: str


class CellDone(Envelope):
    """A sample finished and was graded. Its `timing.json` fields ride along, unlisted here."""

    cell: str
    passed: int
    total: int
    pass_rate: float | None
    wall_ms: int
    stopped: bool


class CellError(Envelope):
    """A sample ended in an infra error. Its `timing.json` fields ride along once written."""

    cell: str
    phase: str | None
    error_kind: str
    message: str
    wall_ms: int


class RunEnd(Envelope):
    """The run finished, written once by the controller after the reports."""

    exit_status: int
    wall_ms: int
    done: int
    errored: int
    benchmark_md: str | None
    benchmark_json: str | None


EVENT_TYPES: dict[str, type[Envelope]] = {
    "run.start": RunStart,
    "cell.start": CellStart,
    "cell.phase": CellPhase,
    "cell.delta": CellDelta,
    "cell.done": CellDone,
    "cell.error": CellError,
    "run.end": RunEnd,
}


def cell_key(*, skill: str, eval_id: str, arm: str, sample: int) -> str:
    """Name one sample the way its artifact path does: `<skill>/<eval_id>/<arm>/<sample>`."""
    return f"{skill}/{eval_id}/{arm}/{sample}"
