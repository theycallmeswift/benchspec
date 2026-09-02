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
import hashlib
import re
import subprocess
import uuid
from dataclasses import dataclass

from harnessbench.sandbox.errors import SandboxRuntimeError
from harnessbench.sandbox.primitives import VM_CPUS, VM_MEMORY_MIB

DOCKER_BINARY = "docker"
# Docker container names match [a-zA-Z0-9][a-zA-Z0-9_.-]*; everything else is replaced.
_ILLEGAL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
# Docker repository names match [a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*; everything else is replaced.
_ILLEGAL_IMAGE_CHARS = re.compile(r"[^a-z0-9._-]")
_REPEATED_SEPARATORS = re.compile(r"[-._]{2,}")
# A blocking `docker` call (preflight, image inspect) must not hang a collection-time check.
SYNC_CALL_TIMEOUT_SECONDS = 30.0

OWNER_LABEL = "harnessbench.owner"
OWNER_LABEL_VALUE = "harnessbench"
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
