"""Tests for the microsandbox backend: runtime resolution, preflight, guest, prune."""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import NoReturn

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.sandbox import microsandbox as microsandbox_mod
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.sandbox import _agent_extra_volumes
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


def test_microsandbox_snapshot_exists_true_when_group_has_a_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot_exists is True when the snapshot group `<name>` names a head snapshot."""
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: tmp_path)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"
    group = tmp_path / ".microsandbox" / "snapshots" / name
    group.mkdir(parents=True)
    (group / "group.json").write_text('{"head": "snap_abc"}', encoding="utf-8")

    assert microsandbox_backend.snapshot_exists(name) is True


def test_microsandbox_snapshot_exists_false_when_group_head_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group directory whose head snapshot was removed is not a restorable snapshot."""
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: tmp_path)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"
    group = tmp_path / ".microsandbox" / "snapshots" / name
    group.mkdir(parents=True)
    (group / "group.json").write_text('{"head": null}', encoding="utf-8")

    assert microsandbox_backend.snapshot_exists(name) is False


def test_microsandbox_snapshot_exists_false_for_an_ungrouped_pre_0_7_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-0.7 snapshot directory is unreachable by bare name, so it is not a cache hit."""
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: tmp_path)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()
    name = "benchspec-microsandbox-claude-code-1.2.3-abcd1234"
    legacy = tmp_path / ".microsandbox" / "snapshots" / name
    legacy.mkdir(parents=True)
    (legacy / "snapshot.json").write_text("{}", encoding="utf-8")

    assert microsandbox_backend.snapshot_exists(name) is False


def test_snapshot_runtime_keeps_major_minor_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a minor release changes the snapshot runtime; a patch release keeps the cache."""
    monkeypatch.setattr(microsandbox_mod.importlib.metadata, "version", lambda _: "0.7.3")

    assert microsandbox_mod.snapshot_runtime() == "microsandbox 0.7"


def test_microsandbox_prune_removes_only_benchspec_sandboxes_and_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify prune walks ~/.microsandbox and removes only benchspec-prefixed entries."""
    home = tmp_path
    sandboxes = home / ".microsandbox" / "sandboxes"
    snapshots = home / ".microsandbox" / "snapshots"
    (sandboxes / "benchspec-eval-hello-trial-gw0").mkdir(parents=True)
    (sandboxes / "unrelated-sandbox").mkdir()
    group = snapshots / "benchspec-microsandbox-claude-code-latest-ab12cd34"
    (group / "snap_abc").mkdir(parents=True)
    (group / "group.json").write_text('{"head": "snap_abc"}', encoding="utf-8")
    legacy = snapshots / "benchspec-microsandbox-claude-code-latest-c30e39d4"
    legacy.mkdir()
    (legacy / "snapshot.json").write_text("{}", encoding="utf-8")
    (snapshots / "unrelated-snapshot").mkdir()
    monkeypatch.setattr(microsandbox_mod.Path, "home", lambda: home)
    shim = home / "msb"
    shim.write_text(
        dedent("""\
        #!/bin/sh
        printf "%s\\n" "$*" >> "$BENCHSPEC_COMMAND_LOG"
        case "$1 $2" in
          "rm -f") rm -rf "$HOME/.microsandbox/sandboxes/$3" ;;
          "snapshot rm") rm -rf "$4" ;;
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
        f"snapshot rm --force {group / 'snap_abc'}",
        f"snapshot rm --force {legacy}",
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


def test_microsandbox_secrets_render_scoped_secret_specs() -> None:
    """One neutral credential renders as a microsandbox secret spec scoped to its hosts."""
    agent = _agent()

    specs = microsandbox_mod.microsandbox_secrets(agent)

    assert specs == {
        "ANTHROPIC_API_KEY": {"value": "test-token", "allowed_hosts": ["api.anthropic.com"]}
    }


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


@dataclass
class _RestoredSandbox(FakeSandbox):
    """A restored native sandbox that records its secret attachment into `calls`."""

    modify_error: Exception | None = None

    async def modify(self, *, secrets: Mapping[str, object], policy: str) -> None:
        """Record the secret attachment, failing with `modify_error` when one is set."""
        self.calls.append(("modify", secrets, policy))
        if self.modify_error is not None:
            raise self.modify_error


def _patch_restore_primitives(monkeypatch: pytest.MonkeyPatch, native: _RestoredSandbox) -> None:
    """Stub the microsandbox lifecycle so `create_sandbox` restores `native`.

    Every lifecycle step appends to `native.calls`, so one list shows their order.
    """
    from microsandbox import ModificationPolicy

    class _FakeHandle:
        """A handle onto a leftover sandbox of the requested name."""

        async def destroy(self, *, force: bool) -> None:
            """Record the destroy."""
            native.calls.append(("destroy", force))

    class _FakeSandboxCls:
        """The `Sandbox` lifecycle statics `create_sandbox` drives."""

        @staticmethod
        async def get(name: str) -> _FakeHandle:
            """Find a leftover sandbox named `name`."""
            native.calls.append(("get", name))
            return _FakeHandle()

        @staticmethod
        async def restore(
            snapshot: str, *, name: str, volumes: Mapping[str, object], cpus: int, memory: int
        ) -> _RestoredSandbox:
            """Record the restore and hand back the native sandbox."""
            native.calls.append(("restore", snapshot, name))
            return native

    class _FakeVolume:
        """A `Volume` whose binds are plain tuples."""

        @staticmethod
        def bind(path: str, *, readonly: bool) -> tuple[str, bool]:
            """Describe a bind mount."""
            return (path, readonly)

    stub = types.ModuleType("microsandbox")
    stub.__dict__.update(
        Sandbox=_FakeSandboxCls, Volume=_FakeVolume, ModificationPolicy=ModificationPolicy
    )
    monkeypatch.setenv("MSB_PATH", "/nonexistent/msb")
    monkeypatch.setitem(sys.modules, "microsandbox", stub)


def test_create_sandbox_replaces_leftover_then_attaches_scoped_secrets_with_a_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A same-named leftover goes first; the secrets attach after restore via a restart."""
    from microsandbox import ModificationPolicy

    native = _RestoredSandbox()
    _patch_restore_primitives(monkeypatch, native)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    asyncio.run(
        microsandbox_backend.create_sandbox(
            agent=_agent(),
            snapshot="benchspec-snap",
            name="benchspec-eval-hello-trial-gw0",
            host_workdir=tmp_path,
            host_repo_root=None,
            extra_volumes=_agent_extra_volumes,
        )
    )

    assert native.calls == [
        ("get", "benchspec-eval-hello-trial-gw0"),
        ("destroy", True),
        ("restore", "benchspec-snap", "benchspec-eval-hello-trial-gw0"),
        (
            "modify",
            {"ANTHROPIC_API_KEY": {"value": "test-token", "allowed_hosts": ["api.anthropic.com"]}},
            ModificationPolicy.RESTART,
        ),
    ]


def test_create_sandbox_stops_the_guest_when_secrets_fail_to_attach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed secret attachment surfaces as `SandboxError` and never leaks a running VM."""
    from microsandbox.errors import MicrosandboxError

    native = _RestoredSandbox(modify_error=MicrosandboxError("interception unavailable"))
    _patch_restore_primitives(monkeypatch, native)
    microsandbox_backend = microsandbox_mod.MicrosandboxBackend()

    with pytest.raises(SandboxError, match="interception unavailable"):
        asyncio.run(
            microsandbox_backend.create_sandbox(
                agent=_agent(),
                snapshot="benchspec-snap",
                name="benchspec-eval-hello-trial-gw0",
                host_workdir=tmp_path,
                host_repo_root=None,
                extra_volumes=_agent_extra_volumes,
            )
        )

    assert native.stopped is True
