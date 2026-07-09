"""Tests for second-pass advisory lint verification."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


def test_verify_findings_includes_docstring_exemption_in_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Serialize caller-owned docstring exemptions into verifier prompts."""
    source = tmp_path / "sample.py"
    source.write_text('"""Module docstring."""\n')
    chunks = framework.chunk_source_files([source], max_lines=80)
    finding = framework.Finding(
        path=source.resolve(),
        line=1,
        column=1,
        rule_id="dedented-multiline-strings",
        message="Use textwrap.dedent.",
    )
    captured_prompts: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        captured_prompts.append(str(kwargs["prompt"]))
        return json.dumps({"keep_indexes": []})

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    framework.verify_findings(
        findings=[finding],
        chunks=chunks,
        rules=[
            framework.Rule(
                id="dedented-multiline-strings",
                description=(
                    "Use textwrap.dedent for indented multiline string values. "
                    "Docstrings are exempt — indented multiline docstrings are correct."
                ),
            )
        ],
        instructions="Use the repository style guide.",
        api_key="test-key",
        model="gemini-test",
    )

    assert len(captured_prompts) == 1
    assert "indented multiline string values" in captured_prompts[0]
    assert "Docstrings are exempt" in captured_prompts[0]
