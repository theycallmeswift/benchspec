from __future__ import annotations

import pytest

from evalspec.agents import make_agent


def test_make_agent_explicit_harness_overrides_env(monkeypatch):
    monkeypatch.setenv("EVALSPEC_AGENT", "claude-code")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")  # opencode credential for from_env()
    agent = make_agent("opencode")
    assert agent.id == "opencode"


def test_make_agent_explicit_codex_harness_overrides_env(monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setenv("EVALSPEC_AGENT", "opencode")

    agent = make_agent("codex")

    assert agent.id == "codex"


def test_make_agent_unknown_harness_raises():
    with pytest.raises(RuntimeError, match="not a known agent"):
        make_agent("cursor")


def test_make_agent_default_reads_env(monkeypatch):
    monkeypatch.setenv("EVALSPEC_AGENT", "claude-code")
    assert make_agent().id == "claude-code"


def test_make_agent_default_reads_codex_env(monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setenv("EVALSPEC_AGENT", "codex")

    agent = make_agent()

    assert agent.id == "codex"
