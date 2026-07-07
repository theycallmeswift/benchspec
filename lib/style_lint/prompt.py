"""Prompt construction for advisory source linting."""

from __future__ import annotations

import json
from textwrap import dedent

from lib.style_lint.models import Rule, SourceChunk

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
