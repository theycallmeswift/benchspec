"""Tests for OpenCodeAgent's judge mode (envelope + infra errors)."""

import asyncio
import json
import subprocess

import pytest

from harnessbench.agents.opencode import OpenCodeAgent
from harnessbench.grading.judges.config import JudgeConfig


def _fake_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stream(*events: dict) -> str:
    """Serialize events as a newline-terminated JSONL stream."""
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _successful_stream(verdict: str) -> str:
    """A JSONL stream for a run that reached the model: verdict text plus a token total.

    parse_opencode_jsonl treats a zero-token run as an infra failure, so a genuine
    success must carry a nonzero token count.
    """
    return _stream(
        {"type": "step_start"},
        {"type": "text", "part": {"text": verdict}},
        {"type": "step_finish", "part": {"tokens": {"total": 42}}},
    )


def _judge(config: JudgeConfig) -> str:
    """Run OpenCodeAgent's judge to completion in the default Host environment."""
    return asyncio.run(OpenCodeAgent.for_host().judge("grade this", config))


def test_judge_wraps_final_text_events_in_result_envelope(monkeypatch: object) -> None:
    """Verify judge wraps the final text events in a result envelope."""
    verdict = '{"assertions": [{"text": "a", "passed": false, "evidence": "no"}]}'
    captured = {}

    def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Capture the command and return a successful process."""
        captured["command"] = command
        return _fake_proc(stdout=_successful_stream(verdict))

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = _judge(JudgeConfig(
        harness="opencode", model="anthropic/claude-sonnet-4-6", effort="high",
    ))

    assert json.loads(out)["result"] == verdict
    assert captured["command"][:2] == ["opencode", "run"]
    assert "--variant" in captured["command"]  # high -> thorough
    assert "thorough" in captured["command"]
    assert captured["command"][-1] == "grade this"


def test_judge_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify judge raises RuntimeError on a nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="connection reset"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        _judge(JudgeConfig(harness="opencode", model="anthropic/claude-sonnet-4-6"))


def test_judge_raises_runtimeerror_when_no_model_call_was_made(monkeypatch: object) -> None:
    """Verify a zero-token run (no successful model call) raises, surfacing stderr.

    Infra detection is the same zero-token signal the task arm uses, not a stderr
    scan — so an auth/quota failure that exits 0 but never reaches the model still
    errors, with the stderr detail carried into the message.
    """
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(
            stdout=_stream({"type": "step_start"}),
            stderr="401 Unauthorized: invalid API key",
            returncode=0,
        ),
    )

    with pytest.raises(RuntimeError, match="no successful model call.*Unauthorized"):
        _judge(JudgeConfig(harness="opencode", model="anthropic/claude-sonnet-4-6"))


def test_judge_does_not_consult_stderr_when_the_run_succeeded(monkeypatch: object) -> None:
    """Verify a successful (nonzero-token) run returns its verdict despite noisy stderr.

    Because success is decided by tokens spent rather than stderr contents, benign
    stderr like "authenticated as user; author: ..." can never be mistaken for a
    failure — the false-positive that a stderr scan was prone to.
    """
    verdict = '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(
            stdout=_successful_stream(verdict),
            stderr="authenticated as user; author: jane@example.com",
            returncode=0,
        ),
    )

    out = _judge(JudgeConfig(harness="opencode", model="anthropic/claude-sonnet-4-6"))

    assert json.loads(out)["result"] == verdict


def test_judge_raises_runtimeerror_when_binary_missing(monkeypatch: object) -> None:
    """Verify judge raises RuntimeError when the binary is missing."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        _judge(JudgeConfig(harness="opencode", model="anthropic/claude-sonnet-4-6"))


def test_judge_raises_runtimeerror_on_unknown_model(monkeypatch: object) -> None:
    """Verify judge raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake provider-qualified model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(
            returncode=1, stderr="error: unknown model 'anthropic/not-a-real-model'"
        ),
    )

    with pytest.raises(RuntimeError, match="unknown model"):
        _judge(JudgeConfig(harness="opencode", model="anthropic/not-a-real-model"))


def test_binary_version_best_effort_none_on_failure(monkeypatch: object) -> None:
    """Verify binary_version returns None on failure."""
    def raise_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    assert OpenCodeAgent.for_host().binary_version() is None
