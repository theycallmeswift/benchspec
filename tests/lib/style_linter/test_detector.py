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
    assert "source" in prompt
    assert "1 | x = 1" in prompt


def test_build_detector_prompt_limits_model_to_direct_rule_matches(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Constrain advisory findings to direct rule matches in executable code."""
    source = tmp_path / "sample.py"
    source.write_text('fixture = "x = 1\\n"\n')
    chunks = framework.chunk_source_files([source], max_lines=80)

    prompt = framework.build_detector_prompt(
        chunks=chunks,
        rules=[
            framework.Rule(
                id="descriptive-names",
                description="Do not use single-letter bindings except `_`.",
            )
        ],
        instructions="Use the repository style guide.",
    )

    assert "Ignore code examples and source text embedded inside strings" in prompt
    assert "Report a finding only when the source directly violates" in prompt
    assert (
        "Do not report general code-quality advice under unrelated rule IDs" in prompt
    )


def test_build_detector_prompt_includes_docstring_exemption(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Serialize caller-owned docstring exemptions into detector prompts."""
    source = tmp_path / "sample.py"
    source.write_text('"""Module docstring."""\n')
    chunks = framework.chunk_source_files([source], max_lines=80)

    prompt = framework.build_detector_prompt(
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
    )

    assert "indented multiline string values" in prompt
    assert "Docstrings are exempt" in prompt


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
                    "source": "value = 1",
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


def test_parse_findings_defaults_missing_columns_to_one(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Keep otherwise valid model findings when the optional column is absent."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "rule_id": "descriptive-names",
                    "source": "value = 1",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )

    findings = framework.parse_findings(
        response,
        chunks=chunks,
        rules=[
            framework.Rule(
                id="descriptive-names",
                description="Do not use single-letter bindings.",
            )
        ],
    )

    assert findings[0].column == 1


def test_parse_findings_rejects_source_excerpts_absent_from_reported_line(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Reject findings whose evidence does not appear on the reported line."""
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
                    "rule_id": "descriptive-names",
                    "source": "missing_name",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="source excerpt"):
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


def test_parse_findings_rejects_source_excerpts_inside_string_literals(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Reject fixture-code findings where evidence only appears in a string."""
    source = tmp_path / "sample.py"
    source.write_text('source.write_text("x = 1\\n")\n')
    chunks = framework.chunk_source_files([source], max_lines=80)
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

    with pytest.raises(ValueError, match="string literal"):
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


def test_parse_findings_uses_line_start_when_line_is_absent(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Keep model findings that use a line range instead of a single line."""
    source = tmp_path / "sample.py"
    source.write_text("value = 1\nother = 2\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line_start": 2,
                    "line_end": 2,
                    "rule_id": "semantic-block-newlines",
                    "source": "other = 2",
                    "message": "Use a blank line between semantic blocks.",
                }
            ]
        }
    )

    findings = framework.parse_findings(
        response,
        chunks=chunks,
        rules=[
            framework.Rule(
                id="semantic-block-newlines",
                description="Use blank lines to separate semantic blocks.",
            )
        ],
    )

    assert findings[0].line == 2
    assert findings[0].column == 1


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
                    "source": "value = 1",
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


def test_parse_findings_recovers_adjacent_same_file_chunk_references(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Recover when the model reports the right line with a stale chunk index."""
    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(f"value_{line_number} = 1" for line_number in range(1, 131))
    )
    chunks = framework.chunk_source_files([source], max_lines=120)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 127,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "source": "value_127 = 1",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )

    findings = framework.parse_findings(
        response,
        chunks=chunks,
        rules=[
            framework.Rule(
                id="descriptive-names",
                description="Do not use single-letter bindings.",
            )
        ],
    )

    assert findings[0].path == source.resolve()
    assert findings[0].line == 127
