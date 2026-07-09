"""Tests for the one-call advisory lint runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def test_run_advisory_lint_encapsulates_framework_call_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Run the reusable lint pipeline from one config object."""
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
    calls: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        return response

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
        )
    )

    assert calls == ["gemini-test"]
    assert len(result.findings) == 1
    assert result.diagnostics == [
        f"{source.resolve()}:1:1: descriptive-names Use a descriptive binding name."
    ]


def test_run_advisory_lint_batches_detector_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Avoid one oversized model prompt for multi-chunk lint runs."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")
    seen_prompts: list[str] = []
    progress_events: list[tuple[Path, int, int]] = []

    def _call_gemini(**kwargs: object) -> str:
        seen_prompts.append(str(kwargs["prompt"]))
        return json.dumps({"findings": []})

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
            max_lines=1,
            chunk_batch_size=2,
            progress_callback=lambda path, current, total: progress_events.append(
                (path, current, total)
            ),
        )
    )
    dry_run_result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert result.warning is None
    assert len(seen_prompts) == 2
    assert dry_run_result.plan is not None
    assert dry_run_result.plan.detector_api_calls == len(seen_prompts)
    assert dry_run_result.files_checked == result.files_checked
    assert dry_run_result.chunks_checked == result.chunks_checked
    assert progress_events == [(source.resolve(), 1, 1)]
    assert result.files_checked == 1
    assert result.chunks_checked == 3


def test_run_advisory_lint_dry_run_skips_gitignored_files_from_repo_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Preview only unignored Python files when scanning a full repository."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[Path(".")],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
        )
    )

    assert result.warning is None
    assert result.plan is not None
    assert result.plan.files == [included_file.resolve()]
    assert ignored_file.resolve() not in result.plan.files
    assert result.files_checked == 1
    assert result.chunks_checked == 1
    assert result.plan.detector_api_calls == 1
    assert result.plan.max_verifier_api_calls == 0
    assert result.plan.max_total_api_calls == 1


def test_run_advisory_lint_aggregates_usage_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Aggregate model usage across detector calls."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    def _call_gemini(**_kwargs: object) -> object:
        return framework.GeminiResponse(
            text=json.dumps({"findings": []}),
            usage=framework.UsageMetadata(
                requests=1,
                prompt_tokens=10,
                output_tokens=2,
                total_tokens=12,
            ),
        )

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert result.usage == framework.UsageMetadata(
        requests=2,
        prompt_tokens=20,
        output_tokens=4,
        total_tokens=24,
    )


def test_run_advisory_lint_drops_invalid_individual_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Keep advisory runs useful when one model finding has bad evidence."""
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
                    "source": "",
                    "message": "Bad empty evidence.",
                },
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "source": "x = 1",
                    "message": "Use a descriptive binding name.",
                },
            ]
        }
    )

    monkeypatch.setattr(framework, "call_gemini", lambda **_kwargs: response)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
        )
    )

    assert result.warning is None
    assert result.diagnostics == [
        f"{source.resolve()}:1:1: descriptive-names Use a descriptive binding name."
    ]


def test_run_advisory_lint_warning_preserves_prepared_scope_counts(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Keep partial file and chunk counts when detector batching fails."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
            max_lines=1,
            chunk_batch_size=0,
        )
    )

    assert result.warning == (
        "advisory style lint skipped due to model error: "
        "chunk_batch_size must be at least 1"
    )
    assert result.files_checked == 1
    assert result.chunks_checked == 3


def test_run_advisory_lint_dry_run_returns_plan_without_model_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Let the reusable linter own dry-run planning mode."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")
    monkeypatch.setattr(
        framework,
        "call_gemini",
        lambda **_kwargs: pytest.fail("dry-run must not call Gemini"),
    )

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
            verify_findings=True,
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert result.warning is None
    assert result.diagnostics == []
    assert result.findings == []
    assert result.files_checked == 1
    assert result.chunks_checked == 3
    assert result.plan == framework.StyleLintPlan(
        files=[source.resolve()],
        files_checked=1,
        chunks_checked=3,
        detector_api_calls=2,
        max_verifier_api_calls=1,
        max_total_api_calls=3,
    )


def test_run_advisory_lint_dry_run_skips_verifier_when_no_chunks(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Skip verifier planning when chunking produces no detector work."""
    source = tmp_path / "empty.py"
    source.write_text("")

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
            verify_findings=True,
        )
    )

    assert result.plan == framework.StyleLintPlan(
        files=[source.resolve()],
        files_checked=1,
        chunks_checked=0,
        detector_api_calls=0,
        max_verifier_api_calls=0,
        max_total_api_calls=0,
    )
