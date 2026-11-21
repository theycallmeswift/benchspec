"""Tailing a run's event files as they grow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchspec.watch import tail
from benchspec.watch.tail import EventTail, UnsupportedFormatError, check_format


def _line(event_type: str = "cell.delta", **fields: object) -> bytes:
    """One enveloped event line."""
    event = {"format_version": 1, "type": event_type, "ts_ms": 1, "worker": "gw0", **fields}

    return json.dumps(event, ensure_ascii=False).encode() + b"\n"


def _append(path: Path, data: bytes) -> None:
    """Append raw bytes to an event file."""
    with path.open("ab") as file:
        file.write(data)


def test_holds_a_partial_trailing_line_until_its_newline(tmp_path: Path) -> None:
    """Holds a partial trailing line until its newline."""
    path = tmp_path / "events-gw0.jsonl"
    tail = EventTail(tmp_path)
    whole = _line(text="first")
    _append(path, whole + _line(text="second")[:20])

    first = tail.poll()
    _append(path, _line(text="second")[20:])
    second = tail.poll()

    assert [event["text"] for event in first] == ["first"]
    assert [event["text"] for event in second] == ["second"]
    assert tail.poll() == []


def test_decodes_a_multibyte_character_split_across_reads(tmp_path: Path) -> None:
    """Decodes a multibyte character split across reads."""
    path = tmp_path / "events-gw0.jsonl"
    tail = EventTail(tmp_path)
    line = _line(text="naïve")
    split = line.index("ï".encode()) + 1
    _append(path, line[:split])

    assert tail.poll() == []
    _append(path, line[split:])

    assert tail.poll()[0]["text"] == "naïve"


def test_picks_up_a_worker_file_that_appears_mid_run(tmp_path: Path) -> None:
    """Picks up a worker file that appears mid run."""
    tail = EventTail(tmp_path)
    _append(tmp_path / "events-main.jsonl", _line(text="main"))
    tail.poll()

    _append(tmp_path / "events-gw1.jsonl", _line(text="worker"))

    assert [event["text"] for event in tail.poll()] == ["worker"]


def test_skips_a_malformed_line_and_reports_it_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skips a malformed line and reports it once."""
    path = tmp_path / "events-gw0.jsonl"
    _append(path, _line(text="before") + b"not json\n" + _line(text="after"))
    tail = EventTail(tmp_path)

    events = tail.poll()
    tail.poll()

    assert [event["text"] for event in events] == ["before", "after"]
    assert capsys.readouterr().err.splitlines() == [f"{path}:2: skipped a malformed event"]


def test_skips_a_file_removed_since_it_was_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skips a file removed since it was listed."""
    kept = tmp_path / "events-main.jsonl"
    _append(kept, _line(text="kept"))
    monkeypatch.setattr(tail, "event_files", lambda directory: [tmp_path / "gone.jsonl", kept])

    assert [event["text"] for event in EventTail(tmp_path).poll()] == ["kept"]


def test_refuses_another_format_version(tmp_path: Path) -> None:
    """Refuses another format version."""
    newer = _line().replace(b'"format_version": 1', b'"format_version": 2')
    _append(tmp_path / "events-main.jsonl", newer)
    message = "events format_version 2 is not supported by this benchspec"

    with pytest.raises(UnsupportedFormatError, match=message):
        EventTail(tmp_path).poll()

    with pytest.raises(UnsupportedFormatError, match=message):
        check_format(tmp_path)
