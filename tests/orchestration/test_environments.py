"""Tests for the host/guest execution environments."""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Callable

import pytest

from benchspec.orchestration.environments import GuestSandbox, Host, ProcResult, WatchedProc
from benchspec.testing import FakeExecEvent, FakeSandbox


def _run_host(
    monkeypatch: pytest.MonkeyPatch, *, exit_code: int, stdout: str = "", stderr: str = ""
) -> dict[str, object]:
    """Run Host.exec against a captured fake subprocess and return the captured kwargs."""
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Capture the call and return a canned CompletedProcess."""
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    asyncio.run(Host().exec(["codex", "exec"], env={}, timeout=5))
    return captured


def test_host_exec_never_inherits_the_harness_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A judge subprocess gets an explicit empty stdin, not the harness's fd 0."""
    captured = _run_host(monkeypatch, exit_code=0)

    assert captured["stdin"] == subprocess.DEVNULL


def test_host_exec_treats_empty_stdin_bytes_as_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guest-style `b""` EOF request maps to the same closed stdin as the default."""
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Capture the call and return a canned CompletedProcess."""
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    asyncio.run(Host().exec(["codex"], env={}, timeout=5, stdin=b""))

    assert captured["stdin"] == subprocess.DEVNULL


def test_host_exec_measures_the_process_wall_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host times the process itself, so a harness stream without timing still has one."""

    def slow_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        """Take a measurable moment, then return a canned CompletedProcess."""
        time.sleep(0.05)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", slow_run)

    result = asyncio.run(Host().exec(["codex", "exec"], env={}, timeout=5))

    assert result.duration_ms >= 50


def test_host_exec_rejects_stdin_it_cannot_feed() -> None:
    """The host never feeds a process input, so non-empty bytes are a programming error."""
    with pytest.raises(ValueError, match="cannot feed stdin"):
        asyncio.run(Host().exec(["codex"], env={}, timeout=5, stdin=b"payload"))


def test_require_success_surfaces_stdout_behind_noisy_stderr() -> None:
    """The real failure on stdout is shown even when stderr carries only progress noise."""
    result = ProcResult(
        command=["codex", "exec"],
        exit_code=1,
        stdout='{"type":"turn.failed","error":{"message":"usage limit reached"}}',
        stderr="Reading additional input from stdin...",
        duration_ms=1200,
    )

    with pytest.raises(RuntimeError) as exc_info:
        result.require_success()

    message = str(exc_info.value)
    assert "usage limit reached" in message
    assert "Reading additional input from stdin" in message


def test_require_success_reports_no_output_when_both_streams_empty() -> None:
    """A silent nonzero exit still raises, naming the command and the empty streams."""
    result = ProcResult(command=["codex"], exit_code=2, stdout="", stderr="", duration_ms=5)

    with pytest.raises(RuntimeError, match="`codex` exited 2: .no output."):
        result.require_success()


def _stdout_events(*chunks: bytes) -> list[FakeExecEvent]:
    """Stdout chunk events for a fake stream, in order."""
    return [FakeExecEvent("stdout", data=chunk) for chunk in chunks]


def _watch_for(stop_line: str, reason: str = "decided") -> Callable[[str], str | None]:
    """A watcher that stops on one exact line."""

    def watch(line: str) -> str | None:
        """Stop on `stop_line`."""
        return reason if line == stop_line else None

    return watch


def _exec_watched(
    guest: FakeSandbox, watch: Callable[[str], str | None], *, timeout: float = 5
) -> WatchedProc:
    """Stream a fake harness command through `GuestSandbox.exec_watched`."""
    return asyncio.run(
        GuestSandbox(guest).exec_watched(
            ["claude", "-p", "hi"], env={"HOME": "/root"}, timeout=timeout, watch=watch, stdin=b""
        )
    )


def test_exec_watched_kills_the_process_when_the_watcher_stops_it() -> None:
    """The tripping line is kept, the process is killed, and nothing after it is read."""
    guest = FakeSandbox(
        stream_events=[
            *_stdout_events(b"first\n", b"stop\n", b"never-read\n"),
            FakeExecEvent("exited", code=0),
        ]
    )

    proc = _exec_watched(guest, _watch_for("stop"))

    assert proc.stopped == "decided"
    assert proc.stdout == "first\nstop"
    assert proc.exit_code is None
    assert proc.timed_out is False
    assert guest.streams[0].killed is True


def test_exec_watched_runs_to_the_end_when_the_watcher_never_stops_it() -> None:
    """A process that exits on its own reports its exit code, stderr, and every line."""
    guest = FakeSandbox(
        stream_events=[
            *_stdout_events(b"one\n", b"two\n"),
            FakeExecEvent("stderr", data=b"warning"),
            FakeExecEvent("exited", code=3),
        ]
    )

    proc = _exec_watched(guest, _watch_for("absent"))

    assert proc.stopped is None
    assert proc.exit_code == 3
    assert proc.stdout == "one\ntwo"
    assert proc.stderr == "warning"
    assert guest.streams[0].killed is False


def test_exec_watched_reassembles_lines_split_across_chunks() -> None:
    """A record split across chunks, even mid-character, reaches the watcher whole."""
    record = '{"text":"héllo"}'.encode()
    split_inside_the_accent = record.index("é".encode()) + 1
    guest = FakeSandbox(
        stream_events=_stdout_events(
            record[:split_inside_the_accent], record[split_inside_the_accent:] + b"\n"
        )
    )

    proc = _exec_watched(guest, _watch_for('{"text":"héllo"}'))

    assert proc.stopped == "decided"


def test_exec_watched_keeps_a_trailing_line_without_a_newline() -> None:
    """The last line survives even when the process exits without terminating it."""
    guest = FakeSandbox(
        stream_events=[*_stdout_events(b"one\n", b"tail"), FakeExecEvent("exited", code=0)]
    )

    proc = _exec_watched(guest, _watch_for("absent"))

    assert proc.stdout == "one\ntail"


def test_exec_watched_kills_and_keeps_output_on_timeout() -> None:
    """A process that outlives its timeout is killed, with the lines it streamed kept."""
    guest = FakeSandbox(stream_events=_stdout_events(b"working\n"), stream_stalls=True)

    proc = _exec_watched(guest, _watch_for("absent"), timeout=0.05)

    assert proc.timed_out is True
    assert proc.stopped is None
    assert proc.stdout == "working"
    assert guest.streams[0].killed is True


def test_exec_watched_forwards_command_env_and_closed_stdin() -> None:
    """The guest stream gets the argv split, the env, and an explicit EOF on stdin."""
    guest = FakeSandbox(stream_events=[FakeExecEvent("exited", code=0)])

    _exec_watched(guest, _watch_for("absent"))

    (call,) = guest.calls
    assert call == (
        "exec_stream",
        "claude",
        ["-p", "hi"],
        {"cwd": None, "env": {"HOME": "/root"}, "stdin": b""},
    )
