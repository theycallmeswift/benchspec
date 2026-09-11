"""Tests for claude."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from benchspec.agents.base import Credential
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.orchestration.results import parse_run_json, parse_stream_run
from benchspec.sandbox.errors import SandboxError
from tests.agents.doubles import exec_call, shell_call
from tests.support import FakeExecOutput, FakeSandbox


def _agent() -> ClaudeCodeAgent:
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

    assert cmd[0] == "/root/.local/bin/claude"
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
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))
    asyncio.run(_agent().provision(sandbox))

    assert "claude.ai/install.sh" in shell_call(sandbox).script


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))
    with pytest.raises(RuntimeError, match="provision"):
        asyncio.run(_agent().provision(sandbox))


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
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    res = asyncio.run(
        _agent().invoke(
            sandbox,
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
    recorded = exec_call(sandbox)
    assert recorded.cmd == "/root/.local/bin/claude"
    assert recorded.cwd == "/workspace"
    assert recorded.env["HOME"] == ClaudeCodeAgent.guest_home


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
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])
    asyncio.run(
        _agent().invoke(
            sandbox,
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
    assert exec_call(sandbox).stdin == b""


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
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    asyncio.run(
        _agent().invoke(
            sandbox,
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

    assert exec_call(sandbox).args[-2:] == ["--plugin-dir", "/project"]


def test_invoke_nonzero_exit_is_error() -> None:
    """Verify invoke nonzero exit is error."""
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=2, stderr_text="bad")])
    res = asyncio.run(
        _agent().invoke(
            sandbox,
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


def _completed_stream() -> str:
    """A stream-json run with one tool call and a healthy-looking `result` event."""
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "result": "done",
                    "is_error": False,
                    "session_id": "s1",
                    "duration_ms": 1500,
                    "usage": {"input_tokens": 20, "output_tokens": 9},
                }
            ),
        ]
    )


def test_invoke_nonzero_exit_keeps_the_stream_and_headlines_stderr() -> None:
    """A crash after a complete-looking stream is still errored; stderr is the headline."""
    stream = _completed_stream()
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text="segfault at exit\n")
        ]
    )

    res = asyncio.run(
        _agent().invoke(
            sandbox,
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

    parsed = parse_stream_run(stream, "e1", "without_skill", None)
    assert res.is_error is True
    assert res.result_text == "segfault at exit"
    assert res.raw == stream
    assert res.trajectory
    assert res.total_tokens == parsed.total_tokens
    assert res.duration_ms == parsed.duration_ms


def test_invoke_nonzero_exit_with_blank_stderr_keeps_the_parsed_text() -> None:
    """With nothing on stderr, the errored result keeps the parser's own text."""
    stream = _completed_stream()
    sandbox = FakeSandbox(
        exec_outputs=[FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text="  \n")]
    )

    res = asyncio.run(
        _agent().invoke(
            sandbox,
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

    parsed = parse_stream_run(stream, "e1", "without_skill", None)
    assert res.is_error is True
    assert res.result_text == parsed.result_text
    assert res.raw == stream
    assert res.trajectory
    assert res.total_tokens == parsed.total_tokens


def test_invoke_records_sandbox_error_as_an_infra_failure() -> None:
    """A SandboxError from the guest's exec is recorded as an is_error result, not raised."""

    class DyingSandbox(FakeSandbox):
        """A sandbox whose exec fails the way a torn-down VM does."""

        async def exec(
            self,
            cmd: str,
            args: list[str] | None = None,
            *,
            cwd: str | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None,
            stdin: bytes | None = None,
        ) -> FakeExecOutput:
            """Fail like a torn-down VM."""
            raise SandboxError("container gone")

    res = asyncio.run(
        _agent().invoke(
            DyingSandbox(),
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
    assert res.result_text.startswith("<sandbox-error>")


def test_secrets_declares_the_configured_credential_scoped_to_anthropic() -> None:
    """The configured credential name reaches the guest, scoped to the Anthropic API host."""
    agent = ClaudeCodeAgent(auth_value="tok-123", auth_env="CLAUDE_CODE_OAUTH_TOKEN")

    credentials = agent.secrets()

    assert credentials == [
        Credential("CLAUDE_CODE_OAUTH_TOKEN", "tok-123", ("api.anthropic.com",))
    ]


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
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))
    asyncio.run(_agent().stage_project_assets(sandbox, "/project"))

    script = shell_call(sandbox).script
    assert "/project/.claude/skills" in script
    assert f"{ClaudeCodeAgent.guest_home}/.claude/skills" in script


def test_from_env_prefers_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env prefers oauth token."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    agent = ClaudeCodeAgent.from_env()
    assert agent._auth_env == "CLAUDE_CODE_OAUTH_TOKEN"
    assert agent._auth_value == "tok"


def test_from_env_falls_back_to_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env falls back to api key."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("BENCHSPEC_CLAUDE_VERSION", "9.9")
    agent = ClaudeCodeAgent.from_env()
    assert agent._auth_env == "ANTHROPIC_API_KEY"
    assert agent.version() == "9.9"


def test_credential_error_set_vs_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify credential error set vs unset."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    unset_error = ClaudeCodeAgent.credential_error()
    assert unset_error is not None
    assert "credential" in unset_error
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
    from benchspec.agents.base import FIXED_SKILLS_HOME

    s = ClaudeCodeAgent().bridge_skills_home_script()

    assert FIXED_SKILLS_HOME in s
    assert "/root/.claude/skills" in s
    assert "ln -s" in s


def test_claude_cell_env_carries_benchspec_vars() -> None:
    """Verify claude cell env carries benchspec vars."""
    env = ClaudeCodeAgent().cell_env(arm="trial", model="opus", eval_set="popular-harnesses")

    assert env["BENCHSPEC_ARM"] == "trial"
    assert env["BENCHSPEC_MODEL"] == "opus"
    assert env["BENCHSPEC_HARNESS"] == "claude-code"
    assert env["BENCHSPEC_SET"] == "popular-harnesses"
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
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    asyncio.run(
        _agent().invoke(
            sandbox,
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

    env = exec_call(sandbox).env
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["TZ"] == "America/New_York"  # collides with guest_env's TZ=UTC; arm wins
    assert env["HOME"] == ClaudeCodeAgent.guest_home  # guest_env still present


def _openrouter_agent() -> ClaudeCodeAgent:
    """Build an arm agent routed through OpenRouter with a known key."""
    return ClaudeCodeAgent(
        auth_value="sk-or-test", auth_env="ANTHROPIC_AUTH_TOKEN", provider="openrouter"
    )


def test_from_env_under_openrouter_reads_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `openrouter` the credential is OPENROUTER_API_KEY, not an Anthropic key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ignored")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    agent = ClaudeCodeAgent.from_env("openrouter")

    assert agent.provider == "openrouter"
    assert agent.secrets() == [
        Credential("ANTHROPIC_AUTH_TOKEN", "sk-or-test", ("openrouter.ai",))
    ]


def test_guest_env_under_openrouter_points_at_the_gateway_and_empties_the_api_key() -> None:
    """The guest gets the cookbook base URL (no `/v1`) and an explicitly empty API key."""
    env = _openrouter_agent().guest_env()

    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["HOME"] == ClaudeCodeAgent.guest_home
    assert env["IS_SANDBOX"] == "1"
    assert env["TZ"] == "UTC"


def test_guest_env_under_default_is_unchanged() -> None:
    """The `default` provider produces exactly today's guest env: no gateway keys at all."""
    assert _agent().guest_env() == {"HOME": "/root", "IS_SANDBOX": "1", "TZ": "UTC"}


def test_secrets_under_default_stay_scoped_to_anthropic() -> None:
    """The `default` provider still scopes the credential to the Anthropic API host."""
    assert _agent().secrets() == [
        Credential("ANTHROPIC_API_KEY", "sk-test", ("api.anthropic.com",))
    ]


def test_credential_error_under_openrouter_needs_only_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Anthropic credential neither satisfies nor is named by the OpenRouter check."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    error = ClaudeCodeAgent.credential_error("openrouter")

    assert error is not None
    assert "OPENROUTER_API_KEY" in error
    assert "ANTHROPIC_API_KEY" not in error


def test_credential_error_under_openrouter_passes_with_only_that_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an OpenRouter arm needs no Anthropic credential at all."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    assert ClaudeCodeAgent.credential_error("openrouter") is None


def test_invoke_under_openrouter_hands_the_guest_the_gateway_env() -> None:
    """The exec env the guest runs with carries the OpenRouter routing, arm env still winning."""
    payload = json.dumps({"type": "result", "result": "ok", "is_error": False, "session_id": "s"})
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=payload)])

    asyncio.run(
        _openrouter_agent().invoke(
            sandbox, "hi", eval_id="e", config="trial", workdir="/w", plugin_dir=None,
            model="anthropic/claude-sonnet-4.6", effort="medium", resume_session_id=None,
            detect_skill=None, extra_env={"ANTHROPIC_BASE_URL": "https://example.test"},
        )
    )

    env = exec_call(sandbox).env
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_BASE_URL"] == "https://example.test"  # arm env wins
    assert "ANTHROPIC_AUTH_TOKEN" not in env  # the key is a scoped secret, not plain env
