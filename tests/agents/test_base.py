"""Tests for shared agent base behaviors."""

from __future__ import annotations

from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.agents.codex import CodexAgent


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
