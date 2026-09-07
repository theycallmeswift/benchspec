"""Tests for the Docker guest surface: argv rendering and the real subprocess behavior.

Every test that touches a process drives the `docker` shim from `tests.support.docker`,
so the suite exercises real `asyncio` subprocess plumbing without a Docker daemon.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from benchspec.sandbox import docker
from benchspec.sandbox.errors import SandboxError
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
