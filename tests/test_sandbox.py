"""Tests for sandbox."""

import asyncio
import json
import subprocess
from typing import NoReturn

import pytest

from evalspec import sandbox
from evalspec.agents.base import FIXED_SKILLS_HOME
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.agents.codex import CodexAgent
from evalspec.agents.opencode import OpenCodeAgent
from evalspec.discovery import EnvConfig
from evalspec.runner import RunResult
from evalspec.testing import FakeExecOutput, FakeSandbox


def _claude_agent() -> object:
    """Build the claude agent test fixture."""
    return ClaudeCodeAgent(auth_value="k", version="v")


class _FakeEvent:
    """One microsandbox `exec_stream` event: a `stdout` chunk, or a terminal.

    `exited` carrying an exit code.
    """

    def __init__(
        self: object, event_type: object, data: object = None, code: object = None
    ) -> None:
        """Initialize the instance."""
        self.event_type, self.data, self.code = event_type, data, code


def _stdout(*chunks: object) -> object:
    """`stdout` events from raw byte chunks."""
    return [_FakeEvent("stdout", data=c) for c in chunks]


def _route_via_fake_vm(
    monkeypatch: object,
    tmp_path: object,
    *,
    events: object,
    agent_factory: object,
    model: object,
    capture: object = None,
) -> object:
    """Drive `route_in_sandbox` against a fake trigger VM whose `exec_stream` yields.

    `events`. If `capture` is given, it receives the `exec_stream` kwargs (so a test can
    assert what was passed). Returns the captured `lines`.
    """

    class FakeHandle:
        """Provide a fake handle for tests."""

        def __init__(self: object) -> None:
            """Initialize the instance."""
            self._i = 0

        def __aiter__(self: object) -> object:
            """Build the aiter test fixture."""
            return self

        async def __anext__(self: object) -> object:
            """Build the anext test fixture."""
            if self._i >= len(events):
                raise StopAsyncIteration
            ev = events[self._i]
            self._i += 1
            return ev

        async def kill(self: object) -> None:
            """Kill."""
            pass

    class FakeTriggerSandbox:
        """Provide a fake trigger sandbox for tests."""

        async def shell(self: object, *a: object, **k: object) -> object:
            """Shell."""
            return FakeExecOutput(0)

        async def exec_stream(self: object, *a: object, **k: object) -> object:
            """Exec stream."""
            if capture is not None:
                capture.update(k)
            return FakeHandle()

        async def stop(self: object, timeout: object = None) -> None:
            """Stop."""
            return None

    monkeypatch.setattr(sandbox, "ensure_snapshot", lambda agent, **k: "snap")
    monkeypatch.setattr(sandbox, "make_agent", agent_factory)

    async def fake_create_trigger(**kwargs: object) -> object:
        """Fake create trigger."""
        return FakeTriggerSandbox()

    monkeypatch.setattr(sandbox, "_create_trigger_sandbox", fake_create_trigger)
    return sandbox.route_in_sandbox(
        "q",
        tmp_path,
        model,
        20,
        effort="low",
        skill_name="archive",
    )


def test_snapshot_name() -> None:
    """Verify snapshot name."""
    agent = ClaudeCodeAgent(auth_value="k", version="1.2.3")
    assert sandbox.snapshot_name(agent) == "evalspec-claude-code-1.2.3"


def test_snapshot_name_unchanged_when_env_absent() -> None:
    """Verify snapshot name unchanged when env absent."""
    agent = ClaudeCodeAgent(auth_value="k", version="1.2.3")
    # An empty EnvConfig is falsy ⇒ no suffix, identical to the no-arg form.
    assert sandbox.snapshot_name(agent, EnvConfig()) == "evalspec-claude-code-1.2.3"


def test_snapshot_name_changes_when_base_image_changes() -> None:
    """Verify snapshot name changes when base image changes."""
    agent = ClaudeCodeAgent(auth_value="k", version="1.2.3")
    a = sandbox.snapshot_name(agent, EnvConfig(base_image="python:3.12-slim"))
    b = sandbox.snapshot_name(agent, EnvConfig(base_image="ubuntu:22.04"))
    assert a != b
    assert a.startswith("evalspec-claude-code-1.2.3-")


def test_snapshot_name_changes_when_script_bytes_change() -> None:
    """Verify snapshot name changes when script bytes change."""
    agent = ClaudeCodeAgent(auth_value="k", version="1.2.3")
    a = sandbox.snapshot_name(agent, EnvConfig(script=b"echo one\n", script_path="s.sh"))
    b = sandbox.snapshot_name(agent, EnvConfig(script=b"echo two\n", script_path="s.sh"))
    assert a != b


def test_snapshot_exists_checks_microsandbox_dir(tmp_path: object, monkeypatch: object) -> None:
    """Verify snapshot exists checks microsandbox dir."""
    monkeypatch.setattr(sandbox.Path, "home", lambda: tmp_path)
    name = "evalspec-claude-code-1.2.3"
    assert sandbox.snapshot_exists(name) is False
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)
    assert sandbox.snapshot_exists(name) is True


def test_preflight_collects_all_failures(monkeypatch: object) -> None:
    """Verify preflight collects all failures."""
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Windows")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as ei:
        sandbox.preflight()
    msg = str(ei.value)
    assert "unsupported platform" in msg
    assert "credential" in msg


def test_preflight_passes_on_supported(monkeypatch: object) -> None:
    """Verify preflight passes on supported."""
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sandbox.platform, "machine", lambda: "arm64")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(sandbox, "_microsandbox_installed", lambda: True)
    sandbox.preflight()  # no raise


def test_ensure_snapshot_skips_build_when_present(monkeypatch: object, tmp_path: object) -> None:
    """Verify ensure snapshot skips build when present."""
    monkeypatch.setattr(sandbox, "snapshot_exists", lambda name: True)
    built = []
    monkeypatch.setattr(sandbox, "build_snapshot", lambda *a, **k: built.append(a))
    agent = ClaudeCodeAgent(auth_value="k", version="v1")
    name = sandbox.ensure_snapshot(agent, repo_root=tmp_path)
    assert name == "evalspec-claude-code-v1"
    assert built == []


def test_ensure_snapshot_builds_when_missing(monkeypatch: object, tmp_path: object) -> None:
    """Verify ensure snapshot builds when missing."""
    states = iter([False, False])  # missing before lock, still missing inside
    monkeypatch.setattr(sandbox, "snapshot_exists", lambda name: next(states))
    built = []
    monkeypatch.setattr(sandbox, "build_snapshot", lambda agent, name, env: built.append(name))
    agent = ClaudeCodeAgent(auth_value="k", version="v1")
    sandbox.ensure_snapshot(agent, repo_root=tmp_path)
    assert built == ["evalspec-claude-code-v1"]


def test_ensure_snapshot_name_reflects_env_config(monkeypatch: object, tmp_path: object) -> None:
    """Verify ensure snapshot name reflects env config."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\nbase_image = "python:3.12-slim"\n', encoding="utf-8"
    )
    monkeypatch.setattr(sandbox, "snapshot_exists", lambda name: False)
    captured = {}
    monkeypatch.setattr(
        sandbox,
        "build_snapshot",
        lambda agent, name, env: captured.update(name=name, image=env.base_image),
    )
    name = sandbox.ensure_snapshot(
        agent=ClaudeCodeAgent(auth_value="k", version="v1"), repo_root=tmp_path
    )
    assert name.startswith("evalspec-claude-code-v1-")  # digest suffix present
    assert captured["name"] == name
    assert captured["image"] == "python:3.12-slim"


def test_plugin_dir_for(tmp_path: object) -> None:
    """Verify plugin dir for."""
    assert sandbox._plugin_dir_for(None) is None
    assert sandbox._plugin_dir_for(tmp_path) is None  # no .claude-plugin/plugin.json
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text("{}")
    assert sandbox._plugin_dir_for(tmp_path) == sandbox.PROJECT_MOUNT


def test_agent_extra_volumes_mounts_codex_auth_json(tmp_path: object) -> None:
    """Verify agent extra volumes mounts codex auth json."""
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    agent = CodexAgent(auth_json_path=str(auth))

    class FakeVolume:
        """Provide a fake volume for tests."""

        @staticmethod
        def bind(path: object, *, readonly: object = False) -> object:
            """Bind."""
            return {"path": path, "readonly": readonly}

    volumes = sandbox._agent_extra_volumes(agent, FakeVolume)

    assert volumes == {
        "/evalspec-codex-auth/auth.json": {
            "path": str(auth),
            "readonly": True,
        },
    }


def test_arm_session_runs_turn_and_tears_down(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session runs turn and tears down."""
    fake = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ]
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        fake.create_kwargs = kwargs
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="with_skill",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="sonnet",
            effort="medium",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="archive")

    res = asyncio.run(drive())
    assert isinstance(res, RunResult)
    assert res.result_text == "ok"
    assert fake.stopped is True


def test_arm_session_propagates_create_failure(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session propagates create failure."""

    async def boom(**kwargs: object) -> NoReturn:
        """Boom."""
        raise RuntimeError("boot failed")

    monkeypatch.setattr(sandbox, "_create_sandbox", boom)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> None:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="without_skill",
            host_workdir=tmp_path / "wd",
            host_repo_root=None,
            model="sonnet",
            effort="medium",
        ) as run:
            await run("p", resume_session_id=None, detect_skill=None)

    with pytest.raises(RuntimeError, match="boot failed"):
        asyncio.run(drive())


def test_route_in_sandbox_returns_lines_on_dispatch(monkeypatch: object, tmp_path: object) -> None:
    """Verify route in sandbox returns lines on dispatch."""
    events = _stdout(
        b'{"type":"system"}\n',
        b'{"type":"assistant","message":{"content":'
        b'[{"type":"tool_use","name":"Skill","input":{"skill":"archive"}}]}}\n',
    )

    lines = _route_via_fake_vm(
        monkeypatch,
        tmp_path,
        events=events,
        agent_factory=_claude_agent,
        model="sonnet",
    )

    from evalspec.trigger import detect_skill_fired

    assert detect_skill_fired(lines, "archive") is True


def test_route_in_sandbox_raises_on_nonzero_exit(monkeypatch: object, tmp_path: object) -> None:
    """Verify route in sandbox raises for on nonzero exit."""
    events = [
        _FakeEvent(
            "stdout",
            data=b'{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        ),
        _FakeEvent("exited", code=1),
    ]

    with pytest.raises(sandbox.RoutingError):
        _route_via_fake_vm(
            monkeypatch,
            tmp_path,
            events=events,
            agent_factory=_claude_agent,
            model="sonnet",
        )


def test_route_passes_empty_stdin_to_exec_stream(monkeypatch: object, tmp_path: object) -> None:
    """Verify route passes empty stdin to exec stream."""
    events = _stdout(
        b'{"type":"system"}\n',
        b'{"type":"assistant","message":{"content":'
        b'[{"type":"tool_use","name":"Skill","input":{"skill":"archive"}}]}}\n',
    )
    captured = {}

    _route_via_fake_vm(
        monkeypatch,
        tmp_path,
        events=events,
        agent_factory=_claude_agent,
        model="sonnet",
        capture=captured,
    )

    assert captured.get("stdin") == b""


def test_cli_clean_tolerates_missing_msb(monkeypatch: object, tmp_path: object) -> None:
    """Verify cli clean tolerates missing msb."""
    import subprocess

    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes" / "eval-x").mkdir(parents=True)
    monkeypatch.setattr(sandbox.Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    def boom(*a: object, **k: object) -> NoReturn:
        """Boom."""
        raise FileNotFoundError("msb")

    monkeypatch.setattr(subprocess, "run", boom)
    sandbox.cli_clean(tmp_path)  # must not raise


def _opencode_agent() -> object:
    """Build the opencode agent test fixture."""
    return OpenCodeAgent(auth_value="k", auth_env="GEMINI_API_KEY", version="v")


def _drive_route_with_stdout_events(
    monkeypatch: object, tmp_path: object, events: object
) -> object:
    """Build the drive route with stdout events test fixture."""
    return _route_via_fake_vm(
        monkeypatch,
        tmp_path,
        events=[_FakeEvent(et, data=data) for et, data in events],
        agent_factory=_opencode_agent,
        model="google/gemini-3.5-flash",
    )


def test_route_reassembles_jsonl_split_across_stream_chunks(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify route reassembles jsonl split across stream chunks."""
    # Regression: exec_stream delivers stdout in arbitrary chunks that do NOT align to
    # newlines. OpenCode's skill `tool_use` event embeds the full skill output, so it's
    # multi-KB and always spans chunks. The router must line-buffer (reassemble complete
    # JSONL lines before detecting) — otherwise json.loads fails on every partial chunk,
    # the dispatch is invisible, early-stop never fires, and the fire tallies 0.
    skill_line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"status": "completed", "input": {"name": "archive"}},
            },
        }
    )
    mid = len(skill_line) // 2
    events = [
        ("stdout", skill_line[:mid].encode()),  # first half — not yet parseable
        ("stdout", skill_line[mid:].encode() + b"\n"),  # completes the line
    ]

    lines = _drive_route_with_stdout_events(monkeypatch, tmp_path, events)

    # The reassembled stream tallies the fire (was False when fed raw chunks).
    assert _opencode_agent().detect_fired(lines, "archive") is True


def test_route_flushes_trailing_partial_line_without_newline(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify route flushes trailing partial line without newline."""
    # The final JSONL event may arrive without a trailing newline. The drain loop must
    # flush the buffered remainder into `lines` after the stream ends, or count_fires'
    # detect_fired(lines) misses a fire that landed in the last (newline-less) line.
    skill_line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"status": "completed", "input": {"name": "archive"}},
            },
        }
    )
    mid = len(skill_line) // 2
    events = [
        ("stdout", b'{"type":"step_start","part":{"type":"step-start"}}\n'),
        ("stdout", skill_line[:mid].encode()),  # final line is itself split...
        ("stdout", skill_line[mid:].encode()),  # ...and never terminated by a newline
    ]

    lines = _drive_route_with_stdout_events(monkeypatch, tmp_path, events)

    assert _opencode_agent().detect_fired(lines, "archive") is True


def _artifact_stream(*pairs: object) -> object:
    """Build the artifact stream test fixture."""
    return "".join(f"\x1e\x1eARTIFACT\x1e\x1e{p}\x1e\x1e\n{c}" for p, c in pairs)


def _sha_lines(*pairs: object) -> object:
    """Build the sha lines test fixture."""
    return "".join(f"{sha}  {p}\n" for p, sha in pairs)


class _QueuedShellSandbox(FakeSandbox):
    """Store queued shell sandbox data."""

    def __init__(self: object, shell_queue: object, exec_outputs: object) -> None:
        """Initialize the instance."""
        super().__init__(exec_outputs=list(exec_outputs))
        self._shell_queue = list(shell_queue)

    async def shell(self: object, script: object, **kw: object) -> object:
        """Shell."""
        self.calls.append(("shell", script, kw))
        return self._shell_queue.pop(0)


def test_arm_session_captures_authored_skill_excluding_staged_baseline(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify arm session captures authored skill excluding staged baseline."""
    # The agent writes a NEW skill into ~/.claude/skills (outside the workdir mount). The
    # session must surface it on RunResult.artifacts — and DROP the staged skill that was
    # already there before turn 1 (unchanged sha in the baseline diff). The hash-first flow
    # is 3 shells: baseline shas, post-turn shas, then a content read of ONLY changed paths.
    staged = "/root/.claude/skills/writing-agent-skills/SKILL.md"
    authored = "/root/.claude/skills/commit-message/SKILL.md"
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0, _sha_lines((staged, "a" * 64))),  # baseline
            FakeExecOutput(0, _sha_lines((staged, "a" * 64), (authored, "b" * 64))),  # post-turn
            FakeExecOutput(0, _artifact_stream((authored, "---\nname: commit\n---\n"))),  # read
        ],
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="with_skill",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="sonnet",
            effort="medium",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="writing-agent-skills")

    res = asyncio.run(drive())
    assert res.artifacts == {"~/.claude/skills/commit-message/SKILL.md": "---\nname: commit\n---\n"}
    # staged skill is NOT reported — it was in the pre-turn baseline, unchanged
    assert "~/.claude/skills/writing-agent-skills/SKILL.md" not in res.artifacts


def test_snapshot_artifact_shas_returns_none_on_shell_failure() -> None:
    """Verify snapshot artifact shas returns none on shell failure."""
    # A3 root cause: a failed snapshot shell must propagate as None — distinct from {}
    # (genuinely-empty success) — so _read_authored can tell "capture nothing this arm"
    # from "nothing to capture". Collapsing both to {} makes changed_paths read an empty
    # baseline as "everything is newly authored".
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    failing = FakeSandbox(shell_output=FakeExecOutput(exit_code=1))
    assert asyncio.run(sandbox._snapshot_artifact_shas(failing, agent)) is None

    ok_empty = FakeSandbox(shell_output=FakeExecOutput(exit_code=0, stdout_text=""))
    assert asyncio.run(sandbox._snapshot_artifact_shas(ok_empty, agent)) == {}


def test_baseline_snapshot_failure_captures_no_artifacts(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify baseline snapshot failure captures no artifacts."""
    # A3: when the BASELINE artifact snapshot shell fails, the staged-skills tree must NOT
    # be surfaced as agent-authored. The failed baseline returns None, so _read_authored
    # captures nothing — never grading staged (given) skills as if the agent wrote them,
    # and never the 296KB-per-turn transfer the hash-first design exists to avoid. The
    # queued post-turn + read outputs would, if the fix regressed, dump the whole tree.
    staged = "/root/.claude/skills/writing-agent-skills/SKILL.md"
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(exit_code=1),  # baseline snapshot FAILS
            FakeExecOutput(0, _sha_lines((staged, "a" * 64))),  # post-turn (must not run)
            FakeExecOutput(0, _artifact_stream((staged, "---\nname: writing\n---\n"))),  # read (")
        ],
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="with_skill",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="sonnet",
            effort="medium",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="writing-agent-skills")

    res = asyncio.run(drive())
    assert res.artifacts == {}  # the staged tree was NOT dumped as authored




class _SetupShellSandbox:
    """Record shell calls and return one canned result.

    Tests use this to assert the cwd, environment, and script a setup.sh run used.
    """

    def __init__(self: object, exit_code: object = 0, stderr: object = "") -> None:
        """Initialize the instance."""
        self.calls = []
        self._result = FakeExecOutput(exit_code=exit_code, stderr_text=stderr)

    async def shell(self: object, script: object, env: object = None, cwd: object = None) -> object:
        """Shell."""
        self.calls.append({"script": script, "env": env, "cwd": cwd})
        return self._result


class _SetupAgent:
    """Minimal agent stub: cell_env mirrors the real claude/opencode contract.

    (guest_env + EVALSPEC_*, harness derived as self.id).
    """

    id = "claude-code"

    def cell_env(self: object, *, arm: object, model: object, eval_set: object = "") -> object:
        """Cell env."""
        return {
            "HOME": "/root",
            "EVALSPEC_ARM": arm,
            "EVALSPEC_MODEL": model,
            "EVALSPEC_HARNESS": self.id,
            "EVALSPEC_SET": eval_set,
        }


def test_run_setup_sh_uses_skill_cwd_and_env() -> None:
    """Verify run setup sh uses skill cwd and env."""
    sb, agent = _SetupShellSandbox(), _SetupAgent()
    asyncio.run(sandbox.run_setup_sh(sb, agent, skill="ingest", arm="trial", model="opus"))

    call = sb.calls[-1]
    # cwd is the mount root; the script itself `cd`s into whichever eval root holds the
    # suite (skills/ or .claude/skills/), so `.claude/skills/<name>` suites resolve too.
    assert call["cwd"] == sandbox.PROJECT_MOUNT
    assert "skills .claude/skills" in call["script"]
    assert f'{sandbox.PROJECT_MOUNT}/$root/"ingest' in call["script"]
    assert call["env"]["EVALSPEC_ARM"] == "trial"
    assert call["env"]["EVALSPEC_MODEL"] == "opus"
    assert "setup.sh" in call["script"]


def test_run_setup_sh_passes_set_and_arm_env() -> None:
    """Verify run setup sh passes set and arm env."""
    # The stub agent's cell_env stamps EVALSPEC_SET from eval_set; arm_env merges over it.
    sb, agent = _SetupShellSandbox(), _SetupAgent()
    asyncio.run(
        sandbox.run_setup_sh(
            sb,
            agent,
            skill="ingest",
            arm="trial",
            model="opus",
            eval_set="popular-harnesses",
            arm_env={"ANTHROPIC_BASE_URL": "https://o"},
        )
    )
    env = sb.calls[-1]["env"]
    assert env["EVALSPEC_SET"] == "popular-harnesses"
    assert env["ANTHROPIC_BASE_URL"] == "https://o"


def test_run_setup_sh_nonzero_exit_raises() -> None:
    """Verify run setup sh nonzero exit raises."""
    sb, agent = _SetupShellSandbox(exit_code=2, stderr="boom"), _SetupAgent()
    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(sandbox.run_setup_sh(sb, agent, skill="ingest", arm="trial", model="opus"))


class _LocalShellSandbox:
    """Runs the script string in a real /bin/sh under a temp cwd, so the shell logic.

    itself is under test: an absent ./evals/setup.sh → exit 0; a present-but-failing
    one → its own exit code. This is the contradiction the `|| true` form hid.
    """

    def __init__(self: object, cwd: object) -> None:
        """Initialize the instance."""
        self._cwd = cwd

    async def shell(self: object, script: object, env: object = None, cwd: object = None) -> object:
        """Shell."""
        proc = subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=str(self._cwd),
            capture_output=True,
            text=True,
        )
        return FakeExecOutput(exit_code=proc.returncode, stderr_text=proc.stderr)


def test_run_setup_sh_absent_file_is_noop(tmp_path: object) -> None:
    """Verify run setup sh absent file is noop."""
    # cwd has no ./evals/setup.sh → clean exit 0, no raise.
    sb = _LocalShellSandbox(tmp_path)
    asyncio.run(sandbox.run_setup_sh(sb, _SetupAgent(), skill="ingest", arm="trial", model="opus"))


def test_run_setup_sh_present_but_failing_propagates(tmp_path: object) -> None:
    """Verify run setup sh present but failing propagates."""
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "setup.sh").write_text("exit 2\n")
    sb = _LocalShellSandbox(tmp_path)
    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(
            sandbox.run_setup_sh(sb, _SetupAgent(), skill="ingest", arm="trial", model="opus")
        )


def test_arm_session_runs_setup_sh_when_skill_set(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session runs setup sh when skill set."""
    # When a session carries `skill`, __aenter__ installs the skill via setup.sh BEFORE
    # the artifact baseline. The first shell on the cell is the setup.sh run, issued under
    # cwd /project (the mount root); the script itself cds into the suite's eval root.
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0),  # setup.sh
            FakeExecOutput(0, ""),  # artifact baseline
            FakeExecOutput(0, ""),  # post-turn shas (empty → no read shell)
        ],
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="opus",
            effort="medium",
            skill="ingest",
            arm="trial",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    setup_call = fake.calls[0]
    assert setup_call[0] == "shell"
    assert "setup.sh" in setup_call[1]
    assert setup_call[2]["cwd"] == sandbox.PROJECT_MOUNT
    assert f'{sandbox.PROJECT_MOUNT}/$root/"ingest' in setup_call[1]
    assert setup_call[2]["env"]["EVALSPEC_ARM"] == "trial"
    assert setup_call[2]["env"]["EVALSPEC_MODEL"] == "opus"


def test_arm_session_does_not_implicitly_pass_plugin_dir(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify arm session does not implicitly pass plugin dir."""
    # Output evals mount the project so setup.sh can install per-arm assets, but the
    # harness command must not load the whole repo as a plugin unless the arm asks for it.
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text("{}")
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0),  # setup.sh
            FakeExecOutput(0, ""),  # artifact baseline
            FakeExecOutput(0, ""),  # post-turn shas
        ],
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="opus",
            effort="medium",
            skill="ingest",
            arm="trial",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    exec_call = next(c for c in fake.calls if c[0] == "exec")
    assert "--plugin-dir" not in exec_call[2]


def test_arm_session_passes_explicit_harness_args(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session passes explicit harness args."""
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0),  # setup.sh
            FakeExecOutput(0, ""),  # artifact baseline
            FakeExecOutput(0, ""),  # post-turn shas
        ],
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="opus",
            effort="medium",
            skill="ingest",
            arm="trial",
            harness_args=["--plugin-dir", "/project"],
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    exec_call = next(c for c in fake.calls if c[0] == "exec")
    assert exec_call[2][-2:] == ["--plugin-dir", "/project"]


def test_arm_session_skips_setup_sh_when_no_skill(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session skips setup sh when no skill."""
    # No `skill` ⇒ no per-cell install: the only shells are the artifact snapshots, never
    # a setup.sh run. (A non-skill cell — baseline of a non-skill suite, or a trigger path.)
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0, ""),
            FakeExecOutput(0, ""),
        ],  # baseline + post-turn
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> object:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="baseline",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="opus",
            effort="medium",
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill=None)

    asyncio.run(drive())
    assert all("setup.sh" not in c[1] for c in fake.calls if c[0] == "shell")


def test_arm_session_setup_sh_failure_stops_vm(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session setup sh failure stops vm."""
    # A failing setup.sh aborts the cell loudly AND tears the VM down — the caller's
    # `async with` never entered, so __aenter__ owns the teardown on this path.
    fake = _QueuedShellSandbox(
        shell_queue=[FakeExecOutput(exit_code=3, stderr_text="install blew up")],
        exec_outputs=[],
    )

    async def fake_create(**kwargs: object) -> object:
        """Fake create."""
        return fake

    monkeypatch.setattr(sandbox, "_create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="k", version="v")

    async def drive() -> None:
        """Drive."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=tmp_path,
            model="opus",
            effort="medium",
            skill="ingest",
            arm="trial",
        ) as run:
            await run("prompt", resume_session_id=None, detect_skill="ingest")

    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(drive())
    assert fake.stopped is True


def test_build_runs_skills_home_bridge_after_provision(monkeypatch: object) -> None:
    """Verify build runs skills home bridge after provision."""
    # The bridge symlinks the agent's load dir → FIXED_SKILLS_HOME, run once at provision
    # (after agent.provision, before the environment script). Three shells: provision,
    # bridge, environment script.
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh"))

    shells = [c for c in fake.calls if c[0] == "shell"]
    assert len(shells) == 3
    assert "claude.ai/install.sh" in shells[0][1]  # provision
    assert "ln -s" in shells[1][1]  # bridge
    assert FIXED_SKILLS_HOME in shells[1][1]
    assert "echo hi" in shells[2][1]  # environment script
    assert fake.sealed is True


def test_build_raises_when_skills_home_bridge_fails(monkeypatch: object) -> None:
    """Verify build raises for when skills home bridge fails."""
    # A broken bridge must fail the build, never seal a snapshot that can't load skills.
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    # provision (first shell) succeeds; the bridge (second shell) fails.
    outputs = iter([FakeExecOutput(0), FakeExecOutput(exit_code=1, stderr_text="ln failed")])

    async def shell(script: object, **kw: object) -> object:
        """Shell."""
        fake.calls.append(("shell", script, kw))
        return next(outputs)

    fake.shell = shell
    with pytest.raises(RuntimeError, match="bridge"):
        sandbox.build_snapshot(agent, "snap", EnvConfig())
    assert fake.sealed is False




def _patch_build_primitives(monkeypatch: object, fake: object) -> None:
    """Patch microsandbox Sandbox/Snapshot so _build_snapshot_async drives `fake`.

    Records the image passed to Sandbox.create on `fake.create_image` and whether a
    snapshot was sealed on `fake.sealed`.
    """
    fake.create_image = None
    fake.sealed = False

    class _FakeSandboxCls:
        """Provide a fake sandbox cls for tests."""

        @staticmethod
        async def create(
            name: object,
            *,
            image: object,
            cpus: object,
            memory: object,
            replace: object,
        ) -> object:
            """Create."""
            fake.create_image = image
            return fake

        @staticmethod
        async def remove(name: object) -> None:
            """Remove."""
            return None

    class _FakeSnapshot:
        """Provide a fake snapshot for tests."""

        @staticmethod
        async def create(build_name: object, *, name: object, record_integrity: object) -> None:
            """Create."""
            fake.sealed = True
            return None

    # _build_snapshot_async does `from microsandbox import Sandbox, Snapshot` and
    # `from microsandbox.errors import MicrosandboxError` — stub the module surface.
    import sys
    import types

    msb = types.ModuleType("microsandbox")
    msb.Sandbox = _FakeSandboxCls
    msb.Snapshot = _FakeSnapshot
    errs = types.ModuleType("microsandbox.errors")
    errs.MicrosandboxError = RuntimeError
    monkeypatch.setitem(sys.modules, "microsandbox", msb)
    monkeypatch.setitem(sys.modules, "microsandbox.errors", errs)


def test_build_passes_base_image_to_sandbox_create(monkeypatch: object) -> None:
    """Verify build passes base image to sandbox create."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))
    assert fake.create_image == "python:3.12-slim"
    assert fake.sealed is True


def test_build_defaults_base_image_when_env_has_none(monkeypatch: object) -> None:
    """Verify build defaults base image when env has none."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(agent, "snap", EnvConfig())
    assert fake.create_image == sandbox.BASE_IMAGE


def test_build_runs_environment_script_after_provision(monkeypatch: object) -> None:
    """Verify build runs environment script after provision."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh"))

    shells = [c for c in fake.calls if c[0] == "shell"]
    # provision runs PROVISION_SCRIPT first (claude.py.provision issues exactly one
    # shell); the skills-home bridge second; the environment script third.
    assert len(shells) == 3
    assert "claude.ai/install.sh" in shells[0][1]  # agent.provision
    assert "ln -s" in shells[1][1]  # skills-home bridge
    assert "echo hi" in shells[2][1]  # environment script
    assert shells[2][1].startswith("set -e\n")  # fail-loud prologue
    assert fake.sealed is True


def test_build_runs_base_image_and_environment_script_together(
    monkeypatch: object,
) -> None:
    """Verify build runs base image and environment script together."""
    # The worked-example shape: custom base image AND an extra-tools script.
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(
        agent,
        "snap",
        EnvConfig(
            base_image="python:3.12-slim",
            script=b"apt-get install -y jq\n",
            script_path="s.sh",
        ),
    )
    assert fake.create_image == "python:3.12-slim"
    shells = [c for c in fake.calls if c[0] == "shell"]
    assert len(shells) == 3  # provision, bridge, environment script
    assert "apt-get install -y jq" in shells[2][1]
    assert fake.sealed is True


def test_build_no_environment_script_runs_only_provision(monkeypatch: object) -> None:
    """Verify build no environment script runs only provision."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    sandbox.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))
    shells = [c for c in fake.calls if c[0] == "shell"]
    assert len(shells) == 2  # provision + skills-home bridge; no environment script declared


def test_build_raises_when_environment_script_fails(monkeypatch: object) -> None:
    """Verify build raises for when environment script fails."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    # provision + bridge (first two shells) succeed; the environment script (third shell)
    # fails. Queue distinct outputs so the fail lands on the script, not provision/bridge.
    outputs = iter(
        [
            FakeExecOutput(0),
            FakeExecOutput(0),
            FakeExecOutput(exit_code=2, stderr_text="boom-detail"),
        ]
    )

    async def shell(script: object, **kw: object) -> object:
        """Shell."""
        fake.calls.append(("shell", script, kw))
        return next(outputs)

    fake.shell = shell
    with pytest.raises(RuntimeError, match="boom-detail"):
        sandbox.build_snapshot(agent, "snap", EnvConfig(script=b"false\n", script_path="s.sh"))
    assert fake.sealed is False  # never sealed a failed environment
