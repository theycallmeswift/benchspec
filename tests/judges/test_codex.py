"""Tests for the Codex judge runner (envelope extraction + infra-error handling)."""

import json
import subprocess

import pytest

from evalspec.judges import codex as codex_judge


def _fake_proc(stdout: object = "", stderr: object = "", returncode: object = 0) -> object:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _jsonl(*events: object) -> object:
    """Serialize events as a newline-terminated JSONL stream."""
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_run_wraps_final_agent_message_in_result_envelope(monkeypatch: object) -> None:
    """Verify run wraps the final agent message in a result envelope."""
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "t1"},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}',
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    )
    captured = {}

    def fake_run(cmd: object, **kw: object) -> object:
        """Capture the command and return a fake process."""
        captured["cmd"] = cmd
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = codex_judge.run(
        "grade this", model="gpt-5.5", effort="medium", timeout=300,
        harness_args=["--sandbox", "read-only"], env={},
    )

    envelope = json.loads(out)
    assert envelope["result"] == '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'
    assert captured["cmd"][:3] == ["codex", "exec", "--json"]
    assert "-m" in captured["cmd"]
    assert "gpt-5.5" in captured["cmd"]
    assert captured["cmd"][-1] == "grade this"  # trailing positional prompt


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="network error"),
    )
    with pytest.raises(RuntimeError, match="exited 1.*network error"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_error_event(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an error event."""
    stdout = _jsonl({"type": "error", "message": "Unauthorized: invalid API key"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(stdout=stdout))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch: object) -> None:
    """Verify run raises RuntimeError when the binary is missing."""
    def boom(*a: object, **k: object) -> object:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_unknown_model(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake or wrong-family model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(returncode=1, stderr="error: unknown model 'sonnet'"),
    )
    with pytest.raises(RuntimeError, match="unknown model"):
        codex_judge.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch: object) -> None:
    """Verify probe_version returns None on failure."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )
    assert codex_judge.probe_version() is None
