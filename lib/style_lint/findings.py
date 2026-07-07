"""Finding parsing, verification, and formatting."""

from __future__ import annotations

import importlib
import json

from lib.style_lint.models import Finding, Rule, SourceChunk
from lib.style_lint.prompt import DEFAULT_SYSTEM_PROMPT


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


def verify_findings(
    *,
    findings: list[Finding],
    chunks: list[SourceChunk],
    rules: list[Rule],
    instructions: str,
    api_key: str,
    model: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> list[Finding]:
    """Ask a second model which detector findings to keep.

    Args:
        findings: Proposed detector findings.
        chunks: Source chunks originally reviewed.
        rules: Advisory rules originally reviewed.
        instructions: Caller-owned policy context.
        api_key: Gemini API key.
        model: Gemini verification model.
        system_prompt: Generic lint instructions shared by framework callers.

    Returns:
        Findings whose indexes the verifier kept.

    Raises:
        ValueError: If the verifier response violates the schema.
    """
    if not findings:
        return findings

    prompt = json.dumps(
        {
            "system_prompt": system_prompt,
            "policy_instructions": instructions,
            "rules": [
                {"id": rule.id, "description": rule.description}
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
            "proposed_findings": [
                {
                    "index": index,
                    "path": str(finding.path),
                    "line": finding.line,
                    "column": finding.column,
                    "rule_id": finding.rule_id,
                    "message": finding.message,
                }
                for index, finding in enumerate(findings)
            ],
            "response_schema": {"keep_indexes": [0]},
            "response_rules": [
                "Return strict JSON only.",
                "Keep only proposed findings that are supported by the source.",
            ],
        },
        indent=2,
        sort_keys=True,
    )
    response = _call_gemini(prompt=prompt, api_key=api_key, model=model)
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("Gemini verification response must be a JSON object")

    keep_indexes = payload.get("keep_indexes")
    if not isinstance(keep_indexes, list):
        raise ValueError("Gemini verification response must include keep_indexes")

    verified_findings: list[Finding] = []
    for keep_index in keep_indexes:
        parsed_keep_index = _strict_int(keep_index)
        if (
            parsed_keep_index is None
            or parsed_keep_index < 0
            or parsed_keep_index >= len(findings)
        ):
            raise ValueError(f"Malformed finding reference: {keep_index!r}")
        verified_findings.append(findings[parsed_keep_index])

    return verified_findings


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


def _call_gemini(**kwargs: object) -> str:
    """Call the package-level Gemini transport for easy test stubbing."""
    style_lint = importlib.import_module("lib.style_lint")
    return style_lint.call_gemini(**kwargs)
