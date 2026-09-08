"""End-to-end tests for `benchspec sandbox:build`.

Preflight and config resolution still stop the command at exit 2 before any runtime is
touched: those cases cover config layering, set and backend resolution, and the host and
credential checks. The Docker case goes further and runs a whole build end to end —
preflight, provision, commit, image identity — against the `docker` shim. Every test
here points `BENCHSPEC_DOCKER_PATH` at the shim or at a nonexistent binary, so none of
them can reach a developer's own daemon.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.cli import SANDBOX_FIXTURES, run_benchspec
from tests.support.docker import write_docker_shim

_CLAUDE_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def test_sandbox_build_docker_set_without_docker_cli_fails_preflight(tmp_path: Path) -> None:
    """Verify a `docker` set with no docker CLI on the host stops at preflight with exit 2."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = run_benchspec(
        "sandbox:build",
        str(repo_root),
        "--set",
        "dock",
        "--config",
        str(SANDBOX_FIXTURES / "two-backends.toml"),
        cwd=tmp_path,
        env={
            "BENCHSPEC_DOCKER_PATH": str(tmp_path / "missing" / "docker"),
            "ANTHROPIC_API_KEY": "test-token",
        },
    )

    assert result.returncode == 2, result.stdout
    assert "error: benchspec sandbox preflight failed:" in result.stderr
    assert "docker CLI not found" in result.stderr
    assert "building snapshot" not in result.stdout


def test_sandbox_build_without_credentials_fails_preflight(tmp_path: Path) -> None:
    """Verify a host with no agent credential stops at preflight with exit 2."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = run_benchspec(
        "sandbox:build",
        str(repo_root),
        "--config",
        str(SANDBOX_FIXTURES / "microsandbox.toml"),
        cwd=tmp_path,
        drop=(*_CLAUDE_CREDENTIALS, "BENCHSPEC_AGENT"),
    )

    assert result.returncode == 2, result.stdout
    assert "error: benchspec sandbox preflight failed:" in result.stderr
    assert "no Claude credential" in result.stderr
    assert "building snapshot" not in result.stdout


def test_sandbox_build_docker_set_builds_through_the_docker_cli(tmp_path: Path) -> None:
    """Verify a `docker` set drives preflight, build, commit, and identity through the CLI."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    shim = write_docker_shim(tmp_path / "bin")
    command_log = tmp_path / "docker-commands.log"

    result = run_benchspec(
        "sandbox:build",
        str(repo_root),
        "--set",
        "dock",
        "--config",
        str(SANDBOX_FIXTURES / "two-backends.toml"),
        cwd=tmp_path,
        env={
            "BENCHSPEC_DOCKER_PATH": str(shim),
            "BENCHSPEC_DOCKER_COMMAND_LOG": str(command_log),
            "ANTHROPIC_API_KEY": "test-token",
            "BENCHSPEC_CLAUDE_VERSION": "1.2.3",
        },
        drop=("CLAUDE_CODE_OAUTH_TOKEN",),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "building snapshot benchspec-docker-claude-code-1.2.3-" in result.stdout
    assert "built benchspec-docker-claude-code-1.2.3-" in result.stdout
    assert "image identity: sha256:deadbeef" in result.stdout
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert commands[0] == "info"
    assert any(
        line.startswith("run -d --name benchspec-build-claude-code ") for line in commands
    )
    assert any(
        line.startswith(
            "commit benchspec-build-claude-code "
            "benchspec-snapshot:benchspec-docker-claude-code-1.2.3-"
        )
        for line in commands
    )
    assert commands[-2].startswith("rm -f benchspec-build-claude-code")
    assert commands[-1].startswith("image inspect --format")
