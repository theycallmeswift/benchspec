"""Tests for shared agent base behaviors."""

from __future__ import annotations

from evalspec.agents.claude import ClaudeCodeAgent


def test_install_fingerprint_changes_with_version() -> None:
    """A different agent version yields a different install fingerprint."""
    older = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    newer = ClaudeCodeAgent(auth_value="test-token", version="1.2.4")

    assert older.install_fingerprint() != newer.install_fingerprint()


def test_install_fingerprint_is_stable_for_same_inputs() -> None:
    """The same version and provisioning inputs fingerprint identically."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")

    assert agent.install_fingerprint() == agent.install_fingerprint()
