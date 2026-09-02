"""Behavior tests for the Docker backend against a REAL daemon.

Every test here shells out to `docker`, so the whole module skips — with the actual
preflight error as the reason — when no daemon answers. `make test` therefore stays green
on a host without Docker while still proving the backend end to end where one exists.
Unit coverage with the CLI faked lives in `test_docker.py`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

from harnessbench.sandbox.docker import (
    OWNER_LABEL,
    OWNER_LABEL_VALUE,
    RUN_NONCE,
    DockerBackend,
    DockerSandbox,
    SandboxRuntimeError,
    _docker,
    _label_flags,
    image_ref,
)
from harnessbench.sandbox.primitives import BASE_IMAGE

_PREFLIGHT_ERRORS = DockerBackend().preflight()

pytestmark = pytest.mark.skipif(
    bool(_PREFLIGHT_ERRORS),
    reason=f"docker daemon not reachable: {'; '.join(_PREFLIGHT_ERRORS)}",
)


def _unique(prefix: str) -> str:
    """Return a collision-proof container or image name for one test."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _start_container(name: str, *, volumes: list | None = None) -> DockerSandbox:
    """Run a detached, labelled base-image container and return a sandbox wrapping it.

    The labels match what the backend itself applies, so the leak check at the end of this
    module sees these containers exactly as it would see a real run's.
    """
    started = await _docker(
        "run",
        "--detach",
        "--name",
        name,
        "--user",
        "0:0",
        *_label_flags(),
        *(volumes or []),
        "--entrypoint",
        "sleep",
        BASE_IMAGE,
        "infinity",
    )
    assert started.exit_code == 0, started.stderr
    return DockerSandbox(name)


def test_shell_and_exec_run_inside_a_real_container() -> None:
    """A real container answers both guest primitives with the contract's field names."""
    container = _unique("harnessbench-test")

    async def exercise() -> tuple:
        """Boot, run one shell and one exec, then tear down."""
        sandbox = await _start_container(container)
        try:
            shelled = await sandbox.shell("echo shelled", env={"TZ": "UTC"}, cwd="/tmp")
            execed = await sandbox.exec("printenv", ["TZ"], env={"TZ": "UTC"}, stdin=b"")
            failed = await sandbox.shell("exit 3")
            return shelled, execed, failed
        finally:
            await sandbox.stop()

    shelled, execed, failed = asyncio.run(exercise())

    assert (shelled.exit_code, shelled.stdout_text.strip()) == (0, "shelled")
    assert (execed.exit_code, execed.stdout_text.strip()) == (0, "UTC")
    assert failed.exit_code == 3


def test_exec_stream_yields_output_then_the_real_exit_code() -> None:
    """Streaming reassembles stdout chunks and ends with the guest process's exit code."""
    container = _unique("harnessbench-test")

    async def exercise() -> list:
        """Boot, stream a multi-line command, then tear down."""
        sandbox = await _start_container(container)
        try:
            handle = await sandbox.exec_stream(
                "/bin/sh", ["-c", "echo one; echo two; exit 5"], stdin=b""
            )
            return [event async for event in handle]
        finally:
            await sandbox.stop()

    events = asyncio.run(exercise())

    streamed = b"".join(event.data for event in events if event.event_type == "stdout")
    assert streamed.decode().splitlines() == ["one", "two"]
    assert events[-1].event_type == "exited"
    assert events[-1].code == 5


def test_stop_removes_the_container_from_the_daemon() -> None:
    """After stop, the container is gone — no leaked containers between cells."""
    container = _unique("harnessbench-test")

    async def exercise() -> int:
        """Boot, stop, then ask the daemon whether the container still exists."""
        sandbox = await _start_container(container)
        await sandbox.stop()
        return (await _docker("container", "inspect", container)).exit_code

    assert asyncio.run(exercise()) != 0


def test_commit_makes_the_snapshot_exist_with_an_available_image_identity() -> None:
    """A committed container is a snapshot: it inspects clean and yields a real digest."""
    container = _unique("harnessbench-test")
    snapshot = _unique("harnessbench-docker-test")
    backend = DockerBackend()

    async def exercise() -> None:
        """Boot, commit as the snapshot image, then remove the container."""
        sandbox = await _start_container(container)
        try:
            committed = await _docker("commit", container, image_ref(snapshot))
            assert committed.exit_code == 0, committed.stderr
        finally:
            await sandbox.stop()

    asyncio.run(exercise())
    try:
        identity = backend.image_identity(snapshot)

        assert backend.snapshot_exists(snapshot) is True
        assert identity.image_digest_status == "available"
        # Not asserting a leading "sha256:": a containerd-snapshotter daemon (Docker
        # Desktop's default) populates RepoDigests for a purely local commit too, so the
        # stronger record is `<local-tag>@sha256:...` rather than the bare image Id.
        assert "sha256:" in identity.image_digest
    finally:
        asyncio.run(_docker("image", "rm", "-f", image_ref(snapshot)))


def test_snapshot_exists_is_false_for_an_image_that_was_never_built() -> None:
    """A name the daemon has never seen reports absent, not an error."""
    assert DockerBackend().snapshot_exists(_unique("harnessbench-docker-absent")) is False


def test_a_container_removed_mid_exec_raises_a_sandbox_runtime_error() -> None:
    """Killing the container under a running exec is infra failure, not a graded miss.

    This is the killed-mid-turn path: an operator runs `docker rm -f`, or the daemon
    restarts, while an agent's turn is in flight. `docker exec` returns a nonzero code that
    looks exactly like a guest exit code, so only the classification makes this an errored
    arm rather than a silently failed eval.
    """
    container = _unique("harnessbench-test")

    async def exercise() -> None:
        """Start a long exec, remove the container underneath it, and see what surfaces."""
        sandbox = await _start_container(container)
        try:
            turn = asyncio.ensure_future(sandbox.exec("/bin/sh", ["-c", "sleep 30"], stdin=b""))
            await asyncio.sleep(1)
            await _docker("rm", "-f", container)
            await turn
        finally:
            await _docker("rm", "-f", container)

    with pytest.raises(SandboxRuntimeError):
        asyncio.run(exercise())


def test_exec_stream_survives_stderr_larger_than_a_real_pipe_buffer() -> None:
    """A guest flooding stderr through a REAL pipe must not stall the stdout stream.

    The unit suite proves the drain task exists; only a real 64 KiB pipe proves it prevents
    the deadlock. Without a concurrent stderr drain this test hangs rather than fails.
    """
    container = _unique("harnessbench-test")

    async def exercise() -> list:
        """Stream a command that writes a megabyte to stderr and a line to stdout."""
        sandbox = await _start_container(container)
        try:
            handle = await sandbox.exec_stream(
                "/bin/sh",
                ["-c", "head -c 1000000 /dev/zero | tr '\\0' 'E' >&2; echo done"],
                stdin=b"",
            )
            return [event async for event in handle]
        finally:
            await sandbox.stop()

    events = asyncio.run(asyncio.wait_for(exercise(), timeout=60))

    streamed = b"".join(event.data for event in events if event.event_type == "stdout")
    assert streamed.decode().strip() == "done"
    assert events[-1].code == 0


@pytest.mark.skipif(sys.platform != "linux", reason="rootful bind ownership is Linux-only")
def test_stop_hands_workspace_files_back_to_the_host_user(tmp_path: object) -> None:
    """After a root-running container stops, the host still owns its own clean room.

    Under rootful Docker on Linux, files the guest writes into the bind mount land as
    root-owned. The host then cannot read facts out of the workspace, and the
    TemporaryDirectory holding it fails to delete — so `stop()` chowns them back first.
    Covers a plain file AND a root-only 0600 file inside a root-only 0700 directory: the
    `-R` chown runs as root inside the guest, so it must reach both regardless of the
    restrictive modes that would block a host-side walk from even seeing them.
    """
    container = _unique("harnessbench-test")
    workspace = tmp_path / "workdir"
    workspace.mkdir()

    async def exercise() -> None:
        """Write a plain file and a root-only file/dir as root, then stop the session."""
        sandbox = await _start_container(
            container, volumes=["-v", f"{workspace.resolve()}:/workspace"]
        )
        sandbox._restore_owner = f"{os.getuid()}:{os.getgid()}"
        written = await sandbox.shell("echo hi > /workspace/authored.txt")
        assert written.exit_code == 0, written.stderr_text
        locked_down = await sandbox.shell(
            "mkdir -m 700 /workspace/private && "
            "echo secret > /workspace/private/secret.txt && "
            "chmod 600 /workspace/private/secret.txt"
        )
        assert locked_down.exit_code == 0, locked_down.stderr_text
        await sandbox.stop()

    asyncio.run(exercise())

    assert (workspace / "authored.txt").read_text(encoding="utf-8").strip() == "hi"
    assert (workspace / "authored.txt").stat().st_uid == os.getuid()

    private_dir = workspace / "private"
    secret_file = private_dir / "secret.txt"
    assert private_dir.stat().st_uid == os.getuid()
    assert private_dir.stat().st_mode & 0o777 == 0o700
    assert secret_file.stat().st_uid == os.getuid()
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert secret_file.read_text(encoding="utf-8").strip() == "secret"


def test_the_suite_leaked_no_harnessbench_containers() -> None:
    """Last test in the module: every container this suite started is gone.

    A leaked container holds its bind mounts, its memory reservation, and its name. Run
    last so it observes the whole module's teardown, and scoped to THIS process's run
    nonce so a concurrent harnessbench run on the same host cannot fail it.
    """
    listed = asyncio.run(
        _docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label={OWNER_LABEL}={OWNER_LABEL_VALUE}",
            "--filter",
            f"label=harnessbench.run={RUN_NONCE}",
        )
    )

    assert listed.exit_code == 0, listed.stderr
    assert listed.stdout.strip() == ""
