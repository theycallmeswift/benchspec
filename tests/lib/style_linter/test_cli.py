"""Tests for the repository-specific style-lint CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.lib.style_linter._helpers import cli, repo_root


def test_cli_owns_repo_specific_rules_prompt_and_default_paths() -> None:
    """Keep evalspec-specific lint policy in the repository script."""
    style_lint_cli = cli()

    assert style_lint_cli.DEFAULT_PATHS == (
        Path("src"),
        Path("tests"),
        Path("evals"),
        Path("bin"),
        Path("lib"),
    )
    assert {rule.id for rule in style_lint_cli.RULES} == {
        "no-suppression-comments",
        "section-header-comments",
        "provenance-comments",
        "descriptive-names",
        "dedented-multiline-strings",
        "semantic-block-newlines",
    }
    assert "docs/style/development.md" in style_lint_cli.POLICY_INSTRUCTIONS
    assert "Review the numbered source chunks" not in style_lint_cli.POLICY_INSTRUCTIONS
    assert "Ruff" not in style_lint_cli.POLICY_INSTRUCTIONS


def test_cli_rules_cover_semantic_block_newlines() -> None:
    """Keep readability spacing as caller-owned style policy."""
    style_lint_cli = cli()

    rules = {rule.id: rule.description for rule in style_lint_cli.RULES}

    assert "semantic-block-newlines" in rules
    assert "semantic blocks" in rules["semantic-block-newlines"]


def test_cli_run_skips_cleanly_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip advisory lint cleanly when the Gemini API key is absent."""
    style_lint_cli = cli()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = style_lint_cli.run()

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GEMINI_API_KEY" in captured.out
    assert "skip" in captured.out.lower()


def test_cli_script_runs_from_makefile_entry_path_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support direct `python bin/linters/style_lint.py` execution."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = subprocess.run(
        [sys.executable, "bin/linters/style_lint.py"],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "GEMINI_API_KEY" in result.stdout
    assert result.stderr == ""


def test_cli_run_catches_malformed_model_output_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Soft-fail model parse errors because custom lint is advisory."""
    style_lint_cli = cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "call_gemini",
        lambda **_kwargs: "not json",
    )

    exit_code = style_lint_cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "warning:" in captured.out
    assert "model error" in captured.out


def test_cli_run_prints_findings_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print findings in path-line-column format without failing the command."""
    style_lint_cli = cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "call_gemini",
        lambda **_kwargs: response,
    )

    exit_code = style_lint_cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"{source.resolve()}:1:1: descriptive-names" in captured.out
    assert "Use a descriptive binding name." in captured.out


def test_cli_verify_model_can_filter_findings_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow optional second-pass verification to drop detector findings."""
    style_lint_cli = cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    calls: list[str] = []
    detector_response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )
    verifier_response = json.dumps({"keep_indexes": []})

    def _call_gemini(**kwargs: object) -> str:
        model = kwargs["model"]
        calls.append(str(model))
        if model == "gemini-verifier":
            return verifier_response
        return detector_response

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint_cli.style_lint, "call_gemini", _call_gemini)

    exit_code = style_lint_cli.run(paths=[source], verify_model="gemini-verifier")

    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [style_lint_cli.DEFAULT_MODEL, "gemini-verifier"]
    assert captured.out == ""


def test_cli_verify_model_is_not_called_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep stronger-model verification opt-in."""
    style_lint_cli = cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    calls: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        return json.dumps({"findings": []})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint_cli.style_lint, "call_gemini", _call_gemini)

    exit_code = style_lint_cli.run(paths=[source])

    assert exit_code == 0
    assert calls == [style_lint_cli.DEFAULT_MODEL]
