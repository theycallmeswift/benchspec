"""Test doubles for `CodingAgent` authors.

`FakeSandbox` records calls and returns canned outputs, letting agent unit tests assert
command-building, secret-injection, and result-parsing behavior without spawning a
microVM. Public so external `CodingAgent` implementers don't have to copy-paste a
sandbox stub. See `docs/harnesses.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeExecOutput:
    """Provide a fake exec output for tests."""

    exit_code: int = 0
    stdout_text: str = ""
    stderr_text: str = ""

    @property
    def success(self: object) -> bool:
        """Create a fake successful exec output for tests."""
        return self.exit_code == 0


@dataclass
class FakeSandbox:
    """Records calls; returns canned outputs.

    `exec_outputs` is consumed in order, one per exec call, falling back to `default_exec`
    when exhausted. Set `exec_error` to make `exec()` raise instead of returning, for
    exercising a caller's handling of a torn-down sandbox runtime. Set `stop_error` to make
    `stop()` raise after recording, for exercising a caller's handling of teardown failing.
    """

    shell_output: FakeExecOutput = field(default_factory=FakeExecOutput)
    default_exec: FakeExecOutput = field(default_factory=FakeExecOutput)
    exec_outputs: list[FakeExecOutput] = field(default_factory=list)
    exec_error: BaseException | None = None
    stop_error: BaseException | None = None
    calls: list[tuple] = field(default_factory=list)
    stopped: bool = False

    async def shell(self: object, script: str, **kw: object) -> FakeExecOutput:
        """Record a fake shell command and return a canned output."""
        self.calls.append(("shell", script, kw))
        return self.shell_output

    async def exec(self: object, cmd: str, args: object = None, **kw: object) -> FakeExecOutput:
        """Record a fake exec command and return a canned output, or raise `exec_error`."""
        self.calls.append(("exec", cmd, args, kw))
        if self.exec_error is not None:
            raise self.exec_error
        if self.exec_outputs:
            return self.exec_outputs.pop(0)
        return self.default_exec

    async def stop(self: object, timeout: object = None) -> None:
        """Mark the fake sandbox as stopped, or raise `stop_error` instead."""
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error
        return None
