"""Tests for sandbox."""

from __future__ import annotations

import asyncio
import subprocess
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import pytest

from benchspec.agents import CodingAgent
from benchspec.agents.base import FIXED_SKILLS_HOME
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.orchestration.results import RunResult
from benchspec.sandbox import backend as backend_mod
from benchspec.sandbox import microsandbox as microsandbox_mod
from benchspec.sandbox import registry, sandbox
from benchspec.sandbox.docker import DockerBackend, DockerMount, DockerVolume
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig
from benchspec.specs.schema import SchemaError
from benchspec.testing import FakeExecOutput, FakeSandbox


def _claude_agent(harness: str | None = None) -> ClaudeCodeAgent:
    """Build the claude agent test fixture (accepts and ignores an optional harness arg)."""
    return ClaudeCodeAgent(auth_value="test-token", version="v")


def _shell_scripts(fake: FakeSandbox) -> list[str]:
    """The script of every `shell` call recorded on `fake`, in call order."""
    scripts: list[str] = []
    for call in fake.calls:
        kind, script = call[0], call[1]
        if kind == "shell":
            assert isinstance(script, str)
            scripts.append(script)
    return scripts


def _first_exec_args(fake: FakeSandbox) -> list[str]:
    """The argument list of the first `exec` call recorded on `fake`."""
    exec_call = next(call for call in fake.calls if call[0] == "exec")
    args = exec_call[2]
    assert isinstance(args, list)
    return args


def _first_exec_timeout(fake: FakeSandbox) -> object:
    """The `timeout` keyword of the first `exec` call recorded on `fake`."""
    exec_call = next(call for call in fake.calls if call[0] == "exec")
    kwargs = exec_call[3]
    assert isinstance(kwargs, dict)
    return kwargs["timeout"]


def test_snapshot_name_carries_backend_id() -> None:
    """The snapshot name is prefixed with the backend id."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    name = sandbox.snapshot_name(agent, EnvConfig(), backend=microsandbox_backend)
    assert name.startswith("benchspec-microsandbox-claude-code-1.2.3-")


def test_snapshot_name_changes_when_base_image_changes() -> None:
    """A different base image changes the snapshot name."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    base = sandbox.snapshot_name(
        agent, EnvConfig(base_image="python:3.12-slim"), backend=microsandbox_backend
    )
    changed = sandbox.snapshot_name(
        agent, EnvConfig(base_image="ubuntu:22.04"), backend=microsandbox_backend
    )
    assert base != changed
    assert base.startswith("benchspec-microsandbox-claude-code-1.2.3-")


def test_snapshot_name_changes_when_script_bytes_change() -> None:
    """Different environment script bytes change the snapshot name."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    base = sandbox.snapshot_name(
        agent, EnvConfig(script=b"echo one\n", script_path="s.sh"), backend=microsandbox_backend
    )
    changed = sandbox.snapshot_name(
        agent, EnvConfig(script=b"echo two\n", script_path="s.sh"), backend=microsandbox_backend
    )
    assert base != changed


def test_preflight_collects_backend_and_credential_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preflight surfaces both the default backend's host errors and the credential error."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight()
    message = str(exc_info.value)
    assert "docker CLI not found" in message
    assert "credential" in message


def test_preflight_dispatches_host_checks_to_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sandbox.preflight()` must call `MicrosandboxBackend.preflight()`, not reimplement.

    host checks inline. Pin this by returning a sentinel from the backend method and
    asserting the sentinel (not platform/KVM/installed text) reaches the raised error —
    a regression that inlines the old Darwin/KVM/installed checks would produce
    plausible-looking message text but never touch this sentinel, so it would fail here.
    """
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend, "preflight", lambda self: ["SENTINEL_HOST_ERR"]
    )
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight(registry.resolve_sandbox("microsandbox"))
    assert "SENTINEL_HOST_ERR" in str(exc_info.value)


def test_preflight_credential_error_surfaces_with_no_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential check still surfaces when the backend reports a clean host.

    (dispatch AND the shared credential check are both wired, independently of each
    other).
    """
    monkeypatch.setattr(microsandbox_mod.MicrosandboxBackend, "preflight", lambda self: [])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight(registry.resolve_sandbox("microsandbox"))
    assert "credential" in str(exc_info.value)


def test_preflight_checks_every_arm_harness_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set whose arms span harnesses preflights each one, naming the harness that fails."""
    monkeypatch.setattr(microsandbox_mod.MicrosandboxBackend, "preflight", lambda self: [])
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # the selected agent is fine
    for codex_env_name in (
        "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "CODEX_AUTH_JSON_PATH"
    ):
        monkeypatch.delenv(codex_env_name, raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        sandbox.preflight(
            registry.resolve_sandbox("microsandbox"),
            harness_providers=[("claude-code", "default"), ("codex", "default")],
        )

    message = str(exc_info.value)
    assert "harness `codex`: no Codex credential" in message
    assert "claude-code" not in message


def test_preflight_reports_each_missing_credential_per_harness_provider_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One harness under two providers is two credentials, so a mixed set reports both."""
    monkeypatch.setattr(microsandbox_mod.MicrosandboxBackend, "preflight", lambda self: [])
    monkeypatch.setenv("BENCHSPEC_AGENT", "codex")
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")  # the selected agent is fine
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    errors = sandbox.preflight_errors(
        registry.resolve_sandbox("microsandbox"),
        harness_providers=[
            ("claude-code", "default"),
            ("claude-code", "openrouter"),
            ("claude-code", "default"),
        ],
    )

    assert len(errors) == 2
    assert errors[0].startswith("harness `claude-code`: ")
    assert errors[1].startswith("harness `claude-code` via provider `openrouter`: ")


def test_preflight_passes_on_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A supported host with a credential set passes without raising."""
    monkeypatch.setattr(microsandbox_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(microsandbox_mod.platform, "machine", lambda: "arm64")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(microsandbox_mod.MicrosandboxBackend, "installed", lambda self: True)
    sandbox.preflight(registry.resolve_sandbox("microsandbox"))  # no raise


def test_ensure_snapshot_skips_build_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A present snapshot is returned without a build."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: True)
    built: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        microsandbox_backend, "build_snapshot", lambda *args, **kwargs: built.append(args)
    )
    agent = ClaudeCodeAgent(auth_value="test-token", version="v1")
    name = sandbox.ensure_snapshot(agent, repo_root=tmp_path, backend=microsandbox_backend)
    assert name.startswith("benchspec-microsandbox-claude-code-v1-")
    assert built == []


def test_ensure_snapshot_builds_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing snapshot is built exactly once under the lock."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    states = iter([False, False])  # missing before lock, still missing inside
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: next(states))
    built: list[str] = []
    monkeypatch.setattr(
        microsandbox_backend, "build_snapshot", lambda agent, name, env: built.append(name)
    )
    agent = ClaudeCodeAgent(auth_value="test-token", version="v1")
    name = sandbox.ensure_snapshot(agent, repo_root=tmp_path, backend=microsandbox_backend)
    assert built == [name]
    assert name.startswith("benchspec-microsandbox-claude-code-v1-")


def test_ensure_snapshot_name_reflects_env_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The env config from repo_root reaches both the snapshot name and the build."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec]\nbase_image = "python:3.12-slim"\n', encoding="utf-8"
    )
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "snapshot_exists", lambda name: False)
    captured: dict[str, object] = {}
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
    assert name.startswith("benchspec-microsandbox-claude-code-v1-")
    assert captured["name"] == name
    assert captured["image"] == "python:3.12-slim"


def test_cli_build_resolves_environment_from_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bare cli_build reads its environment config from the given repo_root, not cwd."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(DockerBackend, "snapshot_exists", lambda self, name: True)
    monkeypatch.setattr(DockerBackend, "build_snapshot", lambda self, agent, name, env: None)
    resolved_roots: list[Path] = []
    monkeypatch.setattr(
        sandbox,
        "resolve_environment_config",
        lambda repo_root: resolved_roots.append(repo_root) or EnvConfig(),
    )

    sandbox.cli_build(repo_root=tmp_path)

    assert resolved_roots == [tmp_path]


def test_cli_build_with_microsandbox_set_resolves_and_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A microsandbox set drives the real cli_build path to a backend build call."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "micro"\n'
        "[tool.benchspec.sets.micro]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        'arms = [{ name = "baseline", harness = "claude-code" }]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list[str] = []
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="micro")

    assert len(built) == 1
    assert built[0].startswith("benchspec-microsandbox-")


def _agent_for_harness(harness: str | None = None) -> CodingAgent:
    """Build a per-harness test fixture agent (mirrors `make_agent`'s harness dispatch)."""
    if harness == "codex":
        return CodexAgent(auth_value="test-token", version="v")
    return ClaudeCodeAgent(auth_value="test-token", version="v")


def test_cli_build_with_set_builds_once_per_distinct_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two DISTINCT-harness arms build two snapshots, one per harness."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "mixed"\n'
        "[tool.benchspec.sets.mixed]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "codex" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    make_agent_calls: list[str | None] = []

    def fake_make_agent(harness: str | None = None) -> CodingAgent:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _agent_for_harness(harness)

    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list[str] = []
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="mixed")

    assert make_agent_calls == ["claude-code", "codex"]
    assert len(built) == 2
    assert built[0] != built[1]
    assert all(name.startswith("benchspec-microsandbox-") for name in built)


def test_cli_build_with_set_dedupes_shared_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two arms sharing one harness build exactly once."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "shared"\n'
        "[tool.benchspec.sets.shared]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "claude-code", model = "opus" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    make_agent_calls: list[str | None] = []

    def fake_make_agent(harness: str | None = None) -> CodingAgent:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _claude_agent()

    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list[str] = []
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend,
        "build_snapshot",
        lambda self, agent, name, env: built.append(name),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="shared")

    assert make_agent_calls == ["claude-code"]
    assert len(built) == 1


def test_cli_build_multi_harness_propagates_second_build_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A later harness's build failure propagates undisturbed after an earlier success.

    A `MicrosandboxError` raised while building a later harness must propagate out of
    `cli_build` unchanged (the CLI maps it to exit 1); the multi-harness loop must not
    swallow or transform it, and must not retry or skip the first harness's built snapshot.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "mixed"\n'
        "[tool.benchspec.sets.mixed]\n"
        'model = "sonnet"\n'
        'sandbox = "microsandbox"\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  { name = "baseline", harness = "claude-code" },\n'
        '  { name = "trial", harness = "codex" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", _agent_for_harness)
    monkeypatch.setattr(
        microsandbox_mod.MicrosandboxBackend, "snapshot_exists", lambda self, name: False
    )
    built: list[str] = []

    def failing_build(
        self: microsandbox_mod.MicrosandboxBackend, agent: CodingAgent, name: str, env: EnvConfig
    ) -> None:
        """Succeed for the first harness, then fail as a real build failure would."""
        if built:
            raise RuntimeError("snapshot build failed")
        built.append(name)

    monkeypatch.setattr(microsandbox_mod.MicrosandboxBackend, "build_snapshot", failing_build)

    with pytest.raises(RuntimeError, match="snapshot build failed"):
        sandbox.cli_build(repo_root=tmp_path, set_name="mixed")

    assert len(built) == 1  # the first harness's snapshot was built before the failure


def test_cli_build_reports_image_identity_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every built/reused snapshot's image-identity status is reported."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(DockerBackend, "snapshot_exists", lambda self, name: True)
    monkeypatch.setattr(
        DockerBackend,
        "image_identity",
        lambda self, name: ImageIdentity.available("sha256:deadbeef"),
    )

    sandbox.cli_build(repo_root=tmp_path)


def test_cli_build_reports_image_identity_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unavailable image-identity lookup surfaces its error, never a bare null."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(DockerBackend, "snapshot_exists", lambda self, name: True)
    monkeypatch.setattr(
        DockerBackend,
        "image_identity",
        lambda self, name: ImageIdentity.unavailable("snapshot manifest missing"),
    )

    sandbox.cli_build(repo_root=tmp_path)

    out = capsys.readouterr().out
    assert "unavailable" in out
    assert "snapshot manifest missing" in out


def test_cli_build_bare_path_requires_no_sets_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bare `sandbox:build` builds only the default agent with no `[tool.benchspec.sets]`."""
    make_agent_calls: list[str | None] = []

    def fake_make_agent(harness: str | None = None) -> CodingAgent:
        """Record the harness each call resolved and delegate to the test fixture."""
        make_agent_calls.append(harness)
        return _claude_agent()

    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None, **kwargs: None)
    monkeypatch.setattr(sandbox, "make_agent", fake_make_agent)
    monkeypatch.setattr(DockerBackend, "snapshot_exists", lambda self, name: True)

    sandbox.cli_build(repo_root=tmp_path)  # no pyproject.toml at all

    assert make_agent_calls == [None]


def test_cli_build_unknown_sandbox_set_raises_schema_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A set naming an unimplemented sandbox fails at resolution, before preflight or build."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "virt"\n'
        "[tool.benchspec.sets.virt]\n"
        'model = "sonnet"\n'
        'sandbox = "qemu"\n'
        'baseline = "baseline"\n'
        'arms = [{ name = "baseline", harness = "claude-code" }]\n',
        encoding="utf-8",
    )
    called_preflight: list[bool] = []
    monkeypatch.setattr(
        sandbox, "preflight", lambda backend=None, **kwargs: called_preflight.append(True)
    )

    with pytest.raises(SchemaError):
        sandbox.cli_build(repo_root=tmp_path, set_name="virt")

    assert called_preflight == []


def test_layer_build_config_rejects_non_table_sets(tmp_path: Path) -> None:
    """A malformed scratch sets value raises a contextual schema error."""
    config = tmp_path / "config.toml"
    config.write_text('[tool.benchspec]\nsets = ["oops"]\n', encoding="utf-8")

    with pytest.raises(SchemaError, match=r"--config.*sets.*table"):
        sandbox._layer_build_config({}, str(config))


def test_agent_extra_volumes_mounts_codex_auth_json(tmp_path: Path) -> None:
    """Verify agent extra volumes mounts codex auth json."""
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    agent = CodexAgent(auth_json_path=str(auth))

    volumes = sandbox._agent_extra_volumes(agent, DockerVolume)

    assert volumes == {
        "/benchspec-codex-auth/auth.json": DockerMount(host_path=str(auth), readonly=True),
    }


def test_arm_session_runs_turn_and_tears_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify arm session runs turn and tears down."""
    fake = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ]
    )

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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


def test_arm_session_hands_timeout_to_invoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session's `timeout` is the cap the agent's exec runs under."""
    fake = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ]
    )

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
            timeout=42,
        ) as run:
            return await run("prompt", resume_session_id=None, detect_skill=None)

    asyncio.run(drive())

    assert _first_exec_timeout(fake) == 42


def test_arm_session_defaults_timeout_to_agent_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A session opened without a `timeout` runs the agent under the built-in cap."""
    fake = FakeSandbox(
        exec_outputs=[
            FakeExecOutput(
                0,
                '{"type":"result","result":"ok","is_error":false,"session_id":"s","usage":{}}',
            ),
        ]
    )

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
            return await run("prompt", resume_session_id=None, detect_skill=None)

    asyncio.run(drive())

    assert _first_exec_timeout(fake) == 600


def test_arm_session_propagates_create_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify arm session propagates create failure."""

    async def boom(**kwargs: object) -> NoReturn:
        """Boom."""
        raise RuntimeError("boot failed")

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
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


class _RecordingBackend(DockerBackend):
    """A backend whose `prune` records that it ran, for `cli_clean` fan-out tests."""

    def __init__(self, calls: list[str], label: str) -> None:
        """Bind the shared call log and this instance's label."""
        super().__init__()
        self._calls = calls
        self._label = label

    def prune(self) -> None:
        """Record that this backend's prune ran."""
        self._calls.append(self._label)


def test_cli_clean_invokes_prune_on_every_registered_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify cli_clean prunes every backend `registered_backends` returns, not just one."""
    calls: list[str] = []
    monkeypatch.setattr(
        sandbox,
        "registered_backends",
        lambda: {
            "alpha": lambda: _RecordingBackend(calls, "alpha"),
            "beta": lambda: _RecordingBackend(calls, "beta"),
        },
    )

    sandbox.cli_clean(tmp_path)

    assert sorted(calls) == ["alpha", "beta"]


def test_cli_clean_removes_repo_snapshot_lock_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify cli_clean unlinks every per-repo snapshot lock file regardless of backends."""
    monkeypatch.setattr(sandbox, "registered_backends", lambda: {})
    lock_file = tmp_path / "tmp" / ".benchspec-snapshot-claude-code.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.touch()

    sandbox.cli_clean(tmp_path)

    assert not lock_file.exists()


def _artifact_stream(*pairs: tuple[str, str]) -> str:
    """Build the artifact stream test fixture."""
    return "".join(f"\x1e\x1eARTIFACT\x1e\x1e{path}\x1e\x1e\n{content}" for path, content in pairs)


def _sha_lines(*pairs: tuple[str, str]) -> str:
    """Build the sha lines test fixture."""
    return "".join(f"{sha}  {path}\n" for path, sha in pairs)


@dataclass(frozen=True)
class _ShellCall:
    """One `shell` call a scripted sandbox saw: the script plus the env and cwd it ran under."""

    script: str
    env: dict[str, str]
    cwd: str | None


class _QueuedShellSandbox(FakeSandbox):
    """A `FakeSandbox` whose `shell` replays queued outputs and keeps typed call records.

    `shell_queue` is consumed in order, one output per shell call. `shell_calls` holds what
    each call was asked to run, so a test asserts on the script, env, and cwd directly.
    """

    def __init__(
        self,
        shell_queue: list[FakeExecOutput],
        exec_outputs: list[FakeExecOutput] | None = None,
    ) -> None:
        """Queue the shell outputs to replay and the exec outputs `FakeSandbox` hands out."""
        super().__init__(exec_outputs=list(exec_outputs or []))
        self._shell_queue = list(shell_queue)
        self.shell_calls: list[_ShellCall] = []

    async def shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> FakeExecOutput:
        """Record the call and replay the next queued output."""
        self.shell_calls.append(_ShellCall(script=script, env=dict(env or {}), cwd=cwd))
        self.calls.append(("shell", script, {"env": env, "cwd": cwd}))
        return self._shell_queue.pop(0)


def test_arm_session_captures_authored_skill_excluding_staged_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    failing = FakeSandbox(shell_output=FakeExecOutput(exit_code=1))
    assert (
        asyncio.run(sandbox._snapshot_artifact_shas(failing, agent, microsandbox_backend))
        is None
    )

    ok_empty = FakeSandbox(shell_output=FakeExecOutput(exit_code=0, stdout_text=""))
    assert asyncio.run(sandbox._snapshot_artifact_shas(ok_empty, agent, microsandbox_backend)) == {}


def test_baseline_snapshot_failure_captures_no_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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


def test_run_setup_sh_runs_reldir_script_and_env() -> None:
    """Verify run setup sh runs the eval-dir script with cell env."""
    fake_sandbox = _QueuedShellSandbox(shell_queue=[FakeExecOutput(0)])

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            _claude_agent(),
            setup_reldir="skills/ingest/evals/single-article",
            arm="trial",
            model="opus",
        )
    )

    call = fake_sandbox.shell_calls[-1]
    assert call.cwd == sandbox.PROJECT_MOUNT
    assert f"{sandbox.PROJECT_MOUNT}/skills/ingest/evals/single-article/setup.sh" in call.script
    assert "bash ./setup.sh" in call.script
    assert call.env["BENCHSPEC_ARM"] == "trial"
    assert call.env["BENCHSPEC_MODEL"] == "opus"


def test_run_setup_sh_passes_set_and_arm_env() -> None:
    """Verify run setup sh passes set and arm env."""
    fake_sandbox = _QueuedShellSandbox(shell_queue=[FakeExecOutput(0)])

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            _claude_agent(),
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            model="opus",
            eval_set="popular-harnesses",
            arm_env={"ANTHROPIC_BASE_URL": "https://o"},
        )
    )

    env = fake_sandbox.shell_calls[-1].env
    assert env["BENCHSPEC_SET"] == "popular-harnesses"
    assert env["ANTHROPIC_BASE_URL"] == "https://o"


def test_run_setup_sh_passes_baseline() -> None:
    """Verify run setup sh exports the set's baseline arm."""
    fake_sandbox = _QueuedShellSandbox(shell_queue=[FakeExecOutput(0)])

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            _claude_agent(),
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            model="opus",
            baseline="baseline",
        )
    )

    assert fake_sandbox.shell_calls[-1].env["BENCHSPEC_BASELINE"] == "baseline"


def test_run_setup_sh_passes_empty_baseline_when_the_set_has_none() -> None:
    """Verify run setup sh exports an empty baseline when the set declares none."""
    fake_sandbox = _QueuedShellSandbox(shell_queue=[FakeExecOutput(0)])

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox,
            _claude_agent(),
            setup_reldir="skills/ingest/evals/x",
            arm="trial",
            model="opus",
        )
    )

    assert fake_sandbox.shell_calls[-1].env["BENCHSPEC_BASELINE"] == ""


def test_run_setup_sh_nonzero_exit_raises() -> None:
    """Verify run setup sh nonzero exit raises."""
    fake_sandbox = _QueuedShellSandbox(
        shell_queue=[FakeExecOutput(exit_code=2, stderr_text="boom")]
    )

    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(
            sandbox.run_setup_sh(
                fake_sandbox,
                _claude_agent(),
                setup_reldir="skills/ingest/evals/x",
                arm="trial",
                model="opus",
            )
        )


class _LocalShellSandbox(FakeSandbox):
    """Runs the script string in a real /bin/sh under a temp cwd, so the shell logic.

    itself is under test: an absent ./evals/setup.sh → exit 0; a present-but-failing
    one → its own exit code. This is the contradiction the `|| true` form hid.
    """

    def __init__(self, cwd: Path) -> None:
        """Pin the directory the real shell runs in."""
        super().__init__()
        self._cwd = cwd

    async def shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> FakeExecOutput:
        """Run `script` under /bin/sh and report its exit code and stderr."""
        proc = subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=str(self._cwd),
            capture_output=True,
            text=True,
        )
        return FakeExecOutput(exit_code=proc.returncode, stderr_text=proc.stderr)


def test_run_setup_sh_absent_file_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify run setup sh absent file is noop."""
    # A real /bin/sh under a project mount with no <reldir>/setup.sh → clean exit 0.
    monkeypatch.setattr(sandbox, "PROJECT_MOUNT", str(tmp_path))
    (tmp_path / "evals" / "x").mkdir(parents=True)
    fake_sandbox = _LocalShellSandbox(tmp_path)

    asyncio.run(
        sandbox.run_setup_sh(
            fake_sandbox, _claude_agent(), setup_reldir="evals/x", arm="trial", model="opus"
        )
    )


def test_run_setup_sh_present_but_failing_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify run setup sh present but failing propagates."""
    monkeypatch.setattr(sandbox, "PROJECT_MOUNT", str(tmp_path))
    eval_dir = tmp_path / "evals" / "x"
    eval_dir.mkdir(parents=True)
    (eval_dir / "setup.sh").write_text("exit 2\n")
    fake_sandbox = _LocalShellSandbox(tmp_path)

    with pytest.raises(RuntimeError, match="setup.sh"):
        asyncio.run(
            sandbox.run_setup_sh(
                fake_sandbox, _claude_agent(), setup_reldir="evals/x", arm="trial", model="opus"
            )
        )


def test_arm_session_runs_setup_sh_when_reldir_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
    assert fake.calls[0][0] == "shell"
    setup_call = fake.shell_calls[0]
    assert "setup.sh" in setup_call.script
    assert setup_call.cwd == sandbox.PROJECT_MOUNT
    assert f"{sandbox.PROJECT_MOUNT}/skills/ingest/evals/x/setup.sh" in setup_call.script
    assert setup_call.env["BENCHSPEC_ARM"] == "trial"
    assert setup_call.env["BENCHSPEC_MODEL"] == "opus"


def test_arm_session_does_not_implicitly_pass_plugin_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
    assert "--plugin-dir" not in _first_exec_args(fake)


def test_arm_session_passes_explicit_harness_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
    assert _first_exec_args(fake)[-2:] == ["--plugin-dir", "/project"]


def test_arm_session_skips_setup_sh_when_no_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> RunResult:
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
    assert all("setup.sh" not in call.script for call in fake.shell_calls)


def test_arm_session_setup_sh_failure_stops_vm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify arm session setup sh failure stops vm."""
    # A failing setup.sh aborts the cell loudly AND tears the VM down — the caller's
    # `async with` never entered, so __aenter__ owns the teardown on this path.
    fake = _QueuedShellSandbox(
        shell_queue=[FakeExecOutput(exit_code=3, stderr_text="install blew up")],
        exec_outputs=[],
    )

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Fake create."""
        return fake

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
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


@dataclass
class _BuildRecorder:
    """What the stubbed microsandbox primitives observed during one snapshot build."""

    create_image: str | None = None
    sealed: bool = False


class _FakeSandboxNotFoundError(RuntimeError):
    """Stand in for microsandbox's `SandboxNotFoundError`."""


class _StubModule(types.ModuleType):
    """A module stand-in whose public names are fixed up front."""

    def __init__(self, name: str, **members: object) -> None:
        """Create the module `name` exposing `members` as its attributes."""
        super().__init__(name)
        self.__dict__.update(members)


def _patch_build_primitives(monkeypatch: pytest.MonkeyPatch, fake: FakeSandbox) -> _BuildRecorder:
    """Patch microsandbox Sandbox/Snapshot so _build_snapshot_async drives `fake`.

    Returns a recorder holding the image passed to Sandbox.create and whether a
    snapshot was sealed.
    """
    recorder = _BuildRecorder()

    class _FakeSandboxCls:
        """Provide a fake sandbox cls for tests."""

        @staticmethod
        async def create(
            name: str,
            *,
            image: str,
            cpus: int,
            memory: int,
            replace: bool,
        ) -> FakeSandbox:
            """Record the requested image and hand back the shared fake guest."""
            recorder.create_image = image
            return fake

        @staticmethod
        async def get(name: str) -> NoReturn:
            """Report the build VM as already gone, the way a stopped-and-removed one is."""
            raise _FakeSandboxNotFoundError(name)

    class _FakeSnapshot:
        """Provide a fake snapshot for tests."""

        @staticmethod
        async def create(
            name: str, *, from_sandbox: str, group: str, record_integrity: bool
        ) -> None:
            """Mark the snapshot sealed."""
            recorder.sealed = True
            return None

    errors = _StubModule(
        "microsandbox.errors",
        MicrosandboxError=RuntimeError,
        SandboxNotFoundError=_FakeSandboxNotFoundError,
    )
    monkeypatch.setattr(
        microsandbox_mod,
        "microsandbox",
        _StubModule("microsandbox", Sandbox=_FakeSandboxCls, Snapshot=_FakeSnapshot, errors=errors),
    )
    return recorder


def test_build_runs_skills_home_bridge_after_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build runs skills home bridge after provision."""
    # The bridge symlinks the agent's load dir → FIXED_SKILLS_HOME, run once at provision
    # (after agent.provision, before the environment script). Three shells: provision,
    # bridge, environment script.
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(
        agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh")
    )

    shells = _shell_scripts(fake)
    assert len(shells) == 3
    assert "claude.ai/install.sh" in shells[0]  # provision
    assert "ln -s" in shells[1]  # bridge
    assert FIXED_SKILLS_HOME in shells[1]
    assert "echo hi" in shells[2]  # environment script
    assert build.sealed is True


def test_build_raises_when_skills_home_bridge_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build raises for when skills home bridge fails."""
    # A broken bridge must fail the build, never seal a snapshot that can't load skills.
    # provision (first shell) succeeds; the bridge (second shell) fails.
    fake = _QueuedShellSandbox(
        shell_queue=[FakeExecOutput(0), FakeExecOutput(exit_code=1, stderr_text="ln failed")]
    )
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    with pytest.raises(RuntimeError, match="bridge"):
        microsandbox_backend.build_snapshot(agent, "snap", EnvConfig())
    assert build.sealed is False


def test_build_passes_base_image_to_sandbox_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build passes the declared base image to sandbox create and seals it."""
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))

    assert build.create_image == "python:3.12-slim"
    assert build.sealed is True


def test_build_uses_the_declared_image_the_fingerprint_hashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build and fingerprint reference the same declared image — benchspec never re-resolves it.

    benchspec hashes and pulls the declared reference as-is (no separate digest lookup), so the
    image the cache key names and the image the build pulls cannot diverge.
    """
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    env = EnvConfig(base_image="python:3.12-slim")
    microsandbox_backend.cache_fingerprint(agent, env)
    microsandbox_backend.build_snapshot(agent, "snap", env)

    assert build.create_image == "python:3.12-slim"


def test_build_defaults_base_image_when_env_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build defaults base image when env has none."""
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig())

    assert build.create_image == backend_mod.BASE_IMAGE


def test_build_runs_environment_script_after_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build runs environment script after provision."""
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(
        agent, "snap", EnvConfig(script=b"echo hi\n", script_path="s.sh")
    )

    shells = _shell_scripts(fake)
    # provision runs provision_script() first (claude.py.provision issues exactly one
    # shell); the skills-home bridge second; the environment script third.
    assert len(shells) == 3
    assert "claude.ai/install.sh" in shells[0]  # agent.provision
    assert "ln -s" in shells[1]  # skills-home bridge
    assert "echo hi" in shells[2]  # environment script
    assert shells[2].startswith("set -e\n")  # fail-loud prologue
    assert build.sealed is True


def test_build_runs_base_image_and_environment_script_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify build runs base image and environment script together."""
    # The worked-example shape: custom base image AND an extra-tools script.
    fake = FakeSandbox()
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
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
    assert build.create_image == "python:3.12-slim"
    shells = _shell_scripts(fake)
    assert len(shells) == 3  # provision, bridge, environment script
    assert "apt-get install -y jq" in shells[2]
    assert build.sealed is True


def test_build_no_environment_script_runs_only_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build no environment script runs only provision."""
    fake = FakeSandbox()
    _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")
    microsandbox_backend.build_snapshot(agent, "snap", EnvConfig(base_image="python:3.12-slim"))
    shells = _shell_scripts(fake)
    assert len(shells) == 2  # provision + skills-home bridge; no environment script declared


def test_build_raises_when_environment_script_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build raises for when environment script fails."""
    # provision + bridge (first two shells) succeed; the environment script (third shell)
    # fails. Queue distinct outputs so the fail lands on the script, not provision/bridge.
    fake = _QueuedShellSandbox(
        shell_queue=[
            FakeExecOutput(0),
            FakeExecOutput(0),
            FakeExecOutput(exit_code=2, stderr_text="boom-detail"),
        ]
    )
    build = _patch_build_primitives(monkeypatch, fake)

    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    with pytest.raises(RuntimeError, match="boom-detail"):
        microsandbox_backend.build_snapshot(
            agent, "snap", EnvConfig(script=b"false\n", script_path="s.sh")
        )
    assert build.sealed is False  # never sealed a failed environment
