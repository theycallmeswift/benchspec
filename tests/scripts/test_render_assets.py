"""The brand-asset renderer: bundled fonts only, outlined wordmarks, stable output."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest
from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render_assets.py"
SVG_PATH = "{http://www.w3.org/2000/svg}path"
SVG_TEXT = "{http://www.w3.org/2000/svg}text"


def _load_script() -> ModuleType:
    """Import ``scripts/render_assets.py``, which is not on the import path."""
    spec = importlib.util.spec_from_file_location("render_assets", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


render_assets = _load_script()


@pytest.mark.parametrize("weight", list(render_assets.FontWeight))
def test_load_font_raises_when_the_bundled_file_is_missing(
    weight: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_font raises when the bundled file is missing, with no system fallback."""
    monkeypatch.setattr(render_assets, "FONTS_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match=f"JetBrainsMono-{weight}.ttf"):
        render_assets.load_font(15, weight)


@pytest.mark.parametrize("name", ["benchspec-wordmark-light.svg", "benchspec-wordmark-dark.svg"])
def test_a_wordmark_is_outlined_paths_inside_its_view_box(name: str) -> None:
    """A wordmark is outlined paths inside its viewBox."""
    svg = ElementTree.fromstring(render_assets.logo_svgs()[name])

    bounds = BoundsPen(None)
    for path in svg.iter(SVG_PATH):
        parse_path(path.attrib["d"], bounds)
    assert bounds.bounds is not None
    left, top, right, bottom = bounds.bounds
    _, _, width, height = (float(value) for value in svg.attrib["viewBox"].split())

    assert svg.find(SVG_TEXT) is None
    assert svg.attrib["aria-label"] == "benchspec"
    assert 0 <= left < right <= width
    assert 0 <= top < bottom <= height


def test_two_renders_write_the_same_bytes(tmp_path: Path) -> None:
    """Two renders write the same bytes."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_files = render_assets.render_assets(first)
    second_files = render_assets.render_assets(second)

    assert [path.name for path in first_files] == [path.name for path in second_files]
    assert len(first_files) == 7
    assert [path.read_bytes() for path in first_files] == [
        path.read_bytes() for path in second_files
    ]
