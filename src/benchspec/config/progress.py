"""Which live progress format a run prints: `cells` or `dots`."""

from __future__ import annotations

import pytest

PROGRESS_STYLES = ("cells", "dots")
DEFAULT_PROGRESS_STYLE = "cells"

# The resolved format, on the pytest config of every process in the run.
PROGRESS_STYLE = pytest.StashKey[str]()


def resolve_progress_style(flag: str | None, configured: object) -> str:
    """Resolve the progress format: the flag, then the config key, then `cells`.

    Args:
        flag: The `--benchspec-progress` value; None when not given.
        configured: The raw `[tool.benchspec] progress` value; None when absent.

    Raises:
        pytest.UsageError: an unknown value, naming its source and the allowed values.
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
