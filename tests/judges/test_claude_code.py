"""Tests for the Claude Code judge runner (native envelope + infra-error handling)."""

import json
import subprocess

import pytest

from evalspec.judges import claude_code


def _fake_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_returns_native_result_envelope(monkeypatch: object) -> None:
    """Verify run returns the native result envelope."""
    payload = json.dumps({"result": '{"assertions": []}', "is_error": False})
    captured = {}

    def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Capture the command and env and return a fake process."""
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = claude_code.run(
        "grade this", model="sonnet", effort="medium", timeout=300,
        harness_args=["--plugin-dir", "/x"], env={"FOO": "bar"},
    )

    assert out == payload
    assert json.loads(out)["result"] == '{"assertions": []}'
    assert captured["command"][:2] == ["claude", "-p"]
    assert "--model" in captured["command"]
    assert "sonnet" in captured["command"]
    assert "--effort" in captured["command"]
    assert "medium" in captured["command"]
    assert captured["command"][-2:] == ["--plugin-dir", "/x"]
    assert captured["env"]["FOO"] == "bar"


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="connection reset"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_is_error_envelope(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an is_error envelope."""
    payload = json.dumps({"result": "Not logged in", "is_error": True})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=payload))
    with pytest.raises(RuntimeError, match="is_error=true.*Not logged in"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch: object) -> None:
    """Verify run raises RuntimeError when the binary is missing."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")

    monkeypatch.setattr(subprocess, "run", raise_not_found)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_unknown_model(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake or wrong-family model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="error: unknown model 'gpt-5.5'"),
    )
    with pytest.raises(RuntimeError, match="unknown model"):
        claude_code.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch: object) -> None:
    """Verify probe_version returns None on failure."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    assert claude_code.probe_version() is None


def test_probe_version_returns_stripped_stdout(monkeypatch: object) -> None:
    """Verify probe_version returns stripped stdout."""
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout="2.1.0\n"))
    assert claude_code.probe_version() == "2.1.0"
