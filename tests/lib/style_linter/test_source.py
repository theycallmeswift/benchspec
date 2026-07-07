"""Tests for source collection and chunking."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.lib.style_linter._helpers import framework


def test_collect_python_files_defaults_to_configured_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect Python files from caller-provided default roots."""
    style_lint = framework()
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

    targets = style_lint.collect_python_files(
        None,
        default_paths=(Path("src"), Path("tests")),
    )

    assert targets == [src_file.resolve(), tests_file.resolve()]


def test_chunk_source_formats_numbered_source_lines(tmp_path: Path) -> None:
    """Format source chunks with stable source line numbers."""
    style_lint = framework()
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    chunks = style_lint.chunk_source_files([source], max_lines=2)

    assert [chunk.index for chunk in chunks] == [0, 1]
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert "1 | one = 1" in chunks[0].numbered_source
    assert "2 | two = 2" in chunks[0].numbered_source
    assert chunks[1].line_start == 3
    assert "3 | three = 3" in chunks[1].numbered_source
