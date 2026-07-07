"""Tests for judge cli."""

import json
import subprocess
from typing import NoReturn

import pytest

from evalspec.agents import judge_cli
from evalspec.agents.judge_cli import run_host_judge


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> object:
    """Provide the fake proc test helper."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_host_judge_returns_stdout_on_healthy_run(monkeypatch: object) -> None:
    """Verify run host judge returns stdout on healthy run."""
    payload = json.dumps({"result": '{"assertions": []}', "is_error": False})
    monkeypatch.setattr(judge_cli.subprocess, "run", lambda *a, **k: _fake_proc(stdout=payload))

    assert run_host_judge("prompt", model="sonnet") == payload


def test_run_host_judge_raises_runtimeerror_on_nonzero_exit(
    monkeypatch: object,
) -> None:
    """Verify run host judge raises for runtimeerror on nonzero exit."""
    # A crashed/exited host CLI must surface as RuntimeError (caught upstream as
    # arm-level errored), not be laundered into fake JUDGE ERROR assertions.
    monkeypatch.setattr(
        judge_cli.subprocess,
        "run",
        lambda *a, **k: _fake_proc(returncode=1, stderr="connection reset"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        run_host_judge("prompt", model="sonnet")


def test_run_host_judge_raises_runtimeerror_on_is_error_envelope(
    monkeypatch: object,
) -> None:
    """Verify run host judge raises for runtimeerror on is error envelope."""
    # `claude -p` reports auth/rate-limit/quota failures with returncode=0 but
    # is_error=true in the JSON envelope; surface the real cause as RuntimeError.
    payload = json.dumps({"result": "Not logged in · Please run /login", "is_error": True})
    monkeypatch.setattr(judge_cli.subprocess, "run", lambda *a, **k: _fake_proc(stdout=payload))

    with pytest.raises(RuntimeError, match="is_error=true.*Not logged in"):
        run_host_judge("prompt", model="sonnet")


def test_run_host_judge_raises_runtimeerror_when_binary_missing(
    monkeypatch: object,
) -> None:
    """Verify run host judge raises for runtimeerror when binary missing."""

    # A1: a missing host `claude` (FileNotFoundError from subprocess) must normalize to
    # RuntimeError so the arm is marked errored — not caught as OSError and counted as an
    # honest 0% pass rate. Reachable via EVALSPEC_AGENT=opencode on a host without claude.
    def boom(*a: object, **k: object) -> NoReturn:
        """Boom."""
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")

    monkeypatch.setattr(judge_cli.subprocess, "run", boom)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        run_host_judge("prompt", model="sonnet")
