"""Tests for shared agent base behaviors."""

from __future__ import annotations

import asyncio

from evalspec.agents.base import probe_guest_version
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.agents.codex import CodexAgent


class _FakeGuestBackend:
    """A `SandboxBackend` stand-in exposing only the guest_shell seam."""

    def __init__(
        self: object, *, output: str | None = None, error: Exception | None = None
    ) -> None:
        """Configure what the fake guest_shell returns or raises."""
        self._output = output
        self._error = error
        self.calls: list[tuple[object, object, str]] = []

    async def guest_shell(self: object, sandbox: object, agent: object, script: str) -> str | None:
        """Record the call and return/raise as configured."""
        self.calls.append((sandbox, agent, script))
        if self._error is not None:
            raise self._error
        return self._output


class _FakeGuestAgent:
    """A minimal agent exposing `agent_bin`; `for_host` fails the test if ever called."""

    agent_bin = "claude"

    @classmethod
    def for_host(cls: object) -> object:
        """Fail the test — the guest probe must never rebind to the host."""
        raise AssertionError("probe_guest_version must not call for_host()")


def test_probe_guest_version_success_uses_guest_seam() -> None:
    """A successful guest_shell probe parses the version via the guest seam, not for_host."""
    backend = _FakeGuestBackend(output="claude-code 1.2.3\n")
    agent = _FakeGuestAgent()
    sandbox = object()

    version, error = asyncio.run(probe_guest_version(backend, sandbox, agent))

    assert (version, error) == ("1.2.3", None)
    assert backend.calls == [(sandbox, agent, "claude --version")]


def test_probe_guest_version_none_output_is_explained_failure() -> None:
    """guest_shell returning None (guest command failed) yields (None, <nonempty reason>)."""
    backend = _FakeGuestBackend(output=None)
    agent = _FakeGuestAgent()

    version, error = asyncio.run(probe_guest_version(backend, object(), agent))

    assert version is None
    assert error


def test_probe_guest_version_raising_guest_shell_is_explained_failure() -> None:
    """A raising guest_shell is caught and reported as an error, never propagated."""
    backend = _FakeGuestBackend(error=RuntimeError("sandbox unreachable"))
    agent = _FakeGuestAgent()

    version, error = asyncio.run(probe_guest_version(backend, object(), agent))

    assert version is None
    assert error


def test_probe_guest_version_unparseable_output_is_explained_failure() -> None:
    """Output with no dotted version token yields (None, <nonempty reason>), not a crash."""
    backend = _FakeGuestBackend(output="command not found")
    agent = _FakeGuestAgent()

    version, error = asyncio.run(probe_guest_version(backend, object(), agent))

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
