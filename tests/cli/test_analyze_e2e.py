"""End-to-end tests for `benchspec analyze`.

The binder classifies bare file-existence assertions on a local fast path without
calling Gemini, so a suite made only of those runs for real with no network. The
placeholder `GEMINI_API_KEY` satisfies the presence preflight and is never sent.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.cli import run_benchspec, write_eval


def test_analyze_classifies_existence_assertions_as_deterministic(tmp_path: Path) -> None:
    """Verify bare `<path> exists` lines classify locally as deterministic, exit 0."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists", "`./notes/summary.md` exists"])

    result = run_benchspec(
        "analyze",
        str(repo_root),
        cwd=tmp_path,
        env={"GEMINI_API_KEY": "e2e-placeholder-never-sent"},
    )

    assert result.returncode == 0, result.stderr
    assert "deterministic  ./out.md exists" in result.stdout
    assert "deterministic  `./notes/summary.md` exists" in result.stdout
    assert "judge-backed" not in result.stdout
    assert "2 assertion(s)" in result.stdout


def test_analyze_malformed_eval_is_a_usage_error(tmp_path: Path) -> None:
    """Verify a non-kebab eval slug fails discovery with a clean error line, exit 2."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"], slug="NotKebab")

    result = run_benchspec(
        "analyze",
        str(repo_root),
        cwd=tmp_path,
        env={"GEMINI_API_KEY": "e2e-placeholder-never-sent"},
    )

    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "NotKebab" in result.stderr
