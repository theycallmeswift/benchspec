"""Tests for ClaudeCodeAgent's judge mode (native envelope + infra errors)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import NoReturn

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.grading.judges.config import JudgeConfig


def _fake_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    """A CompletedProcess stand-in for a monkeypatched subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _judge(config: JudgeConfig) -> str:
    """Run ClaudeCodeAgent's judge to completion in the default Host environment."""
    return asyncio.run(ClaudeCodeAgent.for_host().judge("grade this", config))


def test_judge_returns_native_result_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge returns the native result envelope."""
    payload = json.dumps({"result": '{"assertions": []}', "is_error": False})
    captured_command: list[str] = []
    captured_env: dict[str, str] = {}

    def fake_run(
        command: list[str], *, env: dict[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the command and env and return a fake process."""
        captured_command.extend(command)
        captured_env.update(env)
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = _judge(JudgeConfig(
        model="sonnet", effort="medium", timeout=300,
        harness_args=["--plugin-dir", "/x"], env={"FOO": "bar"},
    ))

    assert out == payload
    assert json.loads(out)["result"] == '{"assertions": []}'
    assert captured_command[:2] == ["claude", "-p"]
    assert "--model" in captured_command
    assert "sonnet" in captured_command
    assert "--effort" in captured_command
    assert "medium" in captured_command
    assert captured_command[-2:] == ["--plugin-dir", "/x"]
    assert captured_env["FOO"] == "bar"


def test_judge_raises_runtimeerror_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError on a nonzero exit."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="connection reset"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        _judge(JudgeConfig(model="sonnet"))


def test_judge_raises_runtimeerror_on_is_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError on an is_error envelope."""
    payload = json.dumps({"result": "Not logged in", "is_error": True})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=payload))

    with pytest.raises(RuntimeError, match="is_error=true.*Not logged in"):
        _judge(JudgeConfig(model="sonnet"))


def test_judge_raises_runtimeerror_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError when the binary is missing."""
    def raise_not_found(*args: object, **kwargs: object) -> NoReturn:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        _judge(JudgeConfig(model="sonnet"))


def test_judge_raises_runtimeerror_on_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify judge raises RuntimeError on an unknown model."""
    # No local model/harness allow-list: a fake or wrong-family model reaches the
    # harness, which rejects it (nonzero exit) — surfaced as infra RuntimeError, not
    # laundered into a passing grade.
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: _fake_proc(returncode=1, stderr="error: unknown model 'gpt-5.5'"),
    )

    with pytest.raises(RuntimeError, match="unknown model"):
        _judge(JudgeConfig(model="gpt-5.5"))


def test_binary_version_best_effort_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify binary_version returns None on failure."""
    def raise_not_found(*args: object, **kwargs: object) -> NoReturn:
        """Raise to simulate a missing binary."""
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", raise_not_found)

    assert ClaudeCodeAgent.for_host().binary_version() is None


def test_binary_version_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify binary_version returns stripped stdout."""
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout="2.1.0\n"))
    assert ClaudeCodeAgent.for_host().binary_version() == "2.1.0"


def _without_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every env credential so only the host login probe can pass the check."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_host_credential_error_none_when_env_credential_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An env credential satisfies the host check without probing the CLI."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def must_not_run(*args: object, **kwargs: object) -> NoReturn:
        """Fail if the probe is spawned despite an env credential."""
        raise AssertionError("claude auth status must not run when ANTHROPIC_API_KEY is set")

    monkeypatch.setattr(subprocess, "run", must_not_run)

    assert ClaudeCodeAgent.for_host().host_credential_error() is None


def test_host_credential_error_none_when_host_is_logged_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an env credential, a `claude auth status` login means the host can judge."""
    _without_claude_env(monkeypatch)
    probed: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Record the probe and report a logged-in host."""
        probed.append(command)
        return _fake_proc(stdout=json.dumps({"loggedIn": True, "authMethod": "oauth_token"}))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ClaudeCodeAgent.for_host().host_credential_error() is None
    assert probed == [["claude", "auth", "status"]]


def test_host_credential_error_when_host_is_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """`claude auth status` reporting no login yields a message naming both remedies."""
    _without_claude_env(monkeypatch)
    logged_out = json.dumps({"loggedIn": False})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=logged_out))

    error = ClaudeCodeAgent.for_host().host_credential_error()

    assert error is not None
    assert "claude login" in error
    assert "CLAUDE_CODE_OAUTH_TOKEN" in error


def test_host_credential_error_when_auth_status_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output that cannot confirm a login (an unknown subcommand, say) counts as logged out."""
    _without_claude_env(monkeypatch)
    unknown_command = _fake_proc(returncode=1, stderr="unknown command")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: unknown_command)

    assert ClaudeCodeAgent.for_host().host_credential_error() is not None


def _openrouter_judge(config: JudgeConfig) -> str:
    """Run the OpenRouter-bound judge to completion in the default Host environment."""
    return asyncio.run(ClaudeCodeAgent.for_host("openrouter").judge("grade this", config))


def test_judge_under_openrouter_routes_through_the_gateway_with_the_host_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge env carries the base URL, an empty API key, and the host key as the token."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-host")
    payload = json.dumps({"result": "{}", "is_error": False})
    captured_env: dict[str, str] = {}

    def fake_run(
        command: list[str], *, env: dict[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the env and return a fake process."""
        captured_env.update(env)
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _openrouter_judge(JudgeConfig(provider="openrouter", model="anthropic/claude-sonnet-4.6"))

    assert captured_env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert captured_env["ANTHROPIC_API_KEY"] == ""
    assert captured_env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-host"


def test_judge_under_openrouter_reads_the_key_from_the_judge_env_and_lets_it_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that lives only in the judge's `env` is used, and a user override is kept."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    payload = json.dumps({"result": "{}", "is_error": False})
    captured_env: dict[str, str] = {}

    def fake_run(
        command: list[str], *, env: dict[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the env and return a fake process."""
        captured_env.update(env)
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _openrouter_judge(JudgeConfig(
        provider="openrouter", model="anthropic/claude-sonnet-4.6",
        env={"OPENROUTER_API_KEY": "sk-or-judge", "ANTHROPIC_BASE_URL": "https://proxy.test"},
    ))

    assert captured_env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-judge"
    assert captured_env["ANTHROPIC_BASE_URL"] == "https://proxy.test"  # user env wins


def test_judge_under_default_adds_no_gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `default` judge adds nothing over the host env and `config.env`: no gateway keys."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-host")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    payload = json.dumps({"result": "{}", "is_error": False})
    captured_env: dict[str, str] = {}

    def fake_run(
        command: list[str], *, env: dict[str, str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Capture the env and return a fake process."""
        captured_env.update(env)
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    _judge(JudgeConfig(model="sonnet", env={"FOO": "bar"}))

    assert captured_env["FOO"] == "bar"
    assert "ANTHROPIC_BASE_URL" not in captured_env
    assert "ANTHROPIC_AUTH_TOKEN" not in captured_env


def test_host_credential_error_under_openrouter_never_probes_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the key set, the host check passes without asking `claude auth status`."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-host")

    def must_not_run(*args: object, **kwargs: object) -> NoReturn:
        """Fail if the login probe runs under OpenRouter."""
        raise AssertionError("claude auth status must not run under openrouter")

    monkeypatch.setattr(subprocess, "run", must_not_run)

    assert ClaudeCodeAgent.for_host("openrouter").host_credential_error() is None


def test_host_credential_error_under_openrouter_ignores_a_host_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A logged-in host cannot stand in for the missing OpenRouter key."""
    _without_claude_env(monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    logged_in = json.dumps({"loggedIn": True})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _fake_proc(stdout=logged_in))

    error = ClaudeCodeAgent.for_host("openrouter").host_credential_error()

    assert error is not None
    assert "OPENROUTER_API_KEY" in error
