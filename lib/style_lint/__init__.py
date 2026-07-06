"""Reusable advisory source linter framework."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GEMINI_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Rule:
    """One advisory style rule."""

    id: str
    description: str


@dataclass(frozen=True)
class SourceChunk:
    """A numbered source chunk sent to the detector model."""

    index: int
    path: Path
    line_start: int
    line_end: int
    numbered_source: str


@dataclass(frozen=True)
class Finding:
    """An advisory finding emitted by the detector model."""

    path: Path
    line: int
    column: int
    rule_id: str
    message: str


def collect_python_files(
    paths: list[Path] | None,
    *,
    default_paths: tuple[Path, ...],
) -> list[Path]:
    """Collect Python source files from explicit or default paths.

    Args:
        paths: Explicit file or directory paths. `None` uses `default_paths`.
        default_paths: Paths to scan when `paths` is absent.

    Returns:
        Sorted absolute paths for existing Python files.
    """
    roots = default_paths if paths is None else tuple(paths)
    targets: set[Path] = set()

    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.exists():
            continue
        if resolved_root.is_file():
            if resolved_root.suffix == ".py":
                targets.add(resolved_root)
            continue

        for path in resolved_root.rglob("*.py"):
            if path.is_file():
                targets.add(path.resolve())

    return sorted(targets)


def chunk_source_files(
    paths: list[Path],
    *,
    max_lines: int = 120,
) -> list[SourceChunk]:
    """Split source files into numbered chunks for model review.

    Args:
        paths: Python source files to chunk.
        max_lines: Maximum source lines per chunk.

    Returns:
        Source chunks with stable indexes and line numbers.

    Raises:
        ValueError: If `max_lines` is less than one.
        OSError: If a source file cannot be read.
        UnicodeDecodeError: If a source file is not UTF-8 compatible.
    """
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1")

    chunks: list[SourceChunk] = []
    for path in paths:
        lines = path.read_text().splitlines()
        if not lines:
            continue

        for offset in range(0, len(lines), max_lines):
            chunk_lines = lines[offset : offset + max_lines]
            line_start = offset + 1
            line_end = offset + len(chunk_lines)
            numbered_source = "\n".join(
                f"{line_number} | {line_text}"
                for line_number, line_text in enumerate(
                    chunk_lines,
                    start=line_start,
                )
            )
            chunks.append(
                SourceChunk(
                    index=len(chunks),
                    path=path.resolve(),
                    line_start=line_start,
                    line_end=line_end,
                    numbered_source=numbered_source,
                )
            )

    return chunks


def build_detector_prompt(
    *,
    chunks: list[SourceChunk],
    rules: list[Rule],
    instructions: str,
) -> str:
    """Build the detector prompt with strict JSON output instructions.

    Args:
        chunks: Numbered source chunks for model review.
        rules: Advisory rules owned by the caller.
        instructions: Caller-owned policy context for the model.

    Returns:
        A JSON prompt string containing rules, chunks, and schema instructions.
    """
    payload = {
        "instructions": instructions,
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


def call_gemini(
    *,
    prompt: str,
    api_key: str,
    model: str,
    timeout: float = GEMINI_TIMEOUT_SECONDS,
) -> str:
    """Call Gemini REST API and return the model text response.

    Args:
        prompt: Prompt text to send.
        api_key: Gemini API key.
        model: Gemini model name.
        timeout: Request timeout in seconds.

    Returns:
        Concatenated text parts from the first candidate.

    Raises:
        ValueError: If Gemini returns an unexpected payload shape.
        urllib.error.URLError: If transport fails.
        TimeoutError: If the request times out.
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    request_body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object")

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response did not include candidates")

    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise ValueError("Gemini response candidate must be an object")

    content = first_candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("Gemini response candidate did not include content")

    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Gemini response content did not include parts")

    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Gemini response part must be an object")
        text = part.get("text")
        if text is None:
            continue
        if not isinstance(text, str):
            raise ValueError("Gemini response part text must be a string")
        texts.append(text)

    if not texts:
        raise ValueError("Gemini response did not include text content")

    return "".join(texts)


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
) -> list[Finding]:
    """Ask a second model which detector findings to keep.

    Args:
        findings: Proposed detector findings.
        chunks: Source chunks originally reviewed.
        rules: Advisory rules originally reviewed.
        instructions: Caller-owned policy context.
        api_key: Gemini API key.
        model: Gemini verification model.

    Returns:
        Findings whose indexes the verifier kept.

    Raises:
        ValueError: If the verifier response violates the schema.
    """
    if not findings:
        return findings

    prompt = json.dumps(
        {
            "instructions": instructions,
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
    response = call_gemini(prompt=prompt, api_key=api_key, model=model)
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
