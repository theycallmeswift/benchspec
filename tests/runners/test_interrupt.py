"""SIGTERM stops a run the way Ctrl-C does, so a cell's teardown still runs."""

from __future__ import annotations

import asyncio
import signal
import textwrap
from collections.abc import Callable, Iterator

import pytest

from benchspec.runners.interrupt import interrupt_on_sigterm
from tests.runners.test_cell_progress import STAND_IN_CONFTEST, _make_project, _run

# A cell that SIGTERMs its own worker mid-turn and marks the file its teardown writes,
# the stand-in for killing its sandbox.
SIGTERMED_CELL = textwrap.dedent(
    """\
    import asyncio
    import pathlib
    import signal


    def sigtermed_arm(*args, **kwargs):
        async def cell():
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.sleep(30)
            finally:
                pathlib.Path(os.environ["TORN_DOWN"]).write_text("torn down")

        asyncio.run(cell())


    cases.run_eval_arm = sigtermed_arm
    """
)


@pytest.fixture
def sigterm_handler() -> Iterator[Callable[..., object]]:
    """Route SIGTERM through `interrupt_on_sigterm`; yield the handler to call directly.

    Calling the handler, rather than sending the signal, keeps a regression from killing
    the test process.
    """
    restore = interrupt_on_sigterm()
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)

    try:
        yield handler
    finally:
        restore()


def test_sigterm_outside_an_event_loop_is_a_keyboard_interrupt(
    sigterm_handler: Callable[..., object],
) -> None:
    """With Python's own SIGINT handler in place, SIGTERM raises `KeyboardInterrupt`."""
    with pytest.raises(KeyboardInterrupt):
        sigterm_handler(signal.SIGTERM, None)


def test_sigterm_inside_asyncio_run_cancels_the_cell_so_its_teardown_runs(
    sigterm_handler: Callable[..., object],
) -> None:
    """Inside `asyncio.run`, SIGTERM cancels the main task like Ctrl-C, unwinding its finally."""
    torn_down: list[bool] = []

    async def cell() -> None:
        """Take SIGTERM mid-turn, then wait on the next await."""
        try:
            sigterm_handler(signal.SIGTERM, None)
            await asyncio.sleep(30)
        finally:
            torn_down.append(True)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(cell())

    assert torn_down == [True]


def test_sigterm_stops_a_process_that_ignores_sigint(
    sigterm_handler: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A background job ignores SIGINT; SIGTERM must still stop it."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        with pytest.raises(KeyboardInterrupt):
            sigterm_handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGINT, previous)


def test_restore_puts_the_previous_sigterm_handler_back() -> None:
    """The returned function reinstates the handler that was there before."""
    before = signal.getsignal(signal.SIGTERM)

    restore = interrupt_on_sigterm()
    during = signal.getsignal(signal.SIGTERM)
    restore()

    assert during is not before
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_sigtermed_xdist_worker_runs_its_cells_teardown(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker SIGTERMed mid-cell unwinds through the cell's teardown, then the run stops."""
    _make_project(pytester)
    pytester.makeconftest(STAND_IN_CONFTEST + SIGTERMED_CELL)
    torn_down = pytester.path / "torn-down"
    monkeypatch.setenv("TORN_DOWN", str(torn_down))

    result = _run(pytester, "-n", "2", "-k", "alpha and baseline")

    assert torn_down.read_text() == "torn down"
    assert result.ret == pytest.ExitCode.INTERRUPTED
