"""The backend-neutral sandbox-runtime failure type.

An agent adapter must be able to classify "the sandbox itself broke" — the daemon went
away, the container was OOM-killed, an exec was torn down — as a recorded infra error
without naming a concrete runtime. `SandboxRuntimeError` is what harnessbench's own
backends raise for that, and `sandbox_error_types()` folds in each runtime's native
exception type so one `except` tuple covers every backend.

IMPORTANT: importing this module must NOT import the `microsandbox` package. The
`import microsandbox` stays inside the function body so `lint`/`analyze` keep working on
a host where the package is absent.
"""

from __future__ import annotations


class SandboxRuntimeError(RuntimeError):
    """A sandbox-runtime failure at the guest boundary, independent of the backend.

    Subclasses RuntimeError to match the harness's existing infra-failure convention
    (`ProcResult.require_success`): grading records the arm as errored and excludes it
    rather than laundering the failure into fake assertion misses.
    """


def sandbox_error_types() -> tuple[type[BaseException], ...]:
    """Return every exception type that means "the sandbox broke".

    Always includes `SandboxRuntimeError`. When the `microsandbox` package is importable
    it also includes that runtime's native `MicrosandboxError`, which subclasses
    `Exception` directly and is therefore covered by no builtin.

    Returns:
        A tuple of exception classes, ready for
        `except (TimeoutError, OSError, *sandbox_error_types())`.
    """
    try:
        from microsandbox.errors import MicrosandboxError
    except ImportError:
        return (SandboxRuntimeError,)
    return (SandboxRuntimeError, MicrosandboxError)
