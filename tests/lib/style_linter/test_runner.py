"""Tests for the one-call advisory lint runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.lib.style_linter._helpers import framework


def test_run_advisory_lint_encapsulates_framework_call_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the reusable lint pipeline from one config object."""
    style_lint = framework()
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

    monkeypatch.setattr(style_lint, "call_gemini", _call_gemini)

    result = style_lint.run_advisory_lint(
        style_lint.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                style_lint.Rule(
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
