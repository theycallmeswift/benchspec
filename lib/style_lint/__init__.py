"""Reusable advisory source linter framework."""

from __future__ import annotations

from lib.style_lint.files import chunk_source_files, collect_python_files
from lib.style_lint.findings import format_findings, parse_findings, verify_findings
from lib.style_lint.gemini import GEMINI_TIMEOUT_SECONDS, call_gemini, urllib
from lib.style_lint.models import Finding, Rule, SourceChunk
from lib.style_lint.prompt import DEFAULT_SYSTEM_PROMPT, build_detector_prompt
from lib.style_lint.runner import (
    StyleLintConfig,
    StyleLintResult,
    run_advisory_lint,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "GEMINI_TIMEOUT_SECONDS",
    "Finding",
    "Rule",
    "SourceChunk",
    "StyleLintConfig",
    "StyleLintResult",
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
