"""Real-daemon lifecycle test for the Docker backend.

Skipped with an explicit reason unless `docker info` succeeds. Everything it creates
carries a unique suffix and is removed by name in teardown; it never calls `prune()`,
which would sweep the developer's own benchspec snapshots.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from benchspec.sandbox.backend import NAME_PREFIX
from benchspec.sandbox.docker import DockerBackend, docker_binary, image_ref
from benchspec.sandbox.sandbox import _agent_extra_volumes
from benchspec.specs.discovery import EnvConfig

# Budget for every `docker` command this module runs itself — the reachability probe and
# the teardown. Long enough for a daemon that just woke up, short enough that a wedged one
# fails the command rather than hanging the suite.
_DOCKER_TIMEOUT_SECONDS = 30


def _daemon_reachable() -> bool:
    """Return whether a `docker` CLI is installed and its daemon answers `docker info`.

    Every failure mode — no CLI, an unexecutable one, a timeout, a nonzero exit — reads
    the same as "unreachable", because the only thing the gate decides is whether this
    module's one test can run at all.
    """
    binary = docker_binary()
    if binary is None:
        return False

    try:
        completed = subprocess.run(
            [str(binary), "info"],
            capture_output=True,
            check=False,
            timeout=_DOCKER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return completed.returncode == 0


pytestmark = pytest.mark.skipif(
    not _daemon_reachable(), reason="Docker daemon unreachable (docker info failed)"
)


class _ProbeAgent:
    """A minimal agent that provisions by writing one marker file into the guest.

    Deliberately needs no agent CLI and no provider credential: the point of the daemon
    test is the backend's container lifecycle, not any real harness's installer.
    """

    guest_home = "/root"

    def __init__(self, suffix: str) -> None:
        """Give the agent an id unique to one test run.

        Args:
            suffix: The run's unique suffix, so the build container this agent's id names
                cannot collide with another run's.
        """
        self.id = f"probe-{suffix}"

    def guest_env(self) -> dict:
        """Return no guest environment variables."""
        return {}

    def secrets(self) -> list:
        """Return no credentials to hand the container."""
        return []

    def bridge_skills_home_script(self) -> str:
        """Return a no-op skills-home bridge script."""
        return "true"

    async def provision(self, sandbox: object) -> None:
        """Write the provisioning marker into the guest.

        Args:
            sandbox: The live build guest.

        Raises:
            RuntimeError: If the marker could not be written.
        """
        result = await sandbox.shell("echo provisioned > /provisioned")
        if result.exit_code != 0:
            raise RuntimeError(f"probe provision failed (exit {result.exit_code})")

    async def stage_project_assets(self, sandbox: object, project_mount: str) -> None:
        """Stage nothing: the probe has no project assets to copy into the guest."""


def _docker_command(*argv: str) -> None:
    """Run one teardown `docker` command, ignoring whatever it reports.

    Teardown runs on the error path too, where a resource that is already gone is exactly
    the wanted state and raising would mask the failure that led here.

    Args:
        *argv: The arguments to pass the `docker` CLI.
    """
    binary = docker_binary()
    if binary is None:
        return

    subprocess.run(
        [str(binary), *argv],
        capture_output=True,
        check=False,
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )


def _remove_quietly(*argv: str) -> None:
    """Run one teardown `docker` command, swallowing a hang as well as a failure.

    Teardown must attempt every removal it owns and must never let one command's
    trouble stand in for the test's own failure: `_docker_command` already treats a
    nonzero exit as fine, but a wedged daemon raises `TimeoutExpired` (or, for a
    missing/unexecutable binary, `OSError`) instead of returning. Left uncaught inside
    a `finally` block, that exception would replace whatever the `try` block was
    raising and would stop the remaining teardown commands from running at all.

    Args:
        *argv: The arguments to pass the `docker` CLI.
    """
    try:
        _docker_command(*argv)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _container_names(name: str) -> str:
    """Return the daemon's own listing of containers matching `name`, however they exit."""
    binary = docker_binary()
    completed = subprocess.run(
        [str(binary), "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )
    return completed.stdout.strip()


async def _drive_cell(
    backend: DockerBackend, agent: _ProbeAgent, snapshot: str, cell: str, room: Path, stage: Path
) -> None:
    """Boot a cell from the snapshot, exercise its guest surface, and stop it.

    Args:
        backend: The backend under test.
        agent: The agent whose secrets and volumes the cell carries.
        snapshot: The snapshot name the cell runs.
        cell: The container name for the cell.
        room: The host workdir bound read-write at `/workspace`.
        stage: The host project root bound read-only at `/project`.

    Raises:
        AssertionError: If any part of the guest surface misbehaves.
    """
    sandbox = await backend.create_sandbox(
        agent=agent,
        snapshot=snapshot,
        name=cell,
        host_workdir=room,
        host_repo_root=stage,
        extra_volumes=_agent_extra_volumes,
    )

    copied = await sandbox.shell("cat /provisioned && cat /project/marker.txt > /workspace/out.txt")
    assert copied.exit_code == 0, copied.stderr_text
    assert "provisioned" in copied.stdout_text
    assert (room / "out.txt").read_text(encoding="utf-8") == "from the project\n"

    handle = await sandbox.exec_stream("printf", ["a\nb\n"])
    events = [event async for event in handle]
    stdout = b"".join(event.data for event in events if event.event_type == "stdout")
    assert stdout == b"a\nb\n"
    assert events[-1].event_type == "exited"
    assert events[-1].code == 0

    readonly = await sandbox.shell("touch /project/nope")
    assert readonly.exit_code != 0

    await sandbox.stop()


def test_docker_backend_builds_boots_and_tears_down_a_cell(tmp_path: Path) -> None:
    """Verify a real daemon builds a snapshot, boots a cell from it, and tears it down."""
    suffix = uuid4().hex[:8]
    agent = _ProbeAgent(suffix)
    snapshot = f"{NAME_PREFIX}docker-probe-test-{suffix}"
    cell = f"{NAME_PREFIX}eval-probe-test-{suffix}"
    room = tmp_path / "room"
    room.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "marker.txt").write_text("from the project\n", encoding="utf-8")
    backend = DockerBackend()

    try:
        backend.build_snapshot(agent, snapshot, EnvConfig(base_image="ubuntu:latest"))

        assert backend.snapshot_exists(snapshot)
        assert backend.image_identity(snapshot).image_digest.startswith("sha256:")
        asyncio.run(_drive_cell(backend, agent, snapshot, cell, room, stage))
        assert _container_names(cell) == ""
    finally:
        _remove_quietly("rm", "-f", cell)
        _remove_quietly("rm", "-f", f"{NAME_PREFIX}build-{agent.id}")
        _remove_quietly("rmi", "-f", image_ref(snapshot))
