"""End-to-end tests for `benchspec analyze`.

The binder classifies bare file-existence assertions on a local fast path without
calling its provider, so a suite made only of those runs for real with no network. The
placeholder key satisfies the presence preflight and is never sent.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tests.support.cli import run_benchspec, write_eval

_OPENROUTER_BINDER_TOML = dedent("""\
    [tool.benchspec.binder]
    provider = "openrouter"
""")


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


def test_analyze_without_gemini_key_exits_two_with_clean_error(tmp_path: Path) -> None:
    """A missing `GEMINI_API_KEY` is a one-line `error:` on stderr and exit 2, no traceback."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])

    result = run_benchspec("analyze", str(repo_root), cwd=tmp_path, drop=("GEMINI_API_KEY",))

    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "GEMINI_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr


def test_analyze_openrouter_binder_needs_only_the_openrouter_key(tmp_path: Path) -> None:
    """With `[tool.benchspec.binder] provider = "openrouter"`, no Gemini credential is needed."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])
    (repo_root / "pyproject.toml").write_text(_OPENROUTER_BINDER_TOML)

    result = run_benchspec(
        "analyze",
        str(repo_root),
        cwd=tmp_path,
        env={"OPENROUTER_API_KEY": "e2e-placeholder-never-sent"},
        drop=("GEMINI_API_KEY",),
    )

    assert result.returncode == 0, result.stderr
    assert "deterministic  ./out.md exists" in result.stdout


def test_analyze_openrouter_binder_without_its_key_names_openrouter_api_key(
    tmp_path: Path,
) -> None:
    """A missing `OPENROUTER_API_KEY` under the OpenRouter binder is the one variable named."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])
    (repo_root / "pyproject.toml").write_text(_OPENROUTER_BINDER_TOML)

    result = run_benchspec(
        "analyze",
        str(repo_root),
        cwd=tmp_path,
        env={"GEMINI_API_KEY": "e2e-placeholder-never-sent"},
        drop=("OPENROUTER_API_KEY",),
    )

    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "OPENROUTER_API_KEY" in result.stderr
    assert "GEMINI_API_KEY" not in result.stderr
