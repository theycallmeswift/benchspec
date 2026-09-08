"""The pluggable sandbox backend seam.

A `SandboxBackend` hides everything sandbox-runtime-specific behind one boundary:
host preflight, snapshot existence, the cache fingerprint, snapshot build, and the
per-arm / trigger session creation. `sandbox.py` and `execution.py` drive a resolved
backend and never import a concrete runtime package themselves. `SandboxError` (defined
in `benchspec.sandbox.errors` and re-exported here) is the one runtime-failure type
every backend speaks, so a torn-down guest is classified without naming a runtime.

The guest side of that boundary is structural too: a backend hands back a
`LiveSandbox` — the booted instance an agent shells into, execs in, and stops — and
every consumer types against that protocol rather than a runtime's own class, so the
Docker container, the microsandbox guest, and a recording fake
(`benchspec.testing.FakeSandbox`) all drive the same code paths.

This module is runtime-free: it holds the protocols, the shared constants, the
fingerprint helper, and the build steps whose bodies are runtime-independent. The two
implementations live beside it — `benchspec.sandbox.docker` (the default, a container
per arm) and `benchspec.sandbox.microsandbox` (the microVM opt-in) — and
`benchspec.sandbox.registry` is the only module that maps a `sandbox` value to one of
them. Adding a third is additive: implement the protocol and register it there.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from benchspec.agents import CodingAgent
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"


def host_mount_path(path: str | os.PathLike[str]) -> str:
    """Return the host path to hand the VM runtime for a bind mount.

    The runtime binds the literal path it is given. On macOS the temp root — where the
    clean room and the staged project live — sits behind the `/var` → `/private/var`
    symlink, and a mount through that link fails with "Not a directory". Resolving
    first makes every bind site immune to that.
    """
    return str(Path(path).resolve())


BASE_IMAGE = "ubuntu:latest"
# Every guest and snapshot benchspec creates — microVM, container, or image — carries this
# prefix, so pruning selects them with one match and leaves other tools' sandboxes alone.
NAME_PREFIX = "benchspec-"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


class ExecResult(Protocol):
    """The outcome of one command run inside a guest: exit code plus decoded streams."""

    @property
    def exit_code(self) -> int:
        """The process exit status."""
        ...

    @property
    def stdout_text(self) -> str:
        """Captured stdout, decoded."""
        ...

    @property
    def stderr_text(self) -> str:
        """Captured stderr, decoded."""
        ...


class ExecStreamEvent(Protocol):
    """One event from a streaming guest exec: an output chunk or a terminal status."""

    @property
    def event_type(self) -> str:
        """`stdout`, `stderr`, `exited`, or `failed`."""
        ...

    @property
    def data(self) -> bytes | None:
        """The raw chunk for an output event; None otherwise."""
        ...

    @property
    def code(self) -> int | None:
        """The exit code for a terminal event; None otherwise."""
        ...


class ExecStream(Protocol):
    """A handle over a streaming guest exec: iterate its events, or kill it early."""

    def __aiter__(self) -> AsyncIterator[ExecStreamEvent]:
        """Yield events as the guest process produces them."""
        ...

    async def kill(self) -> None:
        """Terminate the guest process."""
        ...


@runtime_checkable
class LiveSandbox(Protocol):
    """A booted guest instance: the surface agents, sessions, and routing drive.

    Only the calls benchspec makes are declared here, so any runtime whose guest
    exposes this shape — a Docker container, a microsandbox guest, or a recording fake
    — fits without subclassing. Runtime-checkable so the guest-version probe can
    recognise a live instance behind a unit test's closure-only session fake.
    """

    async def shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        """Run `script` under the guest shell and wait for it."""
        ...

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
        """Run `cmd` with `args` in the guest and wait for it."""
        ...

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> ExecStream:
        """Start `cmd` with `args` in the guest and return its event stream."""
        ...

    async def stop(self) -> None:
        """Shut the guest down."""
        ...


# A backend's mount-config type, carried generically so the runtime-free modules never
# name it: microsandbox's `MountConfig`, Docker's `DockerMount`.
MountT_co = TypeVar("MountT_co", covariant=True)
MountT = TypeVar("MountT")


class VolumeBinder(Protocol[MountT_co]):
    """Builds a bind-mount config from a host path — a backend's volume class."""

    def bind(self, path: str, *, readonly: bool = False) -> MountT_co:
        """Return the mount config binding host `path` into the guest."""
        ...


class ExtraVolumes(Protocol):
    """Resolves the agent-owned host files a guest must see, keyed by guest path.

    The backend supplies its own volume class, so one resolver serves every runtime and
    the runtime-free caller never names a mount type.
    """

    def __call__(self, agent: CodingAgent, volume_cls: VolumeBinder[MountT]) -> dict[str, MountT]:
        """Return the mounts for `agent`, built through `volume_cls`."""
        ...


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

    def preflight(self) -> list[str]:
        """Return host-readiness error strings (empty list ⇒ host is ready)."""
        ...

    def snapshot_exists(self, name: str) -> bool:
        """Return whether a named snapshot already exists on disk."""
        ...

    def fingerprint_inputs(self, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        ...

    def cache_fingerprint(self, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the cache fingerprint folding backend id, image digest, install, env."""
        ...

    def image_identity(self, snapshot: str) -> ImageIdentity:
        """Return the snapshot's native image manifest digest, or why it is unavailable."""
        ...

    def build_snapshot(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision and seal the reusable snapshot."""
        ...

    async def create_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_workdir: Path,
        host_repo_root: Path | None,
        extra_volumes: ExtraVolumes,
    ) -> LiveSandbox:
        """Create a runtime instance from a snapshot for an arm session."""
        ...

    async def create_trigger_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_repo_root: Path,
        extra_volumes: ExtraVolumes,
    ) -> LiveSandbox:
        """Create the sandbox used for trigger-routing probes."""
        ...

    async def guest_shell(
        self, sandbox: LiveSandbox, agent: CodingAgent, script: str
    ) -> str | None:
        """Run a script in the guest, returning stdout on success or None on failure."""
        ...

    async def stop_quietly(self, sandbox: LiveSandbox) -> None:
        """Best-effort VM teardown that never masks the real flow."""
        ...

    async def kill_quietly(self, handle: ExecStream) -> None:
        """Best-effort kill of a streaming exec handle."""
        ...

    def prune(self) -> None:
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


async def bridge_skills_home(sandbox: LiveSandbox, agent: CodingAgent) -> None:
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


async def run_environment_script(
    sandbox: LiveSandbox, agent: CodingAgent, env: EnvConfig
) -> None:
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

    async def guest_shell(
        self, sandbox: LiveSandbox, agent: CodingAgent, script: str
    ) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        try:
            result = await sandbox.shell(script, env=agent.guest_env())
        except (TimeoutError, SandboxError, OSError):
            return None
        return result.stdout_text if result.exit_code == 0 else None

    async def stop_quietly(self, sandbox: LiveSandbox) -> None:
        """Best-effort guest teardown that never masks the real flow."""
        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            await sandbox.stop()

    async def kill_quietly(self, handle: ExecStream) -> None:
        """Best-effort kill of a streaming exec handle (routing timeout path)."""
        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            await handle.kill()
