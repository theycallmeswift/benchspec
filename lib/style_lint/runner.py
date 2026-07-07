"""One-call advisory lint runner."""

from __future__ import annotations

import importlib
import json
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from lib.style_lint.detector import (
    DEFAULT_SYSTEM_PROMPT,
    build_detector_prompt,
    format_findings,
    parse_findings,
)
from lib.style_lint.source import chunk_source_files, collect_python_files
from lib.style_lint.types import Finding, Rule
from lib.style_lint.verifier import verify_findings


@dataclass(frozen=True)
class StyleLintConfig:
    """Configuration for one advisory lint run."""

    paths: list[Path] | None
    default_paths: tuple[Path, ...]
    rules: list[Rule]
    policy_instructions: str
    api_key: str
    model: str
    verify_model: str | None = None
    max_lines: int = 120
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class StyleLintResult:
    """Result from an advisory lint run."""

    findings: list[Finding]
    diagnostics: list[str]
    warning: str | None = None


def run_advisory_lint(config: StyleLintConfig) -> StyleLintResult:
    """Run the advisory lint pipeline.

    Args:
        config: Complete advisory lint configuration.

    Returns:
        Findings, formatted diagnostics, and an optional advisory warning.
    """
    try:
        targets = collect_python_files(
            config.paths,
            default_paths=config.default_paths,
        )
        chunks = chunk_source_files(targets, max_lines=config.max_lines)
        prompt = build_detector_prompt(
            chunks=chunks,
            rules=config.rules,
            instructions=config.policy_instructions,
            system_prompt=config.system_prompt,
        )
        response = _call_gemini(
            prompt=prompt,
            api_key=config.api_key,
            model=config.model,
        )
        findings = parse_findings(response, chunks=chunks, rules=config.rules)
        if config.verify_model is not None:
            findings = verify_findings(
                findings=findings,
                chunks=chunks,
                rules=config.rules,
                instructions=config.policy_instructions,
                api_key=config.api_key,
                model=config.verify_model,
                system_prompt=config.system_prompt,
            )
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        urllib.error.URLError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        return StyleLintResult(
            findings=[],
            diagnostics=[],
            warning=f"advisory style lint skipped due to model error: {error}",
        )

    return StyleLintResult(
        findings=findings,
        diagnostics=format_findings(findings),
    )


def _call_gemini(**kwargs: object) -> str:
    """Call the package-level Gemini transport for easy test stubbing."""
    style_lint = importlib.import_module("lib.style_lint")
    return style_lint.call_gemini(**kwargs)
