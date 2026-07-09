"""Tests for source collection and chunking."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def test_collect_python_files_defaults_to_configured_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Collect Python files from caller-provided default roots."""
    src_file = tmp_path / "src/evalspec/example.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("value = 1\n")
    tests_file = tmp_path / "tests/test_example.py"
    tests_file.parent.mkdir(parents=True, exist_ok=True)
    tests_file.write_text("value = 2\n")
    ignored_file = tmp_path / "tools/ignored.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 3\n")

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        None,
        default_paths=(Path("src"), Path("tests")),
    )

    assert targets == [src_file.resolve(), tests_file.resolve()]


def test_collect_python_files_skips_gitignored_directory_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Skip ignored Python files discovered through directory scans."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path(".")],
        default_paths=(Path("src"),),
    )

    assert included_file.resolve() in targets
    assert ignored_file.resolve() not in targets


def test_collect_python_files_keeps_explicit_gitignored_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Keep explicitly named Python files even when Git would ignore them."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored.py\n")
    ignored_file = tmp_path / "ignored.py"
    ignored_file.write_text("value = 1\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path("ignored.py")],
        default_paths=(Path("src"),),
    )

    assert targets == [ignored_file.resolve()]


def test_collect_python_files_keeps_existing_scan_when_git_ignore_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Keep advisory collection usable when Git ignore checks are unavailable."""
    source = tmp_path / "src/example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("value = 1\n")

    class FakeSubprocess:
        """Stand in for a subprocess module that cannot run git."""

        @staticmethod
        def run(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError("git is unavailable")

    source_module = importlib.import_module("lib.style_lint.source")
    monkeypatch.setattr(source_module, "subprocess", FakeSubprocess, raising=False)
    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path("src")],
        default_paths=(Path("src"),),
    )

    assert targets == [source.resolve()]


def test_collect_python_files_uses_git_discovery_without_rglob_ignored_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Avoid Python recursion through ignored trees when Git discovery works."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    source_module = importlib.import_module("lib.style_lint.source")
    original_rglob = Path.rglob

    def fail_on_ignored_rglob(path: Path, pattern: str) -> object:
        if path.resolve() == (tmp_path / "ignored").resolve():
            raise AssertionError("ignored directory should not be traversed")
        return original_rglob(path, pattern)

    monkeypatch.setattr(source_module.Path, "rglob", fail_on_ignored_rglob)
    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path(".")],
        default_paths=(Path("src"),),
    )

    assert included_file.resolve() in targets
    assert ignored_file.resolve() not in targets


def test_collect_python_files_matches_repo_root_paths_from_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Resolve Git-discovered paths from the repository root."""
    source = tmp_path / "src/included.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("value = 1\n")
    nested_directory = tmp_path / "lib"
    nested_directory.mkdir()
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(nested_directory)

    targets = framework.collect_python_files(
        [Path("..")],
        default_paths=(Path("src"),),
    )

    assert targets == [source.resolve()]


def test_collect_python_files_skips_deleted_tracked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Ignore tracked Python paths that no longer exist in the worktree."""
    source = tmp_path / "src/included.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("value = 1\n")
    deleted_source = tmp_path / "src/deleted.py"
    deleted_source.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "add", "src/included.py", "src/deleted.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    deleted_source.unlink()

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path("src")],
        default_paths=(Path("src"),),
    )

    assert targets == [source.resolve()]


def test_chunk_source_formats_numbered_source_lines(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Format source chunks with stable source line numbers."""
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    chunks = framework.chunk_source_files([source], max_lines=2)

    assert [chunk.index for chunk in chunks] == [0, 1]
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert "1 | one = 1" in chunks[0].numbered_source
    assert "2 | two = 2" in chunks[0].numbered_source
    assert chunks[1].line_start == 3
    assert "3 | three = 3" in chunks[1].numbered_source
