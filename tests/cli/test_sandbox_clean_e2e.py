"""End-to-end tests for `benchspec sandbox:clean`.

Each test runs the real console entry point in a subprocess against a throwaway `HOME`
seeded with leaked sandboxes and snapshots, so the CLI, dispatch, and `cli_clean`'s
pruning all execute for real, across BOTH registered backends. The runtime substitutions
are the `msb` and `docker` CLIs: `MSB_PATH` points at a shell shim that records every
invocation and removes the sandbox or snapshot directory the way the real binary does,
and `BENCHSPEC_DOCKER_PATH` points at `tests.support.docker`'s shim, which answers `ps`
and `images` from `BENCHSPEC_SHIM_CONTAINERS`/`BENCHSPEC_SHIM_IMAGES`. Neither shim ever
touches the developer's own `~/.microsandbox` or a real Docker daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from tests.support.cli import run_benchspec
from tests.support.docker import write_docker_shim


def _write_msb_shim(shim_dir: Path) -> Path:
    """Write an `msb` stand-in that logs its argv and deletes what `rm` would delete."""
    shim = shim_dir / "msb"
    shim.write_text(dedent("""\
        #!/bin/sh
        printf "%s\\n" "$*" >> "$BENCHSPEC_COMMAND_LOG"
        case "$1 $2" in
          "rm -f") rm -rf "$HOME/.microsandbox/sandboxes/$3" ;;
          "snapshot rm") rm -rf "$HOME/.microsandbox/snapshots/$4" ;;
        esac
    """))
    shim.chmod(0o755)
    return shim


def _run_sandbox_clean(
    tmp_path: Path,
    home: Path,
    repo_root: Path,
    *,
    containers: str = "",
    images: str = "",
) -> subprocess.CompletedProcess:
    """Run the real `benchspec sandbox:clean <repo_root>` with every runtime redirected.

    Args:
        tmp_path: The test's scratch directory, holding the shims and both command logs.
        home: The throwaway `HOME` the microsandbox shim reads and writes under.
        repo_root: The repo root passed to `sandbox:clean`.
        containers: Newline-separated container names the docker shim's `ps` prints.
        images: Newline-separated image references the docker shim's `images` prints.

    Returns:
        The finished subprocess, with `returncode`, `stdout`, and `stderr`.
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    msb_shim = _write_msb_shim(shim_dir)
    docker_shim = write_docker_shim(shim_dir)

    return run_benchspec(
        "sandbox:clean",
        str(repo_root),
        cwd=tmp_path,
        env={
            "HOME": str(home),
            "MSB_PATH": str(msb_shim),
            "BENCHSPEC_COMMAND_LOG": str(tmp_path / "commands.log"),
            "BENCHSPEC_DOCKER_PATH": str(docker_shim),
            "BENCHSPEC_DOCKER_COMMAND_LOG": str(tmp_path / "docker-commands.log"),
            "BENCHSPEC_SHIM_CONTAINERS": containers,
            "BENCHSPEC_SHIM_IMAGES": images,
        },
    )


def test_sandbox_clean_removes_leaked_sandboxes_snapshots_and_locks(tmp_path: Path) -> None:
    """Verify the real command prunes every `benchspec-*` entry and leaves the rest alone."""
    home = tmp_path / "home"
    sandboxes = home / ".microsandbox" / "sandboxes"
    snapshots = home / ".microsandbox" / "snapshots"
    for leaked_sandbox in (
        "benchspec-eval-hello-trial-gw0",
        "benchspec-build-abc",
        "benchspec-trigger-hello-gw1",
    ):
        (sandboxes / leaked_sandbox).mkdir(parents=True)
    (sandboxes / "unrelated-sandbox").mkdir()
    (sandboxes / "eval-from-another-tool").mkdir()
    (snapshots / "benchspec-microsandbox-claude-code-latest-c30e39d4").mkdir(parents=True)
    (snapshots / "unrelated-snapshot").mkdir()
    repo_root = tmp_path / "repo"
    lock_file = repo_root / "tmp" / ".benchspec-snapshot-claude-code.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.touch()

    result = _run_sandbox_clean(tmp_path, home, repo_root)

    assert result.returncode == 0, result.stderr
    assert sorted(entry.name for entry in sandboxes.iterdir()) == [
        "eval-from-another-tool",
        "unrelated-sandbox",
    ]
    assert sorted(entry.name for entry in snapshots.iterdir()) == ["unrelated-snapshot"]
    assert not lock_file.exists()
    commands = sorted((tmp_path / "commands.log").read_text().splitlines())
    assert commands == [
        "rm -f benchspec-build-abc",
        "rm -f benchspec-eval-hello-trial-gw0",
        "rm -f benchspec-trigger-hello-gw1",
        "snapshot rm --force benchspec-microsandbox-claude-code-latest-c30e39d4",
        "stop benchspec-build-abc",
        "stop benchspec-eval-hello-trial-gw0",
        "stop benchspec-trigger-hello-gw1",
    ]
    docker_commands = sorted((tmp_path / "docker-commands.log").read_text().splitlines())
    assert docker_commands == [
        "images benchspec-snapshot --format {{.Repository}}:{{.Tag}}",
        "ps -a --filter name=benchspec- --format {{.Names}}",
    ]


def test_sandbox_clean_removes_leaked_docker_containers_and_images(tmp_path: Path) -> None:
    """Verify the real command prunes leaked Docker containers and snapshot images."""
    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes").mkdir(parents=True)
    (home / ".microsandbox" / "snapshots").mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = _run_sandbox_clean(
        tmp_path,
        home,
        repo_root,
        containers="benchspec-eval-hello-trial-gw0\nbenchspec-build-abc\nother-benchspec-thing",
        images=(
            "benchspec-snapshot:benchspec-docker-claude-code-latest-c30e39d4\n"
            "benchspec-snapshot:other-tag"
        ),
    )

    assert result.returncode == 0, result.stderr
    docker_commands = sorted((tmp_path / "docker-commands.log").read_text().splitlines())
    assert docker_commands == [
        "images benchspec-snapshot --format {{.Repository}}:{{.Tag}}",
        "ps -a --filter name=benchspec- --format {{.Names}}",
        "rm -f benchspec-build-abc",
        "rm -f benchspec-eval-hello-trial-gw0",
        "rmi -f benchspec-snapshot:benchspec-docker-claude-code-latest-c30e39d4",
    ]


def test_sandbox_clean_on_a_clean_host_is_a_no_op_success(tmp_path: Path) -> None:
    """Verify a host with nothing matching the prefixes exits 0 and never invokes `msb`."""
    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes" / "unrelated-sandbox").mkdir(parents=True)
    (home / ".microsandbox" / "snapshots").mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = _run_sandbox_clean(tmp_path, home, repo_root)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "commands.log").exists()
    assert (home / ".microsandbox" / "sandboxes" / "unrelated-sandbox").is_dir()
    docker_commands = sorted((tmp_path / "docker-commands.log").read_text().splitlines())
    assert docker_commands == [
        "images benchspec-snapshot --format {{.Repository}}:{{.Tag}}",
        "ps -a --filter name=benchspec- --format {{.Names}}",
    ]


def test_sandbox_clean_with_neither_runtime_is_a_no_op_success(tmp_path: Path) -> None:
    """Verify exit 0 and no pruning attempted when neither runtime resolves to a real CLI."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = run_benchspec(
        "sandbox:clean",
        str(repo_root),
        cwd=tmp_path,
        env={
            "HOME": str(home),
            "MSB_PATH": str(tmp_path / "missing" / "msb"),
            "BENCHSPEC_COMMAND_LOG": str(tmp_path / "commands.log"),
            "BENCHSPEC_DOCKER_PATH": str(tmp_path / "missing" / "docker"),
            "BENCHSPEC_DOCKER_COMMAND_LOG": str(tmp_path / "docker-commands.log"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "commands.log").exists()
    assert not (tmp_path / "docker-commands.log").exists()
