"""Read a run's `events-*.jsonl` files as they grow."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchspec.events.schema import FORMAT_VERSION

POLL_INTERVAL_S = 0.25
# The controller writes `run.start` and `run.end` to its own file.
CONTROLLER_EVENTS = "events-main.jsonl"


class UnsupportedFormatError(Exception):
    """An event file carries a `format_version` this benchspec cannot read."""

    def __init__(self, version: object) -> None:
        """Name the version found."""
        super().__init__(f"events format_version {version} is not supported by this benchspec")


def event_files(iteration_dir: Path) -> list[Path]:
    """The iteration's event files, in name order."""
    return sorted(iteration_dir.glob("events-*.jsonl"))


class EventTail:
    """Yields each complete event line once, across every worker's file."""

    def __init__(self, iteration_dir: Path) -> None:
        """Start at the beginning of every file, including ones that appear later."""
        self._iteration_dir = iteration_dir
        self._offsets: dict[Path, int] = {}
        self._line_numbers: dict[Path, int] = {}

    def poll(self) -> list[dict]:
        """Return the events written since the last poll.

        A trailing partial line is held back until its newline arrives. A complete line
        that is not a JSON object is reported once on stderr and skipped.

        Raises:
            UnsupportedFormatError: an event carries another `format_version`.
        """
        events: list[dict] = []

        for path in event_files(self._iteration_dir):
            events.extend(self._read(path))

        return events

    def _read(self, path: Path) -> list[dict]:
        """Read one file's new complete lines; a file removed since the glob has none."""
        try:
            with path.open("rb") as file:
                file.seek(self._offsets.get(path, 0))
                chunk = file.read()
        except OSError:
            return []

        complete, newline, _ = chunk.rpartition(b"\n")
        self._offsets[path] = self._offsets.get(path, 0) + len(complete) + len(newline)

        events: list[dict] = []
        if not newline:
            return events

        for line in complete.split(b"\n"):
            self._line_numbers[path] = self._line_numbers.get(path, 0) + 1
            event = _parse(line)
            if event is None:
                print(
                    f"{path}:{self._line_numbers[path]}: skipped a malformed event",
                    file=sys.stderr,
                )
                continue

            if event.get("format_version") != FORMAT_VERSION:
                raise UnsupportedFormatError(event.get("format_version"))

            events.append(event)

        return events


def _parse(line: bytes) -> dict | None:
    """One line as a JSON object, or None when it is not one."""
    try:
        event = json.loads(line)
    except ValueError:
        return None

    return event if isinstance(event, dict) else None


def planned_cell(iteration_dir: Path, cell: str) -> dict | None:
    """One sample as the controller's `run.start` plans it; None before that or if unplanned."""
    try:
        with (iteration_dir / CONTROLLER_EVENTS).open("rb") as file:
            for line in file:
                event = _parse(line)
                if event is not None and event.get("type") == "run.start":
                    return next(
                        (planned for planned in event["cells"] if planned["cell"] == cell), None
                    )
    except OSError:
        return None

    return None


def check_format(iteration_dir: Path) -> None:
    """Refuse an iteration whose event files start with an unsupported version.

    Raises:
        UnsupportedFormatError: a file's first line carries another `format_version`.
    """
    for path in event_files(iteration_dir):
        with path.open("rb") as file:
            first = _parse(file.readline())

        if first is not None and first.get("format_version") != FORMAT_VERSION:
            raise UnsupportedFormatError(first.get("format_version"))
