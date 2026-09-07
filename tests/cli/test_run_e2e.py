"""End-to-end tests for `benchspec run`.

`run` spawns a pytest child that loads the benchspec plugin through its entry point.
Passing `--collect-only` after `--` drives that whole chain for real: config resolution,
eval discovery, and per-arm parametrization, while stopping before any VM boots or paid
arm executes. Fixture setup never runs under collection, so neither the sandbox nor the
judge preflights fire.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from tests.support.cli import run_benchspec, write_eval

_TWO_ARM_PYPROJECT = dedent("""\
    [tool.benchspec]
    default-set = "demo"

    [tool.benchspec.sets.demo]
    harness = "claude-code"
    model = "sonnet"
    baseline = "baseline"
    arms = [{ name = "baseline" }, { name = "trial" }]
""")


def test_run_with_no_evals_exits_nothing_to_do(tmp_path: Path) -> None:
    """Verify an eval-free root reports nothing to run and exits 5 without spawning pytest."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = run_benchspec("run", str(repo_root), cwd=tmp_path)

    assert result.returncode == 5, result.stderr
    assert f"no evals discovered under {repo_root.resolve()}" in result.stdout


def test_run_collects_one_test_per_eval_arm_pair(tmp_path: Path) -> None:
    """Verify the real pytest plugin parametrizes the eval across both arms, exit 0."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec(
        "run", str(repo_root), "--", "--collect-only", "-q", cwd=tmp_path
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_eval[greets-greets-baseline]" in result.stdout
    assert "test_eval[greets-greets-trial]" in result.stdout
    assert "2 tests collected" in result.stdout


def test_run_unknown_set_is_a_usage_error(tmp_path: Path) -> None:
    """Verify an unknown `--set` fails at collection and maps to exit 2."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec(
        "run", str(repo_root), "--set", "nope", "--", "--collect-only", "-q", cwd=tmp_path
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "no eval set named `nope` (declared: ['demo'])" in result.stdout
