"""Where run events go: a JSONL file per process, or nowhere."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TextIO

from benchspec.events.schema import FORMAT_VERSION


class EventSink(Protocol):
    """Anything that accepts run events."""

    def emit(self, event: Mapping[str, object]) -> None:
        """Accept one event: its `type` plus that type's fields, without the envelope."""
        ...


class _NullSink:
    """Discards every event."""

    def emit(self, event: Mapping[str, object]) -> None:
        """Drop the event."""


NULL_SINK: EventSink = _NullSink()


class JsonlEventSink:
    """Appends each event, enveloped, as one line of this process's own file."""

    def __init__(self, path: Path, worker: str) -> None:
        """Bind to `path` without creating it.

        Args:
            path: The process's `events-<worker>.jsonl`.
            worker: The envelope's `worker`, matching the file suffix.
        """
        self._path = path
        self._worker = worker
        self._file: TextIO | None = None

    def emit(self, event: Mapping[str, object]) -> None:
        """Write the event as one whole line and flush it, creating the file on first use."""
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("a", encoding="utf-8")

        line = {
            "format_version": FORMAT_VERSION,
            "type": event["type"],
            "ts_ms": int(time.time() * 1000),
            "worker": self._worker,
            **event,
        }
        self._file.write(json.dumps(line) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Close the file if any event opened it."""
        if self._file is not None:
            self._file.close()
            self._file = None
