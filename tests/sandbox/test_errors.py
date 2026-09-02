"""Tests for the backend-neutral sandbox-runtime error seam."""

from __future__ import annotations

import sys

from harnessbench.sandbox.errors import SandboxRuntimeError, sandbox_error_types


def test_sandbox_runtime_error_is_a_runtime_error() -> None:
    """The neutral infra error is a RuntimeError, matching the harness convention."""
    assert issubclass(SandboxRuntimeError, RuntimeError)


def test_sandbox_error_types_always_includes_the_neutral_error() -> None:
    """Whatever the host has installed, the neutral type is always catchable."""
    assert SandboxRuntimeError in sandbox_error_types()


def test_sandbox_error_types_includes_microsandbox_error_when_importable() -> None:
    """With the microsandbox package present, its native error joins the tuple.

    MicrosandboxError subclasses Exception directly, so no builtin covers it — an agent
    that dropped it from the tuple would crash the run instead of recording an errored arm.
    """
    from microsandbox.errors import MicrosandboxError

    assert MicrosandboxError in sandbox_error_types()


def test_sandbox_error_types_degrades_when_microsandbox_is_absent(monkeypatch: object) -> None:
    """On a host without microsandbox the tuple is just the neutral error, never a raise."""
    monkeypatch.setitem(sys.modules, "microsandbox.errors", None)

    assert sandbox_error_types() == (SandboxRuntimeError,)
