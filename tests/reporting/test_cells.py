"""Tests for the `cells` progress line: state, format, and the report-property round trip."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchspec.reporting.cells import (
    CellIdentity,
    CellResult,
    Timing,
    assertion_counts,
    cell_state,
    format_cell_line,
)

RESULTS_DOC = Path(__file__).resolve().parents[2] / "docs" / "results.md"

GREETS = CellIdentity(skill="hello", eval_id="greets-by-name", arm="baseline", sample=0)
WRITES = CellIdentity(skill="hello", eval_id="writes-file", arm="trial", sample=0)
LABEL_WIDTH = len("hello/eval-greets-by-name")
ARM_WIDTH = len("baseline")


def _result(
    passed: int = 2, total: int = 2, *, errored: bool = False, **timing: int | float | bool | None
) -> CellResult:
    """Build a result whose timing holds only what the test names."""
    recorded: Timing = dict(timing)

    return CellResult(passed=passed, total=total, errored=errored, timing=recorded)


@pytest.mark.parametrize(
    ("passed", "total", "errored", "timed_out", "failed_report", "state"),
    [
        (2, 2, False, False, False, "PASS"),
        (1, 2, False, False, False, "FAIL"),
        (0, 3, False, True, False, "TIMEOUT"),
        (2, 2, False, True, False, "TIMEOUT"),
        (0, 3, True, False, True, "ERROR"),
        (0, 3, True, True, True, "ERROR"),
        (0, 0, False, False, True, "ERROR"),
    ],
)
def test_cell_state_names_the_outcome(
    passed: int, total: int, errored: bool, timed_out: bool, failed_report: bool, state: str
) -> None:
    """Verify cell state names the outcome."""
    assert (
        cell_state(
            passed=passed,
            total=total,
            errored=errored,
            timed_out=timed_out,
            failed_report=failed_report,
        )
        == state
    )


def test_a_sample_with_nothing_graded_is_not_applicable_unless_it_errored() -> None:
    """Verify a sample with nothing graded is not applicable unless it errored."""
    assert _result(0, 0).not_applicable
    assert not _result(0, 0, errored=True).not_applicable
    assert not _result(0, 1).not_applicable


def test_assertion_counts_leave_out_scoped_off_lines() -> None:
    """Verify assertion counts leave out scoped off lines."""
    assertions = [
        {"text": "a0", "passed": True},
        {"text": "a1", "passed": False},
        {"text": "a2", "passed": None, "skipped": True},
    ]

    assert assertion_counts(assertions) == (1, 2)


def test_pass_line_shows_every_reported_field() -> None:
    """Verify pass line shows every reported field."""
    line = format_cell_line(
        index=1,
        total=4,
        cell=GREETS,
        result=_result(2, 2, first_output_ms=2800, cost_usd=0.024),
        wall_seconds=31.4,
        label_width=LABEL_WIDTH,
        arm_width=ARM_WIDTH,
    )

    assert line == (
        "[1/4] PASS     hello/eval-greets-by-name  baseline  s0   2/2   31.4s  first 2.8s  $0.024"
    )


def test_fail_line_counts_the_assertions_that_held() -> None:
    """Verify fail line counts the assertions that held."""
    line = format_cell_line(
        index=2,
        total=4,
        cell=CellIdentity(skill="hello", eval_id="greets-by-name", arm="trial", sample=0),
        result=_result(1, 2, first_output_ms=2100, cost_usd=0.031),
        wall_seconds=38.2,
        label_width=LABEL_WIDTH,
        arm_width=ARM_WIDTH,
    )

    assert line == (
        "[2/4] FAIL     hello/eval-greets-by-name  trial     s0   1/2   38.2s  first 2.1s  $0.031"
    )


def test_timeout_line_keeps_its_partial_counts() -> None:
    """Verify timeout line keeps its partial counts."""
    line = format_cell_line(
        index=3,
        total=4,
        cell=CellIdentity(skill="hello", eval_id="writes-file", arm="baseline", sample=0),
        result=_result(0, 3, timed_out=True, first_output_ms=4000, cost_usd=0.212),
        wall_seconds=600.0,
        label_width=LABEL_WIDTH,
        arm_width=ARM_WIDTH,
    )

    assert line == (
        "[3/4] TIMEOUT  hello/eval-writes-file     baseline  s0   0/3  600.0s  first 4.0s  $0.212"
    )


def test_error_line_without_a_result_ends_with_the_first_failure_line() -> None:
    """Verify error line without a result ends with the first failure line."""
    line = format_cell_line(
        index=4,
        total=4,
        cell=WRITES,
        result=None,
        wall_seconds=9.7,
        failed_report=True,
        message="setup.sh exited 2\nTraceback follows",
        label_width=LABEL_WIDTH,
        arm_width=ARM_WIDTH,
    )

    assert line == (
        "[4/4] ERROR    hello/eval-writes-file     trial     s0   —      9.7s  first —     —"
        "  setup.sh exited 2"
    )


def test_error_message_is_cut_to_120_characters() -> None:
    """Verify error message is cut to 120 characters."""
    line = format_cell_line(
        index=1,
        total=1,
        cell=WRITES,
        result=None,
        wall_seconds=1.0,
        failed_report=True,
        message="x" * 500,
    )

    assert line.endswith("  " + "x" * 120)
    assert "x" * 121 not in line


def test_a_passing_line_never_carries_a_message() -> None:
    """Verify a passing line never carries a message."""
    line = format_cell_line(
        index=1, total=1, cell=GREETS, result=_result(), wall_seconds=1.0, message="ignored"
    )

    assert "ignored" not in line


@pytest.mark.parametrize(
    "timing",
    [{}, {"first_output_ms": None, "cost_usd": None}],
    ids=["missing", "null"],
)
def test_unreported_first_output_and_cost_print_a_dash(timing: Timing) -> None:
    """Verify unreported first output and cost print a dash."""
    line = format_cell_line(
        index=1,
        total=1,
        cell=GREETS,
        result=CellResult(passed=2, total=2, errored=False, timing=timing),
        wall_seconds=31.4,
    )

    assert line == (
        "[1/1] PASS     hello/eval-greets-by-name  baseline  s0   2/2   31.4s  first —     —"
    )


def test_counter_pads_to_the_width_of_the_total() -> None:
    """Verify counter pads to the width of the total."""
    line = format_cell_line(index=7, total=180, cell=GREETS, result=_result(), wall_seconds=1.0)

    assert line.startswith("[  7/180] PASS")


def test_unknown_total_prints_a_question_mark() -> None:
    """Verify unknown total prints a question mark."""
    line = format_cell_line(index=12, total=None, cell=GREETS, result=_result(), wall_seconds=1.0)

    assert line.startswith("[12/?] PASS")


@pytest.mark.parametrize(
    ("result", "failed_report", "colored_state"),
    [
        (_result(2, 2), False, "\x1b[32mPASS\x1b[0m"),
        (_result(1, 2), False, "\x1b[31mFAIL\x1b[0m"),
        (_result(0, 2, timed_out=True), False, "\x1b[33mTIMEOUT\x1b[0m"),
        (None, True, "\x1b[31mERROR\x1b[0m"),
    ],
)
def test_color_wraps_only_the_state_word(
    result: CellResult | None, failed_report: bool, colored_state: str
) -> None:
    """Verify color wraps only the state word."""
    plain = format_cell_line(
        index=1,
        total=1,
        cell=GREETS,
        result=result,
        wall_seconds=1.0,
        failed_report=failed_report,
    )

    colored = format_cell_line(
        index=1,
        total=1,
        cell=GREETS,
        result=result,
        wall_seconds=1.0,
        failed_report=failed_report,
        color=True,
    )

    assert "\x1b" not in plain
    assert colored_state in colored
    assert re.sub(r"\x1b\[\d+m", "", colored) == plain


def test_identity_and_result_survive_the_property_round_trip() -> None:
    """Verify identity and result survive the property round trip."""
    identity = CellIdentity(skill="hello", eval_id="greets-by-name", arm="trial", sample=2)
    result = CellResult(
        passed=1,
        total=2,
        errored=False,
        timing={"duration_ms": 38200, "first_output_ms": None, "cost_usd": None, "stopped": True},
    )

    assert CellIdentity.from_json(identity.to_json()) == identity
    assert CellResult.from_json(result.to_json()) == result


def test_results_doc_example_is_what_the_formatter_prints() -> None:
    """Verify results doc example is what the formatter prints."""
    label_width = len("hello-file/eval-writes-greeting-file")
    greets = {"skill": "hello", "eval_id": "greets-by-name", "sample": 0}
    writes = {"skill": "hello-file", "eval_id": "writes-greeting-file", "sample": 0}
    lines = [
        format_cell_line(
            index=1,
            total=4,
            cell=CellIdentity(arm="baseline", **greets),
            result=_result(2, 2, first_output_ms=2400, cost_usd=0.060),
            wall_seconds=31.4,
            label_width=label_width,
            arm_width=ARM_WIDTH,
        ),
        format_cell_line(
            index=2,
            total=4,
            cell=CellIdentity(arm="trial", **greets),
            result=_result(1, 2, first_output_ms=3100, cost_usd=0.071),
            wall_seconds=38.2,
            label_width=label_width,
            arm_width=ARM_WIDTH,
        ),
        format_cell_line(
            index=3,
            total=4,
            cell=CellIdentity(arm="baseline", **writes),
            result=_result(3, 3, first_output_ms=2800, cost_usd=0.083),
            wall_seconds=44.0,
            label_width=label_width,
            arm_width=ARM_WIDTH,
        ),
        format_cell_line(
            index=4,
            total=4,
            cell=CellIdentity(arm="trial", **writes),
            result=_result(0, 3, errored=True),
            wall_seconds=9.7,
            failed_report=True,
            message=(
                "AssertionError: trial run errored (see the arm's grading.json)\n"
                "assert not True"
            ),
            label_width=label_width,
            arm_width=ARM_WIDTH,
        ),
    ]

    assert "\n".join(lines) in RESULTS_DOC.read_text(encoding="utf-8")
