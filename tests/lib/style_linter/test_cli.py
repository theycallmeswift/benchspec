"""Tests for the repository-specific style-lint CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def test_cli_owns_repo_specific_rules_prompt_and_default_paths(
    style_lint_cli: ModuleType,
) -> None:
    """Keep evalspec-specific lint policy in the repository script."""
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


def test_cli_rules_cover_semantic_block_newlines(style_lint_cli: ModuleType) -> None:
    """Keep readability spacing as caller-owned style policy."""
    rules = {rule.id: rule.description for rule in style_lint_cli.RULES}

    assert "semantic-block-newlines" in rules
    assert "semantic blocks" in rules["semantic-block-newlines"]


def test_cli_run_skips_cleanly_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Skip advisory lint cleanly when the Gemini API key is absent."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = style_lint_cli.run()

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GEMINI_API_KEY" in captured.out
    assert "skip" in captured.out.lower()


def test_cli_script_runs_from_makefile_entry_path_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    """Support direct `python bin/linters/style_lint.py` execution."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = subprocess.run(
        [sys.executable, "bin/linters/style_lint.py"],
        cwd=repo_root,
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
    style_lint_cli: ModuleType,
) -> None:
    """Soft-fail model parse errors because custom lint is advisory."""
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
    style_lint_cli: ModuleType,
) -> None:
    """Print findings in path-line-column format without failing the command."""
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
                    "source": "x = 1",
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


def test_cli_run_prints_no_findings_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Confirm successful no-finding runs instead of staying silent."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "call_gemini",
        lambda **_kwargs: json.dumps({"findings": []}),
    )

    exit_code = style_lint_cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == "All checks passed!\n"


def test_cli_run_verbose_logs_progress_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Log Ruff-style debug progress without changing normal stdout."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    result = style_lint_cli.style_lint.StyleLintResult(
        findings=[],
        diagnostics=[],
        files_checked=1,
        chunks_checked=1,
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "run_advisory_lint",
        lambda _config: result,
    )

    exit_code = style_lint_cli.run(paths=[source], verbose=True)

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == "All checks passed!\n"
    assert "[style_lint][DEBUG]" in captured.err
    assert "Using paths: " in captured.err
    assert "Checked 1 files across 1 chunks" in captured.err


def test_cli_run_prints_usage_metadata_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Print token usage metadata after advisory results."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    result = style_lint_cli.style_lint.StyleLintResult(
        findings=[],
        diagnostics=[],
        usage=style_lint_cli.style_lint.UsageMetadata(
            requests=2,
            prompt_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "run_advisory_lint",
        lambda _config: result,
    )

    exit_code = style_lint_cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == (
        "All checks passed!\n"
        "usage: 2 requests, 100 input tokens, 20 output tokens, "
        "120 total tokens\n"
    )


def test_cli_run_filters_findings_to_changed_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Limit changeset output to diagnostics on touched lines."""
    source = tmp_path / "sample.py"
    source.write_text("old_name = 1\nnew_name = 2\n")
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "source": "old_name = 1",
                    "message": "Old untouched finding.",
                },
                {
                    "chunk_index": 0,
                    "line": 2,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "source": "new_name = 2",
                    "message": "New touched finding.",
                },
            ]
        }
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "call_gemini",
        lambda **_kwargs: response,
    )

    exit_code = style_lint_cli.run(
        paths=[source],
        changed_lines={source.resolve(): {2}},
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "New touched finding." in captured.out
    assert "Old untouched finding." not in captured.out


def test_cli_base_filters_run_to_changed_python_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    style_lint_cli: ModuleType,
) -> None:
    """Use --base to scope the run to changed Python files."""
    source = (tmp_path / "sample.py").resolve()
    changed_lines = {source: {2}}
    run_call: dict[str, object] = {}

    def fake_changed_lines_from_base(ref: str) -> dict[Path, set[int]]:
        assert ref == "origin/dev"
        return changed_lines

    def fake_run(
        paths: list[Path] | None,
        **kwargs: object,
    ) -> int:
        run_call["paths"] = paths
        run_call["changed_lines"] = kwargs["changed_lines"]
        run_call["verify_findings"] = kwargs["verify_findings"]
        run_call["verbose"] = kwargs["verbose"]
        return 0

    monkeypatch.setattr(
        style_lint_cli,
        "changed_lines_from_base",
        fake_changed_lines_from_base,
    )
    monkeypatch.setattr(style_lint_cli, "run", fake_run)

    exit_code = style_lint_cli.main(["--base", "origin/dev", "--verbose"])

    assert exit_code == 0
    assert run_call == {
        "paths": [source],
        "changed_lines": changed_lines,
        "verify_findings": True,
        "verbose": True,
    }


def test_changed_lines_from_unified_diff(style_lint_cli: ModuleType) -> None:
    """Parse added-line ranges from a zero-context unified diff."""
    diff_text = "\n".join(
        [
            "diff --git a/sample.py b/sample.py",
            "--- a/sample.py",
            "+++ b/sample.py",
            "@@ -1 +1,2 @@",
            "-old_name = 1",
            "+new_name = 1",
            "+other_name = 2",
            "diff --git a/docs/readme.md b/docs/readme.md",
            "--- a/docs/readme.md",
            "+++ b/docs/readme.md",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ]
    )

    changed_lines = style_lint_cli.changed_lines_from_unified_diff(diff_text)

    assert changed_lines == {Path("sample.py").resolve(): {1, 2}}


def test_cli_verify_findings_uses_verify_model_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Allow optional second-pass verification to drop detector findings."""
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
                    "source": "x = 1",
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

    exit_code = style_lint_cli.run(
        paths=[source],
        verify_findings=True,
        verify_model="gemini-verifier",
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [style_lint_cli.DEFAULT_MODEL, "gemini-verifier"]
    assert captured.out == "All checks passed!\n"


def test_cli_verify_findings_defaults_to_detector_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Use the detector model for verification when no override is provided."""
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
                    "source": "x = 1",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )
    verifier_response = json.dumps({"keep_indexes": []})

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        if len(calls) == 2:
            return verifier_response
        return detector_response

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint_cli.style_lint, "call_gemini", _call_gemini)

    exit_code = style_lint_cli.run(paths=[source], verify_findings=True)

    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [style_lint_cli.DEFAULT_MODEL, style_lint_cli.DEFAULT_MODEL]
    assert captured.out == "All checks passed!\n"


def test_cli_verify_model_does_not_enable_verification_by_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    style_lint_cli: ModuleType,
) -> None:
    """Require --verify-findings to run the second model call."""
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    calls: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        return json.dumps({"findings": []})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint_cli.style_lint, "call_gemini", _call_gemini)

    exit_code = style_lint_cli.run(
        paths=[source],
        verify_findings=False,
        verify_model="gemini-verifier",
    )

    assert exit_code == 0
    assert calls == [style_lint_cli.DEFAULT_MODEL]
