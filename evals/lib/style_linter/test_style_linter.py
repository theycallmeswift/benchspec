"""Happy-path eval coverage for the reusable style-lint framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lib.style_lint as style_lint


def test_style_linter_reports_model_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one advisory lint pass with a stubbed model response."""
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

    monkeypatch.setattr(style_lint, "call_gemini", lambda **_kwargs: response)

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
            policy_instructions="Apply the local style guide.",
            api_key="test-key",
            model="gemini-test",
        )
    )

    assert result.warning is None
    assert result.diagnostics == [
        f"{source.resolve()}:1:1: descriptive-names Use a descriptive binding name."
    ]
