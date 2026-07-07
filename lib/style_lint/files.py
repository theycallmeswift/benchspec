"""File collection and source chunking for advisory linting."""

from __future__ import annotations

from pathlib import Path

from lib.style_lint.models import SourceChunk


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
