"""Tests for factory."""

from __future__ import annotations

import pytest

from benchspec.agents import credential_preflight_error, known_providers, make_agent
from benchspec.agents.claude import ClaudeCodeAgent


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


def test_known_providers_are_default_and_openrouter() -> None:
    """Verify the provider enum is exactly the two transports the config accepts."""
    assert known_providers() == frozenset({"default", "openrouter"})


def test_make_agent_binds_the_arm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the arm's provider lands on the built agent; the default stays `default`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    routed = make_agent("claude-code", provider="openrouter")
    direct = make_agent("claude-code")

    assert routed.provider == "openrouter"
    assert direct.provider == "default"


def test_credential_preflight_error_hands_the_provider_to_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the preflight asks the adapter about the pair's provider, not a default."""
    seen: list[str] = []

    def fake_credential_error(provider: str = "default", environ: object = None) -> None:
        """Record the provider the preflight asked about."""
        seen.append(provider)
        return None

    monkeypatch.setattr(ClaudeCodeAgent, "credential_error", staticmethod(fake_credential_error))

    credential_preflight_error("claude-code", "openrouter")

    assert seen == ["openrouter"]
