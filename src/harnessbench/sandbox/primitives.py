"""The backend-agnostic primitives every `SandboxBackend` builds on.

Mount paths and guest layout, the shared VM/container sizing, the snapshot cache
fingerprint, and the two provisioning steps that run identically under any runtime. These
live below both concrete backends so `backend.py` can import a concrete backend module
without that module importing `backend.py` back.

Nothing here touches a concrete runtime: this module imports only the standard library
and harnessbench's own agent/spec types.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from harnessbench.agents import CodingAgent
from harnessbench.specs.discovery import EnvConfig

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"

BASE_IMAGE = "ubuntu:latest"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


def host_mount_path(path: object) -> str:
    """Return the host path to hand the runtime for a bind mount.

    The runtime binds the literal path it is given. On macOS the temp root — where the
    clean room and the staged project live — sits behind the `/var` → `/private/var`
    symlink, and a mount through that link fails with "Not a directory". Resolving
    first makes every bind site immune to that.
    """
    return str(Path(path).resolve())


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


def build_fingerprint_inputs(
    *, backend_id: str, agent: CodingAgent, env: EnvConfig
) -> FingerprintInputs:
    """Fold one backend's identity and an agent+environment into a cache fingerprint.

    Folds in: the backend id, the DECLARED base-image reference (the tag/ref as
    configured — harnessbench does not resolve it to a digest; a floating tag is therefore
    not reproducible across time/machines, and the backend records the actual pulled
    digest in run artifacts), the agent install fingerprint (installer inputs beyond
    `version()`), and the raw environment script bytes.

    Args:
        backend_id: The resolved backend's `id`, so two backends over one agent+env
            never collide on a snapshot name.
        agent: The agent whose installer inputs are folded in.
        env: The host environment config selecting the base image and script.

    Returns:
        The structured ingredients alongside the 8-character digest built from them.
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
    """Link the agent's skill directory to the fixed, harness-neutral skills home.

    Args:
        sandbox: A live guest exposing `.shell(script, env=...)`.
        agent: The agent whose `bridge_skills_home_script()` is run.

    Raises:
        RuntimeError: If the bridge script exits nonzero, with the stderr tail.
    """
    result = await sandbox.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"skills-home bridge failed (exit {result.exit_code}): "
            f"{result.stderr_text[-2000:]}"
        )


async def run_environment_script(sandbox: object, agent: object, env: EnvConfig) -> None:
    """Run the host's environment script after provisioning, before sealing.

    Args:
        sandbox: A live guest exposing `.shell(script, env=...)`.
        agent: The agent whose `guest_env()` the script runs under.
        env: The host environment config; an empty script is a no-op.

    Raises:
        RuntimeError: If the script exits nonzero, with the stderr tail.
    """
    if not env.script:
        return
    script = b"set -e\n" + env.script
    result = await sandbox.shell(script.decode(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"environment_script failed (exit {result.exit_code}): "
            f"{result.stderr_text[-2000:]}"
        )
