"""Tests for shared agent base behaviors."""

from __future__ import annotations

import asyncio

import pytest

from benchspec.agents import base as agent_base
from benchspec.agents.base import CodingAgent, probe_guest_version
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.sandbox.backend import LiveSandbox
from benchspec.sandbox.microsandbox import MicrosandboxBackend
from benchspec.testing import FakeSandbox


class _FakeGuestBackend(MicrosandboxBackend):
    """The real backend with its guest_shell seam replaced by a recording, canned one."""

    def __init__(self, *, output: str | None = None, error: Exception | None = None) -> None:
        """Configure what the fake guest_shell returns or raises."""
        self._output = output
        self._error = error
        self.calls: list[tuple[LiveSandbox, CodingAgent, str]] = []

    async def guest_shell(
        self, sandbox: LiveSandbox, agent: CodingAgent, script: str
    ) -> str | None:
        """Record the call and return/raise as configured."""
        self.calls.append((sandbox, agent, script))
        if self._error is not None:
            raise self._error
        return self._output


class _FakeGuestAgent(ClaudeCodeAgent):
    """A real agent whose `for_host` fails the test if the guest probe ever rebinds."""

    @classmethod
    def for_host(cls, provider: str = "default") -> ClaudeCodeAgent:
        """Fail the test — the guest probe must never rebind to the host."""
        raise AssertionError("probe_guest_version must not call for_host()")


def _guest_agent() -> _FakeGuestAgent:
    """Build the guest-bound agent the probe runs `<agent_bin> --version` for."""
    return _FakeGuestAgent(auth_value="test-token", agent_bin="claude")


class _HangingGuestBackend(MicrosandboxBackend):
    """A guest backend whose version command never completes on its own."""

    async def guest_shell(
        self, sandbox: LiveSandbox, agent: CodingAgent, script: str
    ) -> str | None:
        """Wait forever to exercise the probe's internal deadline."""
        await asyncio.Event().wait()
        return None


def test_probe_guest_version_success_uses_guest_seam() -> None:
    """A successful guest_shell probe parses the version via the guest seam, not for_host."""
    backend = _FakeGuestBackend(output="claude-code 1.2.3\n")
    agent = _guest_agent()
    sandbox = FakeSandbox()

    version, error = asyncio.run(probe_guest_version(backend, sandbox, agent))

    assert (version, error) == ("1.2.3", None)
    assert backend.calls == [(sandbox, agent, "claude --version")]


def test_probe_guest_version_none_output_is_explained_failure() -> None:
    """guest_shell returning None (guest command failed) yields (None, <nonempty reason>)."""
    backend = _FakeGuestBackend(output=None)
    agent = _guest_agent()

    version, error = asyncio.run(probe_guest_version(backend, FakeSandbox(), agent))

    assert version is None
    assert error


def test_probe_guest_version_raising_guest_shell_is_explained_failure() -> None:
    """A raising guest_shell is caught and reported as an error, never propagated."""
    backend = _FakeGuestBackend(error=RuntimeError("sandbox unreachable"))
    agent = _guest_agent()

    version, error = asyncio.run(probe_guest_version(backend, FakeSandbox(), agent))

    assert version is None
    assert error


def test_probe_guest_version_unparseable_output_is_explained_failure() -> None:
    """Output with no dotted version token yields (None, <nonempty reason>), not a crash."""
    backend = _FakeGuestBackend(output="command not found")
    agent = _guest_agent()

    version, error = asyncio.run(probe_guest_version(backend, FakeSandbox(), agent))

    assert version is None
    assert error


def test_probe_guest_version_times_out_as_explained_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck guest command is bounded and reported instead of blocking the run."""
    monkeypatch.setattr(agent_base, "GUEST_VERSION_PROBE_TIMEOUT_SECONDS", 0.01, raising=False)
    backend = _HangingGuestBackend()
    agent = _guest_agent()

    version, error = asyncio.run(
        asyncio.wait_for(probe_guest_version(backend, FakeSandbox(), agent), timeout=0.1)
    )

    assert version is None
    assert error


def test_install_fingerprint_tracks_version_when_installer_pins_it() -> None:
    """When the installer bakes in the version, a version bump changes the fingerprint."""
    older = CodexAgent(version="1.2.3")
    newer = CodexAgent(version="1.2.4")

    assert older.install_fingerprint() != newer.install_fingerprint()


def test_install_fingerprint_ignores_version_when_installer_fetches_latest() -> None:
    """Claude's installer always fetches latest, so its version is not an install input.

    The install fingerprint is the same across versions — a version bump still rebuilds the
    snapshot, but via the `agent.version()` segment of the snapshot name, not this fingerprint.
    """
    older = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    newer = ClaudeCodeAgent(auth_value="test-token", version="1.2.4")

    assert older.install_fingerprint() == newer.install_fingerprint()
