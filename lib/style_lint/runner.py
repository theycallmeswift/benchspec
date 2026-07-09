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
    dry_run: bool = False
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
    plan: StyleLintPlan | None = None


@dataclass(frozen=True)
class StyleLintPlan:
    """Planned work for one advisory lint run without model calls."""

    files: list[Path]
    files_checked: int
    chunks_checked: int
    detector_api_calls: int
    max_verifier_api_calls: int
    max_total_api_calls: int


@dataclass(frozen=True)
class PreparedLintRun:
    """Source files, chunks, and detector batches for one lint run."""

    targets: list[Path]
    chunks: list[SourceChunk]
    chunk_batches: list[list[SourceChunk]]


@dataclass(frozen=True)
class PreparedSources:
    """Source files and chunks collected before detector batching."""

    targets: list[Path]
    chunks: list[SourceChunk]


def build_lint_plan(config: StyleLintConfig) -> StyleLintPlan:
    """Plan file, chunk, and API-call counts for an advisory lint run."""
    prepared_run = _prepare_lint_run(config)
    return _plan_from_prepared_run(config=config, prepared_run=prepared_run)


def run_advisory_lint(config: StyleLintConfig) -> StyleLintResult:
    """Run the advisory lint pipeline.

    Args:
        config: Complete advisory lint configuration.

    Returns:
        Findings, formatted diagnostics, and an optional advisory warning.
    """
    prepared_sources: PreparedSources | None = None
    try:
        prepared_sources = _prepare_sources(config)
        prepared_run = _prepare_lint_run(config, prepared_sources=prepared_sources)
        if config.dry_run:
            plan = _plan_from_prepared_run(
                config=config,
                prepared_run=prepared_run,
            )
            return StyleLintResult(
                findings=[],
                diagnostics=[],
                files_checked=plan.files_checked,
                chunks_checked=plan.chunks_checked,
                plan=plan,
            )

        findings: list[Finding] = []
        usage = UsageMetadata()
        progressed_files: set[Path] = set()
        for chunk_batch in prepared_run.chunk_batches:
            _emit_file_progress(
                chunks=chunk_batch,
                progressed_files=progressed_files,
                total_files=len(prepared_run.targets),
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
                    chunks=prepared_run.chunks,
                    rules=config.rules,
                    drop_invalid=True,
                )
            )
        if config.verify_findings:
            verification = verify_findings(
                findings=findings,
                chunks=prepared_run.chunks,
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
        return _warning_result(
            error=error,
            usage=usage if "usage" in locals() else UsageMetadata(),
            prepared_sources=prepared_sources,
        )

    return StyleLintResult(
        findings=findings,
        diagnostics=format_findings(findings),
        usage=usage,
        files_checked=len(prepared_run.targets),
        chunks_checked=len(prepared_run.chunks),
    )


def _call_gemini(**kwargs: object) -> object:
    """Call the package-level Gemini transport for easy test stubbing."""
    style_lint = importlib.import_module("lib.style_lint")
    return style_lint.call_gemini(**kwargs)


def _warning_result(
    *,
    error: Exception,
    usage: UsageMetadata,
    prepared_sources: PreparedSources | None,
) -> StyleLintResult:
    """Build an advisory warning result while preserving prepared scope counts."""
    files_checked = 0
    chunks_checked = 0
    if prepared_sources is not None:
        files_checked = len(prepared_sources.targets)
        chunks_checked = len(prepared_sources.chunks)

    return StyleLintResult(
        findings=[],
        diagnostics=[],
        warning=f"advisory style lint skipped due to model error: {error}",
        usage=usage,
        files_checked=files_checked,
        chunks_checked=chunks_checked,
    )


def _prepare_sources(config: StyleLintConfig) -> PreparedSources:
    """Collect source files and chunks for a lint run."""
    targets = collect_python_files(
        config.paths,
        default_paths=config.default_paths,
    )
    chunks = chunk_source_files(targets, max_lines=config.max_lines)

    return PreparedSources(targets=targets, chunks=chunks)


def _prepare_lint_run(
    config: StyleLintConfig,
    *,
    prepared_sources: PreparedSources | None = None,
) -> PreparedLintRun:
    """Collect source files, chunks, and detector batches for a lint run."""
    sources = (
        prepared_sources
        if prepared_sources is not None
        else _prepare_sources(config)
    )
    chunk_batches = _chunk_batches(sources.chunks, size=config.chunk_batch_size)

    return PreparedLintRun(
        targets=sources.targets,
        chunks=sources.chunks,
        chunk_batches=chunk_batches,
    )


def _plan_from_prepared_run(
    *,
    config: StyleLintConfig,
    prepared_run: PreparedLintRun,
) -> StyleLintPlan:
    """Build dry-run counts from prepared lint work."""
    detector_api_calls = len(prepared_run.chunk_batches)
    max_verifier_api_calls = (
        1 if config.verify_findings and detector_api_calls > 0 else 0
    )

    return StyleLintPlan(
        files=prepared_run.targets,
        files_checked=len(prepared_run.targets),
        chunks_checked=len(prepared_run.chunks),
        detector_api_calls=detector_api_calls,
        max_verifier_api_calls=max_verifier_api_calls,
        max_total_api_calls=detector_api_calls + max_verifier_api_calls,
    )


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
