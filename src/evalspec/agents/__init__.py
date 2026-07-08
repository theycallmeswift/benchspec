"""Coding agents for evalspec.

`sandbox.py` talks only to the `CodingAgent` interface and to the factory/preflight
helpers here — never to a concrete agent. The active agent is resolved via
`resolve_agent_name`'s precedence chain (--evalspec-agent flag > EVALSPEC_AGENT env >
[tool.evalspec] agent > claude-code default), with `EVALSPEC_AGENT` being the normalized
handoff that `make_agent()` reads. Unknown values fail loudly. Adding a third agent is
additive: implement the protocol, register it in `_REGISTRY`.
"""

from __future__ import annotations

import os

from evalspec.agents.base import AgentCapabilities, CodingAgent
from evalspec.agents.claude import ClaudeCodeAgent
from evalspec.agents.codex import CodexAgent
from evalspec.agents.opencode import OpenCodeAgent

__all__ = [
    "AgentCapabilities",
    "CodingAgent",
    "ClaudeCodeAgent",
    "CodexAgent",
    "OpenCodeAgent",
    "make_agent",
    "credential_preflight_error",
    "resolve_agent_name",
    "known_harnesses",
]

_REGISTRY: dict[str, type] = {
    "claude-code": ClaudeCodeAgent,
    "codex": CodexAgent,
    "opencode": OpenCodeAgent,
}

_DEFAULT_AGENT = "claude-code"


def known_harnesses() -> frozenset[str]:
    """Registered agent names — the authoritative set of valid harness values."""
    return frozenset(_REGISTRY)


def resolve_agent_name(flag: str | None = None, pyproject: str | None = None) -> str:
    """The agent for this run: --evalspec-agent > EVALSPEC_AGENT > [tool.evalspec].

    agent > claude-code. An unknown value fails naming the source it came from, so a
    typo dies at startup with a fixable message instead of mid-run.
    """
    sources = (
        ("--evalspec-agent", flag),
        ("EVALSPEC_AGENT", os.environ.get("EVALSPEC_AGENT")),
        ("[tool.evalspec] agent", pyproject),
    )
    for source, value in sources:
        if value:
            if value not in _REGISTRY:
                raise RuntimeError(
                    f"{source}={value!r} is not a known agent; valid: {sorted(_REGISTRY)}"
                )
            return value
    return _DEFAULT_AGENT


def _selected_agent_class() -> type:
    """Provide the selected agent class helper."""
    return _REGISTRY[resolve_agent_name()]


def make_agent(harness: str | None = None) -> CodingAgent:
    """The coding agent for a run or a single arm.

    With no `harness`, reads the run-level agent from `EVALSPEC_AGENT` (the plugin
    normalizes the precedence chain at configure time). With an explicit `harness` (an
    arm's `harness`), builds THAT agent so one run's columns can span harnesses. An
    unknown `harness` raises.
    """
    if harness is not None:
        if harness not in _REGISTRY:
            raise RuntimeError(
                f"harness {harness!r} is not a known agent; valid: {sorted(_REGISTRY)}"
            )
        return _REGISTRY[harness].from_env()
    return _selected_agent_class().from_env()


def credential_preflight_error() -> str | None:
    """None if a usable credential is configured for the selected agent, else a.

    remediation message for preflight.
    """
    return _selected_agent_class().credential_error()
