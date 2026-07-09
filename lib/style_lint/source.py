"""File collection and source chunking for advisory linting."""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.style_lint.types import SourceChunk


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
    git_targets = _collect_python_files_with_git(roots)
    if git_targets is not None:
        return sorted(git_targets)

    return sorted(_collect_python_files_from_filesystem(roots))


def _collect_python_files_from_filesystem(roots: tuple[Path, ...]) -> set[Path]:
    """Collect Python files by walking the filesystem."""
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

    return targets


def _collect_python_files_with_git(roots: tuple[Path, ...]) -> set[Path] | None:
    """Collect Python files using Git's tracked and unignored file set."""
    try:
        completed_process = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.py",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None

    if completed_process.returncode != 0:
        return None

    cwd = Path.cwd()
    git_paths = {
        (cwd / relative_path).resolve()
        for relative_path in completed_process.stdout.splitlines()
        if relative_path
    }
    explicit_files = _collect_explicit_python_files(roots)
    directory_roots = _collect_directory_roots(roots)
    matched_git_paths = {
        git_path
        for git_path in git_paths
        if any(_is_relative_to(git_path, directory_root) for directory_root in directory_roots)
    }
    return explicit_files | matched_git_paths


def _collect_explicit_python_files(roots: tuple[Path, ...]) -> set[Path]:
    """Collect explicitly named Python files, even if Git ignores them."""
    explicit_files: set[Path] = set()
    for root in roots:
        resolved_root = root.resolve()
        if resolved_root.is_file() and resolved_root.suffix == ".py":
            explicit_files.add(resolved_root)

    return explicit_files


def _collect_directory_roots(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return existing directory roots that should be matched against Git paths."""
    return tuple(
        resolved_root
        for root in roots
        for resolved_root in [root.resolve()]
        if resolved_root.exists() and resolved_root.is_dir()
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether a path is contained by the given root."""
    try:
        path.relative_to(root)
    except ValueError:
        return False

    return True


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
