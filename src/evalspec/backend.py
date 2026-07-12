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
import functools
import hashlib
import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from evalspec.agents import CodingAgent
from evalspec.discovery import EnvConfig
from evalspec.schema import SchemaError

logger = logging.getLogger(__name__)

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"
BASE_IMAGE = "ubuntu:latest"
# The only implemented backend today; the single source other modules default to.
DEFAULT_SANDBOX = "microsandbox"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


@functools.cache
def resolve_image_digest(base_image: str) -> str:
    """Resolve a (possibly floating) base-image tag to a content digest.

    A moved upstream tag (`ubuntu:latest` re-pointed) yields a new digest, so the snapshot
    fingerprint changes and the VM rebuilds instead of silently reusing a stale image.

    Memoized per `base_image` for the life of the process: `cache_fingerprint` and the
    warm-cache `snapshot_exists` check both resolve the digest on every call, and shelling
    out to `skopeo inspect` (up to a 60s timeout) on each one is wasteful for a value that
    cannot change mid-run.

    This is the seam tests monkeypatch, so the suite never touches the network. In
    production it shells to `skopeo inspect`. If skopeo is unavailable or fails, it falls
    back to the tag string — but does so LOUDLY (a logged warning), never silently
    pretending an unpinned tag is pinned, so an operator can see that digest-pinning is
    inactive on this host. Because the result is cached, that warning fires once per
    distinct `base_image` per process, not on every call.
    """
    if shutil.which("skopeo") is None:
        logger.warning(
            "evalspec: `skopeo` not found; base image %r is NOT digest-pinned. The snapshot "
            "fingerprint will not change if the upstream tag moves. Install skopeo (or another "
            "registry client) to enable digest pinning.",
            base_image,
        )
        return base_image
    try:
        proc = subprocess.run(
            [
                "skopeo",
                "inspect",
                # Guest images always target Linux (microsandbox runs Linux microVMs), so
                # override the host OS — on macOS, `skopeo` otherwise defaults to "darwin"
                # and fails to find a matching variant in the image's manifest list.
                "--override-os",
                "linux",
                "--format",
                "{{.Digest}}",
                f"docker://{base_image}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        logger.warning(
            "evalspec: `skopeo inspect` failed for %r (%s); base image is NOT digest-pinned.",
            base_image,
            err,
        )
        return base_image
    digest = proc.stdout.strip()
    if proc.returncode != 0 or not digest:
        logger.warning(
            "evalspec: `skopeo inspect` returned no digest for %r (exit %s); NOT digest-pinned.",
            base_image,
            proc.returncode,
        )
        return base_image
    return digest


def immutable_image(base_image: str) -> str:
    """Return the exact image reference the cache key names, so the build pulls that image.

    Resolving the tag once (memoized) and using `base_image@digest` for BOTH the fingerprint
    and `Sandbox.create` closes the TOCTOU window where the tag moves between hashing and the
    pull — otherwise a different image could be sealed under a snapshot name that claims the
    old digest and then reused forever. When digest resolution is unavailable (loud fallback),
    `resolve_image_digest` returns the tag unchanged and pinning is skipped.
    """
    digest = resolve_image_digest(base_image)
    if digest == base_image:
        return base_image
    return f"{base_image}@{digest}"


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

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the cache fingerprint folding backend id, image digest, install, env."""
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
        errs: list[str] = []
        system = platform.system()
        if system == "Darwin":
            if platform.machine() != "arm64":
                errs.append("x86_64 macOS is unsupported; microsandbox needs Apple Silicon")
        elif system == "Linux":
            if not Path("/dev/kvm").exists():
                errs.append("KVM not available (/dev/kvm missing)")
        else:
            errs.append(f"unsupported platform: {system} (need Apple Silicon or Linux+KVM)")
        if not self.installed():
            errs.append("microsandbox runtime not installed — run `make evals:build`")
        return errs

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether a named microsandbox snapshot exists on disk."""
        return (Path.home() / ".microsandbox" / "snapshots" / name).exists()

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env.

        Folds in: the backend id, the RESOLVED base-image content digest (pins a floating
        tag so a moved upstream tag rebuilds), the agent install fingerprint (installer
        inputs beyond `version()`), and the raw environment script bytes.
        """
        base_image = env.base_image or BASE_IMAGE
        payload = b"\0".join(
            (
                self.id.encode(),
                immutable_image(base_image).encode(),
                agent.install_fingerprint().encode(),
                env.script,
            )
        )
        return hashlib.sha256(payload).hexdigest()[:8]

    async def guest_shell(self: object, sandbox: object, agent: object, script: str) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        from microsandbox.errors import MicrosandboxError

        try:
            res = await sandbox.shell(script, env=agent.guest_env())
        except (MicrosandboxError, asyncio.TimeoutError, OSError):
            return None
        return res.stdout_text if res.exit_code == 0 else None

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
            build_name,
            image=immutable_image(base_image),
            cpus=VM_CPUS,
            memory=VM_MEMORY_MIB,
            replace=True,
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
        res = await sandbox.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"skills-home bridge failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def _run_environment_script(
        self: object, sandbox: object, agent: object, env: EnvConfig
    ) -> None:
        """Run the host's environment script after provisioning, before sealing."""
        if not env.script:
            return
        script = b"set -e\n" + env.script
        res = await sandbox.shell(script.decode(), env=agent.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"environment_script failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
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
