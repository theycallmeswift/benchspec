"""The Docker implementation of the `SandboxBackend` seam.

Every Docker interaction is a `docker` CLI subprocess — no client library — so this module
imports nothing outside the standard library and `harnessbench` itself, and a host with a
Docker daemon needs no extra Python package. A snapshot is a `docker commit`-ed local
image; a cell is a container run from it.

A container shares the host kernel: it is a weaker boundary than a microVM, and Docker has
no equivalent of microsandbox's network-scoped secrets, so an agent's credential is a plain
readable environment variable inside the guest. Selecting `sandbox = "docker"` is the
user's explicit opt-in to both.

IMPORTANT: every `docker` invocation goes through `_docker_sync`, `_docker`, or
`_docker_stream`. Unit tests fake the CLI by monkeypatching exactly those three module
attributes, so a call site that shells out directly would silently require a live daemon.

IMPORTANT: no error raised from this module may contain a raw argv. Credentials reach a
container through an `--env-file`, and every failure names the call by description
(`docker exec <container>`) rather than by its arguments, because this text ends up in
`<sandbox-error>` result text that is written to run artifacts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from harnessbench.agents import CodingAgent
from harnessbench.sandbox.errors import SandboxRuntimeError
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

DOCKER_BINARY = "docker"
# Docker container names match [a-zA-Z0-9][a-zA-Z0-9_.-]*; everything else is replaced.
_ILLEGAL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
# Docker repository names match [a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*; everything else is replaced.
_ILLEGAL_IMAGE_CHARS = re.compile(r"[^a-z0-9._-]")
_REPEATED_SEPARATORS = re.compile(r"[-._]{2,}")
# A blocking `docker` call (preflight, image inspect) must not hang a collection-time check.
SYNC_CALL_TIMEOUT_SECONDS = 30.0
# The only nonzero `docker image inspect` that means "not cached yet". Every other
# failure — a dead daemon, a permission problem — must be raised, not read as absence.
_IMAGE_ABSENT_PATTERN = re.compile(r"[Nn]o such image", re.MULTILINE)
# Stderr shapes that mean docker itself failed, not the guest command.
_CONTROL_PLANE_PATTERN = re.compile(
    r"Cannot connect to the Docker daemon"
    r"|Is the docker daemon running"
    r"|error during connect"
    r"|No such container"
    r"|is not running"
    r"|is restarting"
    r"|is paused"
    r"|removal of container .* is already in progress",
    re.IGNORECASE,
)
# `docker exec` reserves these for its own failures, but a guest command may exit with
# them too (127 is `command not found` from the guest's own shell; 137 is 128+SIGKILL,
# which an attached `docker exec` also reports when `docker rm -f` kills it out from under
# a running command), so they make a result AMBIGUOUS rather than infra — the container's
# state settles it.
_AMBIGUOUS_EXIT_CODES = frozenset({125, 126, 127, 137})
# The only nonzero `docker rm`/`docker inspect` that means "already gone", which is a
# successful teardown. `rm` says "No such container"; `inspect` says "No such object".
_CONTAINER_ABSENT_PATTERN = re.compile(r"[Nn]o such (container|object)")
# A wedged removal must not hang teardown behind a dead daemon.
REMOVE_TIMEOUT_SECONDS = 30.0

OWNER_LABEL = "harnessbench.owner"
OWNER_LABEL_VALUE = "harnessbench"
# Prints the owner label, or `<no value>` when the container carries none.
OWNER_LABEL_FORMAT = '{{index .Config.Labels "' + OWNER_LABEL + '"}}'
RUN_LABEL = "harnessbench.run"
# One nonce per harnessbench process: it makes this run's containers identifiable in
# `docker ps` and lets a cleanup sweep tell a live sibling run's containers from its own.
RUN_NONCE = uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class DockerResult:
    """The captured outcome of one completed `docker` CLI invocation."""

    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


class DockerCallTimeout(SandboxRuntimeError):
    """One `docker` invocation outlived its deadline.

    A distinct type so a caller can react to the deadline specifically — `DockerSandbox`
    reaps its container on this and only this — while every caller that just wants "the
    sandbox broke" still catches it as a `SandboxRuntimeError`.
    """


def _call_description(args: tuple[str, ...]) -> str:
    """Describe a `docker` call for an error message without echoing a single flag value.

    Keeps the leading positional words (the subcommand and, where it precedes the flags,
    its object) and stops at the first `-`. Callers with a safe, more specific description
    — a container or image name that sits AFTER the flags — pass `description=` instead.
    """
    words: list[str] = []
    for argument in args:
        if argument.startswith("-"):
            break
        words.append(argument)
    return f"`docker {' '.join(words)} ...`"


def _docker_sync(
    *args: str, timeout: float = SYNC_CALL_TIMEOUT_SECONDS, description: str | None = None
) -> DockerResult:
    """Run one blocking `docker` subcommand, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        timeout: Seconds to wait before treating the call as wedged.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`. Never interpolate raw argv into an error message here.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the `docker` binary is absent.
        DockerCallTimeout: If the call outlives `timeout`.
    """
    described = description or _call_description(tuple(args))
    try:
        completed = subprocess.run(
            [DOCKER_BINARY, *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as error:
        raise SandboxRuntimeError("docker CLI not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise DockerCallTimeout(f"{described} timed out after {timeout}s") from error
    return DockerResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


async def _docker(
    *args: str,
    stdin: bytes | None = None,
    timeout: float | None = None,
    description: str | None = None,
) -> DockerResult:
    """Run one `docker` subcommand on the event loop, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin; None writes nothing and
            still closes it, so a guest command can never block on an open pipe.
        timeout: Seconds to wait for completion, or None to wait indefinitely.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned.
        DockerCallTimeout: If the call outlives `timeout`. Killing this client does NOT
            stop the process inside the container — see `DockerSandbox._reap`.
    """
    described = description or _call_description(tuple(args))
    process = await _spawn(*args, description=described)
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin or b""), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise DockerCallTimeout(f"{described} timed out after {timeout}s") from None
    return DockerResult(
        tuple(args),
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _docker_stream(
    *args: str, stdin: bytes | None = None, description: str | None = None
) -> asyncio.subprocess.Process:
    """Spawn one `docker` subcommand for incremental stdout reads.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`.

    Returns:
        The live `asyncio.subprocess.Process`; the caller drains `.stdout`, concurrently
        drains `.stderr`, and awaits `.wait()` for the exit code.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned.
    """
    process = await _spawn(*args, description=description or _call_description(tuple(args)))
    if process.stdin is not None:
        process.stdin.write(stdin or b"")
        process.stdin.close()
    return process


async def _spawn(*args: str, description: str) -> asyncio.subprocess.Process:
    """Start a `docker` subprocess with all three streams piped.

    Args:
        *args: The `docker` subcommand and its arguments.
        description: How to name this call in an error. Required, and never the argv:
            spawn failures are reported to the user and land in run artifacts.

    Raises:
        SandboxRuntimeError: If the binary is missing or the OS refuses the spawn.
    """
    try:
        return await asyncio.create_subprocess_exec(
            DOCKER_BINARY,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise SandboxRuntimeError(f"could not run {description}: {error}") from error


@dataclass(frozen=True)
class DockerVolume:
    """One host→guest bind mount, rendered as a `docker run -v` argument."""

    host_path: str
    readonly: bool

    @classmethod
    def bind(cls: type[DockerVolume], path: str, readonly: bool = False) -> DockerVolume:
        """Bind `path` from the host, read-only when asked.

        Mirrors `microsandbox.Volume.bind` so `sandbox._agent_extra_volumes` hands either
        class the identical call.
        """
        return cls(host_path=str(path), readonly=readonly)

    def flag_value(self: DockerVolume, guest_path: str) -> str:
        """Render the `-v` value that mounts this volume at `guest_path`."""
        suffix = ":ro" if self.readonly else ""
        return f"{self.host_path}:{guest_path}{suffix}"


def container_name(name: str) -> str:
    """Sanitize a harnessbench run name into a legal Docker container name.

    Docker accepts `[a-zA-Z0-9][a-zA-Z0-9_.-]*`, while harnessbench run names are built
    from eval ids and arm names that may carry other characters. Anything illegal becomes
    `-`, and a name that would start with an illegal character gains an `hb-` prefix.
    """
    sanitized = _ILLEGAL_NAME_CHARS.sub("-", name)
    return sanitized if sanitized[:1].isalnum() else f"hb-{sanitized}"


def image_ref(snapshot: str) -> str:
    """Return the local image reference a snapshot name maps to.

    A snapshot name is `harnessbench-<backend>-<harness>-<harness-version>-<fingerprint>`,
    and the harness version is whatever that CLI reports — not constrained to Docker's
    repository grammar, which is lowercase `[a-z0-9]` with `.`, `_`, and `-` separators.
    So the name is lowercased, illegal characters collapse to `-`, and an 8-character
    SHA-256 of the ORIGINAL name is appended.

    That digest is what keeps the mapping injective: lowercasing alone would let two
    distinct snapshot names (differing only in case, or only in a character that sanitizes
    to `-`) collapse onto one image, serving one cache identity's image for another's
    fingerprint. It is deterministic, so `build_snapshot` and `snapshot_exists` always
    agree on the reference.
    """
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:8]
    lowered = _ILLEGAL_IMAGE_CHARS.sub("-", snapshot.lower())
    stem = _REPEATED_SEPARATORS.sub("-", lowered).strip("-._")[:100].rstrip("-._")
    return f"{stem or 'harnessbench-snapshot'}-{digest}"


def _env_flags(env: dict[str, str] | None) -> list[str]:
    """Render a NON-SECRET guest environment mapping as repeated `docker exec -e` arguments.

    `HOME`, `TZ`, and the `HARNESSBENCH_*` cell variables belong here. Credentials do NOT:
    an `-e` value is visible to every user on the host through `ps` and is echoed back by
    `docker inspect`, so `_credential_env_file` renders those through a `--env-file`.
    """
    return [flag for name, value in (env or {}).items() for flag in ("-e", f"{name}={value}")]


def _volume_flags(volumes: dict[str, DockerVolume]) -> list[str]:
    """Render a guest-path → volume mapping as repeated `docker run -v` arguments."""
    return [
        flag
        for guest_path, volume in volumes.items()
        for flag in ("-v", volume.flag_value(guest_path))
    ]


@contextlib.contextmanager
def _credential_env_file(agent: object) -> object:
    """Yield `docker run` flags that carry the agent's credentials out of band.

    Docker has no equivalent of microsandbox's network-scoped secret substitution, so
    `allow_hosts` cannot be enforced and the value IS readable inside the guest. That much
    is the documented cost of selecting `sandbox = "docker"`.

    What is NOT acceptable is leaking the value to the HOST. `-e NAME=value` puts the
    token in the container's argv, where any user on the machine reads it out of `ps` and
    where `docker inspect` echoes it back for as long as the container exists. So the
    values go into a mode-0600 file that is deleted as soon as `docker run` returns — by
    then the daemon has copied them into the container's environment.

    Args:
        agent: The agent whose `secrets()` are rendered.

    Yields:
        The `--env-file` flags, or an empty list when the agent declares no credentials.
    """
    credentials = agent.secrets()
    if not credentials:
        yield []
        return
    handle, path = tempfile.mkstemp(prefix="harnessbench-credentials-")
    try:
        os.fchmod(handle, 0o600)
        # docker's env-file format is one NAME=value per line, unquoted and unescaped.
        with os.fdopen(handle, "w", encoding="utf-8") as env_file:
            for credential in credentials:
                env_file.write(f"{credential.env_name}={credential.value}\n")
        yield ["--env-file", path]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _workspace_restore_owner() -> str | None:
    """Return the `uid:gid` a guest must chown `/workspace` back to, or None.

    Containers run as root. Under rootful Docker on Linux that means every file the agent
    writes into the bind-mounted clean room is root-owned, and the host can then neither
    gather facts from it nor delete the temporary directory holding it. On macOS, Docker
    Desktop maps ownership in its own file-sharing layer, so nothing needs restoring.
    """
    if sys.platform != "linux":
        return None
    return f"{os.getuid()}:{os.getgid()}"


def _resource_flags() -> list[str]:
    """Render the shared sizing constants as `docker run` resource limits."""
    return ["--cpus", str(VM_CPUS), "--memory", f"{VM_MEMORY_MIB}m"]


def _label_flags() -> list[str]:
    """Render the ownership labels every harnessbench container carries.

    The owner label is what makes removal safe: the pre-start `rm -f` only fires on a
    container carrying it, so a name collision with something a developer started by hand
    is refused rather than silently destroying their container. The run nonce identifies
    THIS process's containers, so a sweep can leave a concurrent run's alone.
    """
    return [
        "--label",
        f"{OWNER_LABEL}={OWNER_LABEL_VALUE}",
        "--label",
        f"{RUN_LABEL}={RUN_NONCE}",
    ]


@dataclass(frozen=True)
class ContainerRemoval:
    """The outcome of one bounded `docker rm -f`.

    `absent` and `removed` are both successes — teardown is idempotent, and a container
    that is already gone is exactly what the caller wanted. `error` is set only for a
    genuine daemon failure.
    """

    name: str
    removed: bool
    absent: bool
    error: str | None


async def _remove_container(
    name: str, *, timeout: float = REMOVE_TIMEOUT_SECONDS
) -> ContainerRemoval:
    """Remove one container by name, bounded and idempotent.

    This is the ONLY place in the module that removes a container: teardown, the timeout
    reap, the pre-start replace, and cleanup after a failed start all route through it.

    It never raises. Every caller is either a `finally` block or an `except` handler where
    a raise would mask the primary exception, so a daemon failure comes back in `.error`
    for the caller to decide about.

    Args:
        name: The container to remove.
        timeout: Seconds to allow before treating the removal as wedged.

    Returns:
        Whether the container was removed, was already absent, or the daemon failed.
    """
    try:
        result = await _docker(
            "rm", "-f", name, timeout=timeout, description=f"`docker rm -f {name}`"
        )
    except SandboxRuntimeError as error:
        return ContainerRemoval(name, removed=False, absent=False, error=str(error))
    if result.exit_code == 0:
        return ContainerRemoval(name, removed=True, absent=False, error=None)
    if _CONTAINER_ABSENT_PATTERN.search(result.stderr):
        return ContainerRemoval(name, removed=False, absent=True, error=None)
    return ContainerRemoval(
        name,
        removed=False,
        absent=False,
        error=(
            f"docker rm -f `{name}` exited {result.exit_code}: "
            f"{result.stderr.strip()[-500:] or '(no output)'}"
        ),
    )


def build_container_name(agent_id: str) -> str:
    """Return the name of the throwaway container a snapshot is provisioned in.

    Folds in a short hash of the resolved working directory — harnessbench's repo root.
    `build_snapshot` carries no repo-root argument, and the snapshot file lock in
    `ensure_snapshot` only serializes builds within one repo, so two checkouts building a
    snapshot for the same harness at the same time would otherwise share one name and the
    pre-start replace in one would destroy the other's build mid-provision.
    """
    root_digest = hashlib.sha256(str(Path.cwd().resolve()).encode("utf-8")).hexdigest()[:8]
    return container_name(f"harnessbench-build-{agent_id}-{root_digest}")


async def _replace_container(name: str) -> None:
    """Free a container name, but only by removing a container harnessbench started.

    `replace=True` semantics: a container left behind by an aborted run must not collide
    with this one. A container harnessbench did NOT start is never removed — `docker rm -f`
    on a name collision is indistinguishable from `docker rm -f` on somebody's running
    work, and the owner label is the only thing that tells them apart.

    Raises:
        SandboxRuntimeError: If the name is held by a container harnessbench does not own,
            or the daemon could not answer, or the removal failed.
    """
    owner = await _docker(
        "inspect", "--format", OWNER_LABEL_FORMAT, name, description=f"`docker inspect {name}`"
    )
    if owner.exit_code != 0:
        if _CONTAINER_ABSENT_PATTERN.search(owner.stderr):
            return
        raise SandboxRuntimeError(
            f"docker inspect for `{name}` exited {owner.exit_code}: "
            f"{owner.stderr.strip()[-500:] or '(no output)'}"
        )
    if owner.stdout.strip() != OWNER_LABEL_VALUE:
        raise SandboxRuntimeError(
            f"container `{name}` already exists and is not owned by harnessbench — "
            f"remove it yourself or free the name; harnessbench will not delete it"
        )
    outcome = await _remove_container(name)
    if outcome.error is not None:
        raise SandboxRuntimeError(outcome.error)


@dataclass(frozen=True)
class DockerExecOutput:
    """One completed guest command's captured outcome.

    Field names match the microsandbox exec result that `sandbox.py`, the agents, and
    `GuestSandbox` already read, so the guest contract is identical across backends.
    """

    exit_code: int
    stdout_text: str
    stderr_text: str


@dataclass(frozen=True)
class DockerStreamEvent:
    """One `exec_stream` event: a `stdout` chunk, or the terminal `exited` code.

    Mirrors the microsandbox event shape `sandbox._route_in_sandbox_async` drains, so
    trigger routing reads either backend's stream with the same code. Docker reports a
    failed launch through a nonzero exit code rather than a distinct `failed` event, and
    routing's own `exit_code not in (None, 0)` check already covers that.
    """

    event_type: str
    data: bytes | None = None
    code: int | None = None


class DockerExecStream:
    """An async iterator over a running `docker exec`'s stdout, ending with its exit code.

    Stderr is drained on a concurrent task rather than left in the pipe. `_docker_stream`
    pipes all three streams, and a piped stderr nobody reads blocks the guest the moment
    it writes past one pipe buffer (64 KiB on Linux): stdout stops arriving and the turn
    deadlocks until its timeout. Only a bounded tail is retained, for diagnostics.
    """

    _CHUNK_BYTES = 65536
    # Enough stderr to explain a failure; the rest is discarded so a chatty guest cannot
    # grow the host's memory for the length of a turn.
    _STDERR_TAIL_BYTES = 8192

    def __init__(self: object, process: object) -> None:
        """Wrap the live `docker exec` process whose stdout is drained."""
        self._process = process
        self._finished = False
        self._stderr_tail = bytearray()
        self._stderr_drain = None

    @property
    def stderr_tail(self: object) -> str:
        """The retained tail of the guest's stderr, for diagnosing a failed stream."""
        return self._stderr_tail.decode("utf-8", errors="replace")

    def _start_stderr_drain(self: object) -> None:
        """Start the stderr drain on first use.

        Deferred to first use rather than done in `__init__` so constructing a stream
        outside a running event loop stays legal.
        """
        if self._stderr_drain is None and getattr(self._process, "stderr", None) is not None:
            self._stderr_drain = asyncio.create_task(
                self._drain_stderr(), name="docker-exec-stream-stderr-drain"
            )

    async def _drain_stderr(self: object) -> None:
        """Keep stderr empty, retaining only a bounded tail."""
        while True:
            chunk = await self._process.stderr.read(self._CHUNK_BYTES)
            if not chunk:
                return
            self._stderr_tail.extend(chunk)
            del self._stderr_tail[: -self._STDERR_TAIL_BYTES]

    async def _finish_stderr_drain(self: object) -> None:
        """Wait out the stderr drain, tolerating a cancelled or already-closed pipe."""
        if self._stderr_drain is None:
            return
        drain, self._stderr_drain = self._stderr_drain, None
        with contextlib.suppress(asyncio.CancelledError, OSError, ValueError):
            await drain

    def __aiter__(self: object) -> DockerExecStream:
        """Iterate this stream's own events."""
        return self

    async def __anext__(self: object) -> DockerStreamEvent:
        """Yield the next stdout chunk, then exactly one terminal `exited` event."""
        if self._finished:
            raise StopAsyncIteration
        self._start_stderr_drain()
        chunk = b""
        if self._process.stdout is not None:
            chunk = await self._process.stdout.read(self._CHUNK_BYTES)
        if chunk:
            return DockerStreamEvent("stdout", data=chunk)
        self._finished = True
        code = await self._process.wait()
        await self._finish_stderr_drain()
        return DockerStreamEvent("exited", code=code)

    async def kill(self: object) -> None:
        """Terminate the `docker exec` client process.

        WARNING: this kills the local client, not the process inside the container. The
        caller (`_route_in_sandbox_async`) removes the container immediately afterwards
        via `stop_quietly`, and that removal is what actually reaps the guest process.
        """
        if self._stderr_drain is not None:
            self._stderr_drain.cancel()
        if self._process.returncode is None:
            self._process.kill()
            await self._process.wait()
        await self._finish_stderr_drain()


class DockerSandbox:
    """A live container satisfying the guest contract `sandbox.py` and the agents drive."""

    def __init__(self: object, container: str, *, restore_owner: str | None = None) -> None:
        """Wrap an already-running container by name.

        Args:
            container: The running container's name.
            restore_owner: `uid:gid` to chown `/workspace` to before teardown, or None to
                skip. Set only on Linux hosts, where rootful Docker would otherwise leave
                root-owned files the host cannot read or delete.
        """
        self._container = container
        self._restore_owner = restore_owner
        self._alive = True

    @property
    def container(self: object) -> str:
        """The name of the container this sandbox drives."""
        return self._container

    @property
    def alive(self: object) -> bool:
        """Whether the container is still believed to be usable."""
        return self._alive

    def _require_alive(self: object) -> None:
        """Fail fast once the container is known to be gone.

        Raises:
            SandboxRuntimeError: If a previous call reaped or lost the container. Each
                later call would otherwise pay a full `docker exec` round trip to learn
                the same thing, and the arm is already recorded as errored.
        """
        if not self._alive:
            raise SandboxRuntimeError(
                f"container `{self._container}` is no longer usable (reaped after a "
                f"timeout, or lost to a control-plane failure)"
            )

    def _exec_args(self: object, *, cwd: str | None, env: dict | None) -> list[str]:
        """Build the leading `docker exec` arguments shared by every guest call.

        `-i` keeps stdin attached so the caller can force EOF on it; without that a
        guest CLI can block forever on an open pipe.
        """
        args = ["exec", "-i", *_env_flags(env)]
        if cwd:
            args += ["-w", cwd]
        return [*args, self._container]

    async def _reap(self: object) -> ContainerRemoval:
        """Remove the container, mark this sandbox dead, and report the removal outcome.

        Killing the local `docker exec` client does NOT stop the process inside the
        container: without this, an agent whose turn timed out keeps running — and keeps
        spending — for the rest of the benchmark. Removing the container reaps it.

        Returns:
            The removal outcome, so a caller building a timeout message can tell a
            confirmed reap from a removal that itself failed (the guest may still be
            running).
        """
        self._alive = False
        return await _remove_container(self._container)

    async def _control_plane_failure(self: object, result: DockerResult) -> str | None:
        """Return an error message when `result` is docker failing, not the guest exiting.

        Known daemon/container stderr shapes classify outright. An otherwise-unexplained
        125/126/127 is ambiguous — `docker exec` reserves those codes, but so does a guest
        shell reporting `command not found` — so the container's running state settles it.
        """
        if result.exit_code == 0:
            return None
        if _CONTROL_PLANE_PATTERN.search(result.stderr):
            return (
                f"docker exec against container `{self._container}` failed: "
                f"{result.stderr.strip()[-500:] or '(no output)'}"
            )
        if result.exit_code not in _AMBIGUOUS_EXIT_CODES:
            return None
        try:
            state = await _docker(
                "inspect",
                "--format",
                "{{.State.Running}}",
                self._container,
                timeout=SYNC_CALL_TIMEOUT_SECONDS,
                description=f"`docker inspect {self._container}`",
            )
        except DockerCallTimeout:
            # A wedged disambiguation inspect means the daemon itself is unresponsive,
            # which is a control-plane failure by definition — not a guest exit code.
            return (
                f"could not determine whether container `{self._container}` is still "
                f"running (docker exec exited {result.exit_code}, and the disambiguating "
                f"`docker inspect` timed out)"
            )
        if state.exit_code == 0 and state.stdout.strip() == "true":
            return None
        return (
            f"container `{self._container}` is not running "
            f"(docker exec exited {result.exit_code})"
        )

    async def _guest_call(
        self: object,
        *args: str,
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> DockerExecOutput:
        """Run one guest command, telling a docker failure apart from a guest exit code.

        Raises:
            SandboxRuntimeError: If the call times out (the container is reaped first) or
                docker itself failed rather than the guest command.
        """
        self._require_alive()
        try:
            result = await _docker(
                *args,
                stdin=stdin,
                timeout=timeout,
                description=f"`docker exec {self._container}`",
            )
        except DockerCallTimeout as error:
            removal = await self._reap()
            if removal.removed or removal.absent:
                reap_note = "the container was removed to reap it"
            else:
                reap_note = (
                    f"container removal FAILED: {removal.error} — the guest may still "
                    f"be running"
                )
            raise SandboxRuntimeError(
                f"guest command in container `{self._container}` timed out after "
                f"{timeout}s; {reap_note}"
            ) from error
        failure = await self._control_plane_failure(result)
        if failure is not None:
            self._alive = False
            raise SandboxRuntimeError(failure)
        return DockerExecOutput(result.exit_code, result.stdout, result.stderr)

    async def shell(
        self: object, script: str, *, env: dict | None = None, cwd: str | None = None
    ) -> DockerExecOutput:
        """Run `script` under `/bin/sh -c` inside the container."""
        return await self._guest_call(
            *self._exec_args(cwd=cwd, env=env), "/bin/sh", "-c", script
        )

    async def exec(
        self: object,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecOutput:
        """Run `command` with `args` directly — no shell, so nothing re-quotes a prompt."""
        return await self._guest_call(
            *self._exec_args(cwd=cwd, env=env),
            command,
            *(args or []),
            stdin=stdin,
            timeout=timeout,
        )

    async def exec_stream(
        self: object,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecStream:
        """Start `command` in the container and stream its stdout incrementally."""
        self._require_alive()
        process = await _docker_stream(
            *self._exec_args(cwd=cwd, env=env),
            command,
            *(args or []),
            stdin=stdin,
            description=f"`docker exec {self._container}`",
        )
        return DockerExecStream(process)

    async def stop(self: object, timeout: float | None = None) -> None:
        """Restore host ownership of the workspace, then remove the container.

        A container is disposable, so removal — not a stop — is the Docker analogue of a
        microVM stop. The chown runs first because it needs the container to still exist:
        the guest ran as root, so under rootful Docker every file it wrote into the
        bind-mounted clean room is root-owned, and the host can then neither gather facts
        from it nor delete the temporary directory holding it.

        Args:
            timeout: Seconds to allow the removal, defaulting to `REMOVE_TIMEOUT_SECONDS`.

        Raises:
            SandboxRuntimeError: If the daemon refused or wedged the removal. Callers that
                must not fail on teardown use `DockerBackend.stop_quietly`.
        """
        if self._restore_owner is not None and self._alive:
            # Best effort: a chown failure must not stop the container from being removed.
            # DockerCallTimeout already subclasses SandboxRuntimeError, so a wedged chown
            # is bounded by `timeout` below and caught here rather than hanging teardown.
            with contextlib.suppress(SandboxRuntimeError, OSError):
                await self.exec(
                    "chown",
                    ["-R", self._restore_owner, GUEST_WORKDIR],
                    timeout=REMOVE_TIMEOUT_SECONDS,
                )
        self._alive = False
        outcome = await _remove_container(
            self._container, timeout=timeout or REMOVE_TIMEOUT_SECONDS
        )
        if outcome.error is not None:
            raise SandboxRuntimeError(outcome.error)


class DockerBackend:
    """The Docker implementation of `SandboxBackend`."""

    id = "docker"

    # A `docker commit`-ed image has no registry digest, so RepoDigests is empty and the
    # local image Id is the only stable identity there is; a pulled base image keeps its
    # registry digest, which is the stronger record when one exists.
    _IMAGE_DIGEST_FORMAT = "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}"

    def preflight(self: object) -> list[str]:
        """Return host-readiness errors: the CLI on PATH and a reachable daemon.

        The Docker-specific remedy lives here, not in the shared preflight, exactly as
        the microsandbox backend owns its Apple-Silicon/KVM remedy.
        """
        if shutil.which(DOCKER_BINARY) is None:
            return [
                "docker CLI not found on PATH — install Docker Engine or Docker Desktop"
            ]
        try:
            info = _docker_sync("info")
        except SandboxRuntimeError as error:
            return [f"docker daemon unreachable: {error}"]
        if info.exit_code != 0:
            return [
                f"docker daemon unreachable (`docker info` exited {info.exit_code}) — "
                f"start Docker and retry: {info.stderr.strip()[-500:]}"
            ]
        return []

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether the committed snapshot image is present in the local image store.

        Raises:
            SandboxRuntimeError: If the inspect fails for any reason other than the image
                being absent — an unreachable daemon above all. Only an
                `_IMAGE_ABSENT_PATTERN` match on stderr reads as "not built yet"; every
                other nonzero exit is raised rather than reported as a cache miss.
        """
        reference = image_ref(name)
        result = _docker_sync(
            "image", "inspect", reference, description=f"`docker image inspect {reference}`"
        )
        if result.exit_code == 0:
            return True
        if _IMAGE_ABSENT_PATTERN.search(result.stderr):
            return False
        raise SandboxRuntimeError(
            f"docker image inspect for `{reference}` exited {result.exit_code}: "
            f"{result.stderr.strip()[-500:] or '(no output)'}"
        )

    def fingerprint_inputs(
        self: object, agent: CodingAgent, env: EnvConfig
    ) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return build_fingerprint_inputs(backend_id=self.id, agent=agent, env=env)

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest

    def image_identity(self: object, snapshot: str) -> ImageIdentity:
        """Return the snapshot image's registry digest when it has one, else its image Id.

        Any failure — the image missing, the daemon gone, no docker binary at all —
        becomes an explained `unavailable` result rather than propagating: a backend
        lookup failure must never abort provenance capture or artifact aggregation.
        """
        try:
            reference = image_ref(snapshot)
            result = _docker_sync(
                "image",
                "inspect",
                reference,
                "--format",
                self._IMAGE_DIGEST_FORMAT,
                description=f"`docker image inspect {reference}`",
            )
        except SandboxRuntimeError as error:
            return ImageIdentity.unavailable(str(error) or "docker image digest read failed")
        if result.exit_code != 0:
            detail = result.stderr.strip()[-500:] or "(no output)"
            return ImageIdentity.unavailable(
                f"docker image inspect exited {result.exit_code}: {detail}"
            )
        digest = result.stdout.strip()
        if not digest:
            return ImageIdentity.unavailable(
                f"docker reported no image digest for `{snapshot}`"
            )
        return ImageIdentity.available(digest)

    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision a container from the base image and commit it as the snapshot image."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(
        self: object, agent: object, name: str, env: EnvConfig
    ) -> None:
        """Provision and seal the reusable snapshot image asynchronously.

        Every failure leaves at the neutral error type, which `__main__` maps to exit 1:
        a build that failed is a finding, not a usage error.

        Raises:
            SandboxRuntimeError: If the name is held by a foreign container, the base image
                cannot start, a provisioning step fails, or the commit fails.
        """
        base_image = env.base_image or BASE_IMAGE
        build_container = build_container_name(agent.id)
        await _replace_container(build_container)
        started = await _docker(
            "run",
            "--detach",
            "--name",
            build_container,
            # Agents install their CLIs under /root and run with bypassPermissions: the
            # sandbox, not the uid, is the containment boundary.
            "--user",
            "0:0",
            *_resource_flags(),
            *_label_flags(),
            # An explicit entrypoint keeps the container alive regardless of what the
            # base image declares, so provisioning has something to exec into.
            "--entrypoint",
            "sleep",
            base_image,
            "infinity",
            description=f"`docker run {base_image}`",
        )
        if started.exit_code != 0:
            # A nonzero `run --detach` can still leave a created container behind.
            await _remove_container(build_container)
            raise SandboxRuntimeError(
                f"docker run failed for base image `{base_image}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        sandbox = DockerSandbox(build_container)
        try:
            try:
                await agent.provision(sandbox)
                await bridge_skills_home(sandbox, agent)
                await run_environment_script(sandbox, agent, env)
            except SandboxRuntimeError:
                raise
            except RuntimeError as error:
                # These three raise bare RuntimeErrors, which __main__ maps to exit 2 (a
                # usage error). A build that genuinely failed is exit 1.
                raise SandboxRuntimeError(
                    f"docker snapshot build failed for `{name}`: {error}"
                ) from error
            committed = await _docker(
                "commit",
                build_container,
                image_ref(name),
                description=f"`docker commit {build_container}`",
            )
            if committed.exit_code != 0:
                raise SandboxRuntimeError(
                    f"docker commit failed for `{name}` (exit {committed.exit_code}): "
                    f"{committed.stderr.strip()[-2000:]}"
                )
        finally:
            # `_remove_container` never raises, so teardown can never mask a build failure.
            await _remove_container(build_container)

    async def create_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_workdir: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> DockerSandbox:
        """Boot a container from the snapshot image for one arm session."""
        volumes = {
            GUEST_WORKDIR: DockerVolume.bind(host_mount_path(host_workdir), readonly=False)
        }
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            volumes[PROJECT_MOUNT] = DockerVolume.bind(
                host_mount_path(host_repo_root), readonly=True
            )
        volumes.update(extra_volumes(agent, DockerVolume))
        return await self._run_container(
            snapshot=snapshot,
            name=name,
            agent=agent,
            volumes=volumes,
            restore_owner=_workspace_restore_owner(),
        )

    async def create_trigger_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> DockerSandbox:
        """Boot the container used for trigger-routing probes.

        Trigger routing is microsandbox-only today; this exists so `DockerBackend`
        satisfies the runtime-checkable `SandboxBackend` protocol in full, and so enabling
        routing under Docker later is a switch rather than a rewrite. There is no
        `/workspace` bind here, so no ownership to restore.
        """
        volumes = {PROJECT_MOUNT: DockerVolume.bind(host_mount_path(host_repo_root), readonly=True)}
        volumes.update(extra_volumes(agent, DockerVolume))
        sandbox = await self._run_container(
            snapshot=snapshot, name=name, agent=agent, volumes=volumes
        )
        try:
            await agent.stage_project_assets(sandbox, PROJECT_MOUNT)
        except BaseException:
            await self.stop_quietly(sandbox)
            raise
        return sandbox

    async def _run_container(
        self: object,
        *,
        snapshot: object,
        name: object,
        agent: object,
        volumes: dict,
        restore_owner: str | None = None,
    ) -> DockerSandbox:
        """Free the container name, then run a fresh container from the snapshot image.

        Raises:
            SandboxRuntimeError: If the name is held by a container harnessbench does not
                own, or the container fails to start.
        """
        container = container_name(str(name))
        # `replace=True` semantics: a name collision left by an earlier aborted cell must
        # not fail this one. Label-gated, so a container harnessbench did not start is
        # reported rather than removed.
        await _replace_container(container)
        reference = image_ref(str(snapshot))
        with _credential_env_file(agent) as credential_flags:
            started = await _docker(
                "run",
                "--detach",
                "--name",
                container,
                # Agents install their CLIs under /root and run with bypassPermissions:
                # the sandbox, not the uid, is the containment boundary.
                "--user",
                "0:0",
                *_resource_flags(),
                *_label_flags(),
                *_volume_flags(volumes),
                *credential_flags,
                "--entrypoint",
                "sleep",
                reference,
                "infinity",
                # Never the argv: it holds the --env-file path.
                description=f"`docker run {reference}`",
            )
        if started.exit_code != 0:
            # A nonzero `run --detach` can still leave a created container behind. This
            # helper never raises, so it cannot mask the failure being reported.
            await _remove_container(container)
            raise SandboxRuntimeError(
                f"docker run failed for snapshot `{snapshot}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        return DockerSandbox(container, restore_owner=restore_owner)

    async def guest_shell(
        self: object, sandbox: object, agent: object, script: str
    ) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        try:
            result = await sandbox.shell(script, env=agent.guest_env())
        except (TimeoutError, SandboxRuntimeError, OSError):
            return None
        return result.stdout_text if result.exit_code == 0 else None

    async def stop_quietly(self: object, sandbox: object) -> None:
        """Best-effort container teardown that never masks the real flow."""
        with contextlib.suppress(SandboxRuntimeError, TimeoutError, OSError):
            await sandbox.stop()

    async def kill_quietly(self: object, handle: object) -> None:
        """Best-effort kill of a streaming exec handle (routing timeout path)."""
        with contextlib.suppress(SandboxRuntimeError, OSError):
            await handle.kill()
