"""Tests for the Codex judge runner (envelope + infra-error handling)."""

import json
import subprocess

import pytest

from evalspec.judges import codex as codex_judge


def _fake_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stream(*events: dict) -> str:
    """Serialize events as a newline-terminated JSONL stream."""
    return "\n".join(json.dumps(event) for event in events) + "\n"


def test_run_wraps_final_agent_message_in_result_envelope(monkeypatch: object) -> None:
    """Verify run wraps the final agent message in a result envelope."""
    verdict = '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'
    stdout = _stream(
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": verdict}},
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    )
    captured = {}

    def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Capture the command and return a successful process."""
        captured["command"] = command
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = codex_judge.run(
        "grade this", model="gpt-5.5", effort="medium", timeout=300,
        harness_args=["--sandbox", "read-only"], env={},
    )

    assert json.loads(out)["result"] == verdict
    assert captured["command"][:3] == ["codex", "exec", "--json"]
    assert "-m" in captured["command"]
    assert "gpt-5.5" in captured["command"]
    assert captured["command"][-1] == "grade this"  # trailing positional prompt


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on a nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="network error"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*network error"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_error_event(monkeypatch: object) -> None:
    """Verify a harness error event (surfaced by the shared parser) raises RuntimeError."""
    stdout = _stream({"type": "error", "message": "Unauthorized: invalid API key"})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=stdout))

    with pytest.raises(RuntimeError, match="Unauthorized"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch: object) -> None:
    """Verify run raises RuntimeError when the binary is missing."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_unknown_model(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake or wrong-family model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="error: unknown model 'sonnet'"),
    )

    with pytest.raises(RuntimeError, match="unknown model"):
        codex_judge.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch: object) -> None:
    """Verify probe_version returns None on failure."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    assert codex_judge.probe_version() is None
