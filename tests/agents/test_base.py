"""Tests for shared agent base behaviors."""

from __future__ import annotations

from evalspec.agents.claude import ClaudeCodeAgent


def test_install_fingerprint_defaults_to_version() -> None:
    """The default install fingerprint is the agent version string."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    assert agent.install_fingerprint() == agent.version()
    assert agent.install_fingerprint() == "1.2.3"
