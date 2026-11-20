"""Tests for the cell deadline that every phase of a cell draws from."""

from __future__ import annotations

from benchspec.orchestration.deadline import (
    MINIMUM_PHASE_SECONDS,
    PHASE_BOOT,
    CellDeadline,
    unjudged_evidence,
)


class _Clock:
    """A hand-driven monotonic clock."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """The current reading."""
        return self.now


def test_remaining_counts_down_and_never_goes_negative() -> None:
    """The budget shrinks with the clock and bottoms out at zero once the cap passes."""
    clock = _Clock()
    deadline = CellDeadline(100, clock=clock)

    clock.now = 40
    assert deadline.remaining() == 60

    clock.now = 250
    assert deadline.remaining() == 0
    assert deadline.expired() is True


def test_a_phase_that_overruns_leaves_the_next_phase_no_budget() -> None:
    """Once a phase eats the whole cap, nothing remains for the phases after it."""
    clock = _Clock()
    deadline = CellDeadline(60, clock=clock)

    clock.now = 75

    assert deadline.remaining() == 0
    assert deadline.expired() is True


def test_phase_budget_keeps_the_reserve_for_later_phases() -> None:
    """The agent's budget is what is left minus the judge's reserved share."""
    clock = _Clock()
    deadline = CellDeadline(900, clock=clock)

    clock.now = 100

    assert deadline.phase_budget(reserve=300) == 500
    assert deadline.phase_budget() == 800


def test_phase_budget_is_floored_so_a_phase_always_gets_a_usable_timeout() -> None:
    """A reserve larger than the remaining time still leaves the minimum budget."""
    clock = _Clock()
    deadline = CellDeadline(100, clock=clock)

    clock.now = 90

    assert deadline.phase_budget(reserve=300) == MINIMUM_PHASE_SECONDS


def test_error_names_the_phase_and_the_cap() -> None:
    """The timeout a phase raises says which phase hit which cap."""
    error = CellDeadline(900).error(PHASE_BOOT)

    assert error.phase == "boot"
    assert str(error) == "boot hit the 900s cell cap"


def test_unjudged_evidence_names_the_phase_and_the_cap() -> None:
    """A line the cap left unjudged says why."""
    assert unjudged_evidence("boot", 900) == (
        "CELL TIMEOUT: boot hit the 900s cap before this line was judged"
    )
