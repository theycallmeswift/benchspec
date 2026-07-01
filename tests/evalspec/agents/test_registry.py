"""Agent-name resolution: one precedence chain for flag / env / pyproject."""

from __future__ import annotations

import pytest

from evalspec import agents


def test_default_is_claude_code(monkeypatch):
    monkeypatch.delenv("EVALSPEC_AGENT", raising=False)
    assert agents.resolve_agent_name() == "claude-code"


def test_flag_beats_env_beats_pyproject(monkeypatch):
    monkeypatch.setenv("EVALSPEC_AGENT", "opencode")
    assert agents.resolve_agent_name(flag="claude-code", pyproject="opencode") == "claude-code"
    assert agents.resolve_agent_name(flag=None, pyproject="claude-code") == "opencode"
    monkeypatch.delenv("EVALSPEC_AGENT", raising=False)
    assert agents.resolve_agent_name(flag=None, pyproject="opencode") == "opencode"


def test_known_harnesses_include_codex():
    assert "codex" in agents.known_harnesses()


@pytest.mark.parametrize(
    ("kwargs", "source"),
    [
        ({"flag": "not-a-harness"}, "--evalspec-agent"),
        ({"pyproject": "not-a-harness"}, "[tool.evalspec] agent"),
    ],
)
def test_unknown_value_names_its_source(monkeypatch, kwargs, source):
    monkeypatch.delenv("EVALSPEC_AGENT", raising=False)
    with pytest.raises(RuntimeError) as ei:
        agents.resolve_agent_name(**kwargs)
    assert source in str(ei.value)
    assert "claude-code" in str(ei.value)  # the valid set is listed


def test_unknown_env_value_names_env(monkeypatch):
    monkeypatch.setenv("EVALSPEC_AGENT", "not-a-harness")
    with pytest.raises(RuntimeError, match="EVALSPEC_AGENT"):
        agents.resolve_agent_name()
