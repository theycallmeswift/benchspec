"""Which live progress format a run prints: `cells` or `dots`."""

from __future__ import annotations

import pytest

PROGRESS_STYLES = ("cells", "dots")
DEFAULT_PROGRESS_STYLE = "cells"


def resolve_progress_style(flag: str | None, configured: object) -> str:
    """The progress format for this run: --benchspec-progress > [tool.benchspec] progress > cells.

    Args:
        flag: The `--benchspec-progress` value, None when the flag was not given.
        configured: The raw `[tool.benchspec] progress` value, None when the key is absent.

    Returns:
        `cells` or `dots`.

    Raises:
        pytest.UsageError: an unknown value, naming the source it came from and the
            allowed values.
    """
    sources = (
        ("--benchspec-progress", flag),
        ("[tool.benchspec] progress", configured),
    )
    for source_name, value in sources:
        if value is None:
            continue
        if not isinstance(value, str) or value not in PROGRESS_STYLES:
            allowed = ", ".join(PROGRESS_STYLES)
            raise pytest.UsageError(
                f"{source_name}={value!r} is not a progress format; allowed: {allowed}"
            )

        return value

    return DEFAULT_PROGRESS_STYLE
