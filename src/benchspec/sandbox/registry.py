"""Backend selection: the one module that names a concrete sandbox runtime.

`registered_backends()` maps a set's `sandbox` value to its implementation and
`resolve_sandbox()` turns that value into a fresh backend instance. Nothing else in
benchspec names a runtime: `sandbox.py`, `execution.py`, and `cases.py` drive whatever
this module hands back. Adding a third backend is additive — implement the
`SandboxBackend` protocol in its own module and register it here.
"""

from __future__ import annotations

from collections.abc import Callable

from benchspec.sandbox.backend import SandboxBackend
from benchspec.sandbox.docker import DockerBackend
from benchspec.sandbox.microsandbox import MicrosandboxBackend
from benchspec.specs.schema import SchemaError

# The default every unpinned set and the bare build inherit; microsandbox
# is the microVM opt-in a set asks for by name.
DEFAULT_SANDBOX = "docker"


def registered_backends() -> dict[str, Callable[[], SandboxBackend]]:
    """Return every implemented backend as a fresh-instance factory, keyed by `sandbox` value."""
    return {"microsandbox": MicrosandboxBackend, "docker": DockerBackend}


def resolve_sandbox(name: str) -> SandboxBackend:
    """Return the backend for a set's `sandbox` value, or fail fast.

    Returns a FRESH backend instance each call. An unregistered name raises listing the
    supported values; the raised `SchemaError` maps to exit 2 through the CLI/plugin.
    """
    backends = registered_backends()
    if name in backends:
        return backends[name]()

    raise SchemaError(f"unsupported sandbox `{name}` (supported: {sorted(backends)})")
