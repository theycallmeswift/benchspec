"""The Docker implementation of the sandbox seam, driven through the `docker` CLI.

`DockerBackend` owns the lifecycle — preflight, snapshot build and existence, per-arm and
trigger containers — and `DockerSandbox` is the live guest every caller drives. Every
interaction is a `docker` subprocess: `run -d` to start an idle container, `exec` for
commands, `exec bash -c` for scripts, `commit` to seal a snapshot, `rm -f` for teardown.
There is no Docker client package and no daemon socket handling here — beyond benchspec's
own modules the imports are stdlib, so `lint` and `analyze` keep working on a host with no
Docker installed at all, the same invariant `microsandbox.py` holds for microsandbox by
keeping its imports inside method bodies.

Isolation caveat: a container shares the host kernel and cannot scope a credential to
the hosts an agent may reach, so a Docker run trades microsandbox's VM boundary and
host-scoped secrets for speed and portability; `docs/sandbox.md` carries the tradeoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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
    SharedBackendBehavior,
    bridge_skills_home,
    fingerprint_inputs_for,
    host_mount_path,
    run_environment_script,
)
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.provenance import ImageIdentity
from benchspec.specs.discovery import EnvConfig

# Stderr fragments that mean the daemon or the container tore down, rather than the
# guest command itself failing. Docker reports both on the same stream, so the text is
# the only signal separating "your command exited 1" from "there is no container".
RUNTIME_FAILURE_MARKERS = (
    "Cannot connect to the Docker daemon",
    "Error response from daemon",
    "Error: No such container",
)

# How much of a guest stream one read takes: large enough that a chatty agent's JSONL
# arrives in a handful of events rather than hundreds.
_STREAM_CHUNK_BYTES = 64 * 1024

# The tail of a runtime failure kept when raising, so a wall of daemon output cannot
# bury the exception's own context.
_ERROR_TAIL_CHARS = 2000

_TERMINAL_EVENT_TYPES = frozenset({"exited", "failed"})

# Every snapshot is a tag under one repository, so `docker images benchspec-snapshot` is
# the whole benchspec image inventory and nothing else on the host is ever swept up.
SNAPSHOT_REPOSITORY = "benchspec-snapshot"

# Budget for a synchronous `docker` query (inspect, commit-adjacent bookkeeping). Long
# enough for a busy daemon, short enough that a wedged one fails the command, not the run.
DOCKER_COMMAND_TIMEOUT_SECONDS = 60

# Preflight's own, shorter budget: an unreachable daemon should be reported promptly.
DOCKER_INFO_TIMEOUT_SECONDS = 30

# Prune's own budget per removal. A removal is best-effort, so a wedged daemon should
# cost `sandbox:clean` a few seconds per resource rather than the full query budget.
DOCKER_REMOVE_TIMEOUT_SECONDS = 15

_DOCKER_CLI_NOT_FOUND = (
    "docker CLI not found — install Docker Engine or Docker Desktop, "
    "or set BENCHSPEC_DOCKER_PATH to the binary"
)


def docker_binary() -> Path | None:
    """Return the `docker` CLI benchspec will drive, or None when there is none.

    Returns:
        The `BENCHSPEC_DOCKER_PATH` override when set and non-empty, else the `docker`
        found on `$PATH`, else None.
    """
    override = os.environ.get("BENCHSPEC_DOCKER_PATH")
    if override:
        return Path(override)

    found = shutil.which("docker")
    return Path(found) if found else None


def _resolved_docker_binary() -> Path | None:
    """Return the `docker` CLI only when it resolves to a real file, else None.

    `docker_binary()` reports what the override or `$PATH` names; this reports whether
    that name is actually there, so a `BENCHSPEC_DOCKER_PATH` pointing at nothing reads
    as "no docker CLI" rather than surfacing later as a FileNotFoundError.
    """
    binary = docker_binary()
    return binary if binary is not None and binary.is_file() else None


def _stderr_tail(stderr_text: str) -> str:
    """Return the last non-empty line of a `docker` command's stderr, or an empty string."""
    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _carries_runtime_failure(stderr_text: str) -> bool:
    """Return whether stderr names a daemon or container teardown rather than a bad exit."""
    return any(marker in stderr_text for marker in RUNTIME_FAILURE_MARKERS)


def _piped_reader(stream: asyncio.StreamReader | None, name: str) -> asyncio.StreamReader:
    """Return a process stream that was opened as a pipe; a missing one is a programming error."""
    if stream is None:
        raise RuntimeError(f"docker exec was started without a piped {name}")
    return stream


def _piped_writer(stream: asyncio.StreamWriter | None) -> asyncio.StreamWriter:
    """Return a process stdin that was opened as a pipe; a missing one is a programming error."""
    if stream is None:
        raise RuntimeError("docker exec was started without a piped stdin")
    return stream


@dataclass(frozen=True)
class DockerExecOutput:
    """The finished result of one guest command: its exit code and both streams."""

    exit_code: int
    stdout_bytes: bytes
    stderr_bytes: bytes

    @property
    def stdout_text(self) -> str:
        """Return stdout decoded as UTF-8, replacing anything undecodable."""
        return self.stdout_bytes.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        """Return stderr decoded as UTF-8, replacing anything undecodable."""
        return self.stderr_bytes.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class DockerExecEvent:
    """One event from a streaming guest command.

    Attributes:
        event_type: `stdout` or `stderr` for a chunk, `exited` or `failed` for the
            terminal event that ends the stream.
        data: The chunk's bytes, on a `stdout` or `stderr` event.
        code: The command's exit code, on a terminal event.
    """

    event_type: str
    data: bytes | None = None
    code: int | None = None


@dataclass(frozen=True)
class DockerMount:
    """One host directory to bind into a guest, and whether the guest may write to it."""

    host_path: str
    readonly: bool

    def flag(self, guest_path: str) -> str:
        """Render the mount as the value of a `docker run -v` flag.

        Args:
            guest_path: Where the host directory appears inside the container.

        Returns:
            `<host>:<guest>`, with a `:ro` suffix when the mount is read-only.
        """
        if self.readonly:
            return f"{self.host_path}:{guest_path}:ro"
        return f"{self.host_path}:{guest_path}"


class DockerVolume:
    """The Docker counterpart of microsandbox's `Volume`, so mount code stays shared.

    Callers hand a backend its volume class and build mounts through it, which is why
    this exists as a namespace around one factory rather than as a bare function.
    """

    @staticmethod
    def bind(path: str, *, readonly: bool = False) -> DockerMount:
        """Return a bind mount of a host directory.

        Args:
            path: The host directory to bind.
            readonly: Whether the guest sees it read-only.

        Returns:
            The mount, ready to render into a `-v` flag.
        """
        return DockerMount(host_path=path, readonly=readonly)


def image_ref(snapshot: str) -> str:
    """Return the image reference a named snapshot is committed to and run from."""
    return f"{SNAPSHOT_REPOSITORY}:{snapshot}"


def run_argv(
    name: str, image: str, *, mounts: dict[str, DockerMount], env: Mapping[str, str]
) -> list[str]:
    """Render the `docker run` arguments that start one detached, idle guest.

    The container runs `sleep infinity` rather than any workload: benchspec drives every
    guest command through `docker exec`, so the container only has to stay alive.

    Args:
        name: The container name every later subcommand targets.
        image: The image reference to run.
        mounts: Guest path to bind mount, rendered in insertion order.
        env: Environment variables to set in the container, in insertion order.

    Returns:
        The argument list, starting at `run`.
    """
    argv = [
        "run",
        "-d",
        "--name",
        name,
        "--cpus",
        str(VM_CPUS),
        "--memory",
        f"{VM_MEMORY_MIB}m",
    ]
    for guest_path, mount in mounts.items():
        argv += ["-v", mount.flag(guest_path)]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]

    return [*argv, image, "sleep", "infinity"]


def exec_argv(
    container: str,
    command: str,
    args: list[str],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    interactive: bool,
) -> list[str]:
    """Render the `docker exec` arguments for one guest command.

    The binary itself is never included: the runner prepends whatever `docker_binary()`
    resolved.

    Args:
        container: The container name to exec into.
        command: The guest executable.
        args: The executable's arguments.
        cwd: Working directory inside the guest, or None for the image's default.
        env: Environment variables to set, rendered in insertion order.
        interactive: Whether the command is fed stdin (`-i`).

    Returns:
        The argument list, starting at `exec`.
    """
    argv = ["exec"]
    if interactive:
        argv.append("-i")
    if cwd:
        argv += ["-w", cwd]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]

    return [*argv, container, command, *args]


def ps_argv() -> list[str]:
    """Render the `docker ps` arguments that list every benchspec-named container.

    Docker's `--filter name=...` matches the name as a regular expression, unanchored, so
    a caller must still filter the printed names by `startswith(NAME_PREFIX)` — this only
    narrows the daemon's own listing before that stricter check.

    Returns:
        The argument list, starting at `ps`.
    """
    return ["ps", "-a", "--filter", f"name={NAME_PREFIX}", "--format", "{{.Names}}"]


def images_argv() -> list[str]:
    """Render the `docker images` arguments that list every benchspec snapshot image.

    Every snapshot is a tag under `SNAPSHOT_REPOSITORY`, so scoping to that one
    repository is already an exact match — no further filtering is needed on the
    repository half, only on the tag half of each printed `repository:tag` line.

    Returns:
        The argument list, starting at `images`.
    """
    return ["images", SNAPSHOT_REPOSITORY, "--format", "{{.Repository}}:{{.Tag}}"]


def shell_argv(
    container: str, script: str, *, cwd: str | None, env: Mapping[str, str] | None
) -> list[str]:
    """Render the `docker exec` arguments that run a shell script under bash.

    Args:
        container: The container name to exec into.
        script: The script body, passed to `bash -c`.
        cwd: Working directory inside the guest, or None for the image's default.
        env: Environment variables to set, rendered in insertion order.

    Returns:
        The argument list, starting at `exec`.
    """
    return exec_argv(container, "bash", ["-c", script], cwd=cwd, env=env, interactive=False)


async def _run_docker(
    binary: Path, argv: list[str], *, stdin: bytes | None, timeout: float | None
) -> DockerExecOutput:
    """Run one `docker` command to completion and return its result.

    Args:
        binary: The `docker` CLI to run.
        argv: The arguments to pass it.
        stdin: Bytes to write and then close, or None to give the command no stdin.
        timeout: Seconds to wait, or None to wait indefinitely.

    Returns:
        The command's exit code and both streams.

    Raises:
        TimeoutError: Unnamed, after killing the process, when `timeout` elapses; the
            caller names the guest command it was running.
        SandboxError: When stderr shows the daemon or the container tore down.
    """
    process = await asyncio.create_subprocess_exec(
        str(binary),
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(input=stdin), timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise

    # `communicate` has already reaped the process; `wait` just hands back the code as an
    # `int` rather than the `int | None` the returncode attribute carries.
    exit_code = await process.wait()
    output = DockerExecOutput(
        exit_code=exit_code, stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes
    )
    if _carries_runtime_failure(output.stderr_text):
        raise SandboxError(output.stderr_text.strip()[-_ERROR_TAIL_CHARS:])

    return output


class DockerExecHandle:
    """An async iterator over one streaming `docker exec`, with a kill switch.

    Both guest streams are pumped concurrently into a queue so a chatty command cannot
    deadlock on a full stderr pipe while the caller reads stdout. Iteration ends after
    the terminal `exited` or `failed` event.
    """

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        """Start pumping a live `docker exec` process's streams."""
        self._process = process
        self._events: asyncio.Queue[DockerExecEvent] = asyncio.Queue()
        self._finished = False
        self._pump = asyncio.create_task(self._pump_streams())

    def __aiter__(self) -> DockerExecHandle:
        """Return self as the async iterator over streamed exec events."""
        return self

    async def __anext__(self) -> DockerExecEvent:
        """Return the next event, ending iteration after the terminal one.

        Returns:
            The next stdout, stderr, or terminal event.

        Raises:
            StopAsyncIteration: Once the terminal event has been delivered.
        """
        if self._finished:
            raise StopAsyncIteration

        event = await self._events.get()
        if event.event_type in _TERMINAL_EVENT_TYPES:
            self._finished = True

        return event

    async def kill(self) -> None:
        """Kill the guest command if it is still running and reap it."""
        if self._process.returncode is None:
            self._process.kill()

        await self._process.wait()

    async def _pump_streams(self) -> None:
        """Drain both streams into the queue, then queue the terminal event.

        The terminal event is queued from a `finally` so a read error, a cancellation, or
        a process that never reports a code cannot leave a caller iterating forever: it
        gets a `failed` event with a null code instead of silence.
        """
        stderr_chunks: list[bytes] = []
        exit_code: int | None = None
        try:
            # A read error already reaches the caller as the `failed` event below; left on
            # this task it would only resurface as an unretrieved-exception warning when
            # the task is collected. A cancellation is a `BaseException` and still escapes.
            with contextlib.suppress(Exception):
                await asyncio.gather(
                    self._pump_stream(
                        _piped_reader(self._process.stdout, "stdout"), "stdout", None
                    ),
                    self._pump_stream(
                        _piped_reader(self._process.stderr, "stderr"), "stderr", stderr_chunks
                    ),
                )
                exit_code = await self._process.wait()
        finally:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            failed = exit_code is None or _carries_runtime_failure(stderr_text)
            self._events.put_nowait(
                DockerExecEvent("failed" if failed else "exited", code=exit_code)
            )

    async def _pump_stream(
        self, stream: asyncio.StreamReader, event_type: str, sink: list[bytes] | None
    ) -> None:
        """Queue one stream's chunks as events until it reaches EOF.

        Args:
            stream: The process stream to read.
            event_type: The event type to tag each chunk with.
            sink: Collector for the raw chunks, for streams whose text is inspected
                after the fact, or None to keep nothing.
        """
        while True:
            chunk = await stream.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                return

            if sink is not None:
                sink.append(chunk)
            await self._events.put(DockerExecEvent(event_type, data=chunk))


class DockerSandbox:
    """A live container, exposing the guest surface every benchspec caller drives.

    Mirrors `MicrosandboxGuest` method for method — `shell`, `exec`, `exec_stream`,
    `stop` — so agents and sessions work against either backend unchanged.
    """

    def __init__(self, name: str, *, binary: Path) -> None:
        """Wrap a running container by name.

        Args:
            name: The container name every `docker` subcommand targets.
            binary: The `docker` CLI to drive it with.
        """
        self.name = name
        self._binary = binary

    async def shell(
        self, script: str, *, env: Mapping[str, str] | None = None, cwd: str | None = None
    ) -> DockerExecOutput:
        """Run a shell script in the guest under bash.

        Args:
            script: The script body.
            env: Environment variables to set for it.
            cwd: Working directory inside the guest.

        Returns:
            The script's exit code and both streams.

        Raises:
            SandboxError: When the daemon or the container tore down.
        """
        return await _run_docker(
            self._binary,
            shell_argv(self.name, script, cwd=cwd, env=env),
            stdin=None,
            timeout=None,
        )

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecOutput:
        """Run a command in the guest and wait for it to finish.

        Args:
            cmd: The guest executable.
            args: Its arguments.
            cwd: Working directory inside the guest.
            env: Environment variables to set for it.
            timeout: Seconds to wait before killing it, or None to wait indefinitely.
            stdin: Bytes to feed it before closing stdin; None gives it no stdin at all.

        Returns:
            The command's exit code and both streams.

        Raises:
            TimeoutError: When the command outlives `timeout`.
            SandboxError: When the daemon or the container tore down.
        """
        argv = exec_argv(
            self.name, cmd, args or [], cwd=cwd, env=env, interactive=stdin is not None
        )
        try:
            return await _run_docker(self._binary, argv, stdin=stdin, timeout=timeout)
        except TimeoutError as error:
            raise TimeoutError(f"docker exec of {cmd} timed out after {timeout}s") from error

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecHandle:
        """Start a command in the guest and stream its output as it arrives.

        Args:
            cmd: The guest executable.
            args: Its arguments.
            cwd: Working directory inside the guest.
            env: Environment variables to set for it.
            stdin: Bytes to feed it before closing stdin; None gives it no stdin at all.

        Returns:
            A handle yielding output events and ending with a terminal one.
        """
        argv = exec_argv(
            self.name, cmd, args or [], cwd=cwd, env=env, interactive=stdin is not None
        )
        process = await asyncio.create_subprocess_exec(
            str(self._binary),
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # The pump must be running before stdin is written: a payload larger than the
        # pipe buffer would otherwise block here while the guest blocks on an unread
        # stdout, and neither side would ever move.
        handle = DockerExecHandle(process)
        if stdin is not None:
            # A guest command that exits without reading its input breaks the pipe
            # mid-write; the caller's answer is the terminal event the pump queues, not a
            # write error from a command that has already had its say.
            writer = _piped_writer(process.stdin)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                writer.write(stdin)
                await writer.drain()
                writer.close()

        return handle

    async def stop(self) -> None:
        """Remove the container, whatever state it is in."""
        process = await asyncio.create_subprocess_exec(
            str(self._binary),
            "rm",
            "-f",
            self.name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # The exit status is deliberately ignored: `rm -f` failing because the container
        # is already gone means teardown got what it wanted, and teardown runs on the
        # error path, where raising would mask the failure that led here.
        await process.wait()


class DockerBackend(SharedBackendBehavior):
    """The Docker implementation of `SandboxBackend`: one container per guest.

    The `docker` CLI is resolved on every call rather than cached at construction, so a
    host that gains (or loses) Docker mid-process is picked up without a fresh backend.
    Only `preflight`, `snapshot_exists`, `image_identity`, and `prune` tolerate its
    absence; every other entry point fails loudly, because silently doing nothing would
    look like a clean run.
    """

    id = "docker"

    def preflight(self) -> list[str]:
        """Return host-readiness errors: the CLI is installed and the daemon answers.

        There is no platform gate — Docker Desktop and Docker Engine cover every host
        benchspec runs on, so the only questions are whether the CLI is here and whether
        the daemon is up.

        Returns:
            One remedy string when the host is not ready, otherwise an empty list.
        """
        if _resolved_docker_binary() is None:
            return [_DOCKER_CLI_NOT_FOUND]

        try:
            completed = self._docker_sync(["info"], timeout=DOCKER_INFO_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return [self._daemon_unreachable(str(error) or type(error).__name__)]

        if completed.returncode != 0:
            return [self._daemon_unreachable(_stderr_tail(completed.stderr))]

        return []

    def snapshot_exists(self, name: str) -> bool:
        """Return whether the daemon holds the image a named snapshot was committed to.

        An unreachable daemon (`OSError`, `subprocess.TimeoutExpired`) reads the same
        as an absent snapshot rather than raising: existence checks must never abort a
        caller that is only deciding whether to build.
        """
        if _resolved_docker_binary() is None:
            return False

        try:
            completed = self._docker_sync(["image", "inspect", image_ref(name)])
        except (OSError, subprocess.TimeoutExpired):
            return False

        return completed.returncode == 0

    def fingerprint_inputs(self, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return fingerprint_inputs_for(self.id, agent, env)

    def cache_fingerprint(self, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest

    def image_identity(self, snapshot: str) -> ImageIdentity:
        """Return the snapshot image's id, or why it could not be read.

        Any failure — no CLI, no image, a wedged daemon — becomes an explained
        `unavailable` result rather than propagating: a backend lookup must never abort
        provenance capture or artifact aggregation.
        """
        argv = ["image", "inspect", "--format", "{{.Id}}", image_ref(snapshot)]
        try:
            completed = self._docker_sync(argv)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            return ImageIdentity.unavailable(str(error) or "docker image inspect failed")

        image_id = completed.stdout.strip()
        if completed.returncode == 0 and image_id:
            return ImageIdentity.available(image_id)

        return ImageIdentity.unavailable(
            _stderr_tail(completed.stderr) or "docker image inspect returned no id"
        )

    def build_snapshot(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision a build container and commit it as the reusable snapshot image."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def create_sandbox(
        self,
        *,
        agent: CodingAgent,
        snapshot: str,
        name: str,
        host_workdir: Path,
        host_repo_root: Path | None,
        extra_volumes: ExtraVolumes,
    ) -> DockerSandbox:
        """Create a container from a snapshot image for an arm session."""
        mounts = {GUEST_WORKDIR: DockerVolume.bind(host_mount_path(host_workdir))}
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            mounts[PROJECT_MOUNT] = DockerVolume.bind(
                host_mount_path(host_repo_root), readonly=True
            )
        mounts.update(extra_volumes(agent, DockerVolume))

        return await self._run_container(name, snapshot, mounts=mounts, agent=agent)

    def prune(self) -> None:
        """Remove every `benchspec-*` container and `benchspec-snapshot` image.

        Containers go first, then images: an image cannot be removed while a container
        still uses it. A missing CLI, an unreachable daemon (`docker ps` exiting
        nonzero, or raising `OSError`/`TimeoutExpired`), and individual `rm -f`/`rmi -f`
        removals that fail, wedge, or cannot be run at all are tolerated — this never
        raises, because `sandbox:clean` always exits 0.
        """
        binary = _resolved_docker_binary()
        if binary is None:
            return

        try:
            ps_result = self._docker_sync(ps_argv())
        except (OSError, subprocess.TimeoutExpired):
            return
        if ps_result.returncode != 0:
            return

        for name in ps_result.stdout.splitlines():
            if name.startswith(NAME_PREFIX):
                self._remove_quietly(["rm", "-f", name])

        try:
            images_result = self._docker_sync(images_argv())
        except (OSError, subprocess.TimeoutExpired):
            return
        if images_result.returncode != 0:
            return

        for line in images_result.stdout.splitlines():
            _, _, tag = line.rpartition(":")
            if tag.startswith(NAME_PREFIX):
                self._remove_quietly(["rmi", "-f", line])

    def _remove_quietly(self, argv: list[str]) -> None:
        """Run one prune removal, swallowing a daemon that wedges or a CLI that vanishes.

        One resource that will not go away must not cost the caller every removal queued
        behind it, nor turn `sandbox:clean` into a nonzero exit.

        Args:
            argv: The removal arguments, starting with `rm` or `rmi`.
        """
        with contextlib.suppress(OSError, subprocess.TimeoutExpired, RuntimeError):
            self._docker_sync(argv, timeout=DOCKER_REMOVE_TIMEOUT_SECONDS)

    def _binary(self) -> Path:
        """Return the `docker` CLI to drive.

        Returns:
            The resolved binary.

        Raises:
            RuntimeError: When no `docker` CLI is installed or the override points at
                nothing — every lifecycle command needs one and cannot proceed without it.
        """
        binary = _resolved_docker_binary()
        if binary is None:
            raise RuntimeError(_DOCKER_CLI_NOT_FOUND)

        return binary

    def _docker_sync(
        self, argv: list[str], *, timeout: float = DOCKER_COMMAND_TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess[str]:
        """Run one `docker` command synchronously and return its completed process.

        Args:
            argv: The arguments to pass the CLI.
            timeout: Seconds to wait before killing it.

        Returns:
            The completed process, whatever its exit status — callers decide what a
            nonzero exit means for them.

        Raises:
            RuntimeError: When no `docker` CLI is installed.
            subprocess.TimeoutExpired: When the command outlives `timeout`.
            OSError: When the CLI cannot be executed.
        """
        return subprocess.run(
            [str(self._binary()), *argv],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    async def _docker(self, argv: list[str]) -> DockerExecOutput:
        """Run one lifecycle `docker` command, failing loudly on a nonzero exit.

        Args:
            argv: The arguments to pass the CLI, starting with the subcommand.

        Returns:
            The command's exit code and both streams.

        Raises:
            SandboxError: When the subcommand exits nonzero, or when stderr shows the
                daemon or the container tore down.
        """
        output = await _run_docker(self._binary(), argv, stdin=None, timeout=None)
        if output.exit_code != 0:
            raise SandboxError(
                f"docker {argv[0]} failed (exit {output.exit_code}): "
                f"{output.stderr_text.strip()[-_ERROR_TAIL_CHARS:]}"
            )

        return output

    async def _remove_container(self, name: str) -> None:
        """Remove a container by name, ignoring failure.

        Used for both replace-before-run and teardown, where a container that is already
        gone is exactly the wanted state and raising would mask the real failure.
        """
        with contextlib.suppress(SandboxError, OSError):
            await self._docker(["rm", "-f", name])

    async def _run_container(
        self, name: str, snapshot: str, *, mounts: dict[str, DockerMount], agent: CodingAgent
    ) -> DockerSandbox:
        """Replace any container of this name, start one from `snapshot`, and wrap it.

        Args:
            name: The container name.
            snapshot: The snapshot whose committed image the container runs.
            mounts: Guest path to bind mount, in the order they should be rendered.
            agent: The agent whose credentials become container environment variables.

        Returns:
            The live guest.
        """
        # A container shares the host kernel, so a credential cannot be scoped to the
        # hosts an agent may reach the way a microsandbox secret is; it rides as a plain
        # container environment variable.
        env = {credential.env_var: credential.value for credential in agent.secrets()}

        await self._remove_container(name)
        await self._docker(run_argv(name, image_ref(snapshot), mounts=mounts, env=env))

        return DockerSandbox(name, binary=self._binary())

    async def _build_snapshot_async(self, agent: CodingAgent, name: str, env: EnvConfig) -> None:
        """Provision a build container, commit it as the snapshot image, then remove it."""
        build_name = f"{NAME_PREFIX}build-{agent.id}"
        base_image = env.base_image or BASE_IMAGE

        await self._remove_container(build_name)
        await self._docker(run_argv(build_name, base_image, mounts={}, env={}))
        sandbox = DockerSandbox(build_name, binary=self._binary())
        try:
            await agent.provision(sandbox)
            await bridge_skills_home(sandbox, agent)
            await run_environment_script(sandbox, agent, env)
            await self._docker(["commit", build_name, image_ref(name)])
        finally:
            await self._remove_container(build_name)

    def _daemon_unreachable(self, detail: str) -> str:
        """Render the preflight remedy for a daemon that did not answer `docker info`."""
        return (
            f"Docker daemon unreachable (`docker info` failed: {detail}) — "
            "start Docker Desktop or the docker service"
        )
