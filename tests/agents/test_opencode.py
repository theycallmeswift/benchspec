"""Tests for opencode."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from benchspec.agents.base import Credential
from benchspec.agents.opencode import OpenCodeAgent, parse_opencode_jsonl
from benchspec.sandbox.errors import SandboxError
from tests.agents.doubles import SLOW_EXEC_SECONDS, SlowSandbox, exec_call, shell_call
from tests.support import FakeExecOutput, FakeSandbox

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_lines(name: str) -> list[str]:
    """Build the lines test fixture."""
    return (FIXTURES / name).read_text().splitlines()


def _opencode_agent(auth_env: str = "ANTHROPIC_API_KEY") -> OpenCodeAgent:
    """Build the agent test fixture."""
    return OpenCodeAgent(auth_value="sk-test", auth_env=auth_env, version="latest")


def test_build_command_rejects_unqualified_model() -> None:
    """Verify build command rejects unqualified model."""
    agent = OpenCodeAgent()
    with pytest.raises(ValueError, match="provider-qualified"):
        agent.build_command(
            "hi",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )


def test_build_command_accepts_qualified_model() -> None:
    """Verify build command accepts qualified model."""
    agent = OpenCodeAgent()
    cmd = agent.build_command(
        "hi",
        plugin_dir=None,
        model="google/gemini-3.5-flash",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
    )
    assert "google/gemini-3.5-flash" in cmd


def test_build_command_shape_and_effort_mapping() -> None:
    """Verify build command shape and effort mapping."""
    cmd = _opencode_agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="anthropic/claude-sonnet-4-6",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[0] == "/usr/local/bin/opencode"
    assert cmd[1] == "run"
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--variant") + 1] == "default"  # medium → default
    assert cmd[cmd.index("-m") + 1] == "anthropic/claude-sonnet-4-6"
    # prompt is the trailing positional, not -p (that's Claude Code's shape)
    assert cmd[-1] == "do the thing"


def test_build_command_effort_low_maps_to_fast_variant() -> None:
    """Verify build command effort low maps to fast variant."""
    cmd = _opencode_agent().build_command(
        "question",
        plugin_dir=None,
        model="provider/model",
        effort="low",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[cmd.index("--variant") + 1] == "fast"


def test_build_command_effort_high_maps_to_thorough_variant() -> None:
    """Verify build command effort high maps to thorough variant."""
    cmd = _opencode_agent().build_command(
        "question",
        plugin_dir=None,
        model="provider/model",
        effort="high",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[cmd.index("--variant") + 1] == "thorough"


def test_build_command_ignores_plugin_and_resume() -> None:
    """Verify build command ignores plugin and resume."""
    # OpenCode v1 has no plugin-dir or resume equivalents; the args are accepted for
    # protocol parity but must not leak into the command line.
    cmd = _opencode_agent().build_command(
        "question",
        plugin_dir="/plugin",
        model="provider/model",
        effort="medium",
        resume_session_id="sess-X",
        detect_skill="archive",
    )

    assert "--plugin-dir" not in cmd
    assert "--resume" not in cmd
    assert "/plugin" not in cmd
    assert "sess-X" not in cmd


def test_opencode_streamed_activity_true_when_turn_began() -> None:
    """Verify opencode streamed activity true when turn began."""
    agent = OpenCodeAgent()
    assert agent.streamed_activity(_fixture_lines("opencode_route_nofire.jsonl")) is True


def test_opencode_streamed_activity_false_on_no_events() -> None:
    """Verify opencode streamed activity false on no events."""
    agent = OpenCodeAgent()
    assert agent.streamed_activity(["", "not json", "  "]) is False


def test_parse_opencode_jsonl_populates_run_result() -> None:
    """Verify parse opencode jsonl populates run result."""
    # OpenCode emits step_start / text / tool_use / step_finish events, no
    # terminal `result` event. The result body concatenates the non-empty
    # text.part.text events; totals come from step_finish.part.tokens.total. fired=True
    # picked up via the `skill` dispatcher's state.input.name (the real observed shape).
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "step_start",
                    "timestamp": 1000,
                    "sessionID": "sess-1",
                    "part": {"type": "step-start"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "timestamp": 2000,
                    "sessionID": "sess-1",
                    "part": {
                        "type": "tool",
                        "tool": "skill",
                        "state": {"status": "completed", "input": {"name": "archive"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "timestamp": 3000,
                    "sessionID": "sess-1",
                    "part": {"type": "text", "text": "done"},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 4321,
                    "sessionID": "sess-1",
                    "part": {"type": "step-finish", "tokens": {"total": 15}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill="archive")

    assert res.result_text == "done"
    assert res.total_tokens == 15
    assert res.session_id == "sess-1"
    assert res.is_error is False
    assert res.fired is True


def test_parse_opencode_jsonl_reads_the_token_split_from_a_real_run_stream() -> None:
    """A real `opencode run --format json` run splits each step's tokens; reasoning is output."""
    stream = (FIXTURES / "opencode_run_openrouter.jsonl").read_text()

    res = parse_opencode_jsonl(stream, "e1", "trial", None)

    assert res.result_text == "Hello, Alice!"
    assert res.total_tokens == 10866 + 10932
    assert res.input_tokens == 3 + 1
    assert res.output_tokens == 52 + 7
    assert res.cache_read_tokens == 0 + 10811
    assert res.cache_creation_tokens == 10811 + 113
    assert res.total_tokens == (
        res.input_tokens + res.output_tokens + res.cache_read_tokens + res.cache_creation_tokens
    )


def test_parse_opencode_jsonl_counts_reasoning_tokens_as_output() -> None:
    """Reasoning is billed as output, so it lands in output_tokens like Claude's thinking."""
    stream = json.dumps(
        {
            "type": "step_finish",
            "part": {
                "type": "step-finish",
                "tokens": {
                    "total": 130,
                    "input": 100,
                    "output": 10,
                    "reasoning": 20,
                    "cache": {"read": 0, "write": 0},
                },
            },
        }
    )

    res = parse_opencode_jsonl(stream, "e1", "trial", None)

    assert res.output_tokens == 30


def test_invoke_times_the_harness_process_not_the_event_span() -> None:
    """Event timestamps miss boot and the first model call, so duration is the process's."""
    stream = (FIXTURES / "opencode_run_openrouter.jsonl").read_text()
    sandbox = SlowSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=stream)])

    res = asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is False
    assert res.duration_ms >= SLOW_EXEC_SECONDS * 1000
    assert res.output_tokens > 0


def test_parse_opencode_jsonl_sums_tokens_across_step_finishes() -> None:
    """Verify parse opencode jsonl sums tokens across step finishes."""
    stream = "\n".join(
        [
            json.dumps({"type": "text", "timestamp": 1, "part": {"type": "text", "text": "hi"}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 2,
                    "part": {"type": "step-finish", "tokens": {"total": 100}},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 3,
                    "part": {"type": "step-finish", "tokens": {"total": 50}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.total_tokens == 150


def test_parse_keeps_all_text_when_multiple() -> None:
    """Verify parse keeps all text when multiple."""
    # All text events are accumulated for the judge — mid-run narration is joined
    # with the final agent message rather than overwritten by it.
    stream = "\n".join(
        [
            json.dumps({"type": "text", "part": {"type": "text", "text": "thinking..."}}),
            json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "FINAL ANSWER"}}),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert "thinking..." in res.result_text
    assert "FINAL ANSWER" in res.result_text


def test_parse_keeps_all_text_events_not_just_last() -> None:
    """Verify parse keeps all text events not just last."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "text",
                    "part": {"type": "text", "text": "Flagging a contradiction."},
                }
            ),
            json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "write"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "Done."}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 5}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill=None)

    assert "Flagging a contradiction." in res.result_text
    assert "Done." in res.result_text


def test_parse_opencode_jsonl_no_tokens_marks_errored() -> None:
    """Verify parse opencode jsonl no tokens marks errored."""
    # No step_finish events at all (zero tokens) ⇒ the agent never made an API
    # call. Launch/auth failure shape.
    stream = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "Read"}})

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.is_error is True
    assert res.total_tokens == 0
    assert res.fired is False


def test_parse_opencode_jsonl_rejected_final_tool_call_marks_errored() -> None:
    """A run cut short by a permission prompt the guest cannot answer is errored."""
    # OpenCode answers an unapprovable tool call with a tool error and ends the step
    # on `reason: "tool-calls"`; tokens were spent, but the agent never got to finish.
    stream = "\n".join(
        [
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "glob",
                        "state": {
                            "status": "error",
                            "input": {"pattern": "*", "path": "/"},
                            "error": "The user rejected permission to use this specific tool call.",
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "type": "step-finish",
                        "reason": "tool-calls",
                        "tokens": {"total": 10673},
                    },
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.is_error is True
    assert res.total_tokens == 10673
    assert "rejected permission" in res.result_text


def test_parse_opencode_jsonl_rejection_followed_by_text_is_not_errored() -> None:
    """A rejected tool call the agent talked past is a finished run, not an error."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "glob",
                        "state": {
                            "status": "error",
                            "input": {"pattern": "*", "path": "/"},
                            "error": "The user rejected permission to use this specific tool call.",
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "part": {"type": "text", "text": "I could not list /, so I worked in cwd."},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 300}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.is_error is False
    assert res.result_text == "I could not list /, so I worked in cwd."


def test_parse_opencode_jsonl_rejection_followed_by_completed_tool_is_not_errored() -> None:
    """A rejected tool call followed by a completed one means the agent recovered."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "glob",
                        "state": {
                            "status": "error",
                            "input": {"pattern": "*", "path": "/"},
                            "error": "The user rejected permission to use this specific tool call.",
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "write",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "notes.md", "content": "hello"},
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 300}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.is_error is False
    assert res.total_tokens == 300


def test_parse_opencode_jsonl_tool_only_run_not_errored_when_tokens_present() -> None:
    """Verify parse opencode jsonl tool only run not errored when tokens present."""
    # Some models (e.g., Gemini Flash) execute tools and exit without a wrap-up
    # text. Token count > 0 proves the agent made API calls — not an error.
    stream = "\n".join(
        [
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash"}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 1234}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.is_error is False
    assert res.total_tokens == 1234
    # Falls back to a re-serialized tail of parsed events — valid JSON, never a
    # raw stdout slice (which can cut into binary bytes and poison the transcript).
    tail = json.loads(res.result_text)
    assert isinstance(tail, list)
    assert tail[-1]["type"] == "step_finish"


def test_parse_opencode_jsonl_no_text_event_never_leaks_raw_stdout() -> None:
    """Verify parse opencode jsonl no text event never leaks raw stdout."""
    # Regression: a real Gemini archive run did the task via tools with no wrap-up
    # text, and the old `result_text or stdout[-2000:]` fallback sliced into binary
    # framing in the stream — poisoning prose-grading assertions. The fallback must
    # come from re-serialized events, so trailing non-JSON bytes can't leak through.
    binary_noise = "\udcef\udcbf\x00\x01rawframe"  # non-JSON trailing bytes
    stream = (
        "\n".join(
            [
                json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash"}}),
                json.dumps(
                    {
                        "type": "step_finish",
                        "part": {"type": "step-finish", "tokens": {"total": 7}},
                    }
                ),
            ]
        )
        + "\n"
        + binary_noise
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert "rawframe" not in res.result_text  # the bad tail did not leak
    assert json.loads(res.result_text)[-1]["type"] == "step_finish"


def test_parse_opencode_jsonl_skip_detect_when_no_skill() -> None:
    """Verify parse opencode jsonl skip detect when no skill."""
    # detect_skill=None: `fired` stays False even when matching tool events stream by.
    stream = "\n".join(
        [
            json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "archive"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "ok"}}),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.result_text == "ok"
    assert res.fired is False


def test_secrets_scopes_to_provider_host() -> None:
    """Verify secrets scopes to provider host."""
    agent = OpenCodeAgent(auth_value="or-key", auth_env="OPENROUTER_API_KEY")

    secs = agent.secrets()

    assert secs == [Credential("OPENROUTER_API_KEY", "or-key", ("openrouter.ai",))]


def test_secrets_anthropic_fallback_scopes_to_anthropic_host() -> None:
    """Verify secrets anthropic fallback scopes to anthropic host."""
    secs = OpenCodeAgent(auth_value="ak", auth_env="ANTHROPIC_API_KEY").secrets()

    assert secs == [Credential("ANTHROPIC_API_KEY", "ak", ("api.anthropic.com",))]


def test_secrets_gemini_remaps_to_sdk_env_name_and_scopes_to_google_host() -> None:
    """Verify secrets gemini remaps to sdk env name and scopes to google host."""
    # OpenCode is built on the Vercel AI SDK, whose Google provider reads
    # GOOGLE_GENERATIVE_AI_API_KEY (not GEMINI_API_KEY). Accept the friendlier
    # GEMINI_API_KEY on the host and inject under the SDK's name in the guest.
    secs = OpenCodeAgent(auth_value="gk", auth_env="GEMINI_API_KEY").secrets()

    assert secs == [
        Credential("GOOGLE_GENERATIVE_AI_API_KEY", "gk", ("generativelanguage.googleapis.com",))
    ]


def test_secrets_google_generative_ai_passthrough_unchanged() -> None:
    """Verify secrets google generative ai passthrough unchanged."""
    # The SDK's native env var name passes through unchanged.
    secs = OpenCodeAgent(auth_value="gk", auth_env="GOOGLE_GENERATIVE_AI_API_KEY").secrets()

    assert secs == [
        Credential("GOOGLE_GENERATIVE_AI_API_KEY", "gk", ("generativelanguage.googleapis.com",))
    ]


def test_guest_env_carries_home_tz_and_pinned_version() -> None:
    """Verify guest env carries home tz and pinned version."""
    env = OpenCodeAgent(version="0.4.2").guest_env()
    assert env["HOME"] == OpenCodeAgent.guest_home
    assert env["TZ"] == "UTC"
    # exporting the pinned version lets the provision script's npm install pick it
    # up from the same env when the snapshot is built.
    assert env["BENCHSPEC_OPENCODE_VERSION"] == "0.4.2"


def test_from_env_env_var_beats_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env env var beats pyproject."""
    monkeypatch.setenv("BENCHSPEC_OPENCODE_VERSION", "1.2.3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    agent = OpenCodeAgent.from_env()

    assert agent.version() == "1.2.3"
    assert agent._auth_env == "OPENROUTER_API_KEY"


def test_from_env_falls_back_to_anthropic_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify from env falls back to anthropic credential."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    agent = OpenCodeAgent.from_env()
    assert agent._auth_env == "ANTHROPIC_API_KEY"


def test_credential_error_message_when_no_credentials_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify credential error message when no credentials set."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    unset_error = OpenCodeAgent.credential_error()
    assert unset_error is not None
    assert "credential" in unset_error


def test_credential_error_none_when_anthropic_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify credential error none when anthropic key set."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")

    assert OpenCodeAgent.credential_error() is None


def test_provision_runs_install_script() -> None:
    """Verify provision runs install script."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))

    asyncio.run(_opencode_agent().provision(sandbox))

    script = shell_call(sandbox).script
    assert "npm i -g" in script
    assert "opencode-ai" in script


def test_secrets_raises_on_unmapped_auth_env() -> None:
    """Verify secrets raises for on unmapped auth env."""
    agent = OpenCodeAgent(auth_value="secret-value", auth_env="NEWPROVIDER_KEY")
    with pytest.raises(KeyError):
        agent.secrets()


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sandbox = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))
    with pytest.raises(RuntimeError, match="provision"):
        asyncio.run(_opencode_agent().provision(sandbox))


def test_provision_script_verifies_warmed_db() -> None:
    """Verify provision script verifies warmed db."""
    script = OpenCodeAgent().provision_script()

    assert "opencode.db" in script
    # the warm step must be followed by an existence check, not just fire-and-forget
    assert "test -f /root/.local/share/opencode/opencode.db" in script


def test_provision_script_pre_approves_every_permission() -> None:
    """The baked opencode.json allows every tool call alongside the plugin registration."""
    # `opencode run` in the guest cannot answer permission prompts; the sandbox is the
    # isolation boundary, as it is for the Claude Code and Codex arms.
    script = OpenCodeAgent().provision_script()

    config_heredoc = script.split("opencode.json <<'EOF_CFG'\n", 1)[1].split("\nEOF_CFG", 1)[0]
    config = json.loads(config_heredoc)

    assert config["permission"] == "allow"
    assert config["plugin"] == ["/root/.config/opencode/plugins/benchspec-bootstrap"]


def test_invoke_nonzero_exit_is_error() -> None:
    """Verify invoke nonzero exit is error."""
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=2, stderr_text="bad")])

    res = asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is True
    assert "bad" in res.result_text


def test_invoke_nonzero_exit_keeps_the_stream_and_headlines_stderr() -> None:
    """A crash after a real stream keeps raw, trajectory and tokens; stderr is the headline."""
    stream = (FIXTURES / "opencode_route_fired.jsonl").read_text()
    sandbox = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text="segfault at exit\n")
        ]
    )

    res = asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    parsed = parse_opencode_jsonl(stream, "e1", "without_skill", None)
    assert res.is_error is True
    assert res.result_text == "segfault at exit"
    assert res.raw == stream
    assert res.trajectory
    assert res.total_tokens == parsed.total_tokens


def test_invoke_nonzero_exit_with_blank_stderr_keeps_the_parsed_text() -> None:
    """With nothing on stderr, the errored result keeps the parser's own text."""
    stream = (FIXTURES / "opencode_route_fired.jsonl").read_text()
    sandbox = FakeSandbox(
        exec_outputs=[FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text="  \n")]
    )

    res = asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    parsed = parse_opencode_jsonl(stream, "e1", "without_skill", None)
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
        _opencode_agent().invoke(
            DyingSandbox(),
            "p",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is True
    assert res.result_text.startswith("<sandbox-error>")


def test_build_command_places_harness_args_before_prompt() -> None:
    """Verify build command places harness args before prompt."""
    cmd = _opencode_agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="provider/model",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--print-logs"],
    )

    assert cmd[-2:] == ["--print-logs", "do the thing"]


def test_build_command_allows_pass_through_equals_form() -> None:
    """Verify build command allows pass through equals form."""
    cmd = _opencode_agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="provider/model",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--print-logs=1"],
    )

    assert cmd[-2:] == ["--print-logs=1", "do the thing"]


def test_build_command_rejects_reserved_harness_args() -> None:
    """Verify build command rejects reserved harness args."""
    with pytest.raises(ValueError, match="reserved.*-m"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-m", "other/model"],
        )


def test_build_command_rejects_reserved_harness_arg_equals_form() -> None:
    """Verify build command rejects reserved harness arg equals form."""
    with pytest.raises(ValueError, match="reserved.*--variant"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--variant=fast"],
        )


def test_build_command_rejects_attached_model_short_flag() -> None:
    """Verify build command rejects attached model short flag."""
    with pytest.raises(ValueError, match="reserved.*-m"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-mother/model"],
        )


def test_build_command_rejects_reserved_continue_flag() -> None:
    """Verify build command rejects reserved continue flag."""
    with pytest.raises(ValueError, match="reserved.*--continue"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--continue"],
        )


def test_build_command_rejects_reserved_continue_short_flag() -> None:
    """Verify build command rejects reserved continue short flag."""
    with pytest.raises(ValueError, match="reserved.*-c"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-c"],
        )


def test_build_command_rejects_reserved_session_flag() -> None:
    """Verify build command rejects reserved session flag."""
    with pytest.raises(ValueError, match="reserved.*--session"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session", "session-id"],
        )


def test_build_command_rejects_reserved_session_short_flag() -> None:
    """Verify build command rejects reserved session short flag."""
    with pytest.raises(ValueError, match="reserved.*-s"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-s", "session-id"],
        )


def test_build_command_rejects_reserved_session_equals_form() -> None:
    """Verify build command rejects reserved session equals form."""
    with pytest.raises(ValueError, match="reserved.*--session"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session=session-id"],
        )


def test_build_command_rejects_reserved_command_flag() -> None:
    """Verify build command rejects reserved command flag."""
    with pytest.raises(ValueError, match="reserved.*--command"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--command", "echo hi"],
        )


def test_build_command_rejects_reserved_prompt_flag() -> None:
    """Verify build command rejects reserved prompt flag."""
    with pytest.raises(ValueError, match="reserved.*--prompt"):
        _opencode_agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--prompt", "other prompt"],
        )


def test_invoke_threads_harness_args_into_build_command() -> None:
    """Verify invoke threads harness args into build command."""
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text="")])

    asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "do the thing",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--print-logs"],
        )
    )

    assert exec_call(sandbox).args[-2:] == ["--print-logs", "do the thing"]


def test_parse_opencode_jsonl_carries_raw_stdout() -> None:
    """Verify parse opencode jsonl carries raw stdout."""
    # OpenCode's parser must also keep the full stream so the artifact is agent-agnostic.
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "step_start",
                    "timestamp": 1000,
                    "sessionID": "session-1",
                    "part": {"type": "step-start"},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 2000,
                    "sessionID": "session-1",
                    "part": {"type": "step-finish", "tokens": {"total": 10}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill=None)

    assert res.raw == stream


def test_parse_opencode_jsonl_carries_normalized_trajectory() -> None:
    """Verify parse opencode jsonl carries normalized trajectory."""
    # OpenCode bundles a tool use into ONE completed tool_use event (no separate
    # result frame) -> tool_call events only. A `skill` dispatch is normalized to the
    # canonical Skill shape so the shared consumers stay agent-agnostic.
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "sessionID": "session-1",
                    "part": {
                        "type": "tool",
                        "tool": "skill",
                        "state": {"status": "completed", "input": {"name": "archive"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "sessionID": "session-1",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "ls"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "session-1",
                    "part": {"type": "step-finish", "tokens": {"total": 10}},
                }
            ),
        ]
    )
    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill=None)
    assert res.trajectory == [
        {
            "kind": "tool_call",
            "id": "",
            "name": "Skill",
            "arguments": {"skill": "archive"},
        },
        {"kind": "tool_call", "id": "", "name": "bash", "arguments": {"command": "ls"}},
    ]


def test_parse_opencode_jsonl_trajectory_feeds_shared_consumers() -> None:
    """Verify parse opencode jsonl trajectory feeds shared consumers."""
    # The normalized trajectory must work with the agent-agnostic helpers, so the
    # judge process-facts payoff covers OpenCode evals too.
    from benchspec.grading.trajectory import render_process_facts, skills_dispatched

    stream = json.dumps(
        {
            "type": "tool_use",
            "sessionID": "session-1",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"status": "completed", "input": {"name": "writing-prompts"}},
            },
        }
    )
    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill=None)
    assert skills_dispatched(res.trajectory) == ["writing-prompts"]
    assert "Skill(writing-prompts)" in render_process_facts([res.trajectory])


def test_parse_opencode_jsonl_trajectory_empty_without_tool_uses() -> None:
    """Verify parse opencode jsonl trajectory empty without tool uses."""
    stream = json.dumps(
        {"type": "step_finish", "part": {"type": "step-finish", "tokens": {"total": 5}}}
    )
    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)
    assert res.trajectory == []


def test_opencode_fired_and_skills_dispatched_agree_on_name(tmp_path: Path) -> None:
    """Verify opencode fired and skills dispatched agree on name."""
    from benchspec.grading.trajectory import skills_dispatched

    line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"status": "completed", "input": {"name": "archive"}},
            },
        }
    )
    res = parse_opencode_jsonl(line, "e1", "with_skill", detect_skill="archive")
    assert res.fired is True
    assert skills_dispatched(res.trajectory) == ["archive"]


def test_opencode_non_completed_skill_neither_fires_nor_trajectories() -> None:
    """Verify opencode non completed skill neither fires nor trajectories."""
    # A skill dispatch seen only in a non-completed frame is dropped by the
    # trajectory's completed-frame gate; `fired` must honor the same gate so the two
    # stay consistent (no fired=True with an empty process-facts trajectory).
    from benchspec.grading.trajectory import skills_dispatched

    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "skill",
                        "state": {"status": "running", "input": {"name": "archive"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 5}},
                }
            ),
        ]
    )
    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill="archive")
    assert res.fired is False
    assert skills_dispatched(res.trajectory) == []


def test_parse_opencode_jsonl_skips_non_completed_tool_frames() -> None:
    """Verify parse opencode jsonl skips non completed tool frames."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {"status": "running", "input": {"command": "ls"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "ls"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 5}},
                }
            ),
        ]
    )
    res = parse_opencode_jsonl(stream, "e1", "with_skill", detect_skill=None)
    assert [e["name"] for e in res.trajectory] == ["bash"]  # only the completed frame


def test_parse_opencode_jsonl_trajectory_keeps_rejected_tool_calls() -> None:
    """A permission-rejected call is kept in the trajectory, flagged as rejected."""
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "glob",
                        "state": {
                            "status": "error",
                            "input": {"pattern": "*", "path": "/"},
                            "error": "The user rejected permission to use this specific tool call.",
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "ls"}},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"total": 5}},
                }
            ),
        ]
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert res.trajectory == [
        {
            "kind": "tool_call",
            "id": "",
            "name": "glob",
            "arguments": {"pattern": "*", "path": "/"},
            "status": "rejected",
        },
        {"kind": "tool_call", "id": "", "name": "bash", "arguments": {"command": "ls"}},
    ]


def test_parse_opencode_jsonl_rejected_tool_call_feeds_shared_consumers() -> None:
    """A rejected entry passes through the agent-agnostic trajectory helpers."""
    from benchspec.grading.trajectory import render_process_facts, skills_dispatched

    stream = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "glob",
                "state": {
                    "status": "error",
                    "input": {"pattern": "*", "path": "/"},
                    "error": "The user rejected permission to use this specific tool call.",
                },
            },
        }
    )

    res = parse_opencode_jsonl(stream, "e1", "without_skill", detect_skill=None)

    assert skills_dispatched(res.trajectory) == []
    assert render_process_facts([res.trajectory]) == "Turn 1: glob"


def test_opencode_skill_load_dir_is_config_path() -> None:
    """Verify opencode skill load dir is config path."""
    agent = OpenCodeAgent()
    assert agent.skill_load_dir == f"{agent.guest_home}/.config/opencode/skills"


def test_opencode_bridge_script_symlinks_fixed_home() -> None:
    """Verify opencode bridge script symlinks fixed home."""
    from benchspec.agents.base import FIXED_SKILLS_HOME

    script = OpenCodeAgent().bridge_skills_home_script()

    assert FIXED_SKILLS_HOME in script
    assert "/root/.config/opencode/skills" in script
    assert "ln -s" in script


def test_opencode_cell_env_carries_benchspec_vars() -> None:
    """Verify opencode cell env carries benchspec vars."""
    env = OpenCodeAgent(version="1.2.3").cell_env(
        arm="trial", model="google/gemini-3.5-flash", eval_set="default", baseline="baseline"
    )

    assert env["BENCHSPEC_ARM"] == "trial"
    assert env["BENCHSPEC_MODEL"] == "google/gemini-3.5-flash"
    assert env["BENCHSPEC_HARNESS"] == "opencode"
    assert env["BENCHSPEC_SET"] == "default"
    assert env["BENCHSPEC_BASELINE"] == "baseline"
    assert env["HOME"] == "/root"  # guest_env merged in


def test_opencode_invoke_extra_env_overrides_guest_env() -> None:
    """Verify opencode invoke extra env overrides guest env."""
    # A per-arm env reaches the agent's exec env, merged over guest_env().
    stream = json.dumps(
        {
            "type": "step_finish",
            "sessionID": "session-1",
            "part": {"type": "step-finish", "tokens": {"total": 10}},
        }
    )
    sandbox = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=stream)])

    asyncio.run(
        _opencode_agent().invoke(
            sandbox,
            "p",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            extra_env={
                "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                "TZ": "America/New_York",
            },
        )
    )

    env = exec_call(sandbox).env
    assert env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["TZ"] == "America/New_York"  # collides with guest_env's TZ=UTC; arm wins
    assert env["HOME"] == OpenCodeAgent.guest_home  # guest_env still present


def test_from_env_under_openrouter_forces_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `openrouter` the credential is OPENROUTER_API_KEY, whatever else is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ignored")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    agent = OpenCodeAgent.from_env("openrouter")

    assert agent.provider == "openrouter"
    assert agent.secrets() == [Credential("OPENROUTER_API_KEY", "sk-or-test", ("openrouter.ai",))]


def test_from_env_under_openrouter_does_not_fall_back_to_another_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only an Anthropic key set, the OpenRouter arm carries no usable credential."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    agent = OpenCodeAgent.from_env("openrouter")

    assert agent._auth_env == "OPENROUTER_API_KEY"
    assert agent._auth_value == ""


def test_credential_error_under_openrouter_names_openrouter_api_key_despite_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Anthropic key set and the OpenRouter key unset fails preflight naming the latter."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    error = OpenCodeAgent.credential_error("openrouter")

    assert error is not None
    assert "OPENROUTER_API_KEY" in error
    assert "ANTHROPIC_API_KEY" not in error


def test_credential_error_under_default_keeps_the_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `default` provider still accepts any key in the chain."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gk")

    assert OpenCodeAgent.credential_error() is None


def test_build_command_under_openrouter_requires_the_openrouter_prefix() -> None:
    """A vendor-direct slug would route around the gateway, so it is refused."""
    agent = OpenCodeAgent(auth_value="sk-or", auth_env="OPENROUTER_API_KEY", provider="openrouter")

    with pytest.raises(ValueError, match="openrouter/"):
        agent.build_command(
            "hi", plugin_dir=None, model="anthropic/claude-sonnet-4.6", effort="medium",
            resume_session_id=None, detect_skill=None,
        )


def test_build_command_under_openrouter_accepts_the_openrouter_prefix() -> None:
    """An `openrouter/<vendor>/<model>` slug passes through to `-m` unchanged."""
    agent = OpenCodeAgent(auth_value="sk-or", auth_env="OPENROUTER_API_KEY", provider="openrouter")

    cmd = agent.build_command(
        "hi", plugin_dir=None, model="openrouter/anthropic/claude-sonnet-4.6", effort="medium",
        resume_session_id=None, detect_skill=None,
    )

    assert cmd[cmd.index("-m") + 1] == "openrouter/anthropic/claude-sonnet-4.6"


def test_build_command_under_default_accepts_any_qualified_model() -> None:
    """The `default` provider keeps today's rule: any vendor-qualified slug is fine."""
    cmd = _opencode_agent().build_command(
        "hi", plugin_dir=None, model="anthropic/claude-sonnet-4.6", effort="medium",
        resume_session_id=None, detect_skill=None,
    )

    assert "anthropic/claude-sonnet-4.6" in cmd
