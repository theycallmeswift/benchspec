"""One-call advisory lint runner."""

from __future__ import annotations

import importlib
import json
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lib.style_lint.detector import (
    DEFAULT_SYSTEM_PROMPT,
    build_detector_prompt,
    format_findings,
    parse_findings,
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


@dataclass(frozen=True)
class StyleLintConfig:
    """Configuration for one advisory lint run."""

    paths: list[Path] | None
    default_paths: tuple[Path, ...]
    rules: list[Rule]
    policy_instructions: str
    api_key: str
    model: str
    changed_lines: dict[Path, set[int]] | None = None
    verify_findings: bool = False
    verify_model: str | None = None
    max_lines: int = 120
    chunk_batch_size: int = 8
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    progress_callback: Callable[[Path, int, int], None] | None = None


@dataclass(frozen=True)
class StyleLintResult:
    """Result from an advisory lint run."""

    findings: list[Finding]
    diagnostics: list[str]
    warning: str | None = None
    usage: UsageMetadata = field(default_factory=UsageMetadata)
    files_checked: int = 0
    chunks_checked: int = 0


@dataclass(frozen=True)
class StyleLintPlan:
    """Planned work for one advisory lint run without model calls."""

    files: list[Path]
    files_checked: int
    chunks_checked: int
    detector_api_calls: int
    max_verifier_api_calls: int
    max_total_api_calls: int


def build_lint_plan(config: StyleLintConfig) -> StyleLintPlan:
    """Plan file, chunk, and API-call counts for an advisory lint run."""
    targets = collect_python_files(
        config.paths,
        default_paths=config.default_paths,
    )
    chunks = chunk_source_files(targets, max_lines=config.max_lines)
    detector_api_calls = len(_chunk_batches(chunks, size=config.chunk_batch_size))
    max_verifier_api_calls = (
        1 if config.verify_findings and detector_api_calls > 0 else 0
    )

    return StyleLintPlan(
        files=targets,
        files_checked=len(targets),
        chunks_checked=len(chunks),
        detector_api_calls=detector_api_calls,
        max_verifier_api_calls=max_verifier_api_calls,
        max_total_api_calls=detector_api_calls + max_verifier_api_calls,
    )


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
        findings: list[Finding] = []
        usage = UsageMetadata()
        progressed_files: set[Path] = set()
        for chunk_batch in _chunk_batches(chunks, size=config.chunk_batch_size):
            _emit_file_progress(
                chunks=chunk_batch,
                progressed_files=progressed_files,
                total_files=len(targets),
                progress_callback=config.progress_callback,
            )
            prompt = build_detector_prompt(
                chunks=chunk_batch,
                rules=config.rules,
                instructions=config.policy_instructions,
                system_prompt=config.system_prompt,
            )
            response = _response_parts(
                _call_gemini(
                    prompt=prompt,
                    api_key=config.api_key,
                    model=config.model,
                )
            )
            usage = _add_usage(usage, response.usage)
            findings.extend(
                parse_findings(
                    response.text,
                    chunks=chunks,
                    rules=config.rules,
                    drop_invalid=True,
                )
            )
        if config.verify_findings:
            verification = verify_findings(
                findings=findings,
                chunks=chunks,
                rules=config.rules,
                instructions=config.policy_instructions,
                api_key=config.api_key,
                model=config.verify_model or config.model,
                system_prompt=config.system_prompt,
            )
            findings = verification.findings
            usage = _add_usage(usage, verification.usage)

        if config.changed_lines is not None:
            findings = _findings_on_changed_lines(
                findings=findings,
                changed_lines=config.changed_lines,
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
            usage=usage if "usage" in locals() else UsageMetadata(),
            files_checked=len(targets) if "targets" in locals() else 0,
            chunks_checked=len(chunks) if "chunks" in locals() else 0,
        )

    return StyleLintResult(
        findings=findings,
        diagnostics=format_findings(findings),
        usage=usage,
        files_checked=len(targets),
        chunks_checked=len(chunks),
    )


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


def _add_usage(left: UsageMetadata, right: UsageMetadata) -> UsageMetadata:
    """Add two usage metadata values."""
    return UsageMetadata(
        requests=left.requests + right.requests,
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _emit_file_progress(
    *,
    chunks: list[SourceChunk],
    progressed_files: set[Path],
    total_files: int,
    progress_callback: Callable[[Path, int, int], None] | None,
) -> None:
    """Notify when model work reaches a new source file."""
    if progress_callback is None:
        return

    for chunk in chunks:
        if chunk.path in progressed_files:
            continue
        progressed_files.add(chunk.path)
        progress_callback(chunk.path, len(progressed_files), total_files)


def _findings_on_changed_lines(
    *,
    findings: list[Finding],
    changed_lines: dict[Path, set[int]],
) -> list[Finding]:
    """Return findings whose source line was touched by a changeset."""
    normalized_changes = {
        path.resolve(): lines for path, lines in changed_lines.items() if lines
    }
    return [
        finding
        for finding in findings
        if finding.line in normalized_changes.get(finding.path.resolve(), set())
    ]


def _chunk_batches(chunks: list[SourceChunk], *, size: int) -> list[list[SourceChunk]]:
    """Split chunks into fixed-size batches."""
    if size < 1:
        raise ValueError("chunk_batch_size must be at least 1")
    return [chunks[offset : offset + size] for offset in range(0, len(chunks), size)]
