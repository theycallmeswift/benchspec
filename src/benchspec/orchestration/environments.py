"""Execution environments: where a harness process runs.

An adapter builds commands and parses output; an `ExecutionEnv` owns actually running
the process. `Host` runs on the host machine (judge mode); `GuestSandbox` runs inside
a sandbox guest (task arms). The adapter is transport-blind — sandbox-vs-host is
a parameter, not a code path baked into each harness.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from benchspec.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from benchspec.sandbox.backend import ExecStream, LiveSandbox


@dataclass(frozen=True)
class ProcResult:
    """The environment-neutral outcome of one executed harness process."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    def require_success(self) -> ProcResult:
        """Return self on a zero exit; raise RuntimeError (with output tail) otherwise.

        RuntimeError is the infra-failure contract grading relies on: run_eval_arm
        surfaces it as arm-level errored instead of laundering it into fake
        assertion failures.
        """
        if self.exit_code != 0:
            # Show both streams: a CLI like `codex exec` writes its progress noise to
            # stderr but the actual failure to stdout, so a stderr-only tail hides the
            # real cause. Label each so the reader knows which is which.
            parts = [
                f"{label}: {stream.strip()[-1000:]}"
                for label, stream in (("stderr", self.stderr), ("stdout", self.stdout))
                if stream.strip()
            ]
            detail = "  ".join(parts) or "(no output)"
            raise RuntimeError(f"`{self.command[0]}` exited {self.exit_code}: {detail}")
        return self


@dataclass(frozen=True)
class WatchedProc:
    """The outcome of a harness process streamed past a line watcher.

    Either the watcher stopped it (`stopped`, and `exit_code` is None) or it exited on its
    own. A process that outlives its timeout raises instead, as a buffered exec does.
    """

    command: list[str]
    stdout: str
    stderr: str
    duration_ms: int
    exit_code: int | None = None
    stopped: bool = False


@runtime_checkable
class ExecutionEnv(Protocol):
    """Anywhere a harness process can run — the transport half of an adapter call.

    `stdin` is the bytes fed to the process; `None` and `b""` both mean a closed,
    empty stdin, so a harness that would otherwise block on an open pipe sees EOF.
    """

    async def exec(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        timeout: int,
        cwd: str | None = None,
        stdin: bytes | None = None,
    ) -> ProcResult:
        """Run `command` to completion and return its ProcResult."""
        ...


class Host:
    """Run harness processes directly on the host machine (the judge's environment)."""

    async def exec(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        timeout: int,
        cwd: str | None = None,
        stdin: bytes | None = None,
    ) -> ProcResult:
        """Run `command` as a host subprocess with `env` merged over the host environment.

        A missing binary raises RuntimeError naming it; a subprocess.TimeoutExpired
        propagates unchanged (grade_run records a hung judge as a graded error). The
        host never feeds a process input: a non-empty `stdin` is a programming error.
        """
        if stdin:
            raise ValueError("Host.exec cannot feed stdin; pass None or b'' to close it")
        run_env = {**os.environ, **env}

        def run_on_host() -> subprocess.CompletedProcess[str]:
            """Run the command synchronously; `to_thread` keeps the event loop free."""
            # Never inherit the harness's stdin: a judge CLI like `codex exec` reads it
            # as "additional input", and concurrent judges (xdist) racing for the same
            # terminal fd make some exit nonzero. An explicit empty stdin isolates each
            # process.
            return subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout, env=run_env, cwd=cwd, stdin=subprocess.DEVNULL,
            )

        started = time.monotonic()
        try:
            proc = await asyncio.to_thread(run_on_host)
        except FileNotFoundError as error:
            raise RuntimeError(f"host {command[0]} CLI not found on PATH") from error
        return ProcResult(
            command, proc.returncode, proc.stdout, proc.stderr, _elapsed_ms(started)
        )


class GuestSandbox:
    """Run harness processes inside a live sandbox guest (the task arms' environment)."""

    def __init__(self, sandbox: LiveSandbox) -> None:
        """Wrap a live sandbox session's exec surface."""
        self._sandbox = sandbox

    async def exec(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        timeout: int,
        cwd: str | None = None,
        stdin: bytes | None = None,
    ) -> ProcResult:
        """Run `command` inside the guest via the sandbox's exec primitive."""
        started = time.monotonic()
        res = await self._sandbox.exec(
            command[0], command[1:], cwd=cwd, env=env, timeout=timeout, stdin=stdin,
        )
        return ProcResult(
            command, res.exit_code, res.stdout_text, res.stderr_text, _elapsed_ms(started)
        )

    async def exec_watched(
        self,
        command: list[str],
        *,
        env: Mapping[str, str],
        timeout: float,
        watch: Callable[[str], bool],
        cwd: str | None = None,
        stdin: bytes | None = None,
    ) -> WatchedProc:
        """Stream `command` inside the guest, feeding each stdout line to `watch`.

        The process is killed the moment `watch` returns True, keeping the lines streamed
        so far. Chunks are reassembled into whole lines first, since a stream chunk can
        split a JSONL record (or a multibyte character) anywhere.

        Args:
            command: The harness argv.
            env: The process environment.
            timeout: Wall-clock cap, seconds.
            watch: Whether the process can stop after this line.
            cwd: Working directory inside the guest.
            stdin: Bytes fed to the process; `b""` closes it.

        Returns:
            The streamed output and how the process ended.

        Raises:
            TimeoutError: After killing the process, when it outlives `timeout`.
        """
        started = time.monotonic()
        handle = await self._sandbox.exec_stream(
            command[0], command[1:], cwd=cwd, env=env, stdin=stdin
        )
        lines: list[str] = []
        stderr_parts: list[str] = []
        exit_code: int | None = None
        stopped = False

        async def drain() -> None:
            """Collect stdout lines until the watcher stops the process or it exits."""
            nonlocal exit_code, stopped
            stdout_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            stderr_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            pending = ""

            async for event in handle:
                if event.event_type == "stdout":
                    pending += stdout_decoder.decode(event.data or b"")
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        lines.append(line)
                        if watch(line):
                            stopped = True
                            await _kill_quietly(handle)
                            return
                elif event.event_type == "stderr":
                    stderr_parts.append(stderr_decoder.decode(event.data or b""))
                elif event.event_type == "exited":
                    exit_code = event.code
                elif event.event_type == "failed":
                    # A failure event may carry no code; a real exit code 0 is preserved.
                    exit_code = event.code if event.code is not None else 1

            if pending.strip():
                lines.append(pending)

        try:
            await asyncio.wait_for(drain(), timeout=timeout)
        except TimeoutError as error:
            await _kill_quietly(handle)
            raise TimeoutError(f"{command[0]} timed out after {timeout}s") from error

        return WatchedProc(
            command=command,
            stdout="\n".join(lines),
            stderr="".join(stderr_parts),
            duration_ms=_elapsed_ms(started),
            exit_code=exit_code,
            stopped=stopped,
        )


async def _kill_quietly(handle: ExecStream) -> None:
    """Kill a streamed guest process; one already gone or unreachable is not an error."""
    with contextlib.suppress(SandboxError, TimeoutError, OSError):
        await handle.kill()


def _elapsed_ms(started: float) -> int:
    """Milliseconds elapsed since the `time.monotonic()` reading `started`."""
    return int((time.monotonic() - started) * 1000)
