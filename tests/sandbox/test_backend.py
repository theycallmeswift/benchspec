"""Tests for the pluggable sandbox backend."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.sandbox import backend
from benchspec.sandbox.errors import SandboxError
from benchspec.specs.discovery import EnvConfig
from benchspec.specs.schema import SchemaError


def _agent() -> object:
    """Build a claude agent test fixture."""
    return ClaudeCodeAgent(auth_value="test-token", version="1.2.3")


def test_resolve_sandbox_returns_microsandbox_backend() -> None:
    """`microsandbox` resolves to a backend whose id is `microsandbox`."""
    resolved = backend.resolve_sandbox("microsandbox")
    assert resolved.id == "microsandbox"


def test_resolve_sandbox_returns_docker_backend() -> None:
    """`docker` resolves to a backend whose id is `docker`."""
    resolved = backend.resolve_sandbox("docker")
    assert resolved.id == "docker"


def test_resolve_sandbox_unknown_fails_listing_both_backends() -> None:
    """An unknown backend name fails fast listing every supported value."""
    with pytest.raises(SchemaError, match=r"unsupported sandbox `qemu`") as exc_info:
        backend.resolve_sandbox("qemu")

    message = str(exc_info.value)
    assert "docker" in message
    assert "microsandbox" in message


def test_default_sandbox_is_docker() -> None:
    """Docker is the default every unpinned set and the bare build inherit."""
    assert backend.DEFAULT_SANDBOX == "docker"


def test_docker_fingerprint_folds_in_its_own_backend_id(monkeypatch: object) -> None:
    """The Docker fingerprint records `docker` as the backend that produced it."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    docker_backend = backend.resolve_sandbox("docker")

    inputs = docker_backend.fingerprint_inputs(_agent(), EnvConfig())

    assert inputs.backend_id == "docker"


def test_docker_and_microsandbox_fingerprints_differ_for_one_agent_and_env(
    monkeypatch: object,
) -> None:
    """Two backends over the same agent+env never share a snapshot cache fingerprint."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")

    docker_fingerprint = backend.resolve_sandbox("docker").cache_fingerprint(_agent(), env)
    microsandbox_fingerprint = backend.resolve_sandbox("microsandbox").cache_fingerprint(
        _agent(), env
    )

    assert docker_fingerprint != microsandbox_fingerprint


def test_snapshot_names_differ_across_the_two_backends(monkeypatch: object) -> None:
    """A snapshot name is namespaced by backend, so the two never collide on disk."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    from benchspec.sandbox.sandbox import snapshot_name

    docker_name = snapshot_name(_agent(), backend=backend.resolve_sandbox("docker"))
    microsandbox_name = snapshot_name(
        _agent(), backend=backend.resolve_sandbox("microsandbox")
    )

    assert docker_name.startswith("benchspec-docker-")
    assert microsandbox_name.startswith("benchspec-microsandbox-")


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
        "benchspec-microsandbox-claude-code-latest-ab12cd34"
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
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"

    assert microsandbox_backend.snapshot_exists(name) is False


def test_microsandbox_snapshot_exists_true_when_dir_present(
    tmp_path: object, monkeypatch: object
) -> None:
    """snapshot_exists is True when ~/.microsandbox/snapshots/<name> exists on disk."""
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)

    assert microsandbox_backend.snapshot_exists(name) is True


def test_microsandbox_prune_removes_only_benchspec_sandboxes_and_snapshots(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Verify prune walks ~/.microsandbox and removes only benchspec-prefixed entries."""
    home = tmp_path
    sandboxes = home / ".microsandbox" / "sandboxes"
    snapshots = home / ".microsandbox" / "snapshots"
    (sandboxes / "benchspec-eval-hello-trial-gw0").mkdir(parents=True)
    (sandboxes / "unrelated-sandbox").mkdir()
    (snapshots / "benchspec-microsandbox-claude-code-latest-c30e39d4").mkdir(parents=True)
    (snapshots / "unrelated-snapshot").mkdir()
    monkeypatch.setattr(backend.Path, "home", lambda: home)
    shim = home / "msb"
    shim.write_text(dedent("""\
        #!/bin/sh
        printf "%s\\n" "$*" >> "$BENCHSPEC_COMMAND_LOG"
        case "$1 $2" in
          "rm -f") rm -rf "$HOME/.microsandbox/sandboxes/$3" ;;
          "snapshot rm") rm -rf "$HOME/.microsandbox/snapshots/$4" ;;
        esac
    """))
    shim.chmod(0o755)
    monkeypatch.setenv("MSB_PATH", str(shim))
    monkeypatch.setenv("HOME", str(home))
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_COMMAND_LOG", str(command_log))
    microsandbox_backend = backend.MicrosandboxBackend()

    microsandbox_backend.prune()

    assert sorted(entry.name for entry in sandboxes.iterdir()) == ["unrelated-sandbox"]
    assert sorted(entry.name for entry in snapshots.iterdir()) == ["unrelated-snapshot"]
    assert sorted(command_log.read_text(encoding="utf-8").splitlines()) == [
        "rm -f benchspec-eval-hello-trial-gw0",
        "snapshot rm --force benchspec-microsandbox-claude-code-latest-c30e39d4",
        "stop benchspec-eval-hello-trial-gw0",
    ]


def test_microsandbox_prune_tolerates_a_missing_runtime(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Verify prune is a no-op when the SDK resolves no msb runtime at all."""
    home = tmp_path
    (home / ".microsandbox" / "sandboxes" / "benchspec-eval-x").mkdir(parents=True)
    monkeypatch.setattr(backend.Path, "home", lambda: home)
    monkeypatch.setenv("MSB_PATH", str(tmp_path / "missing-msb"))
    microsandbox_backend = backend.MicrosandboxBackend()

    microsandbox_backend.prune()  # must not raise

    assert (home / ".microsandbox" / "sandboxes" / "benchspec-eval-x").is_dir()


def test_microsandbox_imported_only_under_allowlist() -> None:
    """No source file outside the allowlist imports the microsandbox package.

    `backend.py` is now the ONLY source file that imports microsandbox at all: agent
    credentials are backend-neutral `Credential`s (rendered to a microsandbox `Secret`
    only inside `microsandbox_secrets`), and every microsandbox error is translated to
    the neutral `SandboxError` before it leaves the backend. This guards that boundary
    so a future edit cannot reintroduce a scattered `import microsandbox` elsewhere.
    """
    import re
    from pathlib import Path

    allowlist = {"backend.py"}
    src = Path("src/benchspec")
    pattern = re.compile(r"^\s*(import microsandbox|from microsandbox)", re.MULTILINE)
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"microsandbox imported outside the allowlist: {offenders}"


def test_microsandbox_secrets_render_scoped_secret_entries() -> None:
    """One neutral credential renders as a microsandbox Secret scoped to its hosts."""
    agent = _agent()

    entries = backend.microsandbox_secrets(agent)

    assert [(entry.env_var, entry.value, entry.allow_hosts) for entry in entries] == [
        ("ANTHROPIC_API_KEY", "test-token", ("api.anthropic.com",))
    ]


def test_microsandbox_guest_translates_runtime_errors() -> None:
    """A MicrosandboxError from the native sandbox surfaces as the neutral SandboxError."""
    from microsandbox.errors import MicrosandboxError

    class ExplodingSandbox:
        """A native sandbox whose shell call fails the way a dead VM does."""

        async def shell(self: object, script: str, **kwargs: object) -> object:
            """Fail like a torn-down VM."""
            raise MicrosandboxError("vm gone")

    guest = backend.MicrosandboxGuest(ExplodingSandbox())

    with pytest.raises(SandboxError, match="vm gone"):
        asyncio.run(guest.shell("true"))


def test_host_mount_path_resolves_symlinked_roots(tmp_path: Path) -> None:
    """Verify a bind-mount path is handed to the runtime with symlinked ancestors resolved."""
    real_root = tmp_path / "real"
    (real_root / "room").mkdir(parents=True)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root)

    mount_path = backend.host_mount_path(linked_root / "room")

    assert mount_path == str((real_root / "room").resolve())
    assert "linked" not in mount_path
