"""The backend-neutral sandbox runtime error.

`SandboxError` is the one runtime-failure type every `SandboxBackend` implementation
speaks, so agents and the CLI can classify a torn-down guest without importing (or even
knowing the name of) whatever concrete runtime package is behind the seam. This module
is deliberately import-free: agents import it directly rather than through
`benchspec.sandbox.backend`, which would create an import cycle through the
`benchspec.agents` package.
"""

from __future__ import annotations


class SandboxError(RuntimeError):
    """A sandbox runtime failure: daemon gone, VM or container killed, exec torn down.

    Every backend raises this directly, or wraps its own runtime's exceptions into it,
    so agents and the CLI classify a runtime failure without naming a concrete runtime.
    """
