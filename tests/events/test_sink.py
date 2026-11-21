"""Tests for the JSONL event sink."""

from __future__ import annotations

import json
from pathlib import Path

from benchspec.events.sink import JsonlEventSink


def test_file_is_created_on_the_first_emit_and_not_before(tmp_path: Path) -> None:
    """File is created on the first emit and not before."""
    path = tmp_path / "iteration_01" / "events-main.jsonl"
    sink = JsonlEventSink(path, "main")

    assert not path.parent.exists()

    sink.emit({"type": "cell.phase", "cell": "hello/greets/trial/0", "phase": "bind"})

    assert path.is_file()


def test_each_event_is_one_enveloped_line_readable_before_close(tmp_path: Path) -> None:
    """Each event is one enveloped line readable before close."""
    path = tmp_path / "events-gw1.jsonl"
    sink = JsonlEventSink(path, "gw1")

    sink.emit({"type": "cell.phase", "cell": "hello/greets/trial/0", "phase": "bind"})
    sink.emit({"type": "cell.phase", "cell": "hello/greets/trial/0", "phase": "boot"})

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert isinstance(lines[0]["ts_ms"], int)
    assert lines[0] | {"ts_ms": 0} == {
        "format_version": 2,
        "type": "cell.phase",
        "ts_ms": 0,
        "worker": "gw1",
        "cell": "hello/greets/trial/0",
        "phase": "bind",
    }
    assert [line["phase"] for line in lines] == ["bind", "boot"]
    assert path.read_text(encoding="utf-8").endswith("\n")
