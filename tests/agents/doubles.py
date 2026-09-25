"""Typed views over the agent tests' shared doubles.

`FakeSandbox.calls` records each guest call as a loosely typed tuple; the readers here
narrow one recorded call back into a typed record so a test can assert on the script,
the argv, or the exec environment without re-checking shapes inline. `SlowSandbox` is a
`FakeSandbox` whose guest process takes a measurable moment to run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from benchspec.testing import FakeExecOutput, FakeSandbox

SLOW_EXEC_SECONDS = 0.05


@dataclass(frozen=True)
class ShellCall:
    """One recorded `FakeSandbox.shell` call."""

    script: str
    env: Mapping[str, str]
    cwd: str | None


@dataclass(frozen=True)
class ExecCall:
    """One recorded `FakeSandbox.exec` call."""

    cmd: str
    args: list[str]
    cwd: str | None
    env: Mapping[str, str]
    stdin: bytes | None


def _recorded_kwargs(raw: object) -> dict[str, object]:
    """Narrow the trailing kwargs entry of a recorded call to a dict."""
    assert isinstance(raw, dict)
    return {str(key): value for key, value in raw.items()}


def _recorded_env(kwargs: dict[str, object]) -> Mapping[str, str]:
    """Narrow a recorded call's `env` kwarg; the adapters always pass one."""
    env = kwargs["env"]
    assert isinstance(env, Mapping)
    return {str(key): str(value) for key, value in env.items()}


def _optional_str(value: object) -> str | None:
    """Narrow a recorded kwarg that is a string or unset."""
    assert value is None or isinstance(value, str)
    return value


def shell_call(sandbox: FakeSandbox, index: int = 0) -> ShellCall:
    """Return the recorded shell call at `index`, failing if it is another kind."""
    kind, script, raw_kwargs = sandbox.calls[index]
    assert kind == "shell"
    assert isinstance(script, str)
    kwargs = _recorded_kwargs(raw_kwargs)

    return ShellCall(
        script=script,
        env=_recorded_env(kwargs),
        cwd=_optional_str(kwargs["cwd"]),
    )


def exec_call(sandbox: FakeSandbox, index: int = 0) -> ExecCall:
    """Return the recorded exec call at `index`, failing if it is another kind."""
    kind, cmd, raw_args, raw_kwargs = sandbox.calls[index]
    assert kind == "exec"
    assert isinstance(cmd, str)
    assert isinstance(raw_args, list)
    kwargs = _recorded_kwargs(raw_kwargs)
    stdin = kwargs["stdin"]
    assert stdin is None or isinstance(stdin, bytes)

    return ExecCall(
        cmd=cmd,
        args=[str(arg) for arg in raw_args],
        cwd=_optional_str(kwargs["cwd"]),
        env=_recorded_env(kwargs),
        stdin=stdin,
    )


class SlowSandbox(FakeSandbox):
    """A `FakeSandbox` whose every exec takes `SLOW_EXEC_SECONDS` before returning."""

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
        """Wait, then record and answer the exec like `FakeSandbox`."""
        await asyncio.sleep(SLOW_EXEC_SECONDS)
        return await super().exec(cmd, args, cwd=cwd, env=env, timeout=timeout, stdin=stdin)
