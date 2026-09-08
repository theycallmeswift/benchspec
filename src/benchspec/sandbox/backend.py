"""The pluggable sandbox backend seam.

A `SandboxBackend` hides everything sandbox-runtime-specific behind one boundary:
host preflight, snapshot existence, the cache fingerprint, snapshot build, and the
per-arm / trigger session creation. `sandbox.py` and `execution.py` drive a resolved
backend and never import a concrete runtime package themselves. `SandboxError` (defined
in `benchspec.sandbox.errors` and re-exported here) is the one runtime-failure type
every backend speaks, so a torn-down guest is classified without naming a runtime.

This module is runtime-free: it holds the protocol, the shared constants, the
fingerprint helper, and the build steps whose bodies are runtime-independent. The two
implementations live beside it — `benchspec.sandbox.docker` (the default, a container
per arm) and `benchspec.sandbox.microsandbox` (the microVM opt-in) — and
`benchspec.sandbox.registry` is the only module that maps a `sandbox` value to one of
them. Adding a third is additive: implement the protocol and register it there.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from benchspec.agents import CodingAgent
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig

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
# Every guest and snapshot benchspec creates — microVM, container, or image — carries this
# prefix, so pruning selects them with one match and leaves other tools' sandboxes alone.
NAME_PREFIX = "benchspec-"
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
