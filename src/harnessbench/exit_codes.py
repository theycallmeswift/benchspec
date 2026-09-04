"""The shared exit-code contract for the harnessbench CLI subcommands.

Every subcommand (`lint`, `analyze`, `sandbox:build`, `run`) reports the same four
outcomes so a caller can script against them: `0` success, `1` a finding or gate
failure, `2` a usage error caught before any paid arm runs, and `5` nothing to do.
The pytest-status translation lives here too so `run` and any other pytest-backed
path map raw pytest return codes through one place.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """The exit codes shared by every harnessbench subcommand."""

    SUCCESS = 0
    FINDING = 1
    USAGE = 2
    NOTHING_TO_DO = 5


def exit_code_for_pytest_status(status: int) -> int:
    """Map a raw pytest return code to the harnessbench exit-code contract.

    Args:
        status: The integer return code from a pytest invocation.

    Returns:
        The `ExitCode` this status corresponds to:

        - `0` (all passed) -> `SUCCESS`.
        - `1` (tests failed) -> `FINDING`.
        - `2` -> `USAGE`. A collection-time `pytest.UsageError` (an unknown `--set`,
          an unreadable `--config`) surfaces as pytest status 2, measured, not 4.
          Status 2 ALSO covers `KeyboardInterrupt` / an aborted session; mapping it
          to `USAGE` is an accepted tradeoff so the usage contract works, and the
          collection-UsageError path is the one that matters for the CLI.
        - `3` (internal error) -> `FINDING`, treated as an infra failure.
        - `4` (usage error) -> `USAGE`, kept for completeness.
        - `5` (no tests collected) -> `NOTHING_TO_DO`.
        - any other status -> `FINDING`, treating an unexpected code as a failure.
    """
    mapping = {
        0: ExitCode.SUCCESS,
        1: ExitCode.FINDING,
        2: ExitCode.USAGE,
        3: ExitCode.FINDING,
        4: ExitCode.USAGE,
        5: ExitCode.NOTHING_TO_DO,
    }
    return mapping.get(status, ExitCode.FINDING)
