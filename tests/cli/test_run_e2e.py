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


def test_run_without_gemini_key_exits_two_before_spawning_pytest(tmp_path: Path) -> None:
    """A missing `GEMINI_API_KEY` is one `error:` line and exit 2, not a pytest error per cell."""
    repo_root = tmp_path / "repo"
    write_eval(repo_root, ["./out.md exists"])
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec("run", str(repo_root), cwd=tmp_path, drop=("GEMINI_API_KEY",))

    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "GEMINI_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr
    assert "test session starts" not in result.stdout


def test_run_collects_scoped_eval_same_cells_as_unscoped(tmp_path: Path) -> None:
    """Verify a clause-bearing eval still parametrizes one cell per arm at collection, exit 0."""
    repo_root = tmp_path / "repo"
    write_eval(
        repo_root,
        dedent("""\
            - [ ] Skill `hello` invoked
              - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
            - [ ] ./out.md exists
        """),
    )
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec(
        "run", str(repo_root), "--", "--collect-only", "-q", cwd=tmp_path
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_eval[greets-greets-baseline]" in result.stdout
    assert "test_eval[greets-greets-trial]" in result.stdout
    assert "2 tests collected" in result.stdout


def test_run_rejects_unknown_clause_variable_at_collection(tmp_path: Path) -> None:
    """Verify an unknown `{VAR}` in a clause fails collection naming the eval, clause, and arm."""
    repo_root = tmp_path / "repo"
    eval_file = write_eval(
        repo_root,
        dedent("""\
            - [ ] ./out.md exists
              - if: {NOPE} == 1
        """),
    )
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec(
        "run", str(repo_root), "--", "--collect-only", "-q", cwd=tmp_path
    )

    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert str(eval_file.resolve()) in output
    assert "if: {NOPE} == 1" in output
    assert "baseline" in output  # the first (case × arm) pair the plugin resolves
    assert "NOPE" in output


def test_run_rejects_malformed_clause_at_collection(tmp_path: Path) -> None:
    """Verify a second clause on one item is one `error:` line naming the eval and line, exit 2."""
    repo_root = tmp_path / "repo"
    eval_file = write_eval(
        repo_root,
        dedent("""\
            - [ ] ./out.md exists
              - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
              - if: {BENCHSPEC_ARM} == "trial"
        """),
    )
    (repo_root / "pyproject.toml").write_text(_TWO_ARM_PYPROJECT)

    result = run_benchspec(
        "run", str(repo_root), "--", "--collect-only", "-q", cwd=tmp_path
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr.startswith("error:")
    assert str(eval_file.resolve()) in result.stderr
    assert 'if: {BENCHSPEC_ARM} == "trial"' in result.stderr
    assert "already carries a clause" in result.stderr
    assert "test session starts" not in result.stdout
