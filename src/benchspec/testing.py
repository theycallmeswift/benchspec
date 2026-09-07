"""Test doubles for `CodingAgent` authors.

`FakeSandbox` records calls and returns canned outputs, letting agent unit tests assert
command-building, secret-injection, and result-parsing behavior without spawning a
sandbox. Public so external `CodingAgent` implementers don't have to copy-paste a
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
    when exhausted.
    """

    shell_output: FakeExecOutput = field(default_factory=FakeExecOutput)
    default_exec: FakeExecOutput = field(default_factory=FakeExecOutput)
    exec_outputs: list[FakeExecOutput] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    stopped: bool = False

    async def shell(self: object, script: str, **kw: object) -> FakeExecOutput:
        """Record a fake shell command and return a canned output."""
        self.calls.append(("shell", script, kw))
        return self.shell_output

    async def exec(self: object, cmd: str, args: object = None, **kw: object) -> FakeExecOutput:
        """Record a fake exec command and return a canned output."""
        self.calls.append(("exec", cmd, args, kw))
        if self.exec_outputs:
            return self.exec_outputs.pop(0)
        return self.default_exec

    async def stop(self: object, timeout: object = None) -> None:
        """Mark the fake sandbox as stopped."""
        self.stopped = True
        return None
