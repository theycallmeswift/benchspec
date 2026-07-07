"""Detector prompt construction, parsing, and output formatting."""

from __future__ import annotations

import json
import tokenize
from io import StringIO
from textwrap import dedent

from lib.style_lint.types import Finding, Rule, SourceChunk

DEFAULT_SYSTEM_PROMPT = dedent("""\
    You are an advisory source linter. Review the numbered source chunks and
    return findings for only the supplied rules. Prefer precise,
    high-confidence findings over exhaustive guesses. Do not invent rules.
    Report a finding only when the source directly violates a supplied rule.
    Do not report general code-quality advice under unrelated rule IDs.
    Ignore code examples and source text embedded inside strings; lint the
    surrounding program, not fixture snippets or documentation examples.
    For each finding, source must be non-empty text copied from the reported
    line after the pipe separator. Do not include the line number or pipe in
    source, and do not report blank lines as source evidence.
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
                    "source": "exact source excerpt from the reported line",
                    "message": "Specific advisory message.",
                }
            ]
        },
        "response_rules": [
            "Return strict JSON only.",
            "Use only rule IDs from rules.",
            "Use only line numbers present in the referenced source chunk.",
            "Include source as an exact excerpt from the reported source line.",
            "Do not include the numbered-source prefix in source.",
            "Do not report findings whose source excerpt would be blank.",
            "Return an empty findings list when no advisory findings apply.",
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_findings(
    response: str,
    *,
    chunks: list[SourceChunk],
    rules: list[Rule],
    drop_invalid: bool = False,
) -> list[Finding]:
    """Parse and validate strict JSON detector findings.

    Args:
        response: Detector JSON response text.
        chunks: Source chunks originally sent to the detector.
        rules: Rules originally sent to the detector.
        drop_invalid: When true, skip malformed individual findings while
            preserving otherwise valid findings from the same response.

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
        try:
            findings.append(
                _parse_finding(
                    raw_finding,
                    chunks=chunks,
                    chunks_by_index=chunks_by_index,
                    rule_ids=rule_ids,
                )
            )
        except ValueError:
            if drop_invalid:
                continue
            raise

    return findings


def _parse_finding(
    raw_finding: object,
    *,
    chunks: list[SourceChunk],
    chunks_by_index: dict[int, SourceChunk],
    rule_ids: set[str],
) -> Finding:
    """Parse one model finding into a validated advisory finding."""
    if not isinstance(raw_finding, dict):
        raise ValueError("Each finding must be a JSON object")

    chunk_index = _strict_int(raw_finding.get("chunk_index"))
    line = _strict_int(raw_finding.get("line"))
    if line is None:
        line = _strict_int(raw_finding.get("line_start"))
    column = _strict_int(raw_finding.get("column", 1))
    if chunk_index is None or line is None:
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
            raise ValueError(f"Finding line {line} is outside chunk {chunk_index}")
    if column < 1:
        raise ValueError(f"Malformed finding reference: {raw_finding!r}")

    source = raw_finding.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Finding source excerpt must be a non-empty string")
    line_text = _source_line_text(chunk=chunk, line=line)
    if line_text is None or source.strip() not in line_text:
        raise ValueError(
            f"Finding source excerpt is absent from line {line}: {source!r}"
        )
    if _source_excerpt_is_inside_string(line_text=line_text, source=source.strip()):
        raise ValueError(
            f"Finding source excerpt is inside a string literal: {source!r}"
        )

    rule_id = raw_finding.get("rule_id")
    if rule_id not in rule_ids:
        raise ValueError(f"Unknown rule ID: {rule_id}")

    message = raw_finding.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Finding message must be a non-empty string")

    return Finding(
        path=chunk.path,
        line=line,
        column=column,
        rule_id=rule_id,
        message=message.strip(),
    )


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


def _source_line_text(*, chunk: SourceChunk, line: int) -> str | None:
    """Return the unnumbered source text for a line in a source chunk."""
    line_prefix = f"{line} | "
    for numbered_line in chunk.numbered_source.splitlines():
        if numbered_line.startswith(line_prefix):
            return numbered_line.removeprefix(line_prefix)
    return None


def _source_excerpt_is_inside_string(*, line_text: str, source: str) -> bool:
    """Return whether the source excerpt appears inside a string literal."""
    try:
        tokens = tokenize.generate_tokens(StringIO(line_text).readline)
        return any(
            token.type == tokenize.STRING and source in token.string
            for token in tokens
        )
    except tokenize.TokenError:
        return False


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
