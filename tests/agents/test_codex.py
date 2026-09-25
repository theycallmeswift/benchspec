"""Tests for codex."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from benchspec.agents.base import Credential
from benchspec.agents.codex import CodexAgent, _skill_dispatch_name, parse_codex_jsonl
from benchspec.grading.trajectory import skills_dispatched
from benchspec.sandbox.errors import SandboxError
from tests.agents.doubles import SLOW_EXEC_SECONDS, SlowSandbox, exec_call, shell_call
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


_COMMANDS_NOT_READING_SKILL_MD = [
    "/bin/bash -lc 'cat /home/benchspec/skills/core-data-model/SPONSORS.md'",
    "/bin/bash -lc 'cat /tmp/other/SKILL.md'",
    "/bin/bash -lc 'cat /home/benchspec/skills/hello/SKILL.md.bak'",
    "/bin/bash -lc 'cat /opt/home/benchspec/skills/hello/SKILL.md'",
    "/bin/bash -lc 'cat /home/benchspec/skills/../SKILL.md'",
    "ls",
]


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
        "-c",
        "model_reasoning_effort=medium",
        "-C",
        "/root",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "do the thing",
    ]


def test_build_command_forwards_effort_verbatim() -> None:
    """Effort reaches codex as a config override; the CLI, not benchspec, validates it."""
    cmd = _agent().build_command(
        "prompt",
        plugin_dir=None,
        model="gpt-5.4",
        effort="xhigh",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[cmd.index("-c") + 1] == "model_reasoning_effort=xhigh"


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
def test_build_command_rejects_reserved_harness_args(arg: str) -> None:
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
        "BENCHSPEC_CODEX_VERSION": "0.142.3",
    }


def test_guest_env_omits_credentials() -> None:
    """Verify guest env omits credentials."""
    env = CodexAgent(auth_value="secret").guest_env()

    assert "CODEX_API_KEY" not in env
    assert "CODEX_ACCESS_TOKEN" not in env
    assert "secret" not in env.values()


def test_from_env_prefers_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env prefers api key."""
    monkeypatch.setenv("CODEX_API_KEY", "api-key")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "token")
    monkeypatch.setenv("BENCHSPEC_CODEX_VERSION", "0.142.3")

    agent = CodexAgent.from_env()

    assert agent.secrets() == [Credential("CODEX_API_KEY", "api-key", ("api.openai.com",))]
    assert agent.version() == "0.142.3"


def test_from_env_falls_back_to_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env falls back to access token."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "token")

    agent = CodexAgent.from_env()

    assert agent.secrets() == [
        Credential("CODEX_ACCESS_TOKEN", "token", ("chatgpt.com", "auth.openai.com"))
    ]


def test_from_env_falls_back_to_openai_api_key_under_codex_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with only `OPENAI_API_KEY` still authenticates, under the name Codex reads."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    agent = CodexAgent.from_env()

    assert agent.secrets() == [Credential("CODEX_API_KEY", "sk-openai", ("api.openai.com",))]


def test_from_env_prefers_codex_api_key_over_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex's own name wins when both keys are exported."""
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    agent = CodexAgent.from_env()

    assert agent.secrets() == [Credential("CODEX_API_KEY", "sk-codex", ("api.openai.com",))]


def test_from_env_reads_auth_json_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth))

    agent = CodexAgent.from_env()

    assert agent.auth_json_path() == str(auth)
    assert agent.secrets() == []


def test_credential_error_message_when_no_credentials_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify credential error message when no credentials set."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_AUTH_JSON_PATH", raising=False)

    msg = CodexAgent.credential_error()

    assert msg is not None
    assert "CODEX_AUTH_JSON_PATH" in msg
    assert "CODEX_API_KEY" in msg
    assert "OPENAI_API_KEY" in msg
    assert "CODEX_ACCESS_TOKEN" in msg


def test_credential_error_none_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify credential error none when api key set."""
    monkeypatch.setenv("CODEX_API_KEY", "api-key")

    assert CodexAgent.credential_error() is None


def test_credential_error_none_when_openai_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI-wide name satisfies the preflight on its own."""
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    assert CodexAgent.credential_error() is None


def test_credential_error_none_when_auth_json_path_is_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth))

    assert CodexAgent.credential_error() is None


def test_secrets_scopes_api_key_to_openai_host() -> None:
    """Verify secrets scopes api key to openai host."""
    secs = CodexAgent(auth_value="api-key", auth_env="CODEX_API_KEY").secrets()

    assert secs == [Credential("CODEX_API_KEY", "api-key", ("api.openai.com",))]


def test_secrets_scopes_access_token_to_codex_hosts() -> None:
    """Verify secrets scopes access token to codex hosts."""
    secs = CodexAgent(auth_value="token", auth_env="CODEX_ACCESS_TOKEN").secrets()

    assert secs == [Credential("CODEX_ACCESS_TOKEN", "token", ("chatgpt.com", "auth.openai.com"))]


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

    assert "/home/benchspec/skills" in script
    assert "/root/.codex/skills" in script


def test_codex_cell_env_carries_benchspec_vars() -> None:
    """Verify codex cell env carries benchspec vars."""
    env = CodexAgent(version="0.142.3").cell_env(
        arm="trial",
        model="gpt-5.4",
        eval_set="codex-smoke",
        baseline="baseline",
    )

    assert env["BENCHSPEC_ARM"] == "trial"
    assert env["BENCHSPEC_MODEL"] == "gpt-5.4"
    assert env["BENCHSPEC_HARNESS"] == "codex"
    assert env["BENCHSPEC_SET"] == "codex-smoke"
    assert env["BENCHSPEC_BASELINE"] == "baseline"


def _stream_tool_calls(lines: list[str]) -> list[dict]:
    """Every tool call the adapter reads off `lines`, one line at a time."""
    agent = _agent()
    return [call for line in lines for call in agent.stream_tool_calls(line)]


def test_stream_tool_calls_reads_the_skill_md_read_in_a_real_stream() -> None:
    """Verify a captured Codex 0.154.0 stream yields the SKILL.md read as a `hello` dispatch."""
    calls = _stream_tool_calls(_lines("codex_skill_md_read.jsonl"))

    assert calls[0] == {
        "kind": "tool_call",
        "id": "item_1",
        "name": "Skill",
        "arguments": {"skill": "hello"},
    }
    assert skills_dispatched(calls, "hello") == ["hello"]


def test_stream_tool_calls_matches_the_whole_stream_trajectory() -> None:
    """Verify the per-line view agrees with the parser's trajectory for the whole stream."""
    stream = _text("codex_skill_md_read.jsonl")

    calls = _stream_tool_calls(stream.splitlines())

    assert calls == parse_codex_jsonl(stream, "e1", "trial", None).trajectory


def test_stream_tool_calls_counts_an_item_once_it_completes() -> None:
    """Verify a started item is not a call yet; its completion is."""
    item = {"id": "i1", "type": "skill_invocation", "name": "knowledge-base:archive"}
    started = json.dumps({"type": "item.started", "item": item})
    completed = json.dumps({"type": "item.completed", "item": item})

    assert _agent().stream_tool_calls(started) == []
    assert skills_dispatched(_agent().stream_tool_calls(completed)) == ["knowledge-base:archive"]


def test_stream_tool_calls_counts_a_plain_command_and_skips_messages() -> None:
    """Verify a non-skill command is a call and a message is not."""
    command_item = {"id": "i1", "type": "command_execution", "command": "ls"}
    command = json.dumps({"type": "item.completed", "item": command_item})
    message = json.dumps(
        {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "hi"}}
    )

    assert _agent().stream_tool_calls(command) == [
        {
            "kind": "tool_call",
            "id": "i1",
            "name": "command_execution",
            "arguments": {"command": "ls"},
        }
    ]
    assert _agent().stream_tool_calls(message) == []
    assert _agent().stream_tool_calls("not json") == []


@pytest.mark.parametrize("skills_home", ["/home/benchspec/skills", "/root/.codex/skills"])
def test_skill_dispatch_name_resolves_command_reading_skill_md(skills_home: str) -> None:
    """Verify a command_execution reading a skill's SKILL.md resolves to that skill's name."""
    item = {
        "type": "command_execution",
        "command": f"/bin/bash -lc 'cat {skills_home}/core-data-model/SKILL.md'",
    }

    assert _skill_dispatch_name(item) == "core-data-model"


@pytest.mark.parametrize("command", _COMMANDS_NOT_READING_SKILL_MD)
def test_skill_dispatch_name_none_for_command_not_reading_skill_md(command: str) -> None:
    """Verify a sibling file, an out-of-home SKILL.md, and a plain command are not dispatches."""
    item = {"type": "command_execution", "command": command}

    assert _skill_dispatch_name(item) is None


def test_parse_codex_jsonl_fired_true_when_command_reads_skill_md() -> None:
    """Verify parse codex jsonl reports fired when a command reads the expected SKILL.md."""
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "i1",
                        "type": "command_execution",
                        "command": "/bin/bash -lc 'cat /home/benchspec/skills/hello/SKILL.md'",
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill="hello")

    assert res.fired is True


def test_parse_codex_jsonl_fired_false_without_skill_md_read() -> None:
    """Verify parse codex jsonl reports not fired when no command reads the SKILL.md."""
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "i1", "type": "command_execution", "command": "ls"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill="hello")

    assert res.fired is False


def test_parse_codex_jsonl_trajectory_records_skill_md_read_as_skill_dispatch() -> None:
    """Verify the trajectory records a SKILL.md read as a Skill call and other commands plainly."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "i1",
                        "type": "command_execution",
                        "command": "/bin/bash -lc 'cat /home/benchspec/skills/hello/SKILL.md'",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "i2", "type": "command_execution", "command": "ls"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill=None)

    assert {
        "kind": "tool_call",
        "id": "i1",
        "name": "Skill",
        "arguments": {"skill": "hello"},
    } in res.trajectory
    assert {
        "kind": "tool_call",
        "id": "i2",
        "name": "command_execution",
        "arguments": {"command": "ls"},
    } in res.trajectory


def test_skills_dispatched_reports_codex_skill_md_read() -> None:
    """Verify skills_dispatched sees a Codex SKILL.md read as a dispatched skill end to end."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "i1",
                        "type": "command_execution",
                        "command": "/bin/bash -lc 'cat /home/benchspec/skills/hello/SKILL.md'",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "i2", "type": "command_execution", "command": "ls"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill=None)

    assert skills_dispatched(res.trajectory) == ["hello"]


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
    assert res.input_tokens == 20
    assert res.cache_read_tokens == 5
    assert res.output_tokens == 9
    assert res.total_tokens == 37
    assert res.is_error is False
    assert res.fired is True


def test_parse_codex_jsonl_reads_usage_from_a_real_exec_stream() -> None:
    """A real `codex exec --json` run through OpenRouter reports its usage on turn.completed."""
    res = parse_codex_jsonl(_text("codex_exec_openrouter.jsonl"), "e1", "trial", None)

    assert res.result_text == "Hello, Alice!"
    assert res.input_tokens == 15140
    assert res.cache_read_tokens == 6656
    assert res.output_tokens == 136
    assert res.total_tokens > 0


def test_invoke_times_the_harness_process_because_the_stream_has_no_timing() -> None:
    """`codex exec --json` events carry no timestamps, so duration is the process wall time."""
    stream = _text("codex_exec_openrouter.jsonl")
    sandbox = SlowSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=stream)])

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

    assert res.is_error is False
    assert res.duration_ms >= SLOW_EXEC_SECONDS * 1000
    assert res.output_tokens == 136


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


def test_parse_codex_jsonl_error_events_before_turn_completed_are_recovered() -> None:
    """Reconnect `error` events that Codex recovers from do not fail a completed turn."""
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-6"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "error",
                    "message": "Reconnecting... 2/5 (stream disconnected before completion)",
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "message": "Reconnecting... 3/5 (stream disconnected before completion)",
                }
            ),
            json.dumps({"type": "item.completed", "item": {"type": "error"}}),
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill=None)

    assert res.is_error is False
    assert res.result_text == "OK"
    assert "Reconnecting... 2/5" in res.raw


def test_parse_codex_jsonl_turn_failed_sets_is_error() -> None:
    """A `turn.failed` event is a failed turn, with its nested message as the result."""
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-7"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "turn.failed", "error": {"message": "rate limited"}}),
        ]
    )

    res = parse_codex_jsonl(stream, "e1", "trial", detect_skill=None)

    assert res.is_error is True
    assert res.result_text == "rate limited"


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

    provision_call = shell_call(sandbox)
    script = provision_call.script
    assert "CODEX_NON_INTERACTIVE=1" in script
    assert "npm i -g" in script
    # The version is baked into the ref (not a guest env var), so the cache key tracks it.
    assert "@openai/codex@latest" in script
    assert "codex" in script
    assert "codex.openai.com" not in script
    assert provision_call.env["CODEX_HOME"] == "/root/.codex"


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))

    with pytest.raises(RuntimeError, match="codex provision failed"):
        asyncio.run(_agent().provision(sandbox))


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

    recorded = exec_call(sandbox)
    assert recorded.cmd == "/usr/local/bin/codex"
    assert recorded.args[-1] == "do it"
    assert recorded.args[recorded.args.index("-C") + 1] == "/workspace"
    assert recorded.cwd == "/workspace"
    assert recorded.stdin == b""
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

    env = exec_call(sandbox).env
    assert env["TZ"] == "America/Los_Angeles"
    assert env["EXTRA"] == "1"
    assert env["HOME"] == "/root"


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


def test_invoke_nonzero_exit_keeps_the_stream_and_headlines_stderr() -> None:
    """A crash after a real stream keeps raw, trajectory and tokens; stderr is the headline."""
    stream = _text("codex_parse_success.jsonl")
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
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="gpt-5.4",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    parsed = parse_codex_jsonl(stream, "e1", "trial", None)
    assert res.is_error is True
    assert res.result_text == "segfault at exit"
    assert res.raw == stream
    assert res.trajectory
    assert res.total_tokens == parsed.total_tokens


def test_invoke_nonzero_exit_with_blank_stderr_keeps_the_parsed_text() -> None:
    """With nothing on stderr, the errored result keeps the parser's own text."""
    stream = _text("codex_parse_success.jsonl")
    sandbox = FakeSandbox(
        exec_outputs=[FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text="  \n")]
    )

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

    parsed = parse_codex_jsonl(stream, "e1", "trial", None)
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
    assert res.result_text.startswith("<sandbox-error>")


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

    assert exec_call(sandbox).args[-3:] == ["--color", "never", "do the thing"]


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

    assert len(sandbox.calls) == 2
    auth_copy_script = shell_call(sandbox, 0).script
    assert CodexAgent.AUTH_JSON_GUEST_SOURCE in auth_copy_script
    assert "/root/.codex/auth.json" in auth_copy_script
    assert exec_call(sandbox, 1).cmd == "/usr/local/bin/codex"


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


_OPENROUTER_OVERRIDES = [
    "-c", 'model_provider="openrouter"',
    "-c", 'model_providers.openrouter.name="OpenRouter"',
    "-c", 'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
    "-c", 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"',
    "-c", 'model_providers.openrouter.wire_api="responses"',
]


def _openrouter_agent() -> CodexAgent:
    """Build an arm agent routed through OpenRouter with a known key."""
    return CodexAgent(
        auth_value="sk-or-test", auth_env="OPENROUTER_API_KEY", provider="openrouter"
    )


def test_build_command_under_openrouter_appends_the_provider_overrides() -> None:
    """The benchspec-owned `-c` overrides declare the provider; user args still follow."""
    cmd = _openrouter_agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="openai/gpt-5.5",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--full-auto"],
    )

    assert cmd == [
        "/usr/local/bin/codex",
        "exec",
        "--json",
        "-m",
        "openai/gpt-5.5",
        "-c",
        "model_reasoning_effort=medium",
        *_OPENROUTER_OVERRIDES,
        "-C",
        "/root",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--full-auto",
        "do the thing",
    ]


def test_build_command_under_openrouter_still_reserves_c_for_users() -> None:
    """Benchspec owning `-c` tokens does not open `-c` to `harness_args`."""
    with pytest.raises(ValueError, match="reserved harness arg"):
        _openrouter_agent().build_command(
            "p", plugin_dir=None, model="openai/gpt-5.5", effort="medium",
            resume_session_id=None, detect_skill=None,
            harness_args=["-c", 'model_provider="mine"'],
        )


def test_build_command_under_default_carries_no_provider_overrides() -> None:
    """The `default` command is byte-identical to today's: no `model_provider` at all."""
    cmd = _agent().build_command(
        "p", plugin_dir=None, model="gpt-5.4", effort="medium",
        resume_session_id=None, detect_skill=None,
    )

    assert not any("model_provider" in token for token in cmd)


def test_from_env_under_openrouter_reads_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `openrouter` the credential is OPENROUTER_API_KEY, scoped to the gateway host."""
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex-ignored")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    agent = CodexAgent.from_env("openrouter")

    assert agent.provider == "openrouter"
    assert agent.secrets() == [Credential("OPENROUTER_API_KEY", "sk-or-test", ("openrouter.ai",))]


def test_credential_error_under_openrouter_needs_only_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Codex credential neither satisfies nor is named by the OpenRouter check."""
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    error = CodexAgent.credential_error("openrouter")

    assert error is not None
    assert "OPENROUTER_API_KEY" in error
    assert "CODEX_API_KEY" not in error


def test_credential_error_under_openrouter_passes_with_only_that_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an OpenRouter arm needs no OpenAI credential or auth.json at all."""
    for env_name in (
        "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "CODEX_AUTH_JSON_PATH"
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    assert CodexAgent.credential_error("openrouter") is None
