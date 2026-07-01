"""Test doubles for `CodingAgent` authors.

`FakeSandbox` records calls and returns canned outputs, letting agent unit tests
assert command-building, secret-injection, and result-parsing behavior without
spawning a microVM. Public so external `CodingAgent` implementers don't have to
copy-paste a sandbox stub. See `docs/agents.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeExecOutput:
    exit_code: int = 0
    stdout_text: str = ""
    stderr_text: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0


@dataclass
class FakeSandbox:
    """Records calls; returns canned outputs. `exec_outputs` is consumed in order
    (one per exec call), falling back to `default_exec` when exhausted."""

    shell_output: FakeExecOutput = field(default_factory=FakeExecOutput)
    default_exec: FakeExecOutput = field(default_factory=FakeExecOutput)
    exec_outputs: list[FakeExecOutput] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    stopped: bool = False

    async def shell(self, script: str, **kw) -> FakeExecOutput:
        self.calls.append(("shell", script, kw))
        return self.shell_output

    async def exec(self, cmd: str, args=None, **kw) -> FakeExecOutput:
        self.calls.append(("exec", cmd, args, kw))
        if self.exec_outputs:
            return self.exec_outputs.pop(0)
        return self.default_exec

    async def stop(self, timeout=None):
        self.stopped = True
        return None
