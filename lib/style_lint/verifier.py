"""Second-pass verification for advisory lint findings."""

from __future__ import annotations

import importlib
import json

from lib.style_lint.detector import DEFAULT_SYSTEM_PROMPT, _strict_int
from lib.style_lint.types import Finding, Rule, SourceChunk


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


def _call_gemini(**kwargs: object) -> str:
    """Call the package-level Gemini transport for easy test stubbing."""
    style_lint = importlib.import_module("lib.style_lint")
    return style_lint.call_gemini(**kwargs)
