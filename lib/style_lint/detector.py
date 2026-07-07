"""Detector prompt construction, parsing, and output formatting."""

from __future__ import annotations

import json
from textwrap import dedent

from lib.style_lint.types import Finding, Rule, SourceChunk

DEFAULT_SYSTEM_PROMPT = dedent("""\
    You are an advisory source linter. Review the numbered source chunks and
    return findings for only the supplied rules. Prefer precise,
    high-confidence findings over exhaustive guesses. Do not invent rules.
""")


def build_detector_prompt(
    *,
    chunks: list[SourceChunk],
    rules: list[Rule],
    instructions: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Build the detector prompt with strict JSON output instructions.

    Args:
        chunks: Numbered source chunks for model review.
        rules: Advisory rules owned by the caller.
        instructions: Caller-owned policy context for the model.
        system_prompt: Generic lint instructions shared by framework callers.

    Returns:
        A JSON prompt string containing rules, chunks, and schema instructions.
    """
    payload = {
        "system_prompt": system_prompt,
        "policy_instructions": instructions,
        "rules": [
            {
                "id": rule.id,
                "description": rule.description,
            }
            for rule in rules
        ],
        "source_chunks": [
            {
                "chunk_index": chunk.index,
                "path": str(chunk.path),
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "numbered_source": chunk.numbered_source,
            }
            for chunk in chunks
        ],
        "response_schema": {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "stable-rule-id",
                    "message": "Specific advisory message.",
                }
            ]
        },
        "response_rules": [
            "Return strict JSON only.",
            "Use only rule IDs from rules.",
            "Use only line numbers present in the referenced source chunk.",
            "Return an empty findings list when no advisory findings apply.",
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_findings(
    response: str,
    *,
    chunks: list[SourceChunk],
    rules: list[Rule],
) -> list[Finding]:
    """Parse and validate strict JSON detector findings.

    Args:
        response: Detector JSON response text.
        chunks: Source chunks originally sent to the detector.
        rules: Rules originally sent to the detector.

    Returns:
        Validated advisory findings.

    Raises:
        ValueError: If the model response violates the schema.
        json.JSONDecodeError: If the response is not JSON.
    """
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object")

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("Gemini response must include a findings list")

    rule_ids = {rule.id for rule in rules}
    chunks_by_index = {chunk.index: chunk for chunk in chunks}
    findings: list[Finding] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            raise ValueError("Each finding must be a JSON object")

        chunk_index = _strict_int(raw_finding.get("chunk_index"))
        line = _strict_int(raw_finding.get("line"))
        column = _strict_int(raw_finding.get("column"))
        if chunk_index is None or line is None or column is None:
            raise ValueError(f"Malformed finding reference: {raw_finding!r}")

        chunk = chunks_by_index.get(chunk_index)
        if chunk is None:
            raise ValueError(f"Malformed finding reference: {chunk_index!r}")
        if line < chunk.line_start or line > chunk.line_end:
            chunk = _chunk_for_line_in_same_file(
                chunks=chunks,
                referenced_chunk=chunk,
                line=line,
            )
            if chunk is None:
                raise ValueError(
                    f"Finding line {line} is outside chunk {chunk_index}"
                )
        if column < 1:
            raise ValueError(f"Malformed finding reference: {raw_finding!r}")

        rule_id = raw_finding.get("rule_id")
        if rule_id not in rule_ids:
            raise ValueError(f"Unknown rule ID: {rule_id}")

        message = raw_finding.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Finding message must be a non-empty string")

        findings.append(
            Finding(
                path=chunk.path,
                line=line,
                column=column,
                rule_id=rule_id,
                message=message.strip(),
            )
        )

    return findings


def format_findings(findings: list[Finding]) -> list[str]:
    """Format findings as path:line:column diagnostics."""
    return [
        (
            f"{finding.path}:{finding.line}:{finding.column}: "
            f"{finding.rule_id} {finding.message}"
        )
        for finding in findings
    ]


def _strict_int(value: object) -> int | None:
    """Return integer values while rejecting booleans."""
    if type(value) is int:
        return value
    return None


def _chunk_for_line_in_same_file(
    *,
    chunks: list[SourceChunk],
    referenced_chunk: SourceChunk,
    line: int,
) -> SourceChunk | None:
    """Return the same-file chunk containing a model-reported line."""
    for chunk in chunks:
        if (
            chunk.path == referenced_chunk.path
            and chunk.line_start <= line <= chunk.line_end
        ):
            return chunk
    return None
