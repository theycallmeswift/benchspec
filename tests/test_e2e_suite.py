"""Config-resolution and discovery/parse tests for evalspec's own end-to-end suite.

Exercises the real `[tool.evalspec]` config in this repo's `pyproject.toml` (the `e2e`
set and its Codex judge) and the real `evals/e2e/hello/` suite against the live parsers
— the same "authored example must parse for real" pattern as
`tests/test_readme_examples.py`, extended to config resolution and eval discovery.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from evalspec.arms import parse_sets, resolve_set
from evalspec.discovery import discover_eval_cases, pyproject_table
from evalspec.judges.config import resolve_judge_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_e2e_set_resolves_to_three_arms_with_declared_config() -> None:
    """Verify the e2e set resolves to three arms with the declared harness/model/effort."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    assert default_set == "e2e"
    assert resolved.baseline == "baseline"
    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert set(arms_by_name) == {"baseline", "trial", "trial-overrides"}
    assert arms_by_name["baseline"].harness == "claude-code"
    assert arms_by_name["baseline"].model == "sonnet"
    assert arms_by_name["baseline"].effort == "medium"
    assert arms_by_name["trial"].harness == "claude-code"
    assert arms_by_name["trial"].model == "sonnet"
    assert arms_by_name["trial"].effort == "medium"
    assert arms_by_name["trial-overrides"].harness == "claude-code"
    assert arms_by_name["trial-overrides"].model == "opus"
    assert arms_by_name["trial-overrides"].effort == "high"


def test_e2e_trial_overrides_env_inherits_style_and_overrides_locale() -> None:
    """Verify trial-overrides inherits GREETING_STYLE and overrides GREETING_LOCALE."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert arms_by_name["baseline"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial-overrides"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-GB",
    }


def test_e2e_judge_is_codex_and_distinct_from_the_task_harness() -> None:
    """Verify the e2e judge is a cross-family Codex judge, distinct from the task harness."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)
    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    judge = resolve_judge_config(pyproject_table=table.get("judge"))

    assert judge.harness == "codex"
    assert judge.model == "gpt-5.5"
    assert judge.harness != resolved.arms[0].harness


def test_hello_evals_are_discovered_with_expected_identities() -> None:
    """Verify both hello evals are discovered with a non-empty prompt and assertions.

    Calls `discover_eval_cases(REPO_ROOT)` with no `eval_paths` override — the same call
    shape production uses (`analyze.py:66`, `lint.py:72`) — so this test exercises the
    real `eval_paths = ["evals"]` in `pyproject.toml` (Task 1), not a hardcoded stand-in.
    That also proves the scoping does its job: `tests/fixtures/activation/evals/
    activation-demo/eval.md`, which the *default* `eval_paths` (`skills`, `tests`,
    `evals`, `benchmarks`) would pick up, must NOT appear in the discovered set.
    """
    cases = discover_eval_cases(REPO_ROOT)
    groups = {case.group for case in cases}
    hello_cases = {(case.group, case.eval_id): case for case in cases if case.group == "hello"}

    assert set(hello_cases) == {
        ("hello", "greets-by-name"),
        ("hello", "writes-greeting-file"),
    }
    for case in hello_cases.values():
        assert case.prompt
        assert case.assertions
    assert "activation-demo" not in groups


def test_setup_sh_has_valid_bash_syntax() -> None:
    """Verify setup.sh parses as valid bash without executing any of it."""
    setup_sh = REPO_ROOT / "evals/e2e/hello/evals/hello/setup.sh"

    result = subprocess.run(
        ["bash", "-n", str(setup_sh)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_setup_sh_baseline_arm_is_a_no_op() -> None:
    """Verify the baseline branch exits 0 before any filesystem write.

    Runs the real script exactly as `run_setup_sh` invokes it host-side
    (`src/evalspec/sandbox.py:184`: `cd <eval_dir>; bash ./setup.sh`), with
    `EVALSPEC_ARM=baseline`. This does NOT exercise the trial/trial-overrides branches —
    those `mkdir`/`cp` into the guest-only path `/home/evalspec/skills`, which only
    exists inside a booted microVM, so they stay real-run-only (`make e2e`).
    """
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"

    result = subprocess.run(
        ["bash", "./setup.sh"],
        cwd=eval_dir,
        env={**os.environ, "EVALSPEC_ARM": "baseline"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_setup_sh_relative_skill_path_resolves_to_the_real_skill_md() -> None:
    """Verify setup.sh's `../../SKILL.md` reference resolves to the real skill file."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"

    skill_md = (eval_dir / "../../SKILL.md").resolve()

    assert skill_md == REPO_ROOT / "evals/e2e/hello/SKILL.md"
    assert skill_md.is_file()
