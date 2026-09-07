"""The Docker implementation of the sandbox seam, driven through the `docker` CLI.

Every guest interaction is a `docker` subprocess: `exec` for commands, `exec bash -c`
for scripts, `rm -f` for teardown. There is no Docker client package and no daemon
socket handling here — the module is stdlib only, so `lint` and `analyze` keep working
on a host with no Docker installed at all, the same invariant `backend.py` holds for
microsandbox by keeping its imports inside method bodies.

Isolation caveat: a container shares the host kernel and cannot scope a credential to
the hosts an agent may reach, so a Docker run trades microsandbox's VM boundary and
host-scoped secrets for speed and portability; `docs/sandbox.md` carries the tradeoff.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from benchspec.sandbox.errors import SandboxError

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


def _carries_runtime_failure(stderr_text: str) -> bool:
    """Return whether stderr names a daemon or container teardown rather than a bad exit."""
    return any(marker in stderr_text for marker in RUNTIME_FAILURE_MARKERS)


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


def exec_argv(
    container: str,
    command: str,
    args: list[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
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


def shell_argv(
    container: str, script: str, *, cwd: str | None, env: dict[str, str] | None
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

    output = DockerExecOutput(
        exit_code=process.returncode, stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes
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
        """Drain both streams into the queue, then queue the terminal event."""
        stderr_chunks: list[bytes] = []
        await asyncio.gather(
            self._pump_stream(self._process.stdout, "stdout", None),
            self._pump_stream(self._process.stderr, "stderr", stderr_chunks),
        )
        exit_code = await self._process.wait()

        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        event_type = "failed" if _carries_runtime_failure(stderr_text) else "exited"
        await self._events.put(DockerExecEvent(event_type, code=exit_code))

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
    `stop` — so agents and the routing driver work against either backend unchanged.
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
        self, script: str, *, env: dict | None = None, cwd: str | None = None
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
        env: dict | None = None,
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
        env: dict | None = None,
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
        if stdin is not None:
            process.stdin.write(stdin)
            await process.stdin.drain()
            process.stdin.close()

        return DockerExecHandle(process)

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
