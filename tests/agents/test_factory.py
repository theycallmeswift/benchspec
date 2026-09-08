"""Tests for factory."""

from __future__ import annotations

import pytest

from benchspec.agents import make_agent


def test_make_agent_explicit_harness_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify make agent explicit harness overrides env."""
    monkeypatch.setenv("BENCHSPEC_AGENT", "claude-code")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")  # opencode credential for from_env()
    agent = make_agent("opencode")
    assert agent.id == "opencode"


def test_make_agent_explicit_codex_harness_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify make agent explicit codex harness overrides env."""
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setenv("BENCHSPEC_AGENT", "opencode")

    agent = make_agent("codex")

    assert agent.id == "codex"


def test_make_agent_unknown_harness_raises() -> None:
    """Verify make agent unknown harness raises."""
    with pytest.raises(RuntimeError, match="not a known agent"):
        make_agent("cursor")


def test_make_agent_default_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify make agent default reads env."""
    monkeypatch.setenv("BENCHSPEC_AGENT", "claude-code")
    assert make_agent().id == "claude-code"


def test_make_agent_default_reads_codex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify make agent default reads codex env."""
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setenv("BENCHSPEC_AGENT", "codex")

    agent = make_agent()

    assert agent.id == "codex"
