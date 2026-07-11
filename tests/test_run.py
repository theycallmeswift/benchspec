"""The pure `run` flag translation into plugin `--evalspec-*` option tokens.

Each test builds a namespace inline and asserts the exact tokens `translate_run_flags`
emits for it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evalspec.run import translate_run_flags


def _run_namespace(root: Path, **flags: object) -> argparse.Namespace:
    """Build a `run` namespace with every curated flag absent except the overrides."""
    values: dict[str, object] = {
        dest: None
        for dest in (
            "set",
            "config",
            "model",
            "models",
            "harness",
            "effort",
            "eval_paths",
            "fail_under",
            "judge_harness",
            "judge_model",
            "judge_effort",
        )
    }
    values["env"] = []
    values.update(flags)
    return argparse.Namespace(root=root, **values)


def test_translates_set_to_evalspec_set() -> None:
    """Verify --set becomes an --evalspec-set token."""
    args = _run_namespace(Path("repo"), set="default")

    tokens = translate_run_flags(args)

    assert "--evalspec-set=default" in tokens


def test_translates_config_model_harness_effort() -> None:
    """Verify config/model/harness/effort each become their --evalspec-* token."""
    args = _run_namespace(
        Path("repo"),
        config="evalspec.toml",
        model="opus",
        harness="claude-code",
        effort="high",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-config=evalspec.toml" in tokens
    assert "--evalspec-model=opus" in tokens
    assert "--evalspec-harness=claude-code" in tokens
    assert "--evalspec-effort=high" in tokens


def test_translates_models_and_eval_paths_and_fail_under() -> None:
    """Verify the models sweep, eval paths, and fail-under gate each translate."""
    args = _run_namespace(
        Path("repo"),
        models="opus,sonnet",
        eval_paths="skills,evals",
        fail_under="0.8",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-models=opus,sonnet" in tokens
    assert "--evalspec-eval-paths=skills,evals" in tokens
    assert "--evalspec-fail-under=0.8" in tokens


def test_translates_common_judge_flags() -> None:
    """Verify judge harness/model/effort each become their --evalspec-judge-* token."""
    args = _run_namespace(
        Path("repo"),
        judge_harness="gemini",
        judge_model="gemini-2.5-pro",
        judge_effort="low",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-judge-harness=gemini" in tokens
    assert "--evalspec-judge-model=gemini-2.5-pro" in tokens
    assert "--evalspec-judge-effort=low" in tokens


def test_repeatable_env_emits_one_token_per_value() -> None:
    """Verify a repeatable --env emits one --evalspec-env token per value."""
    args = _run_namespace(Path("repo"), env=["A=1", "B=2"])

    tokens = translate_run_flags(args)

    assert "--evalspec-env=A=1" in tokens
    assert "--evalspec-env=B=2" in tokens


def test_root_becomes_repo_root_token() -> None:
    """Verify the root positional becomes a resolved --evalspec-repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens[0] == f"--evalspec-repo-root={root.resolve()}"


def test_absent_flags_add_nothing() -> None:
    """Verify a namespace with no curated flags emits only the repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens == [f"--evalspec-repo-root={root.resolve()}"]
