"""End-to-end tests for `benchspec lint`.

`lint` needs no credentials, runtime, or network, so every path runs for real: the
seeded eval is discovered, its assertions are linted, and the report and exit code are
asserted from the subprocess.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tests.support.cli import run_benchspec, write_eval


def test_lint_reports_unjudgeable_assertions_and_exits_one(tmp_path: Path) -> None:
    """Verify a vague adverb and an unanchored path each surface as a warning, exit 1."""
    repo_root = tmp_path / "repo"
    write_eval(
        repo_root,
        [
            "The agent handles the request gracefully",
            "notes/summary.md lists every step",
        ],
    )

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 1, result.stderr
    assert "[warning] greets: vague-adverb:" in result.stdout
    assert "[warning] greets: unseen-file:" in result.stdout
    assert "2 warning(s)" in result.stdout


def test_lint_clean_suite_exits_zero(tmp_path: Path) -> None:
    """Verify a suite of judgeable assertions reports zero warnings and exits 0."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists", "./notes/summary.md lists every step"])

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "0 warning(s)" in result.stdout
    assert "[warning]" not in result.stdout


def test_lint_malformed_eval_is_a_usage_error(tmp_path: Path) -> None:
    """Verify a non-kebab eval slug fails discovery with a clean error line, exit 2."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"], slug="NotKebab")

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "NotKebab" in result.stderr
    assert "kebab-case" in result.stderr


def test_lint_warns_on_untagged_skill_trigger_line(tmp_path: Path) -> None:
    """Verify a bare `Skill ... invoked` line with no clause is an `untagged-trigger` warning."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["Skill `hello` invoked", "./out.md exists"])

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 1, result.stderr
    assert "[warning] greets: untagged-trigger:" in result.stdout
    assert "1 warning(s)" in result.stdout


def test_lint_warns_on_constant_clause(tmp_path: Path) -> None:
    """Verify a clause that references no `{VAR}` is a `constant-clause` warning, exit 1."""
    repo_root = tmp_path / "repo"
    write_eval(
        repo_root,
        dedent("""\
            - [ ] ./out.md exists
              - if: true
        """),
    )

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 1, result.stderr
    assert "[warning] greets: constant-clause:" in result.stdout
    assert "1 warning(s)" in result.stdout


def test_lint_is_clean_for_a_tagged_trigger_line(tmp_path: Path) -> None:
    """Verify a `Skill ... invoked` line scoped off the baseline lints clean, exit 0."""
    repo_root = tmp_path / "repo"
    write_eval(
        repo_root,
        dedent("""\
            - [ ] Skill `hello` invoked
              - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
            - [ ] ./out.md exists
        """),
    )

    result = run_benchspec("lint", str(repo_root), cwd=tmp_path)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "0 warning(s)" in result.stdout
    assert "[warning]" not in result.stdout
