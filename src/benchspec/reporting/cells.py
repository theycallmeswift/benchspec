"""The `cells` progress format: one plain line per finished sample.

A worker records a cell's identity and result on its pytest report as JSON strings; the
process that prints parses them back and renders one line with `format_cell_line`.
Everything here is pure, so the same line is produced under a serial run, under xdist,
and in the docs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from benchspec.reporting import ansi

# The `user_properties` keys a cell's report carries its identity and its result under.
CELL_PROPERTY = "benchspec_cell"
RESULT_PROPERTY = "benchspec_cell_result"

NOT_REPORTED = "—"

_STATE_WIDTH = len("TIMEOUT")
_SAMPLE_WIDTH = len("s00")
_ASSERTIONS_WIDTH = len("0/0")
_SECONDS_WIDTH = len("600.0s")
_FIRST_OUTPUT_WIDTH = len("first 0.0s")
_MESSAGE_LIMIT = 120

_STATE_COLORS = {
    "PASS": ansi.GREEN,
    "FAIL": ansi.RED,
    "ERROR": ansi.RED,
    "TIMEOUT": ansi.YELLOW,
}

Timing = dict[str, int | float | bool | str | None]


@dataclass(frozen=True)
class CellIdentity:
    """Which sample a line is about: the eval, the arm, and the sample number."""

    skill: str
    eval_id: str
    arm: str
    sample: int

    @property
    def label(self) -> str:
        """The `<skill>/eval-<eval_id>` name the artifact tree files the eval under."""
        return f"{self.skill}/eval-{self.eval_id}"

    def to_json(self) -> str:
        """Serialize for a pytest report property."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> CellIdentity:
        """Parse what `to_json` wrote."""
        return cls(**json.loads(text))


@dataclass(frozen=True)
class CellResult:
    """What a finished sample measured: its assertion counts and its `timing.json` mapping."""

    passed: int
    total: int
    errored: bool
    timing: Timing

    @property
    def timed_out(self) -> bool:
        """Whether the cell hit its wall-clock cap."""
        return self.timing.get("timed_out") is True

    @property
    def not_applicable(self) -> bool:
        """Whether every assertion was scoped off this arm, leaving nothing to report."""
        return self.total == 0 and not self.errored

    def to_json(self) -> str:
        """Serialize for a pytest report property."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> CellResult:
        """Parse what `to_json` wrote."""
        return cls(**json.loads(text))


def assertion_counts(assertions: list[dict]) -> tuple[int, int]:
    """Count a `grading.json` assertion list as `(passed, total)`, leaving out scoped-off lines."""
    graded = [assertion for assertion in assertions if not assertion.get("skipped")]

    return sum(1 for assertion in graded if assertion.get("passed")), len(graded)


def cell_state(
    *, passed: int, total: int, errored: bool, timed_out: bool, failed_report: bool
) -> str:
    """Name a finished sample's state: `ERROR`, `TIMEOUT`, `FAIL` or `PASS`.

    Args:
        passed: Assertions that held.
        total: Assertions graded.
        errored: The cell recorded an infra error.
        timed_out: The cell hit its wall-clock cap.
        failed_report: The pytest report failed, as it does for an errored cell or one
            that never produced a result.

    Returns:
        `ERROR` for an infra error or a failed report, which outranks every other
        state; otherwise `TIMEOUT`, then `FAIL` when an assertion did not hold, then
        `PASS`.
    """
    if errored or failed_report:
        return "ERROR"
    if timed_out:
        return "TIMEOUT"
    if passed < total:
        return "FAIL"

    return "PASS"


def _seconds(milliseconds: object) -> str | None:
    """Render a millisecond reading as `2.1s`; None when it was not reported."""
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int | float):
        return None

    return f"{milliseconds / 1000:.1f}s"


def _cost(dollars: object) -> str:
    """Render a provider-reported cost as `$0.031`, or the not-reported dash."""
    if isinstance(dollars, bool) or not isinstance(dollars, int | float):
        return NOT_REPORTED

    return f"${dollars:.3f}"


def format_cell_line(
    *,
    index: int,
    total: int | None,
    cell: CellIdentity,
    result: CellResult | None,
    wall_seconds: float,
    failed_report: bool = False,
    message: str = "",
    label_width: int = 0,
    arm_width: int = 0,
    color: bool = False,
) -> str:
    """Render one finished sample as a single plain line.

    Args:
        index: How many samples have finished, this one included.
        total: How many samples the run will finish; None prints `?`.
        cell: The sample's identity.
        result: Its counts and timing; None when the cell ended without recording one.
        wall_seconds: Wall time of the cell's test call.
        failed_report: Whether the pytest report failed.
        message: The failure text; its first line, cut to 120 characters, ends an
            `ERROR` line.
        label_width: Width of the run's longest eval label, so lines share columns.
        arm_width: Width of the run's longest arm name.
        color: Wrap the state word in its ANSI color.

    Returns:
        `[n/N] STATE  <skill>/eval-<id>  <arm>  s<sample>  passed/total  time  first
        <time>  cost`, with a dash for anything not reported.
    """
    state = cell_state(
        passed=result.passed if result else 0,
        total=result.total if result else 0,
        errored=result.errored if result else False,
        timed_out=result.timed_out if result else False,
        failed_report=failed_report,
    )
    padded_state = state.ljust(_STATE_WIDTH)
    if color:
        padded_state = _STATE_COLORS[state] + state + ansi.RESET + padded_state[len(state) :]

    total_text = "?" if total is None else str(total)
    timing = result.timing if result else {}
    assertions = f"{result.passed}/{result.total}" if result and result.total else NOT_REPORTED
    first_output = _seconds(timing.get("first_output_ms")) or NOT_REPORTED

    fields = [
        padded_state,
        cell.label.ljust(label_width),
        cell.arm.ljust(arm_width),
        f"s{cell.sample}".ljust(_SAMPLE_WIDTH),
        assertions.ljust(_ASSERTIONS_WIDTH),
        f"{wall_seconds:.1f}s".rjust(_SECONDS_WIDTH),
        f"first {first_output}".ljust(_FIRST_OUTPUT_WIDTH),
        _cost(timing.get("cost_usd")),
    ]
    first_line = message.strip().partition("\n")[0][:_MESSAGE_LIMIT]
    if state == "ERROR" and first_line:
        fields.append(first_line)

    counter = f"[{str(index).rjust(len(total_text))}/{total_text}]"

    return f"{counter} " + "  ".join(fields)
