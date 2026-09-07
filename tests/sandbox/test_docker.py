"""Tests for the Docker guest surface and the backend lifecycle built on top of it.

Every test that touches a process drives the `docker` shim from `tests.support.docker`,
so the suite exercises real `asyncio` subprocess plumbing without a Docker daemon. Every
test that builds a `DockerBackend` sets `BENCHSPEC_DOCKER_PATH`, so no test can reach a
real daemon even on a developer machine that has one running.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.sandbox import docker
from benchspec.sandbox.errors import SandboxError
from benchspec.sandbox.sandbox import _agent_extra_volumes
from benchspec.specs.discovery import EnvConfig
from tests.support.docker import write_docker_shim


def _docker_sandbox(tmp_path: Path) -> docker.DockerSandbox:
    """Build a `DockerSandbox` whose binary is the shim written under `tmp_path/bin`."""
    return docker.DockerSandbox(
        "benchspec-eval-x-y-main", binary=write_docker_shim(tmp_path / "bin")
    )


async def _stream_events(
    sandbox: docker.DockerSandbox, cmd: str, args: list[str] | None = None
) -> list[docker.DockerExecEvent]:
    """Start a streaming exec and drain every event it yields."""
    handle = await sandbox.exec_stream(cmd, args)
    return [event async for event in handle]


def test_exec_argv_renders_workdir_env_and_interactive_flags() -> None:
    """Verify the exec argv carries -i, -w, and every -e in insertion order before the command."""
    argv = docker.exec_argv(
        "benchspec-eval-hello-trial-gw0",
        "claude",
        ["-p", "hi"],
        cwd="/workspace",
        env={"HOME": "/root", "TZ": "UTC"},
        interactive=True,
    )

    assert argv == [
        "exec", "-i", "-w", "/workspace", "-e", "HOME=/root", "-e", "TZ=UTC",
        "benchspec-eval-hello-trial-gw0", "claude", "-p", "hi",
    ]


def test_shell_argv_wraps_the_script_in_bash() -> None:
    """Verify a shell script runs as `bash -c` without -i."""
    argv = docker.shell_argv("benchspec-build-claude-code", "set -e\necho hi", cwd=None, env=None)

    assert argv == ["exec", "benchspec-build-claude-code", "bash", "-c", "set -e\necho hi"]


def test_docker_mount_flag_marks_readonly() -> None:
    """Verify a read-only bind renders with the :ro suffix and a writable one without."""
    assert docker.DockerVolume.bind("/tmp/stage", readonly=True).flag("/project") == (
        "/tmp/stage:/project:ro"
    )
    assert docker.DockerVolume.bind("/tmp/room").flag("/workspace") == "/tmp/room:/workspace"


def test_docker_binary_prefers_the_env_override(monkeypatch: object, tmp_path: object) -> None:
    """Verify BENCHSPEC_DOCKER_PATH wins over PATH lookup."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "docker"))

    assert docker.docker_binary() == tmp_path / "docker"


def test_exec_returns_the_exit_code_stdout_and_stderr(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify exec reports the guest command's exit code and both of its streams."""
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "printf out; printf err >&2; exit 3")
    sandbox = _docker_sandbox(tmp_path)

    result = asyncio.run(sandbox.exec("greet", ["--loud"]))

    assert result.exit_code == 3
    assert result.stdout_text == "out"
    assert result.stderr_text == "err"


def test_exec_feeds_stdin_bytes_and_closes_the_pipe(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify stdin bytes reach the command, which sees EOF, and that -i is rendered."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "cat")
    sandbox = _docker_sandbox(tmp_path)

    result = asyncio.run(sandbox.exec("cat", stdin=b"payload"))

    assert result.stdout_text == "payload"
    assert " -i " in command_log.read_text(encoding="utf-8")


def test_exec_without_stdin_renders_no_interactive_flag(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify a command with no stdin runs without -i."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    sandbox = _docker_sandbox(tmp_path)

    asyncio.run(sandbox.exec("greet", ["--loud"]))

    assert command_log.read_text(encoding="utf-8").strip() == (
        "exec benchspec-eval-x-y-main greet --loud"
    )


def test_exec_raises_timeout_error_when_the_command_outlives_the_budget(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify a command that runs past `timeout` is killed and reported as a timeout."""
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "sleep 5")
    sandbox = _docker_sandbox(tmp_path)

    with pytest.raises(TimeoutError, match=r"docker exec of sleep timed out after 0\.2s"):
        asyncio.run(sandbox.exec("sleep", ["5"], timeout=0.2))


def test_exec_raises_sandbox_error_on_a_daemon_failure_marker(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify a daemon-side failure on stderr becomes a SandboxError, not a plain result."""
    monkeypatch.setenv(
        "BENCHSPEC_SHIM_EXEC",
        'echo "Error response from daemon: container x is not running" >&2; exit 1',
    )
    sandbox = _docker_sandbox(tmp_path)

    with pytest.raises(SandboxError, match="container x is not running"):
        asyncio.run(sandbox.exec("greet"))


def test_shell_runs_the_script_under_bash(monkeypatch: object, tmp_path: object) -> None:
    """Verify shell wraps the script in `bash -c` and returns its output."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "echo hi")
    sandbox = _docker_sandbox(tmp_path)

    result = asyncio.run(sandbox.shell("echo hi"))

    assert command_log.read_text(encoding="utf-8").strip().endswith("bash -c echo hi")
    assert result.stdout_text == "hi\n"


def test_exec_stream_yields_stdout_chunks_then_an_exited_event(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify streaming yields the command's stdout and a terminal exited event."""
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", 'printf "a\\nb\\n"; exit 0')
    sandbox = _docker_sandbox(tmp_path)

    events = asyncio.run(_stream_events(sandbox, "emit"))

    streamed = b"".join(event.data for event in events if event.event_type == "stdout")
    assert streamed == b"a\nb\n"
    assert (events[-1].event_type, events[-1].code) == ("exited", 0)


def test_exec_stream_reports_failed_when_stderr_carries_a_daemon_marker(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify a daemon-side failure ends the stream with a failed event, not exited."""
    monkeypatch.setenv(
        "BENCHSPEC_SHIM_EXEC",
        'echo "Cannot connect to the Docker daemon" >&2; exit 1',
    )
    sandbox = _docker_sandbox(tmp_path)

    events = asyncio.run(_stream_events(sandbox, "emit"))

    assert (events[-1].event_type, events[-1].code) == ("failed", 1)


def test_exec_stream_kill_ends_a_long_running_command(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify kill tears down a command that would otherwise run for seconds."""
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "sleep 5")
    sandbox = _docker_sandbox(tmp_path)

    async def _kill_then_drain() -> list[docker.DockerExecEvent]:
        """Start the long command, kill it, and drain the stream to its terminal event."""
        handle = await sandbox.exec_stream("sleep", ["5"])
        await asyncio.sleep(0.1)
        await handle.kill()
        return [event async for event in handle]

    started = time.monotonic()
    events = asyncio.run(_kill_then_drain())
    elapsed = time.monotonic() - started

    assert elapsed < 3
    assert events[-1].event_type == "exited"
    assert events[-1].code != 0


def test_stop_removes_the_container_ignoring_a_failing_remove(
    monkeypatch: object, tmp_path: object
) -> None:
    """Verify stop runs `rm -f <name>` and swallows a nonzero exit from it."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    monkeypatch.setenv("BENCHSPEC_SHIM_RM_EXIT", "1")
    sandbox = _docker_sandbox(tmp_path)

    asyncio.run(sandbox.stop())

    assert command_log.read_text(encoding="utf-8").strip() == "rm -f benchspec-eval-x-y-main"


def _docker_backend(tmp_path: Path, monkeypatch: object) -> docker.DockerBackend:
    """Build a `DockerBackend` whose `docker` CLI is the shim written under `tmp_path/bin`."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(write_docker_shim(tmp_path / "bin")))
    return docker.DockerBackend()


class _ProbeAgent:
    """A minimal agent whose provisioning is a single guest shell command."""

    id = "probe"

    def guest_env(self) -> dict:
        """Return no guest environment variables."""
        return {}

    def bridge_skills_home_script(self) -> str:
        """Return a no-op skills-home bridge script."""
        return "true"

    async def provision(self, sandbox: object) -> None:
        """Install the probe by running one command in the guest."""
        result = await sandbox.shell("echo provisioned")
        if result.exit_code != 0:
            raise RuntimeError(f"probe provision failed (exit {result.exit_code})")


def test_run_argv_renders_resource_flags_mounts_env_then_the_idle_command() -> None:
    """Verify `run -d` carries the resource flags, every mount, every env, then `sleep infinity`."""
    argv = docker.run_argv(
        "benchspec-eval-hello-trial-main",
        "benchspec-snapshot:snap",
        mounts={
            "/workspace": docker.DockerVolume.bind("/tmp/room"),
            "/project": docker.DockerVolume.bind("/tmp/stage", readonly=True),
        },
        env={"ANTHROPIC_API_KEY": "test-token"},
    )

    assert argv == [
        "run", "-d", "--name", "benchspec-eval-hello-trial-main",
        "--cpus", "2", "--memory", "2048m",
        "-v", "/tmp/room:/workspace",
        "-v", "/tmp/stage:/project:ro",
        "-e", "ANTHROPIC_API_KEY=test-token",
        "benchspec-snapshot:snap", "sleep", "infinity",
    ]


def test_image_ref_tags_the_snapshot_under_the_benchspec_repository() -> None:
    """Verify a snapshot name becomes a tag under the single benchspec image repository."""
    assert docker.image_ref("benchspec-docker-claude-code-1.2.3-ab12cd34") == (
        "benchspec-snapshot:benchspec-docker-claude-code-1.2.3-ab12cd34"
    )


def test_preflight_reports_the_remedy_when_the_docker_cli_is_missing(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a `BENCHSPEC_DOCKER_PATH` pointing at nothing yields the install remedy."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    backend = docker.DockerBackend()

    errors = backend.preflight()

    assert errors == [
        "docker CLI not found — install Docker Engine or Docker Desktop, "
        "or set BENCHSPEC_DOCKER_PATH to the binary"
    ]


def test_preflight_reports_an_unreachable_daemon_when_docker_info_fails(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a failing `docker info` yields the daemon remedy carrying the CLI's own stderr."""
    monkeypatch.setenv("BENCHSPEC_SHIM_INFO_EXIT", "1")
    backend = _docker_backend(tmp_path, monkeypatch)

    errors = backend.preflight()

    assert len(errors) == 1
    assert "docker info" in errors[0]
    assert "Cannot connect to the Docker daemon" in errors[0]
    assert errors[0].endswith("start Docker Desktop or the docker service")


def test_preflight_is_clean_when_the_daemon_answers(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a healthy daemon yields no preflight errors and no platform gate."""
    backend = _docker_backend(tmp_path, monkeypatch)

    assert backend.preflight() == []


def test_snapshot_exists_true_when_image_inspect_succeeds(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a snapshot whose image the daemon knows reports as present."""
    monkeypatch.setenv("BENCHSPEC_SHIM_INSPECT_EXIT", "0")
    backend = _docker_backend(tmp_path, monkeypatch)

    assert backend.snapshot_exists("benchspec-docker-claude-code-1.2.3-ab12cd34") is True


def test_snapshot_exists_false_when_image_inspect_fails(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a snapshot the daemon has no image for reports as absent."""
    monkeypatch.setenv("BENCHSPEC_SHIM_INSPECT_EXIT", "1")
    backend = _docker_backend(tmp_path, monkeypatch)

    assert backend.snapshot_exists("benchspec-docker-claude-code-1.2.3-ab12cd34") is False


def test_snapshot_exists_false_when_the_docker_cli_is_missing(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a host with no docker CLI reports every snapshot as absent instead of raising."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "missing" / "docker"))
    backend = docker.DockerBackend()

    assert backend.snapshot_exists("benchspec-docker-claude-code-1.2.3-ab12cd34") is False


def test_image_identity_reads_the_image_id_from_inspect(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify the image id `docker image inspect --format` prints becomes the digest."""
    backend = _docker_backend(tmp_path, monkeypatch)

    identity = backend.image_identity("benchspec-docker-claude-code-1.2.3-ab12cd34")

    assert identity.image_digest == "sha256:deadbeef"
    assert identity.image_digest_status == "available"


def test_image_identity_unavailable_when_inspect_fails(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a failing inspect explains itself instead of raising or reporting a null digest."""
    monkeypatch.setenv("BENCHSPEC_SHIM_INSPECT_EXIT", "1")
    monkeypatch.setenv("BENCHSPEC_SHIM_INSPECT_FORMAT_EXIT", "1")
    backend = _docker_backend(tmp_path, monkeypatch)

    identity = backend.image_identity("missing-snapshot")

    assert identity.image_digest is None
    assert identity.image_digest_status == "unavailable"
    assert "No such image" in identity.image_digest_error


def test_build_snapshot_provisions_the_build_container_then_commits_and_removes_it(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a build replaces the build container, provisions it, commits it, then removes it."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    backend = _docker_backend(tmp_path, monkeypatch)

    backend.build_snapshot(_ProbeAgent(), "snap", EnvConfig())

    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "rm -f benchspec-build-probe",
        "run -d --name benchspec-build-probe --cpus 2 --memory 2048m "
        "ubuntu:latest sleep infinity",
        "exec benchspec-build-probe bash -c echo provisioned",
        "exec benchspec-build-probe bash -c true",
        "commit benchspec-build-probe benchspec-snapshot:snap",
        "rm -f benchspec-build-probe",
    ]


def test_build_snapshot_removes_the_build_container_when_provisioning_fails(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a failed provision still tears the build container down instead of leaking it."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "exit 1")
    backend = _docker_backend(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="probe provision failed"):
        backend.build_snapshot(_ProbeAgent(), "snap", EnvConfig())

    logged = command_log.read_text(encoding="utf-8").splitlines()
    assert logged[-1] == "rm -f benchspec-build-probe"
    assert "commit benchspec-build-probe benchspec-snapshot:snap" not in logged


def test_create_sandbox_runs_the_snapshot_image_with_both_mounts_and_the_credential(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify an arm container binds the room rw, the project ro, and carries the credential."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    room = tmp_path / "room"
    room.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    backend = _docker_backend(tmp_path, monkeypatch)

    created = asyncio.run(
        backend.create_sandbox(
            agent=ClaudeCodeAgent(auth_value="test-token", version="1.2.3"),
            snapshot="snap",
            name="benchspec-eval-hello-trial-main",
            host_workdir=room,
            host_repo_root=stage,
            extra_volumes=_agent_extra_volumes,
        )
    )

    assert created.name == "benchspec-eval-hello-trial-main"
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "rm -f benchspec-eval-hello-trial-main",
        "run -d --name benchspec-eval-hello-trial-main --cpus 2 --memory 2048m "
        f"-v {room.resolve()}:/workspace -v {stage.resolve()}:/project:ro "
        "-e ANTHROPIC_API_KEY=test-token benchspec-snapshot:snap sleep infinity",
    ]


def test_create_trigger_sandbox_mounts_only_the_project_and_stages_assets(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a trigger container binds only /project read-only and stages the agent's assets."""
    command_log = tmp_path / "commands.log"
    monkeypatch.setenv("BENCHSPEC_DOCKER_COMMAND_LOG", str(command_log))
    stage = tmp_path / "stage"
    stage.mkdir()
    staged_into: list[str] = []
    backend = _docker_backend(tmp_path, monkeypatch)

    class _StagingAgent(_ProbeAgent):
        """A probe agent that records where its project assets were staged from."""

        def secrets(self) -> list:
            """Return no credentials."""
            return []

        async def stage_project_assets(self, sandbox: object, project_mount: str) -> None:
            """Record the mount the assets were staged from."""
            staged_into.append(project_mount)

    created = asyncio.run(
        backend.create_trigger_sandbox(
            agent=_StagingAgent(),
            snapshot="snap",
            name="benchspec-trigger-main",
            host_repo_root=stage,
            extra_volumes=_agent_extra_volumes,
        )
    )

    assert created.name == "benchspec-trigger-main"
    assert staged_into == ["/project"]
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "rm -f benchspec-trigger-main",
        "run -d --name benchspec-trigger-main --cpus 2 --memory 2048m "
        f"-v {stage.resolve()}:/project:ro benchspec-snapshot:snap sleep infinity",
    ]


def test_exec_stream_feeds_a_large_stdin_payload_without_deadlocking(
    monkeypatch: object, tmp_path: Path
) -> None:
    """Verify a payload larger than a pipe buffer streams through instead of deadlocking.

    The stdout pump must already be running while stdin is written: a guest command that
    echoes back more than one pipe buffer would otherwise block on its own stdout while
    benchspec blocks writing stdin.
    """
    monkeypatch.setenv("BENCHSPEC_SHIM_EXEC", "cat")
    sandbox = _docker_sandbox(tmp_path)
    payload = b"x" * (1024 * 1024)

    async def _echo_back() -> bytes:
        """Stream the payload through the guest and collect everything it echoes."""
        handle = await sandbox.exec_stream("cat", stdin=payload)
        chunks = [event.data async for event in handle if event.event_type == "stdout"]
        return b"".join(chunks)

    echoed = asyncio.run(asyncio.wait_for(_echo_back(), timeout=30))

    assert echoed == payload
