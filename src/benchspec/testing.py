"""Test doubles for `CodingAgent` authors.

`FakeSandbox` records calls and returns canned outputs, letting agent unit tests assert
command-building, secret-injection, and result-parsing behavior without spawning a
sandbox. It satisfies `benchspec.sandbox.backend.LiveSandbox` structurally, so it drops
into every code path a real guest does. Public so external `CodingAgent` implementers
don't have to copy-paste a sandbox stub. See `docs/harnesses.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field


@dataclass
class FakeExecOutput:
    """Provide a fake exec output for tests."""

    exit_code: int = 0
    stdout_text: str = ""
    stderr_text: str = ""

    @property
    def success(self) -> bool:
        """Whether the fake command exited zero."""
        return self.exit_code == 0


@dataclass
class FakeExecEvent:
    """One canned event for a `FakeExecStream`: an output chunk or a terminal status."""

    event_type: str
    data: bytes | None = None
    code: int | None = None


@dataclass
class FakeExecStream:
    """Replays canned events in order; records whether the consumer killed it."""

    events: list[FakeExecEvent] = field(default_factory=list)
    killed: bool = False

    def __aiter__(self) -> AsyncIterator[FakeExecEvent]:
        """Yield the canned events one by one."""
        return self._replay()

    async def _replay(self) -> AsyncIterator[FakeExecEvent]:
        """Drive the canned events as an async iterator."""
        for event in self.events:
            yield event

    async def kill(self) -> None:
        """Record the kill."""
        self.killed = True


@dataclass
class FakeSandbox:
    """Records calls; returns canned outputs.

    `exec_outputs` is consumed in order, one per exec call, falling back to `default_exec`
    when exhausted. `stream_events` feeds every `exec_stream` call. Each recorded call is a
    `(kind, ...)` tuple: `("shell", script, kwargs)`, `("exec", cmd, args, kwargs)`, or
    `("exec_stream", cmd, args, kwargs)`.
    """

    shell_output: FakeExecOutput = field(default_factory=FakeExecOutput)
    default_exec: FakeExecOutput = field(default_factory=FakeExecOutput)
    exec_outputs: list[FakeExecOutput] = field(default_factory=list)
    stream_events: list[FakeExecEvent] = field(default_factory=list)
    calls: list[tuple[object, ...]] = field(default_factory=list)
    stopped: bool = False

    async def shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> FakeExecOutput:
        """Record a fake shell command and return a canned output."""
        self.calls.append(("shell", script, {"env": env, "cwd": cwd}))
        return self.shell_output

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> FakeExecOutput:
        """Record a fake exec command and return a canned output."""
        self.calls.append(
            ("exec", cmd, args, {"cwd": cwd, "env": env, "timeout": timeout, "stdin": stdin})
        )
        if self.exec_outputs:
            return self.exec_outputs.pop(0)
        return self.default_exec

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> FakeExecStream:
        """Record a fake streaming exec and return a stream over the canned events."""
        self.calls.append(("exec_stream", cmd, args, {"cwd": cwd, "env": env, "stdin": stdin}))
        return FakeExecStream(events=list(self.stream_events))

    async def stop(self, timeout: float | None = None) -> None:
        """Mark the fake sandbox as stopped."""
        self.stopped = True
