"""Typed access to the run options a pytest config (or the CLI adapter) exposes.

Every resolver — the eval set, the judge, the repo root — reads `--benchspec-*` values
through `getoption`, which pytest types as `Any` and the CLI adapter as `object`. The
accessors here are the one place that shape becomes a concrete type, failing loudly on
a value of the wrong kind instead of letting it drift into a resolver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RunOptions(Protocol):
    """What the resolvers need from a pytest `Config`, or from the `run` CLI adapter."""

    @property
    def rootpath(self) -> Path:
        """The directory the run resolves relative paths against."""
        ...

    def getoption(self, name: str) -> object:
        """Return the raw value of option `name` (None when unset)."""
        ...


def option_str(options: RunOptions, name: str) -> str | None:
    """Return a scalar string option, or None when unset."""
    value = options.getoption(name)
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"option {name} must be a string, got {type(value).__name__}")


def option_int(options: RunOptions, name: str) -> int | None:
    """Return an integer option, or None when unset."""
    value = options.getoption(name)
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise TypeError(f"option {name} must be an integer, got {type(value).__name__}")


def option_float(options: RunOptions, name: str) -> float | None:
    """Return a float option, or None when unset."""
    value = options.getoption(name)
    if value is None or (isinstance(value, float | int) and not isinstance(value, bool)):
        return value
    raise TypeError(f"option {name} must be a number, got {type(value).__name__}")


def option_str_list(options: RunOptions, name: str) -> list[str]:
    """Return a repeatable string option's values; an unset option is the empty list."""
    value = options.getoption(name)
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return value
    raise TypeError(f"option {name} must be a list of strings, got {type(value).__name__}")
