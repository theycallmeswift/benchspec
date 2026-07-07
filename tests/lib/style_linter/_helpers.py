"""Shared helpers for style-lint tests."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[3]


def framework() -> ModuleType:
    """Import the reusable style-lint framework."""
    return importlib.import_module("lib.style_lint")


def cli() -> ModuleType:
    """Import the repository-specific style-lint CLI module."""
    script_path = repo_root() / "bin/linters/style_lint.py"
    spec = importlib.util.spec_from_file_location(
        "evalspec_style_lint_cli",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not import style lint CLI")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
