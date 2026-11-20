"""Read a run's event files back and check each line against the schema."""

from __future__ import annotations

import json
from pathlib import Path

from benchspec.events.schema import EVENT_TYPES, FORMAT_VERSION


def read_events(iteration_dir: Path) -> list[dict]:
    """Every event in every `events-*.jsonl` of an iteration, each checked against its type."""
    events: list[dict] = []
    for path in sorted(iteration_dir.glob("events-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            assert_matches_schema(event)
            assert event["worker"] == path.stem.removeprefix("events-")
            events.append(event)

    return events


def assert_matches_schema(event: dict) -> None:
    """The event carries exactly its type's keys; a closing event may add timing fields."""
    shape = EVENT_TYPES[event["type"]]

    assert event["format_version"] == FORMAT_VERSION
    assert shape.__required_keys__ <= event.keys(), event
    if event["type"] not in ("cell.done", "cell.error"):
        assert event.keys() <= shape.__required_keys__, event


def of_type(events: list[dict], event_type: str) -> list[dict]:
    """The events of one type, in order."""
    return [event for event in events if event["type"] == event_type]
