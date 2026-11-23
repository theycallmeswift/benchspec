"""Warn once per run, on its terminal, when its cells start waiting for a sandbox slot.

A cell waits in boot when the sandbox backend refuses it for capacity, and says so with
a `cell.wait` event from whichever process runs it. `benchspec run` follows the run's
event files from outside pytest, so one warning covers every worker.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Mapping
from pathlib import Path

from benchspec.watch.tail import EventTail

POLL_SECONDS = 1.0


def capacity_warning(wait: Mapping[str, object], *, workers: int | None) -> str:
    """The warning for a run's first `cell.wait`, naming the limit and a `--workers` to match.

    Args:
        wait: The `cell.wait` event.
        workers: The run's worker count; None when pytest's own `-n` set it.
    """
    sandbox = wait["sandbox"]
    limit = wait["limit"]

    if not isinstance(limit, int):
        return (
            f"warning: this {sandbox} account is at its concurrent-sandbox limit; cells past "
            "it wait in boot for a free one. Pass a lower --workers to match."
        )

    this_run = f" (this run has {workers})" if workers is not None else ""

    return (
        f"warning: this {sandbox} account runs at most {limit} sandboxes at once; cells past "
        f"that wait in boot for a free one. Pass --workers {limit} to match{this_run}."
    )


class CapacityNotice:
    """Follows one iteration's event files and warns on stderr at the first `cell.wait`."""

    def __init__(
        self, iteration_dir: Path, *, workers: int | None, poll_seconds: float = POLL_SECONDS
    ) -> None:
        """Bind to the run's iteration directory, which need not exist yet.

        Args:
            iteration_dir: The run's `tmp/evals/iteration_NN/`.
            workers: The run's worker count, for the warning; None when unknown.
            poll_seconds: How often the files are read.
        """
        self._tail = EventTail(iteration_dir)
        self._workers = workers
        self._poll_seconds = poll_seconds
        self._warned = False
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._follow, name="capacity-notice", daemon=True)

    def start(self) -> None:
        """Start following the event files in the background."""
        self._thread.start()

    def stop(self) -> None:
        """Stop following, after one last read for a wait the run ended on."""
        self._stopping.set()
        self._thread.join()
        self._check()

    def _follow(self) -> None:
        """Read the files every `poll_seconds` until stopped or warned."""
        while not self._stopping.wait(self._poll_seconds):
            if self._check():
                return

    def _check(self) -> bool:
        """Warn at a new `cell.wait`, once; return whether the warning has been given."""
        if self._warned:
            return True

        wait = next((event for event in self._tail.poll() if event["type"] == "cell.wait"), None)
        if wait is None:
            return False

        print(capacity_warning(wait, workers=self._workers), file=sys.stderr, flush=True)
        self._warned = True

        return True
