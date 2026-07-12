"""Tests for codex."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evalspec.agents.codex import CodexAgent, parse_codex_jsonl
from tests.support import FakeExecOutput, FakeSandbox

FIXTURES = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    """Build the text test fixture."""
    return (FIXTURES / name).read_text()


def _lines(name: str) -> list[str]:
    """Build the lines test fixture."""
    return _text(name).splitlines()


def _agent(auth_env: str = "CODEX_API_KEY", auth_json_path: str = "") -> CodexAgent:
    """Build the agent test fixture."""
    return CodexAgent(
        auth_value="sk-test",
        auth_env=auth_env,
        version="latest",
        auth_json_path=auth_json_path,
    )


def _capture_secret(monkeypatch: object) -> dict:
    """Provide the capture secret test helper."""
    captured = {}

    class DummySecret:
        """Store dummy secret data."""

        @staticmethod
        def env(env_var: object, *, value: object, allow_hosts: object) -> str:
            """Env."""
            captured.update(env_var=env_var, value=value, allow_hosts=list(allow_hosts))
            return "secret"

    import microsandbox

    monkeypatch.setattr(microsandbox, "Secret", DummySecret)
    return captured


def test_build_command_shape_for_exec_json() -> None:
    """Verify build command shape for exec json."""
    cmd = _agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="gpt-5.4",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd == [
        "/usr/local/bin/codex",
        "exec",
        "--json",
        "-m",
        "gpt-5.4",
        "-C",
        "/root",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "do the thing",
    ]


def test_build_command_places_harness_args_before_prompt() -> None:
    """Verify build command places harness args before prompt."""
    cmd = _agent().build_command(
        "prompt",
        plugin_dir=None,
        model="gpt-5.4",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--color", "never"],
    )

    assert cmd[-3:] == ["--color", "never", "prompt"]


def test_build_command_ignores_plugin_and_resume_until_supported() -> None:
    """Verify build command ignores plugin and resume until supported."""
    cmd = _agent().build_command(
        "prompt",
        plugin_dir="/plugin",
        model="gpt-5.4",
        effort="high",
        resume_session_id="thread-1",
        detect_skill="archive",
    )

    assert "/plugin" not in cmd
    assert "thread-1" not in cmd
    assert "resume" not in cmd


@pytest.mark.parametrize(
    "arg",
    [
        "-m",
        "--model",
        "--model=gpt-5.4",
        "-mgpt-5.4",
        "-C",
        "--cd",
        "--json",
        "--output-last-message",
        "--output-schema",
        "-i",
        "--image",
        "--enable",
        "--disable",
        "-c",
        "--config",
        "--strict-config",
        "-p",
        "--profile",
        "-s",
        "--sandbox",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--add-dir",
        "--oss",
        "--local-provider",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "resume",
        "review",
    ],
)
def test_build_command_rejects_reserved_harness_args(arg: object) -> None:
    """Verify build command rejects reserved harness args."""
    with pytest.raises(ValueError, match="reserved.*Codex"):
        _agent().build_command(
            "prompt",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=[arg],
        )


def test_guest_env_carries_home_codex_home_tz_and_pinned_version() -> None:
    """Verify guest env carries home codex home tz and pinned version."""
    env = CodexAgent(version="0.142.3").guest_env()

    assert env == {
        "HOME": "/root",
        "CODEX_HOME": "/root/.codex",
        "TZ": "UTC",
        "EVALSPEC_CODEX_VERSION": "0.142.3",
    }


def test_guest_env_omits_credentials() -> None:
    """Verify guest env omits credentials."""
    env = CodexAgent(auth_value="secret").guest_env()

    assert "CODEX_API_KEY" not in env
    assert "CODEX_ACCESS_TOKEN" not in env
    assert "secret" not in env.values()


def test_from_env_prefers_api_key(monkeypatch: object) -> None:
    """Verify from env prefers api key."""
    captured = _capture_secret(monkeypatch)
    monkeypatch.setenv("CODEX_API_KEY", "api-key")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "token")
    monkeypatch.setenv("EVALSPEC_CODEX_VERSION", "0.142.3")

    agent = CodexAgent.from_env()

    assert agent.secrets() == ["secret"]
    assert captured["env_var"] == "CODEX_API_KEY"
    assert captured["value"] == "api-key"
    assert agent.version() == "0.142.3"


def test_from_env_falls_back_to_access_token(monkeypatch: object) -> None:
    """Verify from env falls back to access token."""
    captured = _capture_secret(monkeypatch)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "token")

    agent = CodexAgent.from_env()

    assert agent.secrets() == ["secret"]
    assert captured["env_var"] == "CODEX_ACCESS_TOKEN"
    assert captured["value"] == "token"


def test_from_env_reads_auth_json_path(monkeypatch: object, tmp_path: object) -> None:
    """Verify from env reads auth json path."""
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "cached-token"},
            }
        )
    )
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth))

    agent = CodexAgent.from_env()

    assert agent.auth_json_path() == str(auth)
    assert agent.secrets() == []


def test_credential_error_message_when_no_credentials_set(monkeypatch: object) -> None:
    """Verify credential error message when no credentials set."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_AUTH_JSON_PATH", raising=False)

    msg = CodexAgent.credential_error()

    assert msg is not None
    assert "CODEX_AUTH_JSON_PATH" in msg
    assert "CODEX_API_KEY" in msg
    assert "CODEX_ACCESS_TOKEN" in msg


def test_credential_error_none_when_api_key_set(monkeypatch: object) -> None:
    """Verify credential error none when api key set."""
    monkeypatch.setenv("CODEX_API_KEY", "api-key")

    assert CodexAgent.credential_error() is None


def test_credential_error_none_when_auth_json_path_is_valid(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify credential error none when auth json path is valid."""
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "cached-token"},
            }
        )
    )
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth))

    assert CodexAgent.credential_error() is None


def test_secrets_scopes_api_key_to_openai_host(monkeypatch: object) -> None:
    """Verify secrets scopes api key to openai host."""
    captured = _capture_secret(monkeypatch)

    secs = CodexAgent(auth_value="api-key", auth_env="CODEX_API_KEY").secrets()

    assert secs == ["secret"]
    assert captured == {
        "env_var": "CODEX_API_KEY",
        "value": "api-key",
        "allow_hosts": ["api.openai.com"],
    }


def test_secrets_scopes_access_token_to_codex_hosts(monkeypatch: object) -> None:
    """Verify secrets scopes access token to codex hosts."""
    captured = _capture_secret(monkeypatch)

    CodexAgent(auth_value="token", auth_env="CODEX_ACCESS_TOKEN").secrets()

    assert captured["env_var"] == "CODEX_ACCESS_TOKEN"
    assert "chatgpt.com" in captured["allow_hosts"]
    assert "auth.openai.com" in captured["allow_hosts"]


def test_secrets_empty_when_auth_json_path_is_used() -> None:
    """Verify secrets empty when auth json path is used."""
    assert CodexAgent(auth_json_path="/host/auth.json").secrets() == []


def test_codex_skill_load_dir_is_agents_path() -> None:
    """Verify codex skill load dir is agents path."""
    assert CodexAgent.skill_load_dir == "/root/.codex/skills"


def test_codex_artifact_dirs_excludes_runtime_managed_skills() -> None:
    """Verify codex artifact dirs excludes runtime managed skills."""
    assert _agent().artifact_dirs() == []


def test_codex_bridge_script_symlinks_fixed_home() -> None:
    """Verify codex bridge script symlinks fixed home."""
    script = _agent().bridge_skills_home_script()

    assert "/home/evalspec/skills" in script
    assert "/root/.codex/skills" in script


def test_codex_cell_env_carries_evalspec_vars() -> None:
    """Verify codex cell env carries evalspec vars."""
    env = CodexAgent(version="0.142.3").cell_env(
        arm="trial",
        model="gpt-5.4",
        eval_set="codex-smoke",
    )

    assert env["EVALSPEC_ARM"] == "trial"
    assert env["EVALSPEC_MODEL"] == "gpt-5.4"
    assert env["EVALSPEC_HARNESS"] == "codex"
    assert env["EVALSPEC_SET"] == "codex-smoke"


def test_detect_dispatch_matches_skill_invocation_item() -> None:
    """Verify detect dispatch matches skill invocation item."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "skill_invocation", "name": "knowledge-base:archive"},
        }
    )

    assert _agent().detect_dispatch(line, "archive") is True


def test_detect_dispatch_early_stops_on_any_skill() -> None:
    """Verify detect dispatch early stops on any skill."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "skill_invocation", "name": "bootstrap"},
        }
    )

    assert _agent().detect_dispatch(line, "archive") is True


def test_codex_detect_fired_false_for_other_skill_when_dispatch_detects_any_skill() -> None:
    """Verify codex detect fired false for other skill when dispatch detects any skill."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "skill_invocation", "name": "bootstrap"},
        }
    )

    assert _agent().detect_fired([line], "archive") is False


def test_detect_dispatch_false_for_other_tool() -> None:
    """Verify detect dispatch false for other tool."""
    line = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "ls"},
        }
    )

    assert _agent().detect_dispatch(line, "archive") is False


def test_codex_detect_fired_true_on_our_skill() -> None:
    """Verify codex detect fired true on our skill."""
    assert _agent().detect_fired(_lines("codex_route_fired.jsonl"), "archive") is True


def test_codex_detect_fired_false_on_other_tool() -> None:
    """Verify codex detect fired false on other tool."""
    assert _agent().detect_fired(_lines("codex_route_nofire.jsonl"), "archive") is False


def test_codex_streamed_activity_true_when_turn_began() -> None:
    """Verify codex streamed activity true when turn began."""
    assert _agent().streamed_activity(_lines("codex_route_nofire.jsonl")) is True


def test_codex_streamed_activity_false_on_startup_only() -> None:
    """Verify codex streamed activity false on startup only."""
    assert _agent().streamed_activity(['{"type":"thread.started","thread_id":"t"}']) is False


def test_codex_streamed_activity_false_on_empty_or_invalid_lines() -> None:
    """Verify codex streamed activity false on empty or invalid lines."""
    assert _agent().streamed_activity(["", "not json"]) is False


def test_parse_codex_jsonl_populates_run_result() -> None:
    """Verify parse codex jsonl populates run result."""
    res = parse_codex_jsonl(
        _text("codex_parse_success.jsonl"),
        "e1",
        "trial",
        detect_skill="archive",
    )

    assert res.result_text == "Thinking...\n\nDone."
    assert res.session_id == "thread-3"
    assert res.duration_ms == 6000
    assert res.input_tokens == 20
    assert res.cache_read_tokens == 5
    assert res.output_tokens == 9
    assert res.total_tokens == 37
    assert res.is_error is False
    assert res.fired is True


def test_parse_codex_jsonl_carries_raw_stdout() -> None:
    """Verify parse codex jsonl carries raw stdout."""
    raw = _text("codex_parse_success.jsonl")

    res = parse_codex_jsonl(raw, "e1", "trial", detect_skill="archive")

    assert res.raw == raw


def test_parse_codex_jsonl_keeps_all_agent_messages() -> None:
    """Verify parse codex jsonl keeps all agent messages."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "one"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "two"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill=None)

    assert res.result_text == "one\n\ntwo"


def test_parse_codex_jsonl_tool_only_run_not_errored_when_turn_completed() -> None:
    """Verify parse codex jsonl tool only run not errored when turn completed."""
    res = parse_codex_jsonl(
        _text("codex_parse_tool_only.jsonl"),
        "e1",
        "trial",
        detect_skill="archive",
    )

    assert res.is_error is False
    assert "command_execution" in res.result_text


def test_parse_codex_jsonl_explicit_error_event_sets_is_error() -> None:
    """Verify parse codex jsonl explicit error event sets is error."""
    res = parse_codex_jsonl(
        _text("codex_parse_error.jsonl"),
        "e1",
        "trial",
        detect_skill="archive",
    )

    assert res.is_error is True
    assert "auth failed" in res.result_text


def test_parse_codex_jsonl_carries_normalized_trajectory() -> None:
    """Verify parse codex jsonl carries normalized trajectory."""
    res = parse_codex_jsonl(
        _text("codex_parse_success.jsonl"),
        "e1",
        "trial",
        detect_skill="archive",
    )

    assert {
        "kind": "tool_call",
        "id": "i1",
        "name": "Skill",
        "arguments": {"skill": "archive"},
    } in res.trajectory
    assert {
        "kind": "tool_call",
        "id": "i2",
        "name": "command_execution",
        "arguments": {"command": "ls"},
    } in res.trajectory


def test_provision_runs_install_script() -> None:
    """Verify provision runs install script."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))

    asyncio.run(_agent().provision(sandbox))

    kind, script, kw = sandbox.calls[0]
    assert kind == "shell"
    assert "CODEX_NON_INTERACTIVE=1" in script
    assert "npm i -g" in script
    # The version is baked into the ref (not a guest env var), so the cache key tracks it.
    assert "@openai/codex@latest" in script
    assert "codex" in script
    assert "codex.openai.com" not in script
    assert kw["env"]["CODEX_HOME"] == "/root/.codex"


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))

    with pytest.raises(RuntimeError, match="codex provision failed"):
        asyncio.run(_agent().provision(sandbox))


def test_stage_project_assets_copies_skills_into_codex_discovery_dir() -> None:
    """Verify stage project assets copies skills into codex discovery dir."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))

    asyncio.run(_agent().stage_project_assets(sandbox, "/project"))

    kind, script, kw = sandbox.calls[0]
    assert kind == "shell"
    assert "/project/skills" in script
    assert "/project/.agents/skills" in script
    assert "/project/.claude/skills" in script
    assert "/root/.codex/skills" in script
    assert kw["env"]["HOME"] == "/root"


def test_invoke_success_parses_codex_jsonl_and_closes_stdin() -> None:
    """Verify invoke success parses codex jsonl and closes stdin."""
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=0, stdout_text=_text("codex_parse_success.jsonl")),
        ]
    )

    res = asyncio.run(
        _agent().invoke(
            sandbox,
            "do it",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill="archive",
        )
    )

    kind, cmd, args, kw = sandbox.calls[0]
    assert kind == "exec"
    assert cmd == "/usr/local/bin/codex"
    assert args[-1] == "do it"
    assert args[args.index("-C") + 1] == "/workspace"
    assert kw["cwd"] == "/workspace"
    assert kw["stdin"] == b""
    assert res.result_text == "Thinking...\n\nDone."
    assert res.fired is True


def test_invoke_extra_env_overrides_guest_env() -> None:
    """Verify invoke extra env overrides guest env."""
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=0, stdout_text=_text("codex_parse_tool_only.jsonl")),
        ]
    )

    asyncio.run(
        _agent().invoke(
            sandbox,
            "do it",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            extra_env={"TZ": "America/Los_Angeles", "EXTRA": "1"},
        )
    )

    _, _, _, kw = sandbox.calls[0]
    assert kw["env"]["TZ"] == "America/Los_Angeles"
    assert kw["env"]["EXTRA"] == "1"
    assert kw["env"]["HOME"] == "/root"


def test_invoke_nonzero_exit_is_error() -> None:
    """Verify invoke nonzero exit is error."""
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=2, stderr_text="bad")])

    res = asyncio.run(
        _agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is True
    assert "bad" in res.result_text


def test_invoke_threads_harness_args_into_build_command() -> None:
    """Verify invoke threads harness args into build command."""
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=0, stdout_text=_text("codex_parse_tool_only.jsonl")),
        ]
    )

    asyncio.run(
        _agent().invoke(
            sandbox,
            "do the thing",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--color", "never"],
        )
    )

    _, _, args, _ = sandbox.calls[0]
    assert args[-3:] == ["--color", "never", "do the thing"]


def test_invoke_copies_mounted_auth_json_before_codex_exec() -> None:
    """Verify invoke copies mounted auth json before codex exec."""
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=0, stdout_text=_text("codex_parse_tool_only.jsonl")),
        ]
    )

    asyncio.run(
        _agent(auth_json_path="/host/auth.json").invoke(
            sandbox,
            "do the thing",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    auth_call, exec_call = sandbox.calls
    assert auth_call[0] == "shell"
    assert CodexAgent.AUTH_JSON_GUEST_SOURCE in auth_call[1]
    assert "/root/.codex/auth.json" in auth_call[1]
    assert exec_call[0] == "exec"


def test_invoke_returns_error_when_auth_json_copy_fails() -> None:
    """Verify invoke returns error when auth json copy fails."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="copy failed"))

    res = asyncio.run(
        _agent(auth_json_path="/host/auth.json").invoke(
            sandbox,
            "do the thing",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is True
    assert "copy failed" in res.result_text
