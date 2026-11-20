"""The packaged page: self-contained, and built from the design system."""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN = REPO_ROOT / "docs" / "design"
PAGE = resources.files("benchspec.watch") / "page"


def _page(name: str) -> str:
    """One packaged page file's text."""
    return PAGE.joinpath(name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["index.html", "app.css", "app.js"])
def test_the_page_loads_nothing_from_another_origin_and_nothing_inline(name: str) -> None:
    """The page loads nothing from another origin and nothing inline."""
    text = _page(name)

    assert not re.search(r"https?://|[\"'(]data:|<style|\sstyle=|<script(?![^>]*\ssrc=)", text)


def test_fonts_load_only_from_the_packaged_folder() -> None:
    """Fonts load only from the packaged folder."""
    sources = re.findall(r"@font-face \{[^}]*url\(\"([^\"]+)\"\)", _page("app.css"))

    assert sources
    assert all(source.startswith("fonts/") for source in sources)
    assert all(PAGE.joinpath(source).is_file() for source in sources)


def test_the_stylesheet_is_the_design_systems_tokens_and_components() -> None:
    """The stylesheet is the design systems tokens and components."""
    tokens = (DESIGN / "foundations" / "tokens.css").read_text(encoding="utf-8")
    bundle = (DESIGN / "components" / "bundle.css").read_text(encoding="utf-8")
    bracket = re.search(r"  --bs-bracket: url\(\"data:[^\n]*\n", bundle)
    assert bracket is not None

    stylesheet = _page("app.css")

    assert stylesheet.startswith(tokens)
    assert bundle.replace(bracket.group(), '  --bs-bracket: url("bracket.svg");\n') in stylesheet


def test_watch_adds_no_runtime_dependency() -> None:
    """Watch adds no runtime dependency."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == [
        "pytest>=8",
        "pytest-xdist>=3",
        "python-dotenv>=1.0",
        "pyyaml>=6",
    ]
