"""The pluggable sandbox backend seam.

A `SandboxBackend` hides everything sandbox-runtime-specific behind one boundary:
host preflight, snapshot existence, the cache fingerprint, snapshot build, and the
per-arm / trigger session creation. `sandbox.py` and `execution.py` drive a resolved
backend and never import a concrete runtime package themselves. `SandboxError` (defined
in `benchspec.sandbox.errors` and re-exported here) is the one runtime-failure type
every backend speaks, so a torn-down guest is classified without naming a runtime.

Two backends are implemented: Docker (the default — a container per arm, driven through
the `docker` CLI) and microsandbox (the microVM opt-in, selected with
`sandbox = "microsandbox"`). Adding a third is additive: implement the protocol and
register it; nothing outside `registered_backends()` names a concrete runtime.

IMPORTANT: importing this module must NOT import the `microsandbox` package. Every
`import microsandbox` stays inside a method body so `lint`/`analyze` keep working on a
host where the package is absent.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from benchspec.agents import CodingAgent
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig
from benchspec.specs.schema import SchemaError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"


def host_mount_path(path: object) -> str:
    """Return the host path to hand the VM runtime for a bind mount.

    The runtime binds the literal path it is given. On macOS the temp root — where the
    clean room and the staged project live — sits behind the `/var` → `/private/var`
    symlink, and a mount through that link fails with "Not a directory". Resolving
    first makes every bind site immune to that.
    """
    return str(Path(path).resolve())


BASE_IMAGE = "ubuntu:latest"
# The default every unpinned set, the bare build, and trigger routing inherit; microsandbox
# is the microVM opt-in a set asks for by name.
DEFAULT_SANDBOX = "docker"
# Every guest and snapshot benchspec creates — microVM, container, or image — carries this
# prefix, so pruning selects them with one match and leaves other tools' sandboxes alone.
NAME_PREFIX = "benchspec-"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


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

    def prune(self: object) -> None:
        """Remove every `benchspec-*` artifact this backend owns."""
        ...


def fingerprint_inputs_for(
    backend_id: str, agent: CodingAgent, env: EnvConfig
) -> FingerprintInputs:
    """Return the structured inputs and digest behind one backend's snapshot fingerprint.

    Folds in: the backend id, the DECLARED base-image reference (the tag/ref as
    configured — benchspec does not resolve it to a digest; a floating tag is therefore
    not reproducible across time/machines, and the backend records the actual pulled
    digest in Phase-8 artifacts), the agent install fingerprint (installer inputs beyond
    `version()`), and the raw environment script bytes. `snapshot_name()` and provenance
    capture both reach this through the backend instead of re-hashing or parsing the
    snapshot name. The backend id leads, so one agent+env never shares a snapshot across
    two backends.

    Args:
        backend_id: The resolving backend's `id`.
        agent: The agent whose installer inputs the snapshot bakes in.
        env: The host environment config folded into the snapshot.

    Returns:
        The raw ingredients and the digest hashed from them.
    """
    base_image_ref = env.base_image or BASE_IMAGE
    install_fingerprint = agent.install_fingerprint()
    env_script_sha256 = hashlib.sha256(env.script).hexdigest()
    payload = b"\0".join(
        (
            backend_id.encode(),
            base_image_ref.encode(),
            install_fingerprint.encode(),
            env.script,
        )
    )
    digest = hashlib.sha256(payload).hexdigest()[:8]

    return FingerprintInputs(
        backend_id=backend_id,
        base_image_ref=base_image_ref,
        install_fingerprint=install_fingerprint,
        env_script_sha256=env_script_sha256,
        digest=digest,
    )


async def bridge_skills_home(sandbox: object, agent: object) -> None:
    """Link the agent skill directory to the fixed skills-home path.

    Args:
        sandbox: The live guest, mid-build.
        agent: The agent supplying the bridge script and guest environment.

    Raises:
        RuntimeError: If the bridge script exits nonzero.
    """
    result = await sandbox.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"skills-home bridge failed (exit {result.exit_code}): {result.stderr_text[-2000:]}"
        )


async def run_environment_script(sandbox: object, agent: object, env: EnvConfig) -> None:
    """Run the host's environment script after provisioning, before sealing.

    Args:
        sandbox: The live guest, mid-build.
        agent: The agent supplying the guest environment.
        env: The host environment config; an empty script is a no-op.

    Raises:
        RuntimeError: If the environment script exits nonzero.
    """
    if not env.script:
        return

    script = b"set -e\n" + env.script
    result = await sandbox.shell(script.decode(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"environment_script failed (exit {result.exit_code}): {result.stderr_text[-2000:]}"
        )


class SharedBackendBehavior:
    """The backend methods whose bodies are runtime-independent.

    `guest_shell`, `stop_quietly`, and `kill_quietly` speak only the guest surface every
    backend exposes and the neutral `SandboxError` every backend raises, so both
    implementations inherit one copy instead of keeping byte-identical twins in step.
    """

    async def guest_shell(self: object, sandbox: object, agent: object, script: str) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        try:
            result = await sandbox.shell(script, env=agent.guest_env())
        except (TimeoutError, SandboxError, OSError):
            return None
        return result.stdout_text if result.exit_code == 0 else None

    async def stop_quietly(self: object, sandbox: object) -> None:
        """Best-effort guest teardown that never masks the real flow."""
        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            await sandbox.stop()

    async def kill_quietly(self: object, handle: object) -> None:
        """Best-effort kill of a streaming exec handle (routing timeout path)."""
        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            await handle.kill()


def microsandbox_secrets(agent: object) -> list:
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
    backend-neutral `SandboxError` a future backend would raise.
    """

    def __init__(self: object, native: object) -> None:
        """Wrap a live native `Sandbox` instance."""
        self._native = native

    async def shell(
        self: object, script: str, *, env: dict | None = None, cwd: str | None = None
    ) -> object:
        """Run a shell script in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._native.shell(script, env=env, cwd=cwd)

    async def exec(
        self: object,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        timeout: int | None = None,
        stdin: object = None,
    ) -> object:
        """Run a command in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._native.exec(
                cmd, args, cwd=cwd, env=env, timeout=timeout, stdin=stdin
            )

    async def exec_stream(
        self: object,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        stdin: object = None,
    ) -> MicrosandboxExecHandle:
        """Start a streaming command in the guest, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            native_handle = await self._native.exec_stream(cmd, args, cwd=cwd, env=env, stdin=stdin)
        return MicrosandboxExecHandle(native_handle)

    async def stop(self: object) -> None:
        """Stop the guest sandbox, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            await self._native.stop()


class MicrosandboxExecHandle:
    """Wrap a native microsandbox `ExecHandle`, translating its errors to `SandboxError`."""

    def __init__(self: object, native_handle: object) -> None:
        """Wrap a live native streaming-exec handle."""
        self._native_handle = native_handle

    def __aiter__(self: object) -> MicrosandboxExecHandle:
        """Return self as the async iterator over streamed exec events."""
        return self

    async def __anext__(self: object) -> object:
        """Return the next streamed exec event, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            return await self._native_handle.__anext__()

    async def kill(self: object) -> None:
        """Kill the underlying streaming exec, translating a torn-down VM's error."""
        async with _translate_runtime_errors():
            await self._native_handle.kill()


class MicrosandboxBackend(SharedBackendBehavior):
    """The microsandbox implementation of `SandboxBackend`: one microVM per guest."""

    id = "microsandbox"

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
                "(or `pip install 'benchspec[microsandbox]'`)"
            )
        return errors

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether a named microsandbox snapshot exists on disk."""
        return (Path.home() / ".microsandbox" / "snapshots" / name).exists()

    def fingerprint_inputs(self: object, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return fingerprint_inputs_for(self.id, agent, env)

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

    def prune(self: object) -> None:
        """Remove every `benchspec-*` microsandbox artifact (arrives with `sandbox:clean`)."""

    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable microsandbox snapshot."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(self: object, agent: object, name: str, env: EnvConfig) -> None:
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


def registered_backends() -> dict[str, type]:
    """Return every implemented backend keyed by its `sandbox` value."""
    # docker.py imports this module for the seam, so the registry imports it lazily
    # to keep the dependency one-directional at import time.
    from benchspec.sandbox.docker import DockerBackend

    return {"microsandbox": MicrosandboxBackend, "docker": DockerBackend}


def resolve_sandbox(name: str) -> SandboxBackend:
    """Return the backend for a set's `sandbox` value, or fail fast.

    Returns a FRESH backend instance each call. An unregistered name raises listing the
    supported values; the raised `SchemaError` maps to exit 2 through the CLI/plugin.
    """
    backends = registered_backends()
    if name in backends:
        return backends[name]()

    raise SchemaError(f"unsupported sandbox `{name}` (supported: {sorted(backends)})")
