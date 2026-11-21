"""The E2B implementation of the sandbox seam: one remote microVM per guest.

E2B shares no filesystem with the host, so `create_sandbox` uploads `/workspace` and
`/project` as tar archives and `sync_out` downloads `/workspace` before the host grades
it. The `e2b` package is an optional extra, imported under a guard like microsandbox's.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shlex
import shutil
import socket
import tarfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

from benchspec.agents import CodingAgent
from benchspec.sandbox.backend import (
    BASE_IMAGE,
    GUEST_WORKDIR,
    NAME_PREFIX,
    PROJECT_MOUNT,
    VM_CPUS,
    VM_MEMORY_MIB,
    ExtraVolumes,
    FingerprintInputs,
    LiveSandbox,
    SharedBackendBehavior,
    bridge_skills_home,
    fingerprint_inputs_for,
    run_environment_script,
)
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.project import stage_archive
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig

try:
    import e2b
except ImportError:  # the optional extra is absent
    e2b = None

if TYPE_CHECKING:
    from e2b import AsyncCommandHandle, AsyncSandbox

SDK_MISSING = 'e2b SDK not installed — run `pip install "benchspec[e2b]"`'
API_KEY_ENV = "E2B_API_KEY"
API_KEY_MISSING = f"{API_KEY_ENV} is not set — create a key at https://e2b.dev/dashboard"

# Every sandbox carries this tag and prune lists by it, so nothing else in the E2B
# project is ever selected.
OWNER_METADATA = {"created-by": "benchspec"}
NAME_METADATA_KEY = "benchspec-name"

# Guest commands run as root, as they do in a container.
_GUEST_USER = "root"

# How long a sandbox outlives its cell's cap before E2B kills it: room for the
# sync out, and the bound on what a sandbox whose controller died can cost.
LIFETIME_MARGIN_SECONDS = 120
BUILD_LIFETIME_SECONDS = 1800

# The sync's own budget: it runs after the cell's cap has fired.
SYNC_TIMEOUT_SECONDS = 120

_GUEST_ARCHIVE_DIR = "/tmp"
_WORKSPACE_ARCHIVE = f"{_GUEST_ARCHIVE_DIR}/benchspec-workspace.tar.gz"
_PROJECT_ARCHIVE = f"{_GUEST_ARCHIVE_DIR}/benchspec-project.tar.gz"

# GNU tar exits 1 when a file changed while it was read, which a process the cap
# killed mid-write can cause; the archive is still complete enough to grade.
_TAR_USABLE_EXIT_CODES = frozenset({0, 1})

_ERROR_TAIL_CHARS = 2000


@dataclass(frozen=True)
class E2BExecOutput:
    """The finished result of one guest command."""

    exit_code: int
    stdout_text: str
    stderr_text: str


@dataclass(frozen=True)
class E2BExecEvent:
    """One event from a streaming guest command; see `ExecStreamEvent`."""

    event_type: str
    data: bytes | None = None
    code: int | None = None


@contextlib.asynccontextmanager
async def _translate_errors(
    still_running: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncGenerator[None]:
    """Translate SDK failures into `SandboxError`, or `TimeoutError` for a slow command.

    `SandboxNotRunningException` subclasses `TimeoutException`, so only
    `still_running` separates a dead sandbox from a slow command.
    """
    if e2b is None:
        raise SandboxError(SDK_MISSING)

    try:
        yield
    except e2b.TimeoutException as error:
        if still_running is not None and await still_running():
            raise TimeoutError(str(error)) from error

        raise SandboxError(str(error)) from error
    except (e2b.SandboxException, e2b.AuthenticationException, e2b.BuildException) as error:
        raise SandboxError(str(error)) from error


def _pack(directory: Path) -> bytes:
    """Return a gzipped tar of `directory`'s children, members relative to it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in sorted(directory.iterdir()):
            tar.add(entry, arcname=entry.name)

    return buffer.getvalue()


def _safe_member(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """Apply tarfile's `data` filter, dropping a member it refuses instead of aborting.

    The guest is untrusted: an absolute or escaping link, or a device node, never lands
    on the host, and the rest of the workspace is still graded.
    """
    try:
        return tarfile.data_filter(member, destination)
    except tarfile.FilterError:
        return None


def _replace_contents(directory: Path, archive: bytes) -> None:
    """Replace `directory`'s contents with the gzipped tar `archive`."""
    for entry in directory.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        tar.extractall(directory, filter=_safe_member)


async def _pinned_hosts(hosts: list[str]) -> str:
    """Return `/etc/hosts` lines for `hosts`, resolved here because the guest has no DNS.

    Raises:
        SandboxError: When a host does not resolve.
    """
    lines = []
    for host in hosts:
        try:
            address = await asyncio.to_thread(socket.gethostbyname, host)
        except OSError as error:
            raise SandboxError(f"cannot resolve provider host {host}: {error}") from error

        lines.append(f"{address} {host}")

    return "\n".join(lines)


def _owned(names: list[str]) -> bool:
    """Return whether a snapshot or template name, minus its project slug, is benchspec's."""
    return any(name.rpartition("/")[2].startswith(NAME_PREFIX) for name in names)


class E2BExecHandle:
    """An async iterator over one streaming guest command, with a kill switch."""

    def __init__(self) -> None:
        """Create a handle with nothing attached yet; `attach` starts the stream."""
        self._events: asyncio.Queue[E2BExecEvent] = asyncio.Queue()
        self._native: AsyncCommandHandle | None = None
        self._killed = False
        self._finished = False
        self._waiter: asyncio.Task[None] | None = None

    def emit(self, event_type: str, text: str) -> None:
        """Queue one decoded output chunk as a `stdout` or `stderr` event."""
        self._events.put_nowait(E2BExecEvent(event_type, data=text.encode()))

    def attach(self, native: AsyncCommandHandle) -> None:
        """Follow `native` to its end and queue the terminal event."""
        self._native = native
        self._waiter = asyncio.create_task(self._wait(native))

    def __aiter__(self) -> E2BExecHandle:
        """Return self as the async iterator over streamed exec events."""
        return self

    async def __anext__(self) -> E2BExecEvent:
        """Return the next event, ending iteration after the terminal one."""
        if self._finished:
            raise StopAsyncIteration

        event = await self._events.get()
        if event.event_type in ("exited", "failed"):
            self._finished = True

        return event

    async def kill(self) -> None:
        """SIGKILL the guest command."""
        self._killed = True
        if self._native is not None:
            async with _translate_errors():
                await self._native.kill()

    async def _wait(self, native: AsyncCommandHandle) -> None:
        """Queue `exited` with the command's code, or `failed` when it was killed or lost."""
        try:
            code = (await native.wait()).exit_code
        except Exception as error:
            # The SDK raises a nonzero exit, which carries its code; a dead sandbox
            # raises with none.
            code = getattr(error, "exit_code", None)

        failed = self._killed or code is None
        self._events.put_nowait(E2BExecEvent("failed" if failed else "exited", code=code))


class E2BSandbox:
    """A live E2B sandbox, exposing the guest surface every benchspec caller drives."""

    def __init__(self, native: AsyncSandbox) -> None:
        """Wrap a running SDK sandbox."""
        self._native = native

    async def shell(
        self, script: str, *, env: Mapping[str, str] | None = None, cwd: str | None = None
    ) -> E2BExecOutput:
        """Run a shell script in the guest under bash.

        Raises:
            SandboxError: When the sandbox is gone.
        """
        return await self.exec("bash", ["-c", script], env=env, cwd=cwd)

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> E2BExecOutput:
        """Run a command in the guest and wait for it to finish.

        Raises:
            TimeoutError: When the command outlives `timeout`; the command is killed.
            SandboxError: When the sandbox is gone.
        """
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        async with _translate_errors(self._native.is_running):
            handle = await self._start(cmd, args, cwd=cwd, env=env, stdin=stdin)
            try:
                result = await asyncio.wait_for(handle.wait(), timeout)
            except e2b.CommandExitException as error:
                result = error
            except (TimeoutError, asyncio.CancelledError) as error:
                with contextlib.suppress(Exception):
                    await handle.kill()

                if isinstance(error, TimeoutError):
                    raise TimeoutError(f"{cmd} timed out after {timeout}s") from error

                raise

        return E2BExecOutput(
            exit_code=result.exit_code,
            stdout_text=result.stdout or "",
            stderr_text=result.stderr or "",
        )

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> E2BExecHandle:
        """Start a command in the guest and stream its output as it arrives.

        Raises:
            SandboxError: When the sandbox is gone.
        """
        handle = E2BExecHandle()
        async with _translate_errors(self._native.is_running):
            native = await self._start(
                cmd,
                args,
                cwd=cwd,
                env=env,
                stdin=stdin,
                on_stdout=lambda text: handle.emit("stdout", text),
                on_stderr=lambda text: handle.emit("stderr", text),
            )
        handle.attach(native)

        return handle

    async def stop(self) -> None:
        """Kill the sandbox."""
        async with _translate_errors():
            await self._native.kill()

    async def upload(self, guest_path: str, data: bytes | IO[bytes]) -> None:
        """Write `data` to `guest_path` over the file API."""
        async with _translate_errors(self._native.is_running):
            await self._native.files.write(guest_path, data, user=_GUEST_USER)

    async def download(self, guest_path: str) -> bytes:
        """Return the bytes of `guest_path` over the file API."""
        async with _translate_errors(self._native.is_running):
            return bytes(
                await self._native.files.read(guest_path, format="bytes", user=_GUEST_USER)
            )

    async def _start(
        self,
        cmd: str,
        args: list[str] | None,
        *,
        cwd: str | None,
        env: Mapping[str, str] | None,
        stdin: bytes | None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> AsyncCommandHandle:
        """Start `cmd` in the background, feed it `stdin`, and return the SDK handle."""
        handle = await self._native.commands.run(
            shlex.join([cmd, *(args or [])]),
            background=True,
            envs=dict(env or {}),
            user=_GUEST_USER,
            cwd=cwd,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            stdin=stdin is not None,
            # The SDK default kills a command at 60 s; benchspec enforces its own cap.
            timeout=0,
        )
        if stdin is not None:
            if stdin:
                await handle.send_stdin(stdin)

            await handle.close_stdin()

        return handle


class E2BBackend(SharedBackendBehavior):
    """The E2B implementation of `SandboxBackend`.

    Only `preflight`, `snapshot_exists`, `image_identity`, and `prune` tolerate a
    missing SDK or key; every other entry point fails loudly.
    """

    id = "e2b"

    def preflight(self) -> list[str]:
        """Return host-readiness errors: the SDK is installed and the API key is set."""
        if e2b is None:
            return [SDK_MISSING]

        if not os.environ.get(API_KEY_ENV):
            return [API_KEY_MISSING]

        return []

    def snapshot_exists(self, name: str) -> bool:
        """Return whether the E2B project holds a snapshot called `name`."""
        if self.preflight():
            return False

        return bool(asyncio.run(self._snapshot_ids(name)))

    def fingerprint_inputs(self, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return fingerprint_inputs_for(self.id, agent, env)

    def cache_fingerprint(self, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest

    def image_identity(self, snapshot: str) -> ImageIdentity:
        """Report the digest as unavailable: an E2B snapshot has a build ID, no OCI digest."""
        return ImageIdentity.unavailable("E2B snapshots have no OCI image digest")

    def build_snapshot(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision a sandbox from the base image and seal it as the snapshot `name`."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def create_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_workdir: Path,
        staged_project: Path | None,
        extra_volumes: ExtraVolumes,
        cap_seconds: int,
    ) -> E2BSandbox:
        """Boot a sandbox from `snapshot`, limited to the provider hosts, and upload both trees.

        Raises:
            SandboxError: When the boot or upload fails, or the agent's credentials name
                no provider host to allow.
        """
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        credentials = agent.secrets()
        hosts = sorted({host for credential in credentials for host in credential.allow_hosts})
        if not hosts:
            raise SandboxError(
                f"harness `{agent.id}` declares no provider host for its credential, so "
                "E2B has nothing to allow; use an API-key credential"
            )

        pinned_hosts = await _pinned_hosts(hosts)
        async with _translate_errors():
            native = await e2b.AsyncSandbox.create(
                template=snapshot,
                timeout=cap_seconds + LIFETIME_MARGIN_SECONDS,
                metadata={**OWNER_METADATA, NAME_METADATA_KEY: name},
                envs={credential.env_var: credential.value for credential in credentials},
                network={
                    "allow_out": hosts,
                    "deny_out": [e2b.ALL_TRAFFIC],
                    # The default leaves 8.8.8.8 open on every port, a data channel.
                    "dns_servers": [],
                },
            )
        sandbox = E2BSandbox(native)
        try:
            await self._upload_trees(sandbox, host_workdir, staged_project, pinned_hosts)
        except BaseException:
            await self.stop_quietly(sandbox)
            raise

        return sandbox

    async def sync_out(self, sandbox: LiveSandbox, host_workdir: Path) -> None:
        """Replace `host_workdir` with the guest's `/workspace`, as one tar download.

        Raises:
            SandboxError: When the sandbox is gone or the guest cannot pack the workspace.
            TimeoutError: When packing outlives `SYNC_TIMEOUT_SECONDS`.
        """
        if not isinstance(sandbox, E2BSandbox):
            raise SandboxError("the E2B backend can only sync out an E2B sandbox")

        packed = await sandbox.exec(
            "tar",
            ["-czf", _WORKSPACE_ARCHIVE, "-C", GUEST_WORKDIR, "."],
            timeout=SYNC_TIMEOUT_SECONDS,
        )
        if packed.exit_code not in _TAR_USABLE_EXIT_CODES:
            raise SandboxError(
                f"packing {GUEST_WORKDIR} failed (exit {packed.exit_code}): "
                f"{packed.stderr_text[-_ERROR_TAIL_CHARS:]}"
            )

        _replace_contents(host_workdir, await sandbox.download(_WORKSPACE_ARCHIVE))

    async def discard_quietly(self, name: str) -> None:
        """Kill every sandbox tagged `name` that a cancelled boot may have left running."""
        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            await self._kill_tagged({**OWNER_METADATA, NAME_METADATA_KEY: name})

    def prune(self) -> None:
        """Kill benchspec's sandboxes and delete its `benchspec-*` snapshots and templates.

        Selection is by benchspec's own tag and name prefix, so the project's other
        sandboxes and templates are untouched. Never raises.
        """
        if self.preflight():
            return

        with contextlib.suppress(SandboxError, TimeoutError, OSError):
            asyncio.run(self._prune_async())

    async def _snapshot_ids(self, name: str | None = None) -> list[str]:
        """Return the IDs of benchspec's snapshots, or of the one called `name`."""
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        snapshot_ids = []
        async with _translate_errors():
            pages = e2b.AsyncSandbox.list_snapshots(name=name)
            while pages.has_next:
                snapshot_ids += [
                    snapshot.snapshot_id
                    for snapshot in await pages.next_items()
                    if _owned(snapshot.names)
                ]

        return snapshot_ids

    async def _kill_tagged(self, metadata: dict[str, str]) -> None:
        """Kill every sandbox carrying all of `metadata`."""
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        async with _translate_errors():
            pages = e2b.AsyncSandbox.list(query=e2b.SandboxQuery(metadata=metadata))
            while pages.has_next:
                for listed in await pages.next_items():
                    await e2b.AsyncSandbox.kill(listed.sandbox_id)

    async def _prune_async(self) -> None:
        """Kill benchspec's sandboxes, then delete its snapshots and templates."""
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        await self._kill_tagged(OWNER_METADATA)
        for snapshot_id in await self._snapshot_ids():
            async with _translate_errors():
                await e2b.AsyncSandbox.delete_snapshot(snapshot_id)

        async with _translate_errors():
            pages = e2b.AsyncTemplate.list()
            while pages.has_next:
                for template in await pages.next_items():
                    if _owned(template.names):
                        await e2b.AsyncTemplate.delete(template.template_id)

    async def _upload_trees(
        self,
        sandbox: E2BSandbox,
        host_workdir: Path,
        staged_project: Path | None,
        pinned_hosts: str,
    ) -> None:
        """Upload and unpack `/workspace` and `/project`, then pin the provider hosts."""
        await sandbox.upload(_WORKSPACE_ARCHIVE, _pack(host_workdir))
        unpack = [
            "set -e",
            f"mkdir -p {GUEST_WORKDIR} {PROJECT_MOUNT}",
            f"tar -xzf {_WORKSPACE_ARCHIVE} --no-same-owner -C {GUEST_WORKDIR}",
        ]
        if staged_project is not None:
            with stage_archive(staged_project).open("rb") as archive:
                await sandbox.upload(_PROJECT_ARCHIVE, archive)

            unpack.append(f"tar -xzf {_PROJECT_ARCHIVE} --no-same-owner -C {PROJECT_MOUNT}")

        unpack += [
            f"rm -f {_WORKSPACE_ARCHIVE} {_PROJECT_ARCHIVE}",
            f"printf '%s\\n' {shlex.quote(pinned_hosts)} >> /etc/hosts",
        ]
        result = await sandbox.shell("\n".join(unpack))
        if result.exit_code != 0:
            raise SandboxError(
                f"unpacking the uploaded trees failed (exit {result.exit_code}): "
                f"{result.stderr_text[-_ERROR_TAIL_CHARS:]}"
            )

    async def _build_snapshot_async(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Build a base template, provision a sandbox from it, and snapshot the sandbox.

        The base template only sizes the build sandbox; it is deleted once the
        snapshot is sealed.
        """
        if e2b is None:
            raise SandboxError(SDK_MISSING)

        base_name = f"{name}-base"
        async with _translate_errors():
            await e2b.AsyncTemplate.build(
                e2b.Template().from_image(env.base_image or BASE_IMAGE),
                base_name,
                cpu_count=VM_CPUS,
                memory_mb=VM_MEMORY_MIB,
            )
        try:
            async with _translate_errors():
                native = await e2b.AsyncSandbox.create(
                    template=base_name,
                    timeout=BUILD_LIFETIME_SECONDS,
                    metadata={
                        **OWNER_METADATA,
                        NAME_METADATA_KEY: f"{NAME_PREFIX}build-{agent.id}",
                    },
                )
            sandbox = E2BSandbox(native)
            try:
                await agent.provision(sandbox)
                await bridge_skills_home(sandbox, agent)
                await run_environment_script(sandbox, agent, env)
                async with _translate_errors():
                    await native.create_snapshot(name, mode="filesystem")
            finally:
                await self.stop_quietly(sandbox)
        finally:
            with contextlib.suppress(SandboxError):
                async with _translate_errors():
                    await e2b.AsyncTemplate.delete(base_name)
