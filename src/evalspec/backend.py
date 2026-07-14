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
host where the package is absent (the Phase 5 lazy-import invariant).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from evalspec.agents import CodingAgent
from evalspec.provenance import ImageIdentity
from evalspec.specs.discovery import EnvConfig
from evalspec.specs.schema import SchemaError

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"
BASE_IMAGE = "ubuntu:latest"
# The only implemented backend today; the single source other modules default to.
DEFAULT_SANDBOX = "microsandbox"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


@dataclass(frozen=True)
class FingerprintInputs:
    """The raw ingredients behind a snapshot cache fingerprint, plus the digest itself.

    `snapshot_name()` and provenance capture both read fingerprint data from one
    `fingerprint_inputs()` call rather than re-hashing or parsing the snapshot-name
    suffix — this is the single source both consumers share.
    """

    backend_id: str
    base_image_ref: str
    install_fingerprint: str
    env_script_sha256: str
    digest: str


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
        """Return whether the microsandbox package can be imported and is installed."""
        try:
            import microsandbox
        except ImportError:
            return False
        return bool(microsandbox.is_installed())

    def preflight(self: object) -> list[str]:
        """Return host-readiness errors: Apple Silicon / KVM / installed runtime.

        The microsandbox-specific remedy (`make evals:build`) lives here, not in the
        shared preflight, so a future backend supplies its own host checks and remedy.
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
            errors.append("microsandbox runtime not installed — run `make evals:build`")
        return errors

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether a named microsandbox snapshot exists on disk."""
        return (Path.home() / ".microsandbox" / "snapshots" / name).exists()

    def fingerprint_inputs(self: object, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint.

        Folds in: the backend id, the DECLARED base-image reference (the tag/ref as
        configured — evalspec does not resolve it to a digest; a floating tag is therefore
        not reproducible across time/machines, and the backend records the actual pulled
        digest in Phase-8 artifacts), the agent install fingerprint (installer inputs beyond
        `version()`), and the raw environment script bytes. `snapshot_name()` and provenance
        capture both call this instead of re-hashing or parsing the snapshot name.
        """
        base_image_ref = env.base_image or BASE_IMAGE
        install_fingerprint = agent.install_fingerprint()
        env_script_sha256 = hashlib.sha256(env.script).hexdigest()
        payload = b"\0".join(
            (
                self.id.encode(),
                base_image_ref.encode(),
                install_fingerprint.encode(),
                env.script,
            )
        )
        digest = hashlib.sha256(payload).hexdigest()[:8]
        return FingerprintInputs(
            backend_id=self.id,
            base_image_ref=base_image_ref,
            install_fingerprint=install_fingerprint,
            env_script_sha256=env_script_sha256,
            digest=digest,
        )

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
        except (MicrosandboxError, asyncio.TimeoutError, OSError):
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
        build_name = f"evalspec-build-{agent.id}"
        sandbox = await Sandbox.create(
            build_name, image=base_image, cpus=VM_CPUS, memory=VM_MEMORY_MIB, replace=True
        )
        try:
            await agent.provision(sandbox)
            await self._bridge_skills_home(sandbox, agent)
            await self._run_environment_script(sandbox, agent, env)
            await sandbox.stop()  # snapshots require a stopped sandbox
            await Snapshot.create(build_name, name=name, record_integrity=True)
        finally:
            from microsandbox.errors import MicrosandboxError

            with contextlib.suppress(MicrosandboxError, OSError):
                await Sandbox.remove(build_name)

    async def _bridge_skills_home(self: object, sandbox: object, agent: object) -> None:
        """Link the agent skill directory to the fixed skills-home path."""
        result = await sandbox.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
        if result.exit_code != 0:
            raise RuntimeError(
                f"skills-home bridge failed (exit {result.exit_code}): "
                f"{result.stderr_text[-2000:]}"
            )

    async def _run_environment_script(
        self: object, sandbox: object, agent: object, env: EnvConfig
    ) -> None:
        """Run the host's environment script after provisioning, before sealing."""
        if not env.script:
            return
        script = b"set -e\n" + env.script
        result = await sandbox.shell(script.decode(), env=agent.guest_env())
        if result.exit_code != 0:
            raise RuntimeError(
                f"environment_script failed (exit {result.exit_code}): "
                f"{result.stderr_text[-2000:]}"
            )

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

        volumes = {GUEST_WORKDIR: Volume.bind(str(host_workdir), readonly=False)}
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            volumes[PROJECT_MOUNT] = Volume.bind(str(host_repo_root), readonly=True)
        volumes.update(extra_volumes(agent, Volume))
        return await Sandbox.create(
            name,
            snapshot=snapshot,
            volumes=volumes,
            secrets=agent.secrets(),
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

        volumes = {PROJECT_MOUNT: Volume.bind(str(host_repo_root), readonly=True)}
        volumes.update(extra_volumes(agent, Volume))
        sandbox = await Sandbox.create(
            name,
            snapshot=snapshot,
            volumes=volumes,
            secrets=agent.secrets(),
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
