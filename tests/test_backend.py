"""Tests for the pluggable sandbox backend."""

from __future__ import annotations

import pytest

from evalspec import backend
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.discovery import EnvConfig
from evalspec.schema import SchemaError


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


def test_cache_fingerprint_stable_when_digest_stable(monkeypatch: object) -> None:
    """A pinned digest that does not move keeps the fingerprint stable."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(base_image="ubuntu:latest")
    monkeypatch.setattr(backend, "resolve_image_digest", lambda image: "sha256:aaaa")
    assert mb.cache_fingerprint(_agent(), env) == mb.cache_fingerprint(_agent(), env)


def test_cache_fingerprint_changes_with_install_fingerprint() -> None:
    """A changed agent install fingerprint changes the cache fingerprint."""
    mb = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent_a = _agent()
    agent_b = _agent()
    agent_b.install_fingerprint = lambda: "installer-rev-9"  # type: ignore[method-assign]
    assert mb.cache_fingerprint(agent_a, env) != mb.cache_fingerprint(agent_b, env)


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
