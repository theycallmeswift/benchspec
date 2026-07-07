"""Layout tests for the advisory style-lint package."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType


def test_makefile_wires_custom_lint_target_and_keeps_lint_ruff_only(
    repo_root: Path,
) -> None:
    """Keep the default lint target Ruff-only and expose the custom make target."""
    makefile_text = (repo_root / "Makefile").read_text()

    assert "lint\\:custom" in makefile_text
    assert "uv run ruff check ." in makefile_text
    assert "uv run python bin/linters/style_lint.py" in makefile_text

    lint_target_text = makefile_text.split("lint:  ## Lint with ruff", maxsplit=1)[1]
    lint_target_text = lint_target_text.split("\n\n", maxsplit=1)[0]

    assert "style_lint.py" not in lint_target_text


def test_plan_file_lives_in_docs_plans(repo_root: Path) -> None:
    """Keep implementation plans in the repository-level plans directory."""
    assert (repo_root / "docs/plans/2026-07-05-style-lint-rules.md").exists()
    assert not (
        repo_root / "docs/superpowers/plans/2026-07-05-style-lint-rules.md"
    ).exists()


def test_agent_directory_structure_mentions_doc_buckets(repo_root: Path) -> None:
    """Verify agent instructions mention the canonical docs buckets."""
    agent_instructions = (repo_root / "AGENTS.md").read_text()

    assert "docs/{plans,specs,research}/" in agent_instructions


def test_evalspec_package_does_not_own_style_lint_framework(
    repo_root: Path,
    framework: ModuleType,
) -> None:
    """Keep reusable linter framework code out of the evalspec package."""
    old_module = repo_root / "src/evalspec/style_lint.py"

    assert not old_module.exists()
    assert framework.Rule.__module__ == "lib.style_lint.types"


def test_framework_modules_are_sliced_by_linter_function(repo_root: Path) -> None:
    """Keep reusable modules grouped by cohesive linter function."""
    module_names = {
        path.name
        for path in (repo_root / "lib/style_lint").glob("*.py")
        if path.name != "__pycache__"
    }

    assert {
        "__init__.py",
        "detector.py",
        "gemini.py",
        "runner.py",
        "source.py",
        "types.py",
        "verifier.py",
    } <= module_names
    assert not {"files.py", "findings.py", "models.py", "prompt.py"} & module_names


def test_framework_init_only_exports_submodule_api(
    repo_root: Path,
    framework: ModuleType,
) -> None:
    """Keep framework implementation out of the package __init__ module."""
    init_text = (repo_root / "lib/style_lint/__init__.py").read_text()

    assert "def " not in init_text
    assert "class " not in init_text
    assert framework.StyleLintConfig.__module__ == "lib.style_lint.runner"
    assert (
        importlib.import_module("lib.style_lint.detector").DEFAULT_SYSTEM_PROMPT
        == framework.DEFAULT_SYSTEM_PROMPT
    )


def test_style_linter_tests_live_under_matching_package_path(repo_root: Path) -> None:
    """Keep style-lint tests near the mirrored lib package layout."""
    assert not (repo_root / "tests/test_style_lint.py").exists()
    assert Path(__file__).parent == repo_root / "tests/lib/style_linter"


def test_style_linter_tests_use_conftest_for_shared_helpers(repo_root: Path) -> None:
    """Keep shared test plumbing in pytest's local fixture module."""
    assert (repo_root / "tests/lib/style_linter/conftest.py").exists()
    assert not (repo_root / "tests/lib/style_linter/_helpers.py").exists()
