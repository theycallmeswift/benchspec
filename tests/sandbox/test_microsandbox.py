"""Tests for the microsandbox backend: runtime resolution, preflight, guest, prune."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent
from typing import NoReturn

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.sandbox import microsandbox as microsandbox_mod
from benchspec.sandbox.errors import SandboxError
from benchspec.testing import FakeSandbox


def _agent() -> ClaudeCodeAgent:
    """Build a claude agent test fixture."""
    return ClaudeCodeAgent(auth_value="test-token", version="1.2.3")


def test_image_identity_available_on_successful_digest_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`image_identity` returns available with the digest when the backend read succeeds."""
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

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


def test_image_identity_unavailable_with_error_when_read_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`image_identity` returns unavailable with a non-empty error when the read raises."""
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

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
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    identity = microsandbox_backend.image_identity("any-snapshot")

    assert identity.image_digest is None
    assert identity.image_digest_status == "unavailable"
    assert identity.image_digest_error


def test_msb_binary_honors_msb_path_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`MSB_PATH` wins over the wheel-bundled runtime, matching the SDK's resolver."""
    override = tmp_path / "custom-msb"
    monkeypatch.setenv("MSB_PATH", str(override))

    assert microsandbox_mod.msb_binary() == override


def test_installed_false_when_msb_path_override_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `MSB_PATH` pointing at nothing means the runtime is not installed."""
    monkeypatch.setenv("MSB_PATH", str(tmp_path / "missing-msb"))
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    assert microsandbox_backend.installed() is False


def test_installed_true_when_msb_path_override_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `MSB_PATH` pointing at a real file means the runtime is installed."""
    binary = tmp_path / "msb"
    binary.write_bytes(b"")
    monkeypatch.setenv("MSB_PATH", str(binary))
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    assert microsandbox_backend.installed() is True


def test_microsandbox_preflight_reports_host_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend preflight returns the platform + install errors as a list."""
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    monkeypatch.setattr(microsandbox_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(microsandbox_backend, "installed", lambda: False)

    errors = microsandbox_backend.preflight()

    assert any("unsupported platform" in error for error in errors)
    assert any("microsandbox runtime not installed" in error for error in errors)


def test_microsandbox_preflight_clean_on_supported_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A supported host with the runtime installed yields no preflight errors."""
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    monkeypatch.setattr(microsandbox_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(microsandbox_mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(microsandbox_backend, "installed", lambda: True)

    assert microsandbox_backend.preflight() == []


def test_microsandbox_snapshot_exists_false_when_dir_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot_exists is False when ~/.microsandbox/snapshots/<name> is absent."""
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: tmp_path)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"

    assert microsandbox_backend.snapshot_exists(name) is False


def test_microsandbox_snapshot_exists_true_when_dir_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot_exists is True when ~/.microsandbox/snapshots/<name> exists on disk."""
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: tmp_path)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"
    (tmp_path / ".microsandbox" / "snapshots" / name).mkdir(parents=True)

    assert microsandbox_backend.snapshot_exists(name) is True


def test_microsandbox_prune_removes_only_benchspec_sandboxes_and_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify prune walks ~/.microsandbox and removes only benchspec-prefixed entries."""
    home = tmp_path
    sandboxes = home / ".microsandbox" / "sandboxes"
    snapshots = home / ".microsandbox" / "snapshots"
    (sandboxes / "benchspec-eval-hello-trial-gw0").mkdir(parents=True)
    (sandboxes / "unrelated-sandbox").mkdir()
    (snapshots / "benchspec-microsandbox-claude-code-latest-c30e39d4").mkdir(parents=True)
    (snapshots / "unrelated-snapshot").mkdir()
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: home)
    shim = home / "msb"
    shim.write_text(
        dedent("""\
        #!/bin/sh
        printf "%s\\n" "$*" >> "$BENCHSPEC_COMMAND_LOG"
        case "$1 $2" in
          "rm -f") rm -rf "$HOME/.microsandbox/sandboxes/$3" ;;
          "snapshot rm") rm -rf "$HOME/.microsandbox/snapshots/$4" ;;
        esac
    """)
    )
    shim.chmod(0o755)
    monkeypatch.setenv("MSB_PATH", str(shim))
    monkeypatch.setenv("HOME", str(home))
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_COMMAND_LOG", str(command_log))
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    microsandbox_backend.prune()

    assert sorted(entry.name for entry in sandboxes.iterdir()) == ["unrelated-sandbox"]
    assert sorted(entry.name for entry in snapshots.iterdir()) == ["unrelated-snapshot"]
    assert sorted(command_log.read_text(encoding="utf-8").splitlines()) == [
        "rm -f benchspec-eval-hello-trial-gw0",
        "snapshot rm --force benchspec-microsandbox-claude-code-latest-c30e39d4",
        "stop benchspec-eval-hello-trial-gw0",
    ]


def test_microsandbox_prune_tolerates_a_missing_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify prune is a no-op when the SDK resolves no msb runtime at all."""
    home = tmp_path
    (home / ".microsandbox" / "sandboxes" / "benchspec-eval-x").mkdir(parents=True)
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: home)
    monkeypatch.setenv("MSB_PATH", str(tmp_path / "missing-msb"))
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    microsandbox_backend.prune()  # must not raise

    assert (home / ".microsandbox" / "sandboxes" / "benchspec-eval-x").is_dir()


def test_microsandbox_secrets_render_scoped_secret_entries() -> None:
    """One neutral credential renders as a microsandbox Secret scoped to its hosts."""
    agent = _agent()

    entries = microsandbox_mod.microsandbox_secrets(agent)

    assert [(entry.env_var, entry.value, entry.allow_hosts) for entry in entries] == [
        ("ANTHROPIC_API_KEY", "test-token", ("api.anthropic.com",))
    ]


def test_microsandbox_guest_translates_runtime_errors() -> None:
    """A MicrosandboxError from the native sandbox surfaces as the neutral SandboxError."""
    # The package is an optional extra: only this test needs it, so the module must not.
    from microsandbox.errors import MicrosandboxError

    class ExplodingSandbox(FakeSandbox):
        """A native sandbox whose shell call fails the way a dead VM does."""

        async def shell(
            self,
            script: str,
            *,
            env: Mapping[str, str] | None = None,
            cwd: str | None = None,
        ) -> NoReturn:
            """Fail like a torn-down VM."""
            raise MicrosandboxError("vm gone")

    guest = microsandbox_mod.MicrosandboxGuest(ExplodingSandbox())

    with pytest.raises(SandboxError, match="vm gone"):
        asyncio.run(guest.shell("true"))
