"""Tests for the host/guest execution environments."""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from benchspec.orchestration.environments import Host, ProcResult


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
