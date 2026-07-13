"""Config-resolution and discovery/parse tests for evalspec's own end-to-end suite.

Exercises the real `[tool.evalspec]` config in this repo's `pyproject.toml` (the `e2e`
set and its Codex judge) and the real `evals/e2e/hello/` suite against the live parsers
— the same "authored example must parse for real" pattern as
`tests/test_readme_examples.py`, extended to config resolution and eval discovery.
"""

from __future__ import annotations

from pathlib import Path

from evalspec.arms import parse_sets, resolve_set
from evalspec.discovery import pyproject_table
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
