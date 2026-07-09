"""Reusable advisory source linter framework."""

from __future__ import annotations

from lib.style_lint.detector import (
    DEFAULT_SYSTEM_PROMPT,
    build_detector_prompt,
    format_findings,
    parse_findings,
)
from lib.style_lint.gemini import GEMINI_TIMEOUT_SECONDS, call_gemini, urllib
from lib.style_lint.runner import (
    StyleLintConfig,
    StyleLintPlan,
    StyleLintResult,
    run_advisory_lint,
)
from lib.style_lint.source import chunk_source_files, collect_python_files
from lib.style_lint.types import (
    Finding,
    GeminiResponse,
    Rule,
    SourceChunk,
    UsageMetadata,
)
from lib.style_lint.verifier import verify_findings

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "GEMINI_TIMEOUT_SECONDS",
    "Finding",
    "GeminiResponse",
    "Rule",
    "SourceChunk",
    "StyleLintConfig",
    "StyleLintPlan",
    "StyleLintResult",
    "UsageMetadata",
    "build_detector_prompt",
    "call_gemini",
    "chunk_source_files",
    "collect_python_files",
    "format_findings",
    "parse_findings",
    "run_advisory_lint",
    "urllib",
    "verify_findings",
]
