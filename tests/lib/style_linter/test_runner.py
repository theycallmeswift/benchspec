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
        )
    )

    assert result.warning is None
    assert len(seen_prompts) == 2
