"""The wall-clock cap on one cell, shared by every phase that runs inside it.

A cell's phases (bind, boot, setup, agent, grade) draw from one `CellDeadline`, each
clamped to what the earlier ones left. Blocking calls cannot be interrupted, so the cap
is enforced by clamping and boundary checks, never by signals.
"""

from __future__ import annotations

import time
from collections.abc import Callable

PHASE_BIND = "bind"
PHASE_BOOT = "boot"
PHASE_SETUP = "setup"
PHASE_AGENT = "agent"
PHASE_GRADE = "grade"

# Shortest budget given a phase that still runs, so a timeout is never zero or negative.
MINIMUM_PHASE_SECONDS = 1.0


class CellTimeoutError(TimeoutError):
    """The cell's cap fired in `phase`."""

    def __init__(self, phase: str, cap_seconds: int) -> None:
        """Record the phase that was running and the cap it hit."""
        super().__init__(f"{phase} hit the {cap_seconds}s cell cap")
        self.phase = phase
        self.cap_seconds = cap_seconds


class CellDeadline:
    """A monotonic countdown from the cell's start to its cap."""

    def __init__(self, cap_seconds: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Start the countdown now.

        Args:
            cap_seconds: The cell's whole wall-clock budget.
            clock: A monotonic clock in seconds, injectable so tests never wait.
        """
        self.cap_seconds = cap_seconds
        self._clock = clock
        self._expires_at = clock() + cap_seconds

    def remaining(self) -> float:
        """Seconds left in the budget, never negative."""
        return max(0.0, self._expires_at - self._clock())

    def phase_budget(self, *, reserve: float = 0.0) -> float:
        """Seconds a running phase may use, keeping `reserve` for the phases after it.

        Floored at `MINIMUM_PHASE_SECONDS`.
        """
        return max(MINIMUM_PHASE_SECONDS, self.remaining() - reserve)


def unjudged_evidence(phase: str, cap_seconds: int) -> str:
    """The evidence on a judge-bound line the cap left no time to judge."""
    return f"CELL TIMEOUT: {phase} hit the {cap_seconds}s cap before this line was judged"
