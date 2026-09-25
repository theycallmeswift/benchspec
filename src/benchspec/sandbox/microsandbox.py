"""The microsandbox implementation of the sandbox seam: one microVM per guest.

`MicrosandboxBackend` owns the lifecycle — preflight, snapshot build and existence,
per-arm and trigger microVMs — and `MicrosandboxGuest` (with `MicrosandboxExecHandle`)
is the live guest every caller drives, translating the SDK's native `MicrosandboxError`
into the backend-neutral `SandboxError` before it leaves this module.

IMPORTANT: importing this module must NOT import the `microsandbox` package. Every
`import microsandbox` stays inside a method body so `lint`/`analyze` keep working on a
host where the package is absent. Those statements are absolute imports (the default),
so they reach the third-party `microsandbox` package, not this module — the shared name
does not shadow it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from benchspec.agents import CodingAgent
from benchspec.sandbox.backend import (
    BASE_IMAGE,
    GUEST_WORKDIR,
    NAME_PREFIX,
    PROJECT_MOUNT,
    VM_CPUS,
    VM_MEMORY_MIB,
    ExecResult,
    ExecStream,
    ExecStreamEvent,
    ExtraVolumes,
    FingerprintInputs,
    LiveSandbox,
    SharedBackendBehavior,
    bridge_skills_home,
    fingerprint_inputs_for,
    host_mount_path,
    run_environment_script,
)
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from microsandbox import SecretEntry


def msb_binary() -> Path | None:
    """Return the `msb` runtime binary the SDK will drive, or None when unavailable.

    Mirrors the SDK's own resolution: an `MSB_PATH` override wins, else the binary
    bundled inside the `microsandbox` wheel. Nothing on `$PATH` is consulted — a
    standalone `msb` install is neither required nor used.
    """
    override = os.environ.get("MSB_PATH")
    if override:
        return Path(override)
    try:
        from microsandbox._runtime import msb_path
    except ImportError:
        return None
    return msb_path()


def microsandbox_secrets(agent: CodingAgent) -> list[SecretEntry]:
    """Render an agent's backend-neutral credentials as microsandbox `Secret` entries.

    Args:
        agent: The `CodingAgent` whose `secrets()` yields `Credential`s.

    Returns:
        One `Secret.env(...)` entry per credential, scoped to its declared allow hosts.
    """
    from microsandbox import Secret

    return [
        Secret.env(
            credential.env_var, value=credential.value, allow_hosts=list(credential.allow_hosts)
        )
        for credential in agent.secrets()
    ]


@contextlib.asynccontextmanager
async def _translate_runtime_errors() -> AsyncIterator[None]:
    """Translate a native `MicrosandboxError` into the backend-neutral `SandboxError`.

    Every microsandbox call that can hit a torn-down VM (guest commands, sandbox
    creation, snapshot sealing) runs under this so callers never need to know the
    concrete runtime's exception type.
    """
    from microsandbox.errors import MicrosandboxError

    try:
        yield
    except MicrosandboxError as error:
        raise SandboxError(str(error)) from error


class MicrosandboxGuest:
    """Wrap a native microsandbox `Sandbox`, translating its errors to `SandboxError`.

    Every agent and driver in benchspec talks to a live sandbox through this wrapper
    (never the native `Sandbox` directly), so a torn-down VM surfaces the same
    backend-neutral `SandboxError` a future backend would raise. The native instance is
    held by the `LiveSandbox` shape it satisfies, not the SDK's final class, so a test can
    hand the wrapper a scripted stand-in.
    """

    def __init__(self, native: LiveSandbox) -> None:
        """Wrap a live native `Sandbox` instance."""
        self._native = native

    async def shell(
        self, script: str, *, env: Mapping[str, str] | None = None, cwd: str | None = None
    ) -> ExecResult:
        """Run a shell script in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._native.shell(script, env=env, cwd=cwd)

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> ExecResult:
        """Run a command in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._native.exec(
                cmd, args, cwd=cwd, env=env, timeout=timeout, stdin=stdin
            )

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> MicrosandboxExecHandle:
        """Start a streaming command in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            native_handle = await self._native.exec_stream(cmd, args, cwd=cwd, env=env, stdin=stdin)
        return MicrosandboxExecHandle(native_handle)

    async def stop(self) -> None:
        """Stop the guest sandbox, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            await self._native.stop()


class MicrosandboxExecHandle:
    """Wrap a native microsandbox `ExecHandle`, translating its errors to `SandboxError`."""

    def __init__(self, native_handle: ExecStream) -> None:
        """Wrap a live native streaming-exec handle."""
        self._native_handle = native_handle
        self._events = native_handle.__aiter__()

    def __aiter__(self) -> MicrosandboxExecHandle:
        """Return self as the async iterator over streamed exec events."""
        return self

    async def __anext__(self) -> ExecStreamEvent:
        """Return the next streamed exec event, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._events.__anext__()

    async def kill(self) -> None:
        """Kill the underlying streaming exec, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            await self._native_handle.kill()


class MicrosandboxBackend(SharedBackendBehavior):
    """The microsandbox implementation of `SandboxBackend`: one microVM per guest."""

    id = "microsandbox"

    def installed(self) -> bool:
        """Return whether the `msb` runtime the SDK resolves is present on disk."""
        binary = msb_binary()
        return binary is not None and binary.is_file()

    def preflight(self) -> list[str]:
        """Return host-readiness errors: Apple Silicon / KVM / installed runtime.

        The microsandbox-specific remedy lives here, not in the shared preflight, so a
        future backend supplies its own host checks and remedy.
        """
        errors: list[str] = []
        system = platform.system()
        if system == "Darwin":
            if platform.machine() != "arm64":
                errors.append("x86_64 macOS is unsupported; microsandbox needs Apple Silicon")
        elif system == "Linux":
            if not Path("/dev/kvm").exists():
                errors.append("KVM not available (/dev/kvm missing)")
        else:
            errors.append(f"unsupported platform: {system} (need Apple Silicon or Linux+KVM)")
        if not self.installed():
            errors.append(
                "microsandbox runtime not installed — run `uv sync` "
                "(or `pip install 'benchspec[microsandbox]'`)"
            )
        return errors

    def snapshot_exists(self, name: str) -> bool:
        """Return whether a named microsandbox snapshot exists on disk."""
        return (Path.home() / ".microsandbox" / "snapshots" / name).exists()

    def fingerprint_inputs(self, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return fingerprint_inputs_for(self.id, agent, env)

    def cache_fingerprint(self, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest

    def image_identity(self, snapshot: str) -> ImageIdentity:
        """Return `snapshot`'s native image manifest digest, or why it is unavailable.

        Reads the digest via the microsandbox `Snapshot` handle. Any exception —
        the snapshot missing on disk, a corrupt manifest, a client error — becomes
        an explained `unavailable` result rather than propagating: a backend lookup
        failure must never abort provenance capture or artifact aggregation.
        """
        try:
            digest = asyncio.run(self._image_manifest_digest_async(snapshot))
        except Exception as error:
            return ImageIdentity.unavailable(str(error) or "microsandbox image digest read failed")
        return ImageIdentity.available(digest)

    async def _image_manifest_digest_async(self, snapshot: str) -> str:
        """Open `snapshot` and return its native image manifest digest."""
        from microsandbox import Snapshot

        handle = await Snapshot.open(snapshot)
        return handle.image_manifest_digest

    def prune(self) -> None:
        """Remove every `benchspec-*` sandbox and snapshot under `~/.microsandbox`.

        One prefix selects leaked cells, trigger probes, and build VMs alike; anything
        another tool put under `~/.microsandbox` is left alone. A missing SDK-resolved
        runtime means nothing to prune; a runtime that resolves but is not actually on
        disk raises `FileNotFoundError` per invocation, which is tolerated the same way.
        """
        binary = msb_binary()

        def _msb(*args: str) -> None:
            """Run the SDK-resolved msb binary; a missing runtime means nothing to prune."""
            if binary is None:
                return
            try:
                subprocess.run([str(binary), *args], check=False)
            except FileNotFoundError:
                pass

        home = Path.home() / ".microsandbox"
        for sub in ("sandboxes", "snapshots"):
            target_dir = home / sub
            if not target_dir.is_dir():
                continue
            for entry in target_dir.iterdir():
                if not entry.name.startswith(NAME_PREFIX):
                    continue
                if sub == "sandboxes":
                    _msb("stop", entry.name)
                    _msb("rm", "-f", entry.name)
                else:
                    _msb("snapshot", "rm", "--force", entry.name)

    def build_snapshot(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable microsandbox snapshot."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable microsandbox snapshot asynchronously."""
        from microsandbox import Sandbox, Snapshot

        base_image = env.base_image or BASE_IMAGE
        build_name = f"{NAME_PREFIX}build-{agent.id}"
        async with _translate_runtime_errors():
            native = await Sandbox.create(
                build_name, image=base_image, cpus=VM_CPUS, memory=VM_MEMORY_MIB, replace=True
            )
        sandbox = MicrosandboxGuest(native)
        try:
            await agent.provision(sandbox)
            await bridge_skills_home(sandbox, agent)
            await run_environment_script(sandbox, agent, env)
            await sandbox.stop()  # snapshots require a stopped sandbox
            async with _translate_runtime_errors():
                await Snapshot.create(name, from_sandbox=build_name, record_integrity=True)
        finally:
            with contextlib.suppress(SandboxError, OSError):
                async with _translate_runtime_errors():
                    await Sandbox.remove(build_name)

    async def create_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_workdir: Path,
        host_repo_root: Path | None,
        extra_volumes: ExtraVolumes,
    ) -> MicrosandboxGuest:
        """Create a microsandbox instance from a snapshot for an arm session."""
        from microsandbox import Sandbox, Volume

        volumes = {GUEST_WORKDIR: Volume.bind(host_mount_path(host_workdir), readonly=False)}
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            volumes[PROJECT_MOUNT] = Volume.bind(host_mount_path(host_repo_root), readonly=True)
        volumes.update(extra_volumes(agent, Volume))
        async with _translate_runtime_errors():
            native = await Sandbox.create(
                name,
                from_snapshot=snapshot,
                volumes=volumes,
                secrets=microsandbox_secrets(agent),
                cpus=VM_CPUS,
                memory=VM_MEMORY_MIB,
                replace=True,
            )
        return MicrosandboxGuest(native)

    async def create_trigger_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_repo_root: Path,
        extra_volumes: ExtraVolumes,
    ) -> MicrosandboxGuest:
        """Create the sandbox used for trigger-routing probes."""
        from microsandbox import Sandbox, Volume

        volumes = {PROJECT_MOUNT: Volume.bind(host_mount_path(host_repo_root), readonly=True)}
        volumes.update(extra_volumes(agent, Volume))
        async with _translate_runtime_errors():
            native = await Sandbox.create(
                name,
                from_snapshot=snapshot,
                volumes=volumes,
                secrets=microsandbox_secrets(agent),
                cpus=VM_CPUS,
                memory=VM_MEMORY_MIB,
                replace=True,
            )
        sandbox = MicrosandboxGuest(native)
        try:
            await agent.stage_project_assets(sandbox, PROJECT_MOUNT)
        except BaseException:
            await self.stop_quietly(sandbox)
            raise
        return sandbox
