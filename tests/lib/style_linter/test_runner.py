"""Tests for the one-call advisory lint runner."""

from __future__ import annotations

import json
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
    plan = framework.build_lint_plan(
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
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert result.warning is None
    assert len(seen_prompts) == 2
    assert plan.detector_api_calls == len(seen_prompts)
    assert plan.files_checked == result.files_checked
    assert plan.chunks_checked == result.chunks_checked
    assert progress_events == [(source.resolve(), 1, 1)]
    assert result.files_checked == 1
    assert result.chunks_checked == 3


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


def test_build_lint_plan_predicts_detector_and_max_verifier_calls(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Plan detector batches and verifier upper bounds from real chunking."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    plan = framework.build_lint_plan(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            verify_findings=True,
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert plan.files == [source.resolve()]
    assert plan.files_checked == 1
    assert plan.chunks_checked == 3
    assert plan.detector_api_calls == 2
    assert plan.max_verifier_api_calls == 1
    assert plan.max_total_api_calls == 3


def test_build_lint_plan_skips_verifier_when_no_chunks(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Skip verifier planning when chunking produces no detector work."""
    source = tmp_path / "empty.py"
    source.write_text("")

    plan = framework.build_lint_plan(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            verify_findings=True,
        )
    )

    assert plan.files == [source.resolve()]
    assert plan.files_checked == 1
    assert plan.chunks_checked == 0
    assert plan.detector_api_calls == 0
    assert plan.max_verifier_api_calls == 0
    assert plan.max_total_api_calls == 0
