"""Data models for advisory source linting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UsageMetadata:
    """Aggregated model usage for advisory lint requests."""

    requests: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class GeminiResponse:
    """Gemini text response plus optional usage metadata."""

    text: str
    usage: UsageMetadata = UsageMetadata()


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
