"""Tests for CodexAgent's judge mode (envelope + infra errors)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Mapping
from typing import NoReturn

import pytest

from benchspec.agents.codex import CodexAgent
from benchspec.grading.judges.config import JudgeConfig


def _fake_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stream(*events: dict) -> str:
    """Serialize events as a newline-terminated JSONL stream."""
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _judge(prompt: str, config: JudgeConfig) -> str:
    """Run CodexAgent's judge to completion in the default Host environment."""
    return asyncio.run(CodexAgent.for_host().judge(prompt, config))


def test_judge_wraps_final_agent_message_in_result_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify judge wraps the final agent message in a result envelope."""
    verdict = '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'
    stdout = _stream(
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": verdict}},
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    )
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Capture the command and return a successful process."""
        captured["command"] = command
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = _judge("grade this", JudgeConfig(
        harness="codex", model="gpt-5.5", harness_args=["--sandbox", "read-only"],
    ))

    assert json.loads(out)["result"] == verdict
    assert captured["command"][:3] == ["codex", "exec", "--json"]
    assert "-m" in captured["command"]
    assert "gpt-5.5" in captured["command"]
    assert captured["command"][-1] == "grade this"  # trailing positional prompt


def test_judge_hands_openai_api_key_to_codex_under_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with only `OPENAI_API_KEY` judges: the key rides as `CODEX_API_KEY`."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    captured: dict[str, dict[str, str]] = {}

    def fake_run(
        command: list[str], *, env: Mapping[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the subprocess env and return a completed turn."""
        captured["env"] = dict(env)
        return _fake_proc(stdout=_stream({"type": "turn.completed", "usage": {}}))

    monkeypatch.setattr(subprocess, "run", fake_run)

    _judge("grade this", JudgeConfig(harness="codex", model="gpt-5.5"))

    assert captured["env"]["CODEX_API_KEY"] == "sk-openai"


def test_judge_keeps_host_codex_api_key_over_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that exports Codex's own name is left alone."""
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    captured: dict[str, dict[str, str]] = {}

    def fake_run(
        command: list[str], *, env: Mapping[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the subprocess env and return a completed turn."""
        captured["env"] = dict(env)
        return _fake_proc(stdout=_stream({"type": "turn.completed", "usage": {}}))

    monkeypatch.setattr(subprocess, "run", fake_run)

    _judge("grade this", JudgeConfig(harness="codex", model="gpt-5.5"))

    assert captured["env"]["CODEX_API_KEY"] == "sk-codex"


def test_judge_pins_reasoning_effort_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured effort reaches codex as an explicit override, not host config."""
    stdout = _stream(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
    )
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Capture the command and return a successful process."""
        captured["command"] = command
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _judge("p", JudgeConfig(harness="codex", model="gpt-5.5", effort="high"))

    command = captured["command"]
    assert command[command.index("-c") + 1] == "model_reasoning_effort=high"


def test_judge_surfaces_turn_failed_message_over_stderr_chatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected request is reported from the stdout `turn.failed` event, not stderr."""
    stdout = _stream({
        "type": "turn.failed",
        "error": {"message": "Unsupported value: 'max' is not supported with this model"},
    })
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(
            stdout=stdout, stderr="Reading additional input from stdin...", returncode=1,
        ),
    )

    with pytest.raises(RuntimeError, match="Unsupported value: 'max'"):
        _judge("p", JudgeConfig(harness="codex", model="gpt-5.5"))


def test_judge_raises_runtimeerror_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError on a nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="network error"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*network error"):
        _judge("p", JudgeConfig(harness="codex", model="gpt-5.5"))


def test_judge_raises_runtimeerror_on_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a harness error event (surfaced by the shared parser) raises RuntimeError."""
    stdout = _stream({"type": "error", "message": "Unauthorized: invalid API key"})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=stdout))

    with pytest.raises(RuntimeError, match="Unauthorized"):
        _judge("p", JudgeConfig(harness="codex", model="gpt-5.5"))


def test_judge_raises_runtimeerror_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError when the binary is missing."""
    def raise_not_found(*args: object, **kwargs: object) -> NoReturn:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        _judge("p", JudgeConfig(harness="codex", model="gpt-5.5"))


def test_judge_raises_runtimeerror_on_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake or wrong-family model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="error: unknown model 'sonnet'"),
    )

    with pytest.raises(RuntimeError, match="unknown model"):
        _judge("p", JudgeConfig(harness="codex", model="sonnet"))


def test_binary_version_best_effort_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify binary_version returns None on failure."""
    def raise_not_found(*args: object, **kwargs: object) -> NoReturn:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    assert CodexAgent.for_host().binary_version() is None


def _without_codex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every env credential so only the host login probe can pass the check."""
    for env_name in (
        "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "CODEX_AUTH_JSON_PATH"
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_host_credential_error_none_when_env_credential_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An env credential satisfies the host check without probing the CLI."""
    monkeypatch.setenv("CODEX_API_KEY", "api-key")

    def must_not_run(*args: object, **kwargs: object) -> NoReturn:
        """Fail if the probe is spawned despite an env credential."""
        raise AssertionError("codex login status must not run when CODEX_API_KEY is set")

    monkeypatch.setattr(subprocess, "run", must_not_run)

    assert CodexAgent.for_host().host_credential_error() is None


def test_host_credential_error_none_when_host_is_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an env credential, a passing `codex login status` means the host can judge."""
    _without_codex_env(monkeypatch)
    probed: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Record the probe and report a logged-in host."""
        probed.append(command)
        return _fake_proc(stdout="Logged in using ChatGPT\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert CodexAgent.for_host().host_credential_error() is None
    assert probed == [["codex", "login", "status"]]


def test_host_credential_error_when_host_is_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing `codex login status` yields a message naming both remedies."""
    _without_codex_env(monkeypatch)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: _fake_proc(returncode=1, stdout="Not logged in")
    )

    error = CodexAgent.for_host().host_credential_error()

    assert error is not None
    assert "codex login" in error
    assert "CODEX_API_KEY" in error
