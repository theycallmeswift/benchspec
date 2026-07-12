"""Tests for the pluggable sandbox backend."""

from __future__ import annotations

import subprocess

import pytest

from evalspec import backend
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.discovery import EnvConfig
from evalspec.schema import SchemaError

# Captured before the autouse fixture below ever runs, so this always refers to the real
# `lru_cache`-wrapped function — not whatever stub a given test's `monkeypatch.setattr`
# swaps onto the `backend.resolve_image_digest` module attribute.
_real_resolve_image_digest = backend.resolve_image_digest


@pytest.fixture(autouse=True)
def _stub_image_digest(monkeypatch: object) -> None:
    """Keep the digest resolver off the network for every backend test by default.

    Different images map to different stub digests, so name/fingerprint tests that vary
    the base image still see a change without a real registry lookup. Tests that need a
    *specific* digest value re-monkeypatch `resolve_image_digest` after this fixture runs.
    """
    monkeypatch.setattr(backend, "resolve_image_digest", lambda image: f"sha256:stub-{image}")


def _agent() -> object:
    """Build a claude agent test fixture."""
    return ClaudeCodeAgent(auth_value="test-token", version="1.2.3")


def test_resolve_sandbox_returns_microsandbox_backend() -> None:
    """`microsandbox` resolves to a backend whose id is `microsandbox`."""
    resolved = backend.resolve_sandbox("microsandbox")
    assert resolved.id == "microsandbox"


def test_resolve_sandbox_docker_fails_naming_not_implemented() -> None:
    """`docker` is a known-but-unimplemented backend: fail fast naming Docker."""
    with pytest.raises(SchemaError, match="docker.*not implemented"):
        backend.resolve_sandbox("docker")


def test_resolve_sandbox_unknown_fails() -> None:
    """An unknown backend name fails fast listing the supported values."""
    with pytest.raises(SchemaError, match="microsandbox"):
        backend.resolve_sandbox("qemu")


def test_cache_fingerprint_stable_when_nothing_changes() -> None:
    """The fingerprint is reproducible for the same backend, agent, and env."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    assert mb.cache_fingerprint(_agent(), env) == mb.cache_fingerprint(_agent(), env)


def test_cache_fingerprint_changes_with_resolved_image_digest(monkeypatch: object) -> None:
    """A moved base-image tag (new resolved digest) changes the fingerprint."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(base_image="ubuntu:latest")

    monkeypatch.setattr(backend, "resolve_image_digest", lambda image: "sha256:aaaa")
    first = mb.cache_fingerprint(_agent(), env)
    monkeypatch.setattr(backend, "resolve_image_digest", lambda image: "sha256:bbbb")
    second = mb.cache_fingerprint(_agent(), env)

    assert first != second


def test_resolve_image_digest_memoizes_per_base_image(monkeypatch: object) -> None:
    """`resolve_image_digest` shells out to skopeo at most once per distinct base image."""
    _real_resolve_image_digest.cache_clear()
    call_count = 0

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        return subprocess.CompletedProcess(args=(), returncode=0, stdout="sha256:cafe\n", stderr="")

    monkeypatch.setattr(backend.shutil, "which", lambda name: "/usr/bin/skopeo")
    monkeypatch.setattr(backend.subprocess, "run", fake_run)

    first = _real_resolve_image_digest("ubuntu:latest")
    second = _real_resolve_image_digest("ubuntu:latest")

    assert first == second == "sha256:cafe"
    assert call_count == 1


def test_cache_fingerprint_stable_when_digest_stable(monkeypatch: object) -> None:
    """A pinned digest that does not move keeps the fingerprint stable."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(base_image="ubuntu:latest")
    monkeypatch.setattr(backend, "resolve_image_digest", lambda image: "sha256:aaaa")
    assert mb.cache_fingerprint(_agent(), env) == mb.cache_fingerprint(_agent(), env)


def test_cache_fingerprint_changes_with_install_fingerprint(monkeypatch: object) -> None:
    """A changed agent install fingerprint changes the cache fingerprint."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent_a = _agent()
    agent_b = _agent()
    monkeypatch.setattr(agent_b, "install_fingerprint", lambda: "installer-rev-9")

    assert mb.cache_fingerprint(agent_a, env) != mb.cache_fingerprint(agent_b, env)


def test_install_fingerprint_changes_with_provision_script(monkeypatch: object) -> None:
    """Changing bytes consumed by provision invalidates the agent install fingerprint."""
    agent = _agent()
    original = agent.install_fingerprint()

    monkeypatch.setattr(ClaudeCodeAgent, "PROVISION_SCRIPT", "install a different revision")

    assert agent.install_fingerprint() != original


def test_cache_fingerprint_changes_with_env_script_bytes() -> None:
    """Different environment script bytes yield a different fingerprint."""
    mb = backend.resolve_sandbox("microsandbox")
    one = mb.cache_fingerprint(_agent(), EnvConfig(script=b"echo one\n", script_path="s.sh"))
    two = mb.cache_fingerprint(_agent(), EnvConfig(script=b"echo two\n", script_path="s.sh"))
    assert one != two


def test_microsandbox_preflight_reports_host_errors(monkeypatch: object) -> None:
    """The backend preflight returns the platform + install errors as a list."""
    mb = backend.resolve_sandbox("microsandbox")
    monkeypatch.setattr(backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(mb, "installed", lambda: False)

    errs = mb.preflight()

    assert any("unsupported platform" in e for e in errs)
    assert any("microsandbox runtime not installed" in e for e in errs)


def test_microsandbox_preflight_clean_on_supported_host(monkeypatch: object) -> None:
    """A supported host with the runtime installed yields no preflight errors."""
    mb = backend.resolve_sandbox("microsandbox")
    monkeypatch.setattr(backend.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(backend.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mb, "installed", lambda: True)

    assert mb.preflight() == []


def test_microsandbox_snapshot_exists_checks_dir(tmp_path: object, monkeypatch: object) -> None:
    """snapshot_exists checks ~/.microsandbox/snapshots/<name> on the backend."""
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    mb = backend.resolve_sandbox("microsandbox")
    name = "evalspec-microsandbox-claude-code-1.2.3-abcd1234"
    assert mb.snapshot_exists(name) is False
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)
    assert mb.snapshot_exists(name) is True


def test_microsandbox_imported_only_under_allowlist() -> None:
    """No source file outside the allowlist imports the microsandbox package.

    The backend is the primary home for the concrete runtime; the CLI wrapper and the
    three agent adapters keep their own legitimate lazy imports (exit-code split /
    microsandbox.Secret). Everything else — notably sandbox.py and execution.py, which
    Phase 7 cleared — must drive a resolved SandboxBackend. This guards that boundary so a
    future edit cannot reintroduce a scattered `import microsandbox` there.
    """
    import re
    from pathlib import Path

    allowlist = {
        "backend.py",
        "__main__.py",
        "claude.py",
        "codex.py",
        "opencode.py",
    }
    src = Path("src/evalspec")
    pattern = re.compile(r"^\s*(import microsandbox|from microsandbox)", re.MULTILINE)
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"microsandbox imported outside the allowlist: {offenders}"
