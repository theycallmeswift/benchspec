"""End-to-end tests for `benchspec sandbox:build`.

A real snapshot build needs a booted microVM runtime and lives in `make e2e`. What runs
for real here is everything before provisioning: config layering, set and backend
resolution, and the host and credential preflight, each of which must stop the command
at exit 2 before any runtime is touched.
"""

from __future__ import annotations

from pathlib import Path

from tests.cli.support import SANDBOX_FIXTURES, run_benchspec

_CLAUDE_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def test_sandbox_build_docker_set_is_a_usage_error(tmp_path: Path) -> None:
    """Verify selecting a `docker` set fails at config resolution with exit 2."""
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
    )

    assert result.returncode == 2, result.stdout
    assert result.stderr.startswith("error:")
    assert "unsupported sandbox `docker`" in result.stderr


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
