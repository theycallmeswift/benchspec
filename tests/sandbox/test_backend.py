"""Tests for the pluggable sandbox backend."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.sandbox import backend
from harnessbench.specs.discovery import EnvConfig
from harnessbench.specs.schema import SchemaError


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


def test_fingerprint_inputs_digest_matches_cache_fingerprint() -> None:
    """`fingerprint_inputs(...).digest` equals `cache_fingerprint(...)` for the same args."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent = _agent()

    inputs = microsandbox_backend.fingerprint_inputs(agent, env)

    assert inputs.digest == microsandbox_backend.cache_fingerprint(agent, env)


def test_fingerprint_inputs_carries_structured_fields() -> None:
    """`fingerprint_inputs` exposes the raw ingredients the digest was built from."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    env = EnvConfig(base_image="ubuntu:22.04", script=b"echo hi\n", script_path="s.sh")
    agent = _agent()

    inputs = microsandbox_backend.fingerprint_inputs(agent, env)

    assert inputs.backend_id == "microsandbox"
    assert inputs.base_image_ref == "ubuntu:22.04"
    assert inputs.install_fingerprint == agent.install_fingerprint()
    assert inputs.env_script_sha256 == hashlib.sha256(b"echo hi\n").hexdigest()


def test_fingerprint_inputs_defaults_base_image_ref_when_unset() -> None:
    """With no declared base image, `base_image_ref` falls back to `backend.BASE_IMAGE`."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    inputs = microsandbox_backend.fingerprint_inputs(_agent(), EnvConfig())

    assert inputs.base_image_ref == backend.BASE_IMAGE


def test_image_identity_available_on_successful_digest_read(monkeypatch: object) -> None:
    """`image_identity` returns available with the digest when the backend read succeeds."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    async def _fake_digest(snapshot: str) -> str:
        """Fake a successful microsandbox manifest-digest read."""
        return "sha256:abc123"

    monkeypatch.setattr(microsandbox_backend, "_image_manifest_digest_async", _fake_digest)

    identity = microsandbox_backend.image_identity(
        "harnessbench-microsandbox-claude-code-latest-ab12cd34"
    )

    assert identity.image_digest == "sha256:abc123"
    assert identity.image_digest_status == "available"
    assert identity.image_digest_error is None


def test_image_identity_unavailable_with_error_when_read_raises(monkeypatch: object) -> None:
    """`image_identity` returns unavailable with a non-empty error when the read raises."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    async def _raise_digest(snapshot: str) -> str:
        """Fake a failing microsandbox manifest-digest read."""
        raise RuntimeError("snapshot not found")

    monkeypatch.setattr(microsandbox_backend, "_image_manifest_digest_async", _raise_digest)

    identity = microsandbox_backend.image_identity("missing-snapshot")

    assert identity.image_digest is None
    assert identity.image_digest_status == "unavailable"
    assert identity.image_digest_error == "snapshot not found"


def test_image_identity_unavailable_when_microsandbox_not_installed() -> None:
    """Without the microsandbox package installed, image_identity degrades, never raises.

    This environment genuinely lacks the `microsandbox` package, so this exercises the
    defensive wrapping end-to-end (no mocking) rather than relying on a faked failure.
    """
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    identity = microsandbox_backend.image_identity("any-snapshot")

    assert identity.image_digest is None
    assert identity.image_digest_status == "unavailable"
    assert identity.image_digest_error


def test_msb_binary_honors_msb_path_override(monkeypatch: object, tmp_path: object) -> None:
    """`MSB_PATH` wins over the wheel-bundled runtime, matching the SDK's resolver."""
    override = tmp_path / "custom-msb"
    monkeypatch.setenv("MSB_PATH", str(override))

    assert backend.msb_binary() == override


def test_installed_false_when_msb_path_override_missing(
    monkeypatch: object, tmp_path: object
) -> None:
    """An `MSB_PATH` pointing at nothing means the runtime is not installed."""
    monkeypatch.setenv("MSB_PATH", str(tmp_path / "missing-msb"))
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    assert microsandbox_backend.installed() is False


def test_installed_true_when_msb_path_override_exists(
    monkeypatch: object, tmp_path: object
) -> None:
    """An `MSB_PATH` pointing at a real file means the runtime is installed."""
    binary = tmp_path / "msb"
    binary.write_bytes(b"")
    monkeypatch.setenv("MSB_PATH", str(binary))
    microsandbox_backend = backend.resolve_sandbox("microsandbox")

    assert microsandbox_backend.installed() is True


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
    name = "harnessbench-microsandbox-claude-code-1.2.3-abcd1234"

    assert microsandbox_backend.snapshot_exists(name) is False


def test_microsandbox_snapshot_exists_true_when_dir_present(
    tmp_path: object, monkeypatch: object
) -> None:
    """snapshot_exists is True when ~/.microsandbox/snapshots/<name> exists on disk."""
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    name = "harnessbench-microsandbox-claude-code-1.2.3-abcd1234"
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)

    assert microsandbox_backend.snapshot_exists(name) is True


def test_microsandbox_imported_only_under_allowlist() -> None:
    """No source file outside the allowlist imports the microsandbox package.

    The backend is the primary home for the concrete runtime; the CLI wrapper and the
    three agent adapters keep their own legitimate lazy imports (exit-code split /
    microsandbox.Secret). Everything else — notably sandbox.py and execution.py — must
    drive a resolved SandboxBackend. This guards that boundary so a
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
    src = Path("src/harnessbench")
    pattern = re.compile(r"^\s*(import microsandbox|from microsandbox)", re.MULTILINE)
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"microsandbox imported outside the allowlist: {offenders}"


def test_host_mount_path_resolves_symlinked_roots(tmp_path: Path) -> None:
    """Verify a bind-mount path is handed to the runtime with symlinked ancestors resolved."""
    real_root = tmp_path / "real"
    (real_root / "room").mkdir(parents=True)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root)

    mount_path = backend.host_mount_path(linked_root / "room")

    assert mount_path == str((real_root / "room").resolve())
    assert "linked" not in mount_path
