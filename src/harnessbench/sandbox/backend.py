"""The pluggable sandbox backend seam.

A `SandboxBackend` hides everything sandbox-runtime-specific behind one boundary:
host preflight, snapshot existence, the cache fingerprint, snapshot build, and the
per-arm / trigger session creation. `sandbox.py` and `execution.py` drive a resolved
backend and never import a concrete runtime package themselves.

microsandbox is the only implementation today. Docker is a registered-but-unimplemented
name so `sandbox = "docker"` fails fast with a readable diagnostic instead of silently
defaulting. Adding a real second backend is additive: implement the protocol, register it.

IMPORTANT: importing this module must NOT import the `microsandbox` package. Every
`import microsandbox` stays inside a method body so `lint`/`analyze` keep working on a
host where the package is absent.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
from pathlib import Path
from typing import Protocol, runtime_checkable

from harnessbench.agents import CodingAgent
from harnessbench.sandbox.primitives import (
    BASE_IMAGE,
    GUEST_WORKDIR,
    PROJECT_MOUNT,
    VM_CPUS,
    VM_MEMORY_MIB,
    FingerprintInputs,
    bridge_skills_home,
    build_fingerprint_inputs,
    host_mount_path,
    run_environment_script,
)
from harnessbench.sandbox.provenance import ImageIdentity
from harnessbench.specs.discovery import EnvConfig
from harnessbench.specs.schema import SchemaError

__all__ = [
    "BASE_IMAGE",
    "DEFAULT_SANDBOX",
    "GUEST_WORKDIR",
    "PROJECT_MOUNT",
    "VM_CPUS",
    "VM_MEMORY_MIB",
    "FingerprintInputs",
    "MicrosandboxBackend",
    "SandboxBackend",
    "host_mount_path",
    "msb_binary",
    "resolve_sandbox",
]

# The only implemented backend today; the single source other modules default to.
DEFAULT_SANDBOX = "microsandbox"


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


@runtime_checkable
class SandboxBackend(Protocol):
    """The sandbox-runtime boundary a set's `sandbox` value selects."""

    id: str  # snapshot-cache key segment + report label

    def preflight(self: object) -> list[str]:
        """Return host-readiness error strings (empty list ⇒ host is ready)."""
        ...

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether a named snapshot already exists on disk."""
        ...

    def fingerprint_inputs(self: object, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        ...

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the cache fingerprint folding backend id, image digest, install, env."""
        ...

    def image_identity(self: object, snapshot: str) -> ImageIdentity:
        """Return the snapshot's native image manifest digest, or why it is unavailable."""
        ...

    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable snapshot."""
        ...

    async def create_sandbox(self: object, **kwargs: object) -> object:
        """Create a runtime instance from a snapshot for an arm session."""
        ...

    async def create_trigger_sandbox(self: object, **kwargs: object) -> object:
        """Create the sandbox used for trigger-routing probes."""
        ...

    async def guest_shell(self: object, sandbox: object, agent: object, script: str) -> str | None:
        """Run a script in the guest, returning stdout on success or None on failure."""
        ...

    async def stop_quietly(self: object, sandbox: object) -> None:
        """Best-effort VM teardown that never masks the real flow."""
        ...

    async def kill_quietly(self: object, handle: object) -> None:
        """Best-effort kill of a streaming exec handle."""
        ...


class MicrosandboxBackend:
    """The microsandbox implementation of `SandboxBackend`."""

    id = DEFAULT_SANDBOX

    def installed(self: object) -> bool:
        """Return whether the `msb` runtime the SDK resolves is present on disk."""
        binary = msb_binary()
        return binary is not None and binary.is_file()

    def preflight(self: object) -> list[str]:
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
                "(or `pip install 'harnessbench[microsandbox]'`)"
            )
        return errors

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether a named microsandbox snapshot exists on disk."""
        return (Path.home() / ".microsandbox" / "snapshots" / name).exists()

    def fingerprint_inputs(self: object, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return build_fingerprint_inputs(backend_id=self.id, agent=agent, env=env)

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest

    def image_identity(self: object, snapshot: str) -> ImageIdentity:
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

    async def _image_manifest_digest_async(self: object, snapshot: str) -> str:
        """Open `snapshot` and return its native image manifest digest."""
        from microsandbox import Snapshot

        handle = await Snapshot.open(snapshot)
        return handle.image_manifest_digest

    async def guest_shell(self: object, sandbox: object, agent: object, script: str) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        from microsandbox.errors import MicrosandboxError

        try:
            result = await sandbox.shell(script, env=agent.guest_env())
        except (TimeoutError, MicrosandboxError, OSError):
            return None
        return result.stdout_text if result.exit_code == 0 else None

    async def stop_quietly(self: object, sandbox: object) -> None:
        """Best-effort VM teardown that never masks the real flow."""
        from microsandbox.errors import MicrosandboxError

        with contextlib.suppress(MicrosandboxError, asyncio.TimeoutError, OSError):
            await sandbox.stop()

    async def kill_quietly(self: object, handle: object) -> None:
        """Best-effort kill of a streaming exec handle (routing timeout path)."""
        from microsandbox.errors import MicrosandboxError

        with contextlib.suppress(MicrosandboxError, OSError):
            await handle.kill()

    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable microsandbox snapshot."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable microsandbox snapshot asynchronously."""
        from microsandbox import Sandbox, Snapshot

        base_image = env.base_image or BASE_IMAGE
        build_name = f"harnessbench-build-{agent.id}"
        sandbox = await Sandbox.create(
            build_name, image=base_image, cpus=VM_CPUS, memory=VM_MEMORY_MIB, replace=True
        )
        try:
            await agent.provision(sandbox)
            await bridge_skills_home(sandbox, agent)
            await run_environment_script(sandbox, agent, env)
            await sandbox.stop()  # snapshots require a stopped sandbox
            await Snapshot.create(name, from_sandbox=build_name, record_integrity=True)
        finally:
            from microsandbox.errors import MicrosandboxError

            with contextlib.suppress(MicrosandboxError, OSError):
                await Sandbox.remove(build_name)

    def _runtime_secrets(self: object, agent: object) -> list:
        """Map the agent's backend-neutral credentials onto microsandbox scoped secrets."""
        from microsandbox import Secret

        return [
            Secret.env(
                credential.env_name,
                value=credential.value,
                allow_hosts=list(credential.allow_hosts),
            )
            for credential in agent.secrets()
        ]

    async def create_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_workdir: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> object:
        """Create a microsandbox instance from a snapshot for an arm session."""
        from microsandbox import Sandbox, Volume

        volumes = {GUEST_WORKDIR: Volume.bind(host_mount_path(host_workdir), readonly=False)}
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            volumes[PROJECT_MOUNT] = Volume.bind(host_mount_path(host_repo_root), readonly=True)
        volumes.update(extra_volumes(agent, Volume))
        return await Sandbox.create(
            name,
            from_snapshot=snapshot,
            volumes=volumes,
            secrets=self._runtime_secrets(agent),
            cpus=VM_CPUS,
            memory=VM_MEMORY_MIB,
            replace=True,
        )

    async def create_trigger_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> object:
        """Create the sandbox used for trigger-routing probes."""
        from microsandbox import Sandbox, Volume

        volumes = {PROJECT_MOUNT: Volume.bind(host_mount_path(host_repo_root), readonly=True)}
        volumes.update(extra_volumes(agent, Volume))
        sandbox = await Sandbox.create(
            name,
            from_snapshot=snapshot,
            volumes=volumes,
            secrets=self._runtime_secrets(agent),
            cpus=VM_CPUS,
            memory=VM_MEMORY_MIB,
            replace=True,
        )
        try:
            await agent.stage_project_assets(sandbox, PROJECT_MOUNT)
        except BaseException:
            await self.stop_quietly(sandbox)
            raise
        return sandbox


_REGISTRY: dict[str, type] = {DEFAULT_SANDBOX: MicrosandboxBackend}

# Known-but-unimplemented backends: named so the fail-fast message can be specific.
_NOT_IMPLEMENTED = {"docker": "Docker is not implemented"}


def resolve_sandbox(name: str) -> SandboxBackend:
    """Return the backend for a set's `sandbox` value, or fail fast.

    Returns a FRESH backend instance each call. An implemented name returns its backend.
    A known-but-unimplemented name (`docker`) raises naming it as not implemented. Any
    other name raises listing the supported values. The raised `SchemaError` maps to
    exit 2 through the CLI/plugin.
    """
    if name in _REGISTRY:
        return _REGISTRY[name]()
    supported = sorted(_REGISTRY)
    if name in _NOT_IMPLEMENTED:
        raise SchemaError(
            f"unsupported sandbox `{name}` (supported: {supported}). "
            f"{_NOT_IMPLEMENTED[name]}."
        )
    raise SchemaError(f"unsupported sandbox `{name}` (supported: {supported})")
