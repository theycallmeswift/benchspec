"""Tests for the OpenCode judge runner (envelope + infra-error handling)."""

import json
import subprocess

import pytest

from evalspec.judges import opencode as opencode_judge


def _fake_proc(stdout: object = "", stderr: object = "", returncode: object = 0) -> object:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _jsonl(*events: object) -> object:
    """Serialize events as a newline-terminated JSONL stream."""
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_run_wraps_final_text_events_in_result_envelope(monkeypatch: object) -> None:
    """Verify run wraps the final text events in a result envelope."""
    stdout = _jsonl(
        {"type": "step_start"},
        {
            "type": "text",
            "part": {"text": '{"assertions": [{"text": "a", "passed": false, "evidence": "no"}]}'},
        },
        {"type": "step_finish", "part": {"tokens": {"total": 42}}},
    )
    captured = {}

    def fake_run(cmd: object, **kw: object) -> object:
        """Capture the command and return a fake process."""
        captured["cmd"] = cmd
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = opencode_judge.run(
        "grade this", model="anthropic/claude-sonnet-4-6", effort="high", timeout=300,
        harness_args=[], env={},
    )

    envelope = json.loads(out)
    assert (
        envelope["result"]
        == '{"assertions": [{"text": "a", "passed": false, "evidence": "no"}]}'
    )
    assert captured["cmd"][:2] == ["opencode", "run"]
    assert "--variant" in captured["cmd"]  # high -> thorough
    assert "thorough" in captured["cmd"]
    assert captured["cmd"][-1] == "grade this"


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="connection reset"),
    )
    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_on_infra_looking_stderr(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on infra-looking stderr."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(
            stdout="", stderr="401 Unauthorized: invalid API key", returncode=0
        ),
    )
    with pytest.raises(RuntimeError, match="Unauthorized"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_does_not_raise_on_successful_run_with_innocuous_auth_substring(
    monkeypatch: object,
) -> None:
    """Verify run does not raise on a successful run with an innocuous auth substring."""
    stdout = _jsonl(
        {
            "type": "text",
            "part": {"text": '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'},
        },
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(
            stdout=stdout,
            stderr="authenticated as user; author: jane@example.com",
            returncode=0,
        ),
    )

    out = opencode_judge.run(
        "p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
        harness_args=[], env={},
    )

    envelope = json.loads(out)
    assert envelope["result"] == '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'


def test_run_raises_runtimeerror_on_reordered_quota_message(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on a reordered quota message."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(
            stdout="",
            stderr=(
                "You have exceeded your current quota, please check your plan and "
                "billing details."
            ),
            returncode=0,
        ),
    )
    with pytest.raises(RuntimeError, match="quota"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_on_authorization_header_missing(monkeypatch: object) -> None:
    """Verify run raises RuntimeError when the Authorization header is missing."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(stdout="", stderr="Authorization header missing", returncode=0),
    )
    with pytest.raises(RuntimeError, match="Authorization"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch: object) -> None:
    """Verify run raises RuntimeError when the binary is missing."""
    def boom(*a: object, **k: object) -> object:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_on_unknown_model(monkeypatch: object) -> None:
    """Verify run raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake provider-qualified model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(
            returncode=1, stderr="error: unknown model 'anthropic/not-a-real-model'"
        ),
    )
    with pytest.raises(RuntimeError, match="unknown model"):
        opencode_judge.run("p", model="anthropic/not-a-real-model", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch: object) -> None:
    """Verify probe_version returns None on failure."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )
    assert opencode_judge.probe_version() is None
