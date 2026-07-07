"""Tests for detector prompt construction and response validation."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


def test_build_detector_prompt_includes_rules_chunks_and_schema(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Build a strict detector prompt from caller-owned policy."""
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    rules = [
        framework.Rule(
            id="descriptive-names",
            description="Do not use single-letter bindings except `_`.",
        )
    ]

    prompt = framework.build_detector_prompt(
        chunks=chunks,
        rules=rules,
        instructions="Use the repository style guide.",
    )

    assert "Use the repository style guide." in prompt
    assert "Review the numbered source chunks" in prompt
    assert "descriptive-names" in prompt
    assert "chunk_index" in prompt
    assert "1 | x = 1" in prompt


def test_parse_findings_rejects_unknown_rule_ids(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Reject model findings that reference unknown rule identifiers."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "unknown-rule",
                    "message": "Bad rule.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="unknown-rule"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[framework.Rule(id="known-rule", description="Known rule.")],
        )


def test_parse_findings_rejects_boolean_indexes_and_lines(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Reject booleans where JSON schema requires integers."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": False,
                    "line": True,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Bad reference.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="Malformed finding reference"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
        )


def test_parse_findings_rejects_lines_outside_referenced_chunk(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Reject model findings that point outside the referenced source chunk."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 2,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Bad reference.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="outside chunk"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
        )
