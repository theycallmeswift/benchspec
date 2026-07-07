"""Second-pass verification for advisory lint findings."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass

from lib.style_lint.detector import DEFAULT_SYSTEM_PROMPT, _strict_int
from lib.style_lint.types import (
    Finding,
    GeminiResponse,
    Rule,
    SourceChunk,
    UsageMetadata,
)


@dataclass(frozen=True)
class VerificationResult:
    """Verified findings and verifier model usage."""

    findings: list[Finding]
    usage: UsageMetadata


def verify_findings(
    *,
    findings: list[Finding],
    chunks: list[SourceChunk],
    rules: list[Rule],
    instructions: str,
    api_key: str,
    model: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> VerificationResult:
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
        Findings whose indexes the verifier kept plus usage metadata.

    Raises:
        ValueError: If the verifier response violates the schema.
    """
    if not findings:
        return VerificationResult(findings=findings, usage=UsageMetadata())

    prompt = json.dumps(
        {
            "task": "Verify proposed advisory lint findings.",
            "system_prompt": system_prompt,
            "policy_instructions": instructions,
            "rules": [
                {"id": rule.id, "description": rule.description}
                for rule in rules
            ],
            "decision_rubric": [
                (
                    'Return a JSON object exactly like {"keep_indexes": [0]}. '
                    "Do not return finding objects."
                ),
                (
                    "Keep only findings that directly violate the named rule "
                    "and are supported by the shown line context."
                ),
                (
                    "For descriptive-names, keep only if the reported source "
                    "line visibly binds a single-character name other than `_`. "
                    "Reject kwargs, args, function calls, and helper function "
                    "definitions."
                ),
                (
                    "For semantic-block-newlines, keep only if the source has "
                    "adjacent executable statements in the same scope that need "
                    "a blank line. Reject dataclass fields, class/function "
                    "boundaries, parse/validate steps, and returns already "
                    "separated by a blank line."
                ),
            ],
            "proposed_findings": [
                {
                    "index": index,
                    "path": str(finding.path),
                    "line": finding.line,
                    "column": finding.column,
                    "rule_id": finding.rule_id,
                    "message": finding.message,
                    "line_context": _line_context(chunks=chunks, finding=finding),
                }
                for index, finding in enumerate(findings)
            ],
        },
        indent=2,
        sort_keys=True,
    )
    response = _response_parts(
        _call_gemini(prompt=prompt, api_key=api_key, model=model)
    )
    payload = json.loads(response.text)

    keep_indexes = _keep_indexes(payload)
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

    return VerificationResult(findings=verified_findings, usage=response.usage)


def _call_gemini(**kwargs: object) -> object:
    """Call the package-level Gemini transport for easy test stubbing."""
    style_lint = importlib.import_module("lib.style_lint")
    return style_lint.call_gemini(**kwargs)


def _response_parts(response: object) -> GeminiResponse:
    """Normalize string stubs and real Gemini responses."""
    if isinstance(response, GeminiResponse):
        return response
    if isinstance(response, str):
        return GeminiResponse(text=response)
    raise ValueError("Gemini response must be text or GeminiResponse")


def _keep_indexes(payload: object) -> object:
    """Return verifier keep indexes from supported response shapes."""
    if isinstance(payload, dict):
        return payload.get("keep_indexes")
    if isinstance(payload, list):
        return [
            finding.get("index")
            for finding in payload
            if isinstance(finding, dict) and "index" in finding
        ]
    raise ValueError("Gemini verification response must be a JSON object")


def _line_context(*, chunks: list[SourceChunk], finding: Finding) -> str:
    """Return nearby numbered source lines for one finding."""
    for chunk in chunks:
        if chunk.path != finding.path:
            continue
        if not chunk.line_start <= finding.line <= chunk.line_end:
            continue
        selected_lines = []
        for numbered_line in chunk.numbered_source.splitlines():
            line_number = int(numbered_line.split(" | ", 1)[0])
            if finding.line - 2 <= line_number <= finding.line + 2:
                selected_lines.append(numbered_line)
        return "\n".join(selected_lines)
    return ""
