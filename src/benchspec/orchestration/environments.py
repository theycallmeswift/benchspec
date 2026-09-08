"""Execution environments: where a harness process runs.

An adapter builds commands and parses output; an `ExecutionEnv` owns actually running
the process. `Host` runs on the host machine (judge mode); `GuestSandbox` runs inside
a sandbox guest (task arms). The adapter is transport-blind — sandbox-vs-host is
a parameter, not a code path baked into each harness.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ProcResult:
    """The environment-neutral outcome of one executed harness process."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    def require_success(self: object) -> ProcResult:
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


@runtime_checkable
class ExecutionEnv(Protocol):
    """Anywhere a harness process can run — the transport half of an adapter call."""

    async def exec(
        self: object,
        command: list[str],
        *,
        env: dict,
        timeout: int,
        cwd: str | None = None,
        stdin: object = None,
    ) -> ProcResult:
        """Run `command` to completion and return its ProcResult."""
        ...


class Host:
    """Run harness processes directly on the host machine (the judge's environment)."""

    async def exec(
        self: object,
        command: list[str],
        *,
        env: dict,
        timeout: int,
        cwd: str | None = None,
        stdin: object = None,
    ) -> ProcResult:
        """Run `command` as a host subprocess with `env` merged over the host environment.

        A missing binary raises RuntimeError naming it; a subprocess.TimeoutExpired
        propagates unchanged (grade_run records a hung judge as a graded error).
        """
        run_env = {**os.environ, **env}
        # Never inherit the harness's stdin: a judge CLI like `codex exec` reads it as
        # "additional input", and concurrent judges (xdist) racing for the same terminal
        # fd make some exit nonzero. An explicit empty stdin isolates each process.
        proc_stdin = subprocess.DEVNULL if stdin is None else stdin
        try:
            proc = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True,
                timeout=timeout, env=run_env, cwd=cwd, stdin=proc_stdin,
            )
        except FileNotFoundError as error:
            raise RuntimeError(f"host {command[0]} CLI not found on PATH") from error
        return ProcResult(command, proc.returncode, proc.stdout, proc.stderr)


class GuestSandbox:
    """Run harness processes inside a live sandbox guest (the task arms' environment)."""

    def __init__(self: object, sandbox: object) -> None:
        """Wrap a live sandbox session's exec surface."""
        self._sandbox = sandbox

    async def exec(
        self: object,
        command: list[str],
        *,
        env: dict,
        timeout: int,
        cwd: str | None = None,
        stdin: object = None,
    ) -> ProcResult:
        """Run `command` inside the guest via the sandbox's exec primitive."""
        res = await self._sandbox.exec(
            command[0], command[1:], cwd=cwd, env=env, timeout=timeout, stdin=stdin,
        )
        return ProcResult(command, res.exit_code, res.stdout_text, res.stderr_text)
