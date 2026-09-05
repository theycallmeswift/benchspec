"""Tests for the host/guest execution environments."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from benchspec.orchestration.environments import Host, ProcResult


def _run_host(monkeypatch: object, *, exit_code: int, stdout: str = "", stderr: str = "") -> dict:
    """Run Host.exec against a captured fake subprocess and return the captured kwargs."""
    captured: dict = {}

    def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Capture the call and return a canned CompletedProcess."""
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    asyncio.run(Host().exec(["codex", "exec"], env={}, timeout=5))
    return captured


def test_host_exec_never_inherits_the_harness_stdin(monkeypatch: object) -> None:
    """A judge subprocess gets an explicit empty stdin, not the harness's fd 0."""
    captured = _run_host(monkeypatch, exit_code=0)

    assert captured["stdin"] == subprocess.DEVNULL


def test_host_exec_forwards_an_explicit_stdin_unchanged(monkeypatch: object) -> None:
    """An explicit stdin is passed straight through — only None becomes DEVNULL."""
    captured: dict = {}

    def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Capture the call and return a canned CompletedProcess."""
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    asyncio.run(Host().exec(["codex"], env={}, timeout=5, stdin=subprocess.PIPE))

    assert captured["stdin"] == subprocess.PIPE


def test_require_success_surfaces_stdout_behind_noisy_stderr() -> None:
    """The real failure on stdout is shown even when stderr carries only progress noise."""
    result = ProcResult(
        command=["codex", "exec"],
        exit_code=1,
        stdout='{"type":"turn.failed","error":{"message":"usage limit reached"}}',
        stderr="Reading additional input from stdin...",
    )

    with pytest.raises(RuntimeError) as exc_info:
        result.require_success()

    message = str(exc_info.value)
    assert "usage limit reached" in message
    assert "Reading additional input from stdin" in message


def test_require_success_reports_no_output_when_both_streams_empty() -> None:
    """A silent nonzero exit still raises, naming the command and the empty streams."""
    result = ProcResult(command=["codex"], exit_code=2, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="`codex` exited 2: .no output."):
        result.require_success()
