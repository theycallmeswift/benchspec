"""Stop a run on SIGTERM the way Ctrl-C stops it, so its sandboxes are torn down.

Python's default SIGTERM ends the process on the spot, skipping every `finally` and
`async with` exit, so a terminated worker leaves its live sandbox running until the
backend's own lifetime cap. Ctrl-C instead cancels the cell and unwinds through the
session's teardown. Routing SIGTERM to the SIGINT handler gives it that same path.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from types import FrameType


def _interrupt(signum: int, frame: FrameType | None) -> None:
    """Handle SIGTERM with whatever handles SIGINT now, or as a bare Ctrl-C.

    Inside `asyncio.run` that handler cancels the running cell; elsewhere it raises
    `KeyboardInterrupt`. When SIGINT is ignored, as in a background job, SIGTERM still
    stops the process.

    Raises:
        KeyboardInterrupt: When SIGINT has no Python handler to defer to.
    """
    handler = signal.getsignal(signal.SIGINT)
    if callable(handler):
        handler(signal.SIGINT, frame)
        return

    raise KeyboardInterrupt


def interrupt_on_sigterm() -> Callable[[], None]:
    """Route SIGTERM to the SIGINT path, and return the function that puts it back.

    Call from the main thread, which alone may set signal handlers.
    """
    previous = signal.signal(signal.SIGTERM, _interrupt)

    def restore() -> None:
        """Reinstate the SIGTERM handler that was in place before.

        A handler installed outside Python reads as None and cannot be reinstated.
        """
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)

    return restore
