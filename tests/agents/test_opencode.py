"""Tests for opencode."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from evalspec.agents.opencode import OpenCodeAgent, parse_opencode_jsonl
from tests.support import FakeExecOutput, FakeSandbox

FIXTURES = Path(__file__).parent / "fixtures"


def _lines(name: str) -> list[str]:
    """Build the lines test fixture."""
    return (FIXTURES / name).read_text().splitlines()


def _agent(auth_env: str = "ANTHROPIC_API_KEY") -> OpenCodeAgent:
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
    cmd = _agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="anthropic/claude-sonnet-4-6",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[0] == OpenCodeAgent.OPENCODE_BIN
    assert cmd[1] == "run"
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--variant") + 1] == "default"  # medium → default
    assert cmd[cmd.index("-m") + 1] == "anthropic/claude-sonnet-4-6"
    # prompt is the trailing positional, not -p (that's Claude Code's shape)
    assert cmd[-1] == "do the thing"


def test_build_command_effort_low_maps_to_fast_variant() -> None:
    """Verify build command effort low maps to fast variant."""
    cmd = _agent().build_command(
        "q",
        plugin_dir=None,
        model="x/m",
        effort="low",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[cmd.index("--variant") + 1] == "fast"


def test_build_command_effort_high_maps_to_thorough_variant() -> None:
    """Verify build command effort high maps to thorough variant."""
    cmd = _agent().build_command(
        "q",
        plugin_dir=None,
        model="x/m",
        effort="high",
        resume_session_id=None,
        detect_skill=None,
    )

    assert cmd[cmd.index("--variant") + 1] == "thorough"


def test_build_command_ignores_plugin_and_resume() -> None:
    """Verify build command ignores plugin and resume."""
    # OpenCode v1 has no plugin-dir or resume equivalents; the args are accepted for
    # protocol parity but must not leak into the command line.
    cmd = _agent().build_command(
        "q",
        plugin_dir="/plugin",
        model="x/m",
        effort="medium",
        resume_session_id="sess-X",
        detect_skill="archive",
    )

    assert "--plugin-dir" not in cmd
    assert "--resume" not in cmd
    assert "/plugin" not in cmd
    assert "sess-X" not in cmd


def test_detect_dispatch_matches_skill_dispatcher_with_input_name() -> None:
    """Verify detect dispatch matches skill dispatcher with input name."""
    # Primary OpenCode shape: `skill` tool dispatcher with `state.input.name`. This
    # is what fires when the agent uses OpenCode's native `skill` tool to load a
    # discovered skill from ~/.config/opencode/skills/<name>/SKILL.md.
    agent = _agent()
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"input": {"name": "archive"}},
            },
        }
    )
    assert agent.detect_dispatch(line, "archive") is True
    ns_line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"input": {"name": "knowledge-base:archive"}},
            },
        }
    )
    assert agent.detect_dispatch(ns_line, "archive") is True


def test_detect_dispatch_matches_tool_use_by_part_tool_fallback() -> None:
    """Verify detect dispatch matches tool use by part tool fallback."""
    # Fallback shape: tool name IS the skill name (some agents register skills
    # directly as tools instead of going through a dispatcher).
    agent = _agent()
    line = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "archive"}})
    assert agent.detect_dispatch(line, "archive") is True
    ns_line = json.dumps(
        {"type": "tool_use", "part": {"type": "tool", "tool": "knowledge-base:archive"}}
    )
    assert agent.detect_dispatch(ns_line, "archive") is True


def test_detect_dispatch_early_stops_on_different_skill_dispatcher() -> None:
    """Verify detect dispatch early stops on different skill dispatcher."""
    # A DIFFERENT skill's dispatcher is now an intended any-skill early-stop: routing
    # is decided, so don't wait out the turn. The our-skill distinction no longer
    # lives here — it lives in detect_fired, which stays strict (see the dedicated
    # test_detect_dispatch_early_stops_on_any_skill test).
    agent = _agent()
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"input": {"name": "bootstrap"}},
            },
        }
    )
    assert agent.detect_dispatch(line, "archive") is True


def test_detect_dispatch_false_for_other_tool_names() -> None:
    """Verify detect dispatch false for other tool names."""
    read = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash"}})

    assert _agent().detect_dispatch(read, "archive") is False


def test_detect_dispatch_false_for_non_json_input() -> None:
    """Verify detect dispatch false for non json input."""
    agent = _agent()

    assert agent.detect_dispatch("not json", "archive") is False
    assert agent.detect_dispatch("", "archive") is False


def test_detect_dispatch_false_when_skill_name_none() -> None:
    """Verify detect dispatch false when skill name none."""
    # No skill_name → nothing to match against; explicitly False (no generic Skill
    # dispatcher in OpenCode today).
    skill_line = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "archive"}})

    assert _agent().detect_dispatch(skill_line, None) is False


def test_detect_dispatch_early_stops_on_any_skill() -> None:
    """Verify detect dispatch early stops on any skill."""
    agent = OpenCodeAgent()
    other = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"input": {"name": "bootstrap"}},
            },
        }
    )
    # early-stop fires on ANY skill dispatch...
    assert agent.detect_dispatch(other, "archive") is True
    # ...but the tally counts only OUR skill, so a different skill is not a fire.
    assert agent.detect_fired([other], "archive") is False


def test_opencode_detect_fired_true_on_our_skill() -> None:
    """Verify opencode detect fired true on our skill."""
    agent = OpenCodeAgent()
    assert agent.detect_fired(_lines("opencode_route_fired.jsonl"), "archive") is True


def test_opencode_detect_fired_false_on_different_tool() -> None:
    """Verify opencode detect fired false on different tool."""
    agent = OpenCodeAgent()
    assert agent.detect_fired(_lines("opencode_route_nofire.jsonl"), "archive") is False


def test_opencode_streamed_activity_true_when_turn_began() -> None:
    """Verify opencode streamed activity true when turn began."""
    agent = OpenCodeAgent()
    assert agent.streamed_activity(_lines("opencode_route_nofire.jsonl")) is True


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
    assert res.duration_ms == 3321  # 4321 - 1000
    assert res.total_tokens == 15
    assert res.session_id == "sess-1"
    assert res.is_error is False
    assert res.fired is True


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


def test_secrets_scopes_to_provider_host(monkeypatch: object) -> None:
    """Verify secrets scopes to provider host."""
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
    agent = OpenCodeAgent(auth_value="or-key", auth_env="OPENROUTER_API_KEY")

    secs = agent.secrets()

    assert len(secs) == 1
    assert captured["env_var"] == "OPENROUTER_API_KEY"
    assert captured["value"] == "or-key"
    assert captured["allow_hosts"] == ["openrouter.ai"]


def test_secrets_anthropic_fallback_scopes_to_anthropic_host(
    monkeypatch: object,
) -> None:
    """Verify secrets anthropic fallback scopes to anthropic host."""
    captured = {}

    class FakeSecret:
        """Provide a fake secret for tests."""

        @staticmethod
        def env(env_var: object, *, value: object, allow_hosts: object) -> object:
            """Env."""
            captured.update(env_var=env_var, allow_hosts=list(allow_hosts))
            return ("secret", env_var)

    import microsandbox

    monkeypatch.setattr(microsandbox, "Secret", FakeSecret)

    OpenCodeAgent(auth_value="ak", auth_env="ANTHROPIC_API_KEY").secrets()

    assert captured["allow_hosts"] == ["api.anthropic.com"]


def test_secrets_gemini_remaps_to_sdk_env_name_and_scopes_to_google_host(
    monkeypatch: object,
) -> None:
    """Verify secrets gemini remaps to sdk env name and scopes to google host."""
    # OpenCode is built on the Vercel AI SDK, whose Google provider reads
    # GOOGLE_GENERATIVE_AI_API_KEY (not GEMINI_API_KEY). Accept the friendlier
    # GEMINI_API_KEY on the host and inject under the SDK's name in the guest.
    captured = {}

    class FakeSecret:
        """Provide a fake secret for tests."""

        @staticmethod
        def env(env_var: object, *, value: object, allow_hosts: object) -> object:
            """Env."""
            captured.update(env_var=env_var, allow_hosts=list(allow_hosts))
            return ("secret", env_var)

    import microsandbox

    monkeypatch.setattr(microsandbox, "Secret", FakeSecret)

    OpenCodeAgent(auth_value="gk", auth_env="GEMINI_API_KEY").secrets()

    assert captured["env_var"] == "GOOGLE_GENERATIVE_AI_API_KEY"
    assert captured["allow_hosts"] == ["generativelanguage.googleapis.com"]


def test_secrets_google_generative_ai_passthrough_unchanged(
    monkeypatch: object,
) -> None:
    """Verify secrets google generative ai passthrough unchanged."""
    # The SDK's native env var name passes through unchanged.
    captured = {}

    class FakeSecret:
        """Provide a fake secret for tests."""

        @staticmethod
        def env(env_var: object, *, value: object, allow_hosts: object) -> object:
            """Env."""
            captured.update(env_var=env_var, allow_hosts=list(allow_hosts))
            return ("secret", env_var)

    import microsandbox

    monkeypatch.setattr(microsandbox, "Secret", FakeSecret)

    OpenCodeAgent(auth_value="gk", auth_env="GOOGLE_GENERATIVE_AI_API_KEY").secrets()

    assert captured["env_var"] == "GOOGLE_GENERATIVE_AI_API_KEY"
    assert captured["allow_hosts"] == ["generativelanguage.googleapis.com"]


def test_guest_env_carries_home_tz_and_pinned_version() -> None:
    """Verify guest env carries home tz and pinned version."""
    env = OpenCodeAgent(version="0.4.2").guest_env()
    assert env["HOME"] == OpenCodeAgent.guest_home
    assert env["TZ"] == "UTC"
    # exporting the pinned version lets the provision script's npm install pick it
    # up from the same env when the snapshot is built.
    assert env["EVALSPEC_OPENCODE_VERSION"] == "0.4.2"


def test_from_env_env_var_beats_pyproject(monkeypatch: object) -> None:
    """Verify from env env var beats pyproject."""
    monkeypatch.setenv("EVALSPEC_OPENCODE_VERSION", "1.2.3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    agent = OpenCodeAgent.from_env()

    assert agent.version() == "1.2.3"
    assert agent._auth_env == "OPENROUTER_API_KEY"


def test_from_env_falls_back_to_anthropic_credential(monkeypatch: object) -> None:
    """Verify from env falls back to anthropic credential."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    agent = OpenCodeAgent.from_env()
    assert agent._auth_env == "ANTHROPIC_API_KEY"


def test_credential_error_message_when_no_credentials_set(monkeypatch: object) -> None:
    """Verify credential error message when no credentials set."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert "credential" in OpenCodeAgent.credential_error()


def test_credential_error_none_when_anthropic_key_set(monkeypatch: object) -> None:
    """Verify credential error none when anthropic key set."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")

    assert OpenCodeAgent.credential_error() is None


def test_provision_runs_install_script() -> None:
    """Verify provision runs install script."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))

    asyncio.run(_agent().provision(sb))

    kind, script, _ = sb.calls[0]
    assert kind == "shell"
    assert "npm i -g" in script
    assert "opencode-ai" in script


def test_secrets_raises_on_unmapped_auth_env() -> None:
    """Verify secrets raises for on unmapped auth env."""
    agent = OpenCodeAgent(auth_value="x", auth_env="NEWPROVIDER_KEY")
    with pytest.raises(KeyError):
        agent.secrets()


def test_provision_raises_on_failure() -> None:
    """Verify provision raises for on failure."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=1, stderr_text="boom"))
    with pytest.raises(RuntimeError, match="provision"):
        asyncio.run(_agent().provision(sb))


def test_stage_project_assets_copies_skills_into_opencode_discovery_dir() -> None:
    """Verify stage project assets copies skills into opencode discovery dir."""
    sb = FakeSandbox(shell_output=FakeExecOutput(exit_code=0))

    asyncio.run(_agent().stage_project_assets(sb, "/project"))

    kind, script, _ = sb.calls[0]
    assert kind == "shell"
    # Stage from all three plugin-shaped layouts (merged into one dest dir).
    # `skills/` (no leading dot) is the canonical Claude Code plugin layout; leaving it
    # out makes the eval skill invisible to OpenCode even when .claude/skills/ is staged.
    assert "/project/skills" in script
    assert "/project/.opencode/skills" in script
    assert "/project/.claude/skills" in script
    # Staging target MUST be OpenCode's personal-discovery path. $HOME/.opencode/skills
    # is NOT discovered (regression guard: skills staged there silently never fire).
    assert f"{OpenCodeAgent.guest_home}/.config/opencode/skills" in script
    assert f"{OpenCodeAgent.guest_home}/.opencode/skills" not in script


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> object:
    """Provide the fake proc test helper."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_judge_raises_runtimeerror_on_nonzero_exit(monkeypatch: object) -> None:
    """Verify judge raises for runtimeerror on nonzero exit."""
    # OpenCode delegates judging to the host `claude` CLI; a crashed CLI must
    # surface as RuntimeError (caught upstream as arm-level errored), not as
    # fake JUDGE ERROR assertions. Same masking trap as ClaudeCodeAgent.judge.
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _fake_proc(returncode=1, stderr="connection reset"),
    )

    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        _agent().judge("prompt", model="sonnet")


def test_judge_raises_runtimeerror_on_is_error_envelope(monkeypatch: object) -> None:
    """Verify judge raises for runtimeerror on is error envelope."""
    # `claude -p` wraps auth/rate-limit/quota errors in a 0-exit envelope with
    # is_error=true. Without surfacing as RuntimeError, the message gets laundered
    # into parse_judge_json and the arm reports fake "JUDGE ERROR" gradings.
    payload = json.dumps({"result": "Invalid API key", "is_error": True})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _fake_proc(stdout=payload),
    )

    with pytest.raises(RuntimeError, match="is_error=true.*Invalid API key"):
        _agent().judge("prompt", model="sonnet")


def test_provision_script_verifies_warmed_db() -> None:
    """Verify provision script verifies warmed db."""
    assert "opencode.db" in OpenCodeAgent.PROVISION_SCRIPT
    # the warm step must be followed by an existence check, not just fire-and-forget
    assert "test -f /root/.local/share/opencode/opencode.db" in OpenCodeAgent.PROVISION_SCRIPT


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
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert res.is_error is True
    assert "bad" in res.result_text


def test_build_command_places_harness_args_before_prompt() -> None:
    """Verify build command places harness args before prompt."""
    cmd = _agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="x/m",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--print-logs"],
    )

    assert cmd[-2:] == ["--print-logs", "do the thing"]


def test_build_command_allows_pass_through_equals_form() -> None:
    """Verify build command allows pass through equals form."""
    cmd = _agent().build_command(
        "do the thing",
        plugin_dir=None,
        model="x/m",
        effort="medium",
        resume_session_id=None,
        detect_skill=None,
        harness_args=["--print-logs=1"],
    )

    assert cmd[-2:] == ["--print-logs=1", "do the thing"]


def test_build_command_rejects_reserved_harness_args() -> None:
    """Verify build command rejects reserved harness args."""
    with pytest.raises(ValueError, match="reserved.*-m"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-m", "other/model"],
        )


def test_build_command_rejects_reserved_harness_arg_equals_form() -> None:
    """Verify build command rejects reserved harness arg equals form."""
    with pytest.raises(ValueError, match="reserved.*--variant"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--variant=fast"],
        )


def test_build_command_rejects_attached_model_short_flag() -> None:
    """Verify build command rejects attached model short flag."""
    with pytest.raises(ValueError, match="reserved.*-m"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-mother/model"],
        )


def test_build_command_rejects_reserved_continue_flag() -> None:
    """Verify build command rejects reserved continue flag."""
    with pytest.raises(ValueError, match="reserved.*--continue"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--continue"],
        )


def test_build_command_rejects_reserved_continue_short_flag() -> None:
    """Verify build command rejects reserved continue short flag."""
    with pytest.raises(ValueError, match="reserved.*-c"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-c"],
        )


def test_build_command_rejects_reserved_session_flag() -> None:
    """Verify build command rejects reserved session flag."""
    with pytest.raises(ValueError, match="reserved.*--session"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session", "session-id"],
        )


def test_build_command_rejects_reserved_session_short_flag() -> None:
    """Verify build command rejects reserved session short flag."""
    with pytest.raises(ValueError, match="reserved.*-s"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["-s", "session-id"],
        )


def test_build_command_rejects_reserved_session_equals_form() -> None:
    """Verify build command rejects reserved session equals form."""
    with pytest.raises(ValueError, match="reserved.*--session"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--session=session-id"],
        )


def test_build_command_rejects_reserved_command_flag() -> None:
    """Verify build command rejects reserved command flag."""
    with pytest.raises(ValueError, match="reserved.*--command"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--command", "echo hi"],
        )


def test_build_command_rejects_reserved_prompt_flag() -> None:
    """Verify build command rejects reserved prompt flag."""
    with pytest.raises(ValueError, match="reserved.*--prompt"):
        _agent().build_command(
            "do the thing",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--prompt", "other prompt"],
        )


def test_invoke_threads_harness_args_into_build_command() -> None:
    """Verify invoke threads harness args into build command."""
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text="")])

    asyncio.run(
        _agent().invoke(
            sb,
            "do the thing",
            eval_id="e1",
            config="without_skill",
            workdir="/workspace",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            harness_args=["--print-logs"],
        )
    )

    _, _cmd, args, _kw = sb.calls[0]
    assert args[-2:] == ["--print-logs", "do the thing"]


def test_parse_opencode_jsonl_carries_raw_stdout() -> None:
    """Verify parse opencode jsonl carries raw stdout."""
    # OpenCode's parser must also keep the full stream so the artifact is agent-agnostic.
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "step_start",
                    "timestamp": 1000,
                    "sessionID": "s",
                    "part": {"type": "step-start"},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 2000,
                    "sessionID": "s",
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
                    "sessionID": "s",
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
                    "sessionID": "s",
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
                    "sessionID": "s",
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
    from evalspec.trajectory import render_process_facts, skills_dispatched

    stream = json.dumps(
        {
            "type": "tool_use",
            "sessionID": "s",
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


def test_opencode_fired_and_skills_dispatched_agree_on_name(tmp_path: object) -> None:
    """Verify opencode fired and skills dispatched agree on name."""
    from evalspec.trajectory import skills_dispatched

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
    from evalspec.trajectory import skills_dispatched

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


def test_opencode_skill_load_dir_is_config_path() -> None:
    """Verify opencode skill load dir is config path."""
    a = OpenCodeAgent()
    assert a.skill_load_dir == f"{a.guest_home}/.config/opencode/skills"


def test_opencode_bridge_script_symlinks_fixed_home() -> None:
    """Verify opencode bridge script symlinks fixed home."""
    from evalspec.agents.base import FIXED_SKILLS_HOME

    s = OpenCodeAgent().bridge_skills_home_script()

    assert FIXED_SKILLS_HOME in s
    assert "/root/.config/opencode/skills" in s
    assert "ln -s" in s


def test_opencode_cell_env_carries_evalspec_vars() -> None:
    """Verify opencode cell env carries evalspec vars."""
    env = OpenCodeAgent(version="1.2.3").cell_env(
        arm="trial", model="google/gemini-3.5-flash", eval_set="default"
    )

    assert env["EVALSPEC_ARM"] == "trial"
    assert env["EVALSPEC_MODEL"] == "google/gemini-3.5-flash"
    assert env["EVALSPEC_HARNESS"] == "opencode"
    assert env["EVALSPEC_SET"] == "default"
    assert env["HOME"] == "/root"  # guest_env merged in


def test_opencode_invoke_extra_env_overrides_guest_env() -> None:
    """Verify opencode invoke extra env overrides guest env."""
    # A per-arm env reaches the agent's exec env, merged over guest_env().
    stream = json.dumps(
        {
            "type": "step_finish",
            "sessionID": "s",
            "part": {"type": "step-finish", "tokens": {"total": 10}},
        }
    )
    sb = FakeSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=stream)])

    asyncio.run(
        _agent().invoke(
            sb,
            "p",
            eval_id="e1",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="x/m",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
            extra_env={
                "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                "TZ": "America/New_York",
            },
        )
    )

    _, _cmd, _args, kw = sb.calls[0]
    assert kw["env"]["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert kw["env"]["TZ"] == "America/New_York"  # collides with guest_env's TZ=UTC; arm wins
    assert kw["env"]["HOME"] == OpenCodeAgent.guest_home  # guest_env still present
