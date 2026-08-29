"""Tests for sandbox."""

import asyncio
import json
import subprocess
from typing import NoReturn

import pytest

from harnessbench.agents.base import FIXED_SKILLS_HOME
from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.agents.codex import CodexAgent
from harnessbench.agents.opencode import OpenCodeAgent
from harnessbench.orchestration.results import RunResult
from harnessbench.sandbox import backend as backend_mod
from harnessbench.sandbox import sandbox
from harnessbench.specs.discovery import EnvConfig
from harnessbench.testing import FakeExecOutput, FakeSandbox


def _claude_agent(harness: object = None) -> object:
    """Build the claude agent test fixture (accepts and ignores an optional harness arg)."""
    return ClaudeCodeAgent(auth_value="test-token", version="v")


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
    return [_FakeEvent("stdout", data=chunk) for chunk in chunks]


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
            self._index = 0

        def __aiter__(self: object) -> object:
            """Build the aiter test fixture."""
            return self

        async def __anext__(self: object) -> object:
            """Build the anext test fixture."""
            if self._index >= len(events):
                raise StopAsyncIteration
            event = events[self._index]
            self._index += 1
            return event

        async def kill(self: object) -> None:
            """Kill."""
            pass

    class FakeTriggerSandbox:
        """Provide a fake trigger sandbox for tests."""

        async def shell(self: object, *args: object, **kwargs: object) -> object:
            """Shell."""
            return FakeExecOutput(0)

        async def exec_stream(self: object, *args: object, **kwargs: object) -> object:
            """Exec stream."""
            if capture is not None:
                capture.update(kwargs)
            return FakeHandle()

        async def stop(self: object, timeout: object = None) -> None:
            """Stop."""
            return None

    monkeypatch.setattr(sandbox, "ensure_snapshot", lambda agent, **kwargs: "snap")
    monkeypatch.setattr(sandbox, "make_agent", agent_factory)

    async def fake_create_trigger(self: object, **kwargs: object) -> object:
        """Fake create trigger (class-level: takes self)."""
        return FakeTriggerSandbox()

    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "create_trigger_sandbox", fake_create_trigger
    )
    return sandbox.route_in_sandbox(
        "query",
        tmp_path,
        model,
        20,
        effort="low",
        skill_name="archive",
    )


def test_snapshot_name_carries_backend_id() -> None:
    """The snapshot name is prefixed with the backend id."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    name = sandbox.snapshot_name(agent, EnvConfig(), backend=microsandbox_backend)
    assert name.startswith("harnessbench-microsandbox-claude-code-1.2.3-")


def test_snapshot_name_changes_when_base_image_changes() -> None:
    """A different base image changes the snapshot name."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    base = sandbox.snapshot_name(
        agent, EnvConfig(base_image="python:3.12-slim"), backend=microsandbox_backend
    )
    changed = sandbox.snapshot_name(
        agent, EnvConfig(base_image="ubuntu:22.04"), backend=microsandbox_backend
    )
    assert base != changed
    assert base.startswith("harnessbench-microsandbox-claude-code-1.2.3-")


def test_snapshot_name_changes_when_script_bytes_change() -> None:
    """Different environment script bytes change the snapshot name."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    base = sandbox.snapshot_name(
        agent, EnvConfig(script=b"echo one\n", script_path="s.sh"), backend=microsandbox_backend
    )
    changed = sandbox.snapshot_name(
        agent, EnvConfig(script=b"echo two\n", script_path="s.sh"), backend=microsandbox_backend
    )
    assert base != changed


def test_preflight_collects_backend_and_credential_failures(monkeypatch: object) -> None:
    """Preflight surfaces both backend host errors and the shared credential error."""
    monkeypatch.setattr(backend_mod.platform, "system", lambda: "Windows")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight()
    message = str(exc_info.value)
    assert "unsupported platform" in message
    assert "credential" in message


def test_preflight_dispatches_host_checks_to_backend(monkeypatch: object) -> None:
    """`sandbox.preflight()` must call `MicrosandboxBackend.preflight()`, not reimplement.

    host checks inline. Pin this by returning a sentinel from the backend method and
    asserting the sentinel (not platform/KVM/installed text) reaches the raised error —
    a regression that inlines the old Darwin/KVM/installed checks would produce
    plausible-looking message text but never touch this sentinel, so it would fail here.
    """
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "preflight", lambda self: ["SENTINEL_HOST_ERR"]
    )
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight()
    assert "SENTINEL_HOST_ERR" in str(exc_info.value)


def test_preflight_credential_error_surfaces_with_no_backend_errors(monkeypatch: object) -> None:
    """The credential check still surfaces when the backend reports a clean host.

    (dispatch AND the shared credential check are both wired, independently of each
    other).
    """
    monkeypatch.setattr(backend_mod.MicrosandboxBackend, "preflight", lambda self: [])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight()
    assert "credential" in str(exc_info.value)


def test_preflight_passes_on_supported(monkeypatch: object) -> None:
    """A supported host with a credential set passes without raising."""
    monkeypatch.setattr(backend_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(backend_mod.platform, "machine", lambda: "arm64")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(backend_mod.MicrosandboxBackend, "installed", lambda self: True)
    sandbox.preflight()  # no raise


def test_ensure_snapshot_skips_build_when_present(monkeypatch: object, tmp_path: object) -> None:
    """A present snapshot is returned without a build."""
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: True)
    built: list = []
    monkeypatch.setattr(microsandbox_backend, "build_snapshot", lambda *a, **k: built.append(a))
    agent = ClaudeCodeAgent(auth_value="test-token", version="v1")
    name = sandbox.ensure_snapshot(agent, repo_root=tmp_path, backend=microsandbox_backend)
    assert name.startswith("harnessbench-microsandbox-claude-code-v1-")
    assert built == []


def test_ensure_snapshot_builds_when_missing(monkeypatch: object, tmp_path: object) -> None:
    """A missing snapshot is built exactly once under the lock."""
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    states = iter([False, False])  # missing before lock, still missing inside
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: next(states))
    built: list = []
    monkeypatch.setattr(
        microsandbox_backend, "build_snapshot", lambda agent, name, env: built.append(name)
    )
    agent = ClaudeCodeAgent(auth_value="test-token", version="v1")
    name = sandbox.ensure_snapshot(agent, repo_root=tmp_path, backend=microsandbox_backend)
    assert built == [name]
    assert name.startswith("harnessbench-microsandbox-claude-code-v1-")


def test_ensure_snapshot_name_reflects_env_config(monkeypatch: object, tmp_path: object) -> None:
    """The env config from repo_root reaches both the snapshot name and the build."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.harnessbench]\nbase_image = "python:3.12-slim"\n', encoding="utf-8"
    )
    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: False)
    captured: dict = {}
    monkeypatch.setattr(
        microsandbox_backend,
        "build_snapshot",
        lambda agent, name, env: captured.update(name=name, image=env.base_image),
    )
    name = sandbox.ensure_snapshot(
        agent=ClaudeCodeAgent(auth_value="test-token", version="v1"),
        repo_root=tmp_path,
        backend=microsandbox_backend,
    )
    assert name.startswith("harnessbench-microsandbox-claude-code-v1-")
    assert captured["name"] == name
    assert captured["image"] == "python:3.12-slim"


def test_cli_build_resolves_environment_from_repo_root(
    monkeypatch: object, tmp_path: object
) -> None:
    """Bare cli_build reads its environment config from the given repo_root, not cwd."""
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: True
    )
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "build_snapshot", lambda self, agent, name, env: None
    )
    resolved_roots: list = []
    monkeypatch.setattr(
        sandbox,
        "resolve_environment_config",
        lambda repo_root: resolved_roots.append(repo_root) or EnvConfig(),
    )

    sandbox.cli_build(repo_root=tmp_path)

    assert resolved_roots == [tmp_path]


def test_cli_build_with_microsandbox_set_resolves_and_builds(
    monkeypatch: object, tmp_path: object
) -> None:
    """A microsandbox set drives the real cli_build path to a backend build call."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "micro"\n'
        "[tool.harnessbench.sets.micro]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        'arms = [{ name = "baseline", harness = "claude-code" }]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list = []
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="micro")

    assert len(built) == 1
    assert built[0].startswith("harnessbench-microsandbox-")


def _agent_for_harness(harness: object = None) -> object:
    """Build a per-harness test fixture agent (mirrors `make_agent`'s harness dispatch)."""
    if harness == "codex":
        return CodexAgent(auth_value="test-token", version="v")
    return ClaudeCodeAgent(auth_value="test-token", version="v")


def test_cli_build_with_set_builds_once_per_distinct_harness(
    monkeypatch: object, tmp_path: object
) -> None:
    """Two DISTINCT-harness arms build two snapshots, one per harness."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "mixed"\n'
        "[tool.harnessbench.sets.mixed]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "codex" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    make_agent_calls: list = []

    def fake_make_agent(harness: object = None) -> object:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _agent_for_harness(harness)

    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list = []
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="mixed")

    assert make_agent_calls == ["claude-code", "codex"]
    assert len(built) == 2
    assert built[0] != built[1]
    assert all(name.startswith("harnessbench-microsandbox-") for name in built)


def test_cli_build_with_set_dedupes_shared_harness(
    monkeypatch: object, tmp_path: object
) -> None:
    """Two arms sharing one harness build exactly once."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "shared"\n'
        "[tool.harnessbench.sets.shared]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "claude-code", model = "opus" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    make_agent_calls: list = []

    def fake_make_agent(harness: object = None) -> object:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _claude_agent()

    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list = []
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="shared")

    assert make_agent_calls == ["claude-code"]
    assert len(built) == 1


def test_cli_build_multi_harness_propagates_second_build_failure(
    monkeypatch: object, tmp_path: object
) -> None:
    """A later harness's build failure propagates undisturbed after an earlier success.

    A `MicrosandboxError` raised while building a later harness must propagate out of
    `cli_build` unchanged (the CLI maps it to exit 1); the multi-harness loop must not
    swallow or transform it, and must not retry or skip the first harness's built snapshot.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "mixed"\n'
        "[tool.harnessbench.sets.mixed]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "codex" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", _agent_for_harness)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list = []

    def failing_build(self: object, agent: object, name: object, env: object) -> None:
        """Succeed for the first harness, then fail as a real build failure would."""
        if built:
            raise RuntimeError("snapshot build failed")
        built.append(name)

    monkeypatch.setattr(backend_mod.MicrosandboxBackend, "build_snapshot", failing_build)

    with pytest.raises(RuntimeError, match="snapshot build failed"):
        sandbox.cli_build(repo_root=tmp_path, set_name="mixed")

    assert len(built) == 1  # the first harness's snapshot was built before the failure


def test_cli_build_reports_image_identity_available(
    monkeypatch: object, tmp_path: object
) -> None:
    """Every built/reused snapshot's image-identity status is reported."""
    from harnessbench.sandbox.provenance import ImageIdentity

    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: True
    )
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend,
        "image_identity",
        lambda self, name: ImageIdentity.available("sha256:deadbeef"),
    )

    sandbox.cli_build(repo_root=tmp_path)


def test_cli_build_reports_image_identity_unavailable(
    monkeypatch: object, tmp_path: object, capsys: object
) -> None:
    """An unavailable image-identity lookup surfaces its error, never a bare null."""
    from harnessbench.sandbox.provenance import ImageIdentity

    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: True
    )
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend,
        "image_identity",
        lambda self, name: ImageIdentity.unavailable("snapshot manifest missing"),
    )

    sandbox.cli_build(repo_root=tmp_path)

    out = capsys.readouterr().out
    assert "unavailable" in out
    assert "snapshot manifest missing" in out


def test_cli_build_bare_path_requires_no_sets_table(
    monkeypatch: object, tmp_path: object
) -> None:
    """Bare `sandbox:build` builds only the default agent with no `[tool.harnessbench.sets]`."""
    make_agent_calls: list = []

    def fake_make_agent(harness: object = None) -> object:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _claude_agent()

    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: None)
    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: True
    )

    sandbox.cli_build(repo_root=tmp_path)  # no pyproject.toml at all

    assert make_agent_calls == [None]


def test_cli_build_docker_set_raises_schema_error(monkeypatch: object, tmp_path: object) -> None:
    """A docker set fails fast at resolution, never reaching preflight or the build."""
    from harnessbench.specs.schema import SchemaError

    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "dock"\n'
        "[tool.harnessbench.sets.dock]\n"
        'model = "sonnet"\n'
        'sandbox = "docker"\n'
        'baseline = "baseline"\n'
        'arms = [{ name = "baseline", harness = "claude-code" }]\n',
        encoding="utf-8",
    )
    called_preflight: list = []
    monkeypatch.setattr(
        sandbox, "preflight", lambda backend=None: called_preflight.append(True)
    )

    with pytest.raises(SchemaError):
        sandbox.cli_build(repo_root=tmp_path, set_name="dock")

    assert called_preflight == []


def test_layer_build_config_rejects_non_table_sets(tmp_path: object) -> None:
    """A malformed scratch sets value raises a contextual schema error."""
    from harnessbench.specs.schema import SchemaError

    config = tmp_path / "config.toml"
    config.write_text('[tool.harnessbench]\nsets = ["oops"]\n', encoding="utf-8")

    with pytest.raises(SchemaError, match=r"--config.*sets.*table"):
        sandbox._layer_build_config({}, str(config))


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
        "/harnessbench-codex-auth/auth.json": {
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="archive")

    response = asyncio.run(drive())
    assert isinstance(response, RunResult)
    assert response.result_text == "ok"
    assert fake.stopped is True


def test_arm_session_propagates_create_failure(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session propagates create failure."""

    async def boom(**kwargs: object) -> NoReturn:
        """Boom."""
        raise RuntimeError("boot failed")

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", boom)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            backend=microsandbox_backend,
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

    from harnessbench.grading.trigger import detect_skill_fired

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

    def boom(*args: object, **kwargs: object) -> NoReturn:
        """Boom."""
        raise FileNotFoundError("msb")

    monkeypatch.setattr(subprocess, "run", boom)
    sandbox.cli_clean(tmp_path)  # must not raise


def test_cli_clean_runs_the_sdk_resolved_msb_binary(monkeypatch: object, tmp_path: object) -> None:
    """Pruning drives the runtime the SDK resolves, never a bare `msb` from $PATH."""
    import subprocess

    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes" / "eval-x").mkdir(parents=True)
    monkeypatch.setattr(sandbox.Path, "home", lambda: home)
    binary = tmp_path / "bundled" / "msb"
    monkeypatch.setattr(sandbox, "msb_binary", lambda: binary)
    commands = []
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: commands.append(command)
    )

    sandbox.cli_clean(tmp_path)

    assert commands == [
        [str(binary), "stop", "eval-x"],
        [str(binary), "rm", "-f", "eval-x"],
    ]


def test_cli_clean_skips_msb_when_runtime_unavailable(
    monkeypatch: object, tmp_path: object
) -> None:
    """Without a resolvable runtime there is nothing to prune through msb."""
    import subprocess

    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes" / "eval-x").mkdir(parents=True)
    monkeypatch.setattr(sandbox.Path, "home", lambda: home)
    monkeypatch.setattr(sandbox, "msb_binary", lambda: None)

    def boom(*args: object, **kwargs: object) -> NoReturn:
        """Fail loudly if msb is invoked at all."""
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", boom)

    sandbox.cli_clean(tmp_path)  # must not raise


def _opencode_agent() -> object:
    """Build the opencode agent test fixture."""
    return OpenCodeAgent(auth_value="test-token", auth_env="GEMINI_API_KEY", version="v")


def _drive_route_with_stdout_events(
    monkeypatch: object, tmp_path: object, events: object
) -> object:
    """Build the drive route with stdout events test fixture."""
    return _route_via_fake_vm(
        monkeypatch,
        tmp_path,
        events=[_FakeEvent(event_type, data=data) for event_type, data in events],
        agent_factory=_opencode_agent,
        model="google/gemini-3.5-flash",
    )


def test_route_reassembles_jsonl_split_across_stream_chunks(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify route reassembles jsonl split across stream chunks."""
    # Stream chunks can split JSONL records; routing must reassemble complete lines.
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
    return "".join(f"\x1e\x1eARTIFACT\x1e\x1e{path}\x1e\x1e\n{content}" for path, content in pairs)


def _sha_lines(*pairs: object) -> object:
    """Build the sha lines test fixture."""
    return "".join(f"{sha}  {path}\n" for path, sha in pairs)


class _QueuedShellSandbox(FakeSandbox):
    """Store queued shell sandbox data."""

    def __init__(self: object, shell_queue: object, exec_outputs: object) -> None:
        """Initialize the instance."""
        super().__init__(exec_outputs=list(exec_outputs))
        self._shell_queue = list(shell_queue)

    async def shell(self: object, script: object, **kwargs: object) -> object:
        """Shell."""
        self.calls.append(("shell", script, kwargs))
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="writing-agent-skills")

    response = asyncio.run(drive())
    assert response.artifacts == {
        "~/.claude/skills/commit-message/SKILL.md": "---\nname: commit\n---\n"
    }
    # staged skill is NOT reported — it was in the pre-turn baseline, unchanged
    assert "~/.claude/skills/writing-agent-skills/SKILL.md" not in response.artifacts


def test_snapshot_artifact_shas_returns_none_on_shell_failure() -> None:
    """Verify snapshot artifact shas returns none on shell failure."""
    # Failed snapshots return None, while genuinely empty successful snapshots return {}.

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    failing = FakeSandbox(shell_output=FakeExecOutput(exit_code=1))
    assert (
        asyncio.run(sandbox._snapshot_artifact_shas(failing, agent, microsandbox_backend))
        is None
    )

    ok_empty = FakeSandbox(shell_output=FakeExecOutput(exit_code=0, stdout_text=""))
    assert asyncio.run(sandbox._snapshot_artifact_shas(ok_empty, agent, microsandbox_backend)) == {}


def test_baseline_snapshot_failure_captures_no_artifacts(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify baseline snapshot failure captures no artifacts."""
    # Failed baseline snapshots capture no authored artifacts.
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="writing-agent-skills")

    response = asyncio.run(drive())
    assert response.artifacts == {}  # the staged tree was NOT dumped as authored




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

    (guest_env + HARNESSBENCH_*, harness derived as self.id).
    """

    id = "claude-code"

    def cell_env(self: object, *, arm: object, model: object, eval_set: object = "") -> object:
        """Cell env."""
        return {
            "HOME": "/root",
            "HARNESSBENCH_ARM": arm,
            "HARNESSBENCH_MODEL": model,
            "HARNESSBENCH_HARNESS": self.id,
            "HARNESSBENCH_SET": eval_set,
        }


def test_run_setup_sh_runs_reldir_script_and_env() -> None:
    """Verify run setup sh runs the eval-dir script with cell env."""
    fake_sandbox, agent = _SetupShellSandbox(), _SetupAgent()

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            agent,
            setup_reldir="skills/ingest/evals/single-article",
            arm="trial",
            model="opus",
        )
    )

    call = fake_sandbox.calls[-1]
    assert call["cwd"] == sandbox.PROJECT_MOUNT
    assert f"{sandbox.PROJECT_MOUNT}/skills/ingest/evals/single-article/setup.sh" in call["script"]
    assert "bash ./setup.sh" in call["script"]
    assert call["env"]["HARNESSBENCH_ARM"] == "trial"
    assert call["env"]["HARNESSBENCH_MODEL"] == "opus"


def test_run_setup_sh_passes_set_and_arm_env() -> None:
    """Verify run setup sh passes set and arm env."""
    fake_sandbox, agent = _SetupShellSandbox(), _SetupAgent()

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            agent,
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            model="opus",
            eval_set="popular-harnesses",
            arm_env={"ANTHROPIC_BASE_URL": "https://o"},
        )
    )

    env = fake_sandbox.calls[-1]["env"]
    assert env["HARNESSBENCH_SET"] == "popular-harnesses"
    assert env["ANTHROPIC_BASE_URL"] == "https://o"


def test_run_setup_sh_nonzero_exit_raises() -> None:
    """Verify run setup sh nonzero exit raises."""
    fake_sandbox, agent = _SetupShellSandbox(exit_code=2, stderr="boom"), _SetupAgent()

    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(
            sandbox.run_setup_sh(
                fake_sandbox, agent, setup_reldir="skills/ingest/evals/x", arm="trial", model="opus"
            )
        )


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


def test_run_setup_sh_absent_file_is_noop(tmp_path: object, monkeypatch: object) -> None:
    """Verify run setup sh absent file is noop."""
    # A real /bin/sh under a project mount with no <reldir>/setup.sh → clean exit 0.
    monkeypatch.setattr(sandbox, "PROJECT_MOUNT", str(tmp_path))
    (tmp_path / "evals" / "x").mkdir(parents=True)
    fake_sandbox = _LocalShellSandbox(tmp_path)

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox, _SetupAgent(), setup_reldir="evals/x", arm="trial", model="opus"
        )
    )


def test_run_setup_sh_present_but_failing_propagates(tmp_path: object, monkeypatch: object) -> None:
    """Verify run setup sh present but failing propagates."""
    monkeypatch.setattr(sandbox, "PROJECT_MOUNT", str(tmp_path))
    eval_dir = tmp_path / "evals" / "x"
    eval_dir.mkdir(parents=True)
    (eval_dir / "setup.sh").write_text("exit 2\n")
    fake_sandbox = _LocalShellSandbox(tmp_path)

    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(
            sandbox.run_setup_sh(
                fake_sandbox, _SetupAgent(), setup_reldir="evals/x", arm="trial", model="opus"
            )
        )


def test_arm_session_runs_setup_sh_when_reldir_set(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session runs setup sh when reldir set."""
    # When a session carries `setup_reldir`, __aenter__ installs the eval's setup.sh
    # BEFORE the artifact baseline. The first shell on the cell is the setup.sh run,
    # issued under cwd /project (the mount root); the script cds into the eval dir.
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    setup_call = fake.calls[0]
    assert setup_call[0] == "shell"
    assert "setup.sh" in setup_call[1]
    assert setup_call[2]["cwd"] == sandbox.PROJECT_MOUNT
    assert f"{sandbox.PROJECT_MOUNT}/skills/ingest/evals/x/setup.sh" in setup_call[1]
    assert setup_call[2]["env"]["HARNESSBENCH_ARM"] == "trial"
    assert setup_call[2]["env"]["HARNESSBENCH_MODEL"] == "opus"


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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    exec_call = next(call for call in fake.calls if call[0] == "exec")
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            harness_args=["--plugin-dir", "/project"],
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill="ingest")

    asyncio.run(drive())
    exec_call = next(call for call in fake.calls if call[0] == "exec")
    assert exec_call[2][-2:] == ["--plugin-dir", "/project"]


def test_arm_session_skips_setup_sh_when_no_skill(monkeypatch: object, tmp_path: object) -> None:
    """Verify arm session skips setup sh when setup_reldir is None."""
    # No `setup_reldir` ⇒ no per-cell install: the only shells are the artifact snapshots,
    # never a setup.sh run. (A cell whose eval dir isn't located relative to a mount.)
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            backend=microsandbox_backend,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill=None)

    asyncio.run(drive())
    assert all("setup.sh" not in call[1] for call in fake.calls if call[0] == "shell")


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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

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
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            backend=microsandbox_backend,
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(
        agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh")
    )

    shells = [call for call in fake.calls if call[0] == "shell"]
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    # provision (first shell) succeeds; the bridge (second shell) fails.
    outputs = iter([FakeExecOutput(0), FakeExecOutput(exit_code=1, stderr_text="ln failed")])

    async def shell(script: object, **kwargs: object) -> object:
        """Shell."""
        fake.calls.append(("shell", script, kwargs))
        return next(outputs)

    fake.shell = shell
    with pytest.raises(RuntimeError, match="bridge"):
        microsandbox_backend.build_snapshot(agent, "snap", EnvConfig())
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
        async def create(name: object, *, from_sandbox: object, record_integrity: object) -> None:
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
    """Verify build passes the declared base image to sandbox create and seals it."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))

    assert fake.create_image == "python:3.12-slim"
    assert fake.sealed is True


def test_build_uses_the_declared_image_the_fingerprint_hashed(monkeypatch: object) -> None:
    """Build and fingerprint reference the same declared image — harnessbench never re-resolves it.

    harnessbench hashes and pulls the declared reference as-is (no separate digest lookup), so the
    image the cache key names and the image the build pulls cannot diverge.
    """
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    env = EnvConfig(base_image="python:3.12-slim")
    microsandbox_backend.cache_fingerprint(agent, env)
    microsandbox_backend.build_snapshot(agent, "snap", env)

    assert fake.create_image == "python:3.12-slim"


def test_build_defaults_base_image_when_env_has_none(monkeypatch: object) -> None:
    """Verify build defaults base image when env has none."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig())

    assert fake.create_image == backend_mod.BASE_IMAGE


def test_build_runs_environment_script_after_provision(monkeypatch: object) -> None:
    """Verify build runs environment script after provision."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(
        agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh")
    )

    shells = [call for call in fake.calls if call[0] == "shell"]
    # provision runs provision_script() first (claude.py.provision issues exactly one
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

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(
        agent,
        "snap",
        EnvConfig(
            base_image="python:3.12-slim",
            script=b"apt-get install -y jq\n",
            script_path="s.sh",
        ),
    )
    assert fake.create_image == "python:3.12-slim"
    shells = [call for call in fake.calls if call[0] == "shell"]
    assert len(shells) == 3  # provision, bridge, environment script
    assert "apt-get install -y jq" in shells[2][1]
    assert fake.sealed is True


def test_build_no_environment_script_runs_only_provision(monkeypatch: object) -> None:
    """Verify build no environment script runs only provision."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))
    shells = [call for call in fake.calls if call[0] == "shell"]
    assert len(shells) == 2  # provision + skills-home bridge; no environment script declared


def test_build_raises_when_environment_script_fails(monkeypatch: object) -> None:
    """Verify build raises for when environment script fails."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    # provision + bridge (first two shells) succeed; the environment script (third shell)
    # fails. Queue distinct outputs so the fail lands on the script, not provision/bridge.
    outputs = iter(
        [
            FakeExecOutput(0),
            FakeExecOutput(0),
            FakeExecOutput(exit_code=2, stderr_text="boom-detail"),
        ]
    )

    async def shell(script: object, **kwargs: object) -> object:
        """Shell."""
        fake.calls.append(("shell", script, kwargs))
        return next(outputs)

    fake.shell = shell
    with pytest.raises(RuntimeError, match="boom-detail"):
        microsandbox_backend.build_snapshot(
            agent, "snap", EnvConfig(script=b"false\n", script_path="s.sh")
        )
    assert fake.sealed is False  # never sealed a failed environment
