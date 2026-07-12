"""Tests for the pluggable sandbox backend."""

from __future__ import annotations

import pytest

from evalspec import backend
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.discovery import EnvConfig
from evalspec.schema import SchemaError


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
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    assert microsandbox_backend.cache_fingerprint(
        _agent(), env
    ) == microsandbox_backend.cache_fingerprint(_agent(), env)


def test_cache_fingerprint_changes_with_base_image() -> None:
    """A different declared base-image reference changes the fingerprint."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    first = microsandbox_backend.cache_fingerprint(_agent(), EnvConfig(base_image="ubuntu:22.04"))
    second = microsandbox_backend.cache_fingerprint(_agent(), EnvConfig(base_image="ubuntu:24.04"))

    assert first != second


def test_cache_fingerprint_changes_with_install_fingerprint(monkeypatch: object) -> None:
    """A changed agent install fingerprint changes the cache fingerprint."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent_a = _agent()
    agent_b = _agent()
    monkeypatch.setattr(agent_b, "install_fingerprint", lambda: "installer-rev-9")

    assert microsandbox_backend.cache_fingerprint(
        agent_a, env
    ) != microsandbox_backend.cache_fingerprint(agent_b, env)


def test_install_fingerprint_changes_with_provision_script(monkeypatch: object) -> None:
    """Changing the resolved provision script invalidates the agent install fingerprint."""
    agent = _agent()
    original = agent.install_fingerprint()

    monkeypatch.setattr(agent, "provision_script", lambda: "install a different revision")

    assert agent.install_fingerprint() != original


def test_cache_fingerprint_changes_with_env_script_bytes() -> None:
    """Different environment script bytes yield a different fingerprint."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    one = microsandbox_backend.cache_fingerprint(
        _agent(), EnvConfig(script=b"echo one\n", script_path="s.sh")
    )
    two = microsandbox_backend.cache_fingerprint(
        _agent(), EnvConfig(script=b"echo two\n", script_path="s.sh")
    )
    assert one != two


def test_microsandbox_preflight_reports_host_errors(monkeypatch: object) -> None:
    """The backend preflight returns the platform + install errors as a list."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    monkeypatch.setattr(backend.platform, "system", lambda: "Windows")
    monkeypatch.setattr(microsandbox_backend, "installed", lambda: False)

    errors = microsandbox_backend.preflight()

    assert any("unsupported platform" in error for error in errors)
    assert any("microsandbox runtime not installed" in error for error in errors)


def test_microsandbox_preflight_clean_on_supported_host(monkeypatch: object) -> None:
    """A supported host with the runtime installed yields no preflight errors."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    monkeypatch.setattr(backend.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(backend.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(microsandbox_backend, "installed", lambda: True)

    assert microsandbox_backend.preflight() == []


def test_microsandbox_snapshot_exists_false_when_dir_absent(
    tmp_path: object, monkeypatch: object
) -> None:
    """snapshot_exists is False when ~/.microsandbox/snapshots/<name> is absent."""
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    name = "evalspec-microsandbox-claude-code-1.2.3-abcd1234"

    assert microsandbox_backend.snapshot_exists(name) is False


def test_microsandbox_snapshot_exists_true_when_dir_present(
    tmp_path: object, monkeypatch: object
) -> None:
    """snapshot_exists is True when ~/.microsandbox/snapshots/<name> exists on disk."""
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    name = "evalspec-microsandbox-claude-code-1.2.3-abcd1234"
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)

    assert microsandbox_backend.snapshot_exists(name) is True


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
