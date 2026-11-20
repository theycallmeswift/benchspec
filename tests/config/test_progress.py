"""Tests for resolving the run's progress format."""

from __future__ import annotations

import pytest

from benchspec.config.progress import resolve_progress_style


@pytest.mark.parametrize(
    ("flag", "configured", "style"),
    [
        (None, None, "cells"),
        (None, "dots", "dots"),
        ("cells", "dots", "cells"),
        ("dots", None, "dots"),
        ("dots", "cells", "dots"),
    ],
)
def test_flag_beats_config_beats_the_cells_default(
    flag: str | None, configured: str | None, style: str
) -> None:
    """Verify flag beats config beats the cells default."""
    assert resolve_progress_style(flag, configured) == style


def test_unknown_flag_value_names_the_flag_and_the_allowed_values() -> None:
    """Verify unknown flag value names the flag and the allowed values."""
    with pytest.raises(pytest.UsageError) as raised:
        resolve_progress_style("spinner", "dots")

    assert str(raised.value) == (
        "--benchspec-progress='spinner' is not a progress format; allowed: cells, dots"
    )


@pytest.mark.parametrize("configured", ["spinner", 3, True])
def test_unknown_config_value_names_the_key_and_the_allowed_values(configured: object) -> None:
    """Verify unknown config value names the key and the allowed values."""
    with pytest.raises(pytest.UsageError) as raised:
        resolve_progress_style(None, configured)

    assert str(raised.value) == (
        f"[tool.benchspec] progress={configured!r} is not a progress format; allowed: cells, dots"
    )
