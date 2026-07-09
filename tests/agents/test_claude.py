"""Tests for claude."""

from __future__ import annotations

import asyncio
import json

import pytest

from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.runner import parse_run_json
from tests.support import FakeExecOutput, FakeSandbox


def _agent() -> object:
    """Build the agent test fixture."""
    return ClaudeCodeAgent(auth_value="sk-test", version="latest")


def test_build_command_always_streams_even_without_detect() -> None:
    """Verify build command always streams even without detect."""
    # Both arms stream, so the baseline captures a trajectory too.
    cmd = _agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="sonnet",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[0] == ClaudeCodeAgent.CLAUDE_BIN
    assert cmd[1:3] == ["-p", "do the thing"]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allowedTools" not in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert "--resume" not in cmd
    assert "--plugin-dir" not in cmd


def test_build_command_stream_when_detect() -> None:
    """Verify build command stream when detect."""
    cmd = _agent().build_command(
        "q",
        plugin_dir="/plugin",
        model="haiku",
        effort="low",
        resume_session_id="sess-1",
        detect_skill="archive",
    )
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert cmd[cmd.index("--plugin-dir") + 1] == "/plugin"
    assert cmd[cmd.index("--resume") + 1] == "sess-1"


def test_build_command_stream_with_resume_and_plugin() -> None:
    """Verify build command stream with resume and plugin."""
    cmd = _agent().build_command(
        "p",
        plugin_dir="/plugin",
        model="sonnet",
        effort="high",
        resume_session_id="s9",
        detect_skill=None,
    )

    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert cmd[cmd.index("--resume") + 1] == "s9"
    assert cmd[cmd.index("--plugin-dir") + 1] == "/plugin"


def test_build_command_appends_harness_args() -> None:
    """Verify build command appends harness args."""
    cmd = _agent().build_command(
        "q",
        plugin_dir=None,
        model="sonnet",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--plugin-dir", "/project"],
    )

    assert cmd[-2:] == ["--plugin-dir", "/project"]


def test_build_command_rejects_harness_plugin_dir_when_first_class_plugin_dir_set() -> None:
    """Verify build command rejects harness plugin dir when first class plugin dir set."""
    with pytest.raises(ValueError, match="plugin_dir"):
        _agent().build_command(
            "q",
            plugin_dir="/a",
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--plugin-dir", "/b"],
        )


def test_build_command_rejects_harness_plugin_dir_equals_form_when_first_class_plugin_dir_set() -> (
    None
):
    """Verify build command rejects harness plugin dir equals form when first class."""
    with pytest.raises(ValueError, match="plugin_dir"):
        _agent().build_command(
            "q",
            plugin_dir="/a",
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--plugin-dir=/b"],
        )


def test_build_command_allows_pass_through_equals_form() -> None:
    """Verify build command allows pass through equals form."""
    cmd = _agent().build_command(
        "q",
        plugin_dir=None,
        model="sonnet",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--plugin-dir=/project"],
    )

    assert cmd[-1] == "--plugin-dir=/project"


def test_build_command_rejects_reserved_harness_args() -> None:
    """Verify build command rejects reserved harness args."""
    with pytest.raises(ValueError, match="reserved.*--model"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--model", "opus"],
        )


def test_build_command_rejects_reserved_harness_arg_equals_form() -> None:
    """Verify build command rejects reserved harness arg equals form."""
    with pytest.raises(ValueError, match="reserved.*--model"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--model=opus"],
        )


def test_build_command_rejects_reserved_permission_mode_exact_form() -> None:
    """Verify build command rejects reserved permission mode exact form."""
    with pytest.raises(ValueError, match="reserved.*--permission-mode"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--permission-mode", "default"],
        )


def test_build_command_rejects_reserved_permission_mode_equals_form() -> None:
    """Verify build command rejects reserved permission mode equals form."""
    with pytest.raises(ValueError, match="reserved.*--permission-mode"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--permission-mode=default"],
        )


def test_build_command_rejects_attached_prompt_short_flag() -> None:
    """Verify build command rejects attached prompt short flag."""
    with pytest.raises(ValueError, match="reserved.*-p"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-phello"],
        )


def test_build_command_rejects_reserved_print_long_flag() -> None:
    """Verify build command rejects reserved print long flag."""
    with pytest.raises(ValueError, match="reserved.*--print"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--print"],
        )


def test_build_command_rejects_reserved_resume_short_flag() -> None:
    """Verify build command rejects reserved resume short flag."""
    with pytest.raises(ValueError, match="reserved.*-r"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-r", "session-id"],
        )


def test_build_command_rejects_reserved_continue_flag() -> None:
    """Verify build command rejects reserved continue flag."""
    with pytest.raises(ValueError, match="reserved.*--continue"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--continue"],
        )


def test_build_command_rejects_reserved_session_id_flag() -> None:
    """Verify build command rejects reserved session id flag."""
    with pytest.raises(ValueError, match="reserved.*--session-id"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session-id", "00000000-0000-0000-0000-000000000000"],
        )


def test_build_command_rejects_reserved_session_id_equals_form() -> None:
    """Verify build command rejects reserved session id equals form."""
    with pytest.raises(ValueError, match="reserved.*--session-id"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session-id=00000000-0000-0000-0000-000000000000"],
        )


def test_build_command_rejects_reserved_no_session_persistence_flag() -> None:
    """Verify build command rejects reserved no session persistence flag."""
    with pytest.raises(ValueError, match="reserved.*--no-session-persistence"):
        _agent().build_command(
            "q",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--no-session-persistence"],
        )


def test_provision_runs_install_script() -> None:
    """Verify provision runs install script."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))
    asyncio.run(_agent().provision(sb))
    kind, script, _ = sb.calls[0]
    assert kind == "shell"
    assert "claude.ai/install.sh" in script


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))
    with pytest.raises(RuntimeError, match="provision"):
        asyncio.run(_agent().provision(sb))


def test_invoke_baseline_parses_stream_result() -> None:
    """Verify invoke baseline parses stream result."""
    # detect_skill=None still streams; parse_stream_run reads the result event and
    # captures raw, leaving fired False.
    payload = json.dumps(
        {
            "type": "result",
            "result": "done",
            "is_error": False,
            "session_id": "s1",
            "usage": {},
        }
    )
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    res = asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.result_text == "done"
    assert res.session_id == "s1"
    assert res.is_error is False
    assert res.fired is False
    assert res.raw == payload
    _, cmd, args, kw = sb.calls[0]
    assert cmd == ClaudeCodeAgent.CLAUDE_BIN
    assert kw["cwd"] == "/workspace"
    assert kw["env"]["HOME"] == ClaudeCodeAgent.guest_home


def test_invoke_closes_stdin_to_avoid_cli_wait() -> None:
    """Verify invoke closes stdin to avoid cli wait."""
    # claude -p waits ~3s on an open stdin pipe; pass an empty stdin (immediate EOF) so the
    # CLI proceeds at once instead of racing ("no stdin data received in 3s").
    payload = json.dumps(
        {
            "type": "result",
            "result": "done",
            "is_error": False,
            "session_id": "s1",
            "usage": {},
        }
    )
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])
    asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )
    _, _cmd, _args, kw = sb.calls[0]
    assert kw["stdin"] == b""


def test_invoke_threads_harness_args_into_build_command() -> None:
    """Verify invoke threads harness args into build command."""
    payload = json.dumps(
        {
            "type": "result",
            "result": "done",
            "is_error": False,
            "session_id": "s1",
            "usage": {},
        }
    )
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--plugin-dir", "/project"],
        )
    )

    _, _cmd, args, _kw = sb.calls[0]
    assert args[-2:] == ["--plugin-dir", "/project"]


def test_invoke_nonzero_exit_is_error() -> None:
    """Verify invoke nonzero exit is error."""
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=2, stderr_text="bad")])
    res = asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )
    assert res.is_error is True
    assert "bad" in res.result_text


def test_secrets_uses_configured_auth_env(monkeypatch: object) -> None:
    """Verify secrets uses configured auth env."""
    captured = {}

    class FakeSecret:
        """Provide a fake secret for tests."""

        @staticmethod
        def env(env_var: object, *, value: object, allow_hosts: object) -> object:
            """Env."""
            captured.update(env_var=env_var, value=value, allow_hosts=list(allow_hosts))
            return ("secret", env_var)

    import microsandbox

    monkeypatch.setattr(microsandbox, "Secret", FakeSecret)
    agent = ClaudeCodeAgent(auth_value="tok-123", auth_env="CLAUDE_CODE_OAUTH_TOKEN")
    secs = agent.secrets()
    assert len(secs) == 1
    # the configured credential name (not a hardcoded one) reaches the injected secret,
    # scoped to the Anthropic API host
    assert captured["env_var"] == "CLAUDE_CODE_OAUTH_TOKEN"
    assert captured["value"] == "tok-123"
    assert captured["allow_hosts"] == ["api.anthropic.com"]


def test_guest_env_carries_home_and_sandbox_flag() -> None:
    """Verify guest env carries home and sandbox flag."""
    env = _agent().guest_env()
    assert env["HOME"] == ClaudeCodeAgent.guest_home
    assert env["IS_SANDBOX"] == "1"  # lets claude run bypassPermissions as root in the VM


def test_guest_env_pins_utc_timezone() -> None:
    """Verify guest env pins utc timezone."""
    # The host substitutes {TODAY} as a UTC date; the agent's in-VM `date` must
    # agree or dated-path assertions fail spuriously, so the guest clock is UTC.
    assert _agent().guest_env()["TZ"] == "UTC"


def test_stage_project_assets_copies_project_skills_into_guest_home() -> None:
    """Verify stage project assets copies project skills into guest home."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))
    asyncio.run(_agent().stage_project_assets(sb, "/project"))
    kind, script, _ = sb.calls[0]
    assert kind == "shell"
    assert "/project/.claude/skills" in script
    assert f"{ClaudeCodeAgent.guest_home}/.claude/skills" in script


def test_from_env_prefers_oauth_token(monkeypatch: object) -> None:
    """Verify from env prefers oauth token."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    agent = ClaudeCodeAgent.from_env()
    assert agent._auth_env == "CLAUDE_CODE_OAUTH_TOKEN"
    assert agent._auth_value == "tok"


def test_from_env_falls_back_to_api_key(monkeypatch: object) -> None:
    """Verify from env falls back to api key."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("EVALSPEC_CLAUDE_VERSION", "9.9")
    agent = ClaudeCodeAgent.from_env()
    assert agent._auth_env == "ANTHROPIC_API_KEY"
    assert agent.version() == "9.9"


def test_credential_error_set_vs_unset(monkeypatch: object) -> None:
    """Verify credential error set vs unset."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "credential" in ClaudeCodeAgent.credential_error()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert ClaudeCodeAgent.credential_error() is None


def _skill_line(skill: str) -> str:
    """Build the skill line test fixture."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": skill}}]
            },
        }
    )


def test_claude_detect_fired_delegates_to_trigger_helper() -> None:
    """Verify claude detect fired delegates to trigger helper."""
    agent = ClaudeCodeAgent()
    assert agent.detect_fired([_skill_line("knowledge-base:archive")], "archive") is True
    assert agent.detect_fired([_skill_line("bootstrap")], "archive") is False


def test_claude_streamed_activity_true_on_assistant_event() -> None:
    """Verify claude streamed activity true on assistant event."""
    agent = ClaudeCodeAgent()
    assert agent.streamed_activity([json.dumps({"type": "assistant", "message": {}})]) is True
    assert agent.streamed_activity([json.dumps({"type": "system"})]) is False


def test_wrong_shape_json_object_does_not_raise_uncaught() -> None:
    """Verify wrong shape json object does not raise uncaught."""
    # A valid-but-wrong-shape JSON *object* (the only malformed shape `claude -p
    # --output-format json` can realistically emit — it always wraps output in an object
    # envelope) must not raise outside claude.py's narrow `(JSONDecodeError, TypeError)`
    # catch. It doesn't: parse_run_json reads missing keys via .get() defaults and returns
    # a clean (empty) RunResult. This pins why the catch is deliberately NOT widened — the
    # AttributeError path only exists for bare top-level scalars the CLI never produces.
    try:
        parse_run_json('{"unexpected":"envelope"}', "e1", "with_skill")
    except (ValueError, AttributeError) as e:
        pytest.fail(f"parse_run_json raised {type(e).__name__} not caught by claude.py: {e}")
    except (json.JSONDecodeError, TypeError):
        pass  # already caught by claude.py — no widening needed


def test_claude_skill_load_dir() -> None:
    """Verify claude skill load dir."""
    assert ClaudeCodeAgent().skill_load_dir == "/root/.claude/skills"


def test_claude_bridge_script_symlinks_fixed_home() -> None:
    """Verify claude bridge script symlinks fixed home."""
    from evalspec.agents.base import FIXED_SKILLS_HOME

    s = ClaudeCodeAgent().bridge_skills_home_script()

    assert FIXED_SKILLS_HOME in s
    assert "/root/.claude/skills" in s
    assert "ln -s" in s


def test_claude_cell_env_carries_evalspec_vars() -> None:
    """Verify claude cell env carries evalspec vars."""
    env = ClaudeCodeAgent().cell_env(arm="trial", model="opus", eval_set="popular-harnesses")

    assert env["EVALSPEC_ARM"] == "trial"
    assert env["EVALSPEC_MODEL"] == "opus"
    assert env["EVALSPEC_HARNESS"] == "claude-code"
    assert env["EVALSPEC_SET"] == "popular-harnesses"
    assert env["HOME"] == "/root"  # guest_env merged in


def test_claude_invoke_extra_env_overrides_guest_env() -> None:
    """Verify claude invoke extra env overrides guest env."""
    # A per-arm leaky env (the OpenRouter case) must reach the agent's exec env,
    # merged over guest_env().
    payload = json.dumps(
        {
            "type": "result",
            "result": "done",
            "is_error": False,
            "session_id": "s1",
            "usage": {},
        }
    )
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="opus",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            extra_env={
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api/v1",
                "TZ": "America/New_York",
            },
        )
    )

    _, _cmd, _args, kw = sb.calls[0]
    assert kw["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert kw["env"]["TZ"] == "America/New_York"  # collides with guest_env's TZ=UTC; arm wins
    assert kw["env"]["HOME"] == ClaudeCodeAgent.guest_home  # guest_env still present
