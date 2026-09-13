"""Agents for benchspec.

`sandbox.py` talks only to the `CodingAgent` interface and to the factory/preflight
helpers here — never to a concrete agent. The active agent is resolved via
`resolve_agent_name`'s precedence chain (--benchspec-agent flag > BENCHSPEC_AGENT env >
[tool.benchspec] agent > claude-code default), with `BENCHSPEC_AGENT` being the normalized
handoff that `make_agent()` reads. Unknown values fail loudly. Adding a third agent is
additive: implement the protocol, register it in `_REGISTRY`.
"""

from __future__ import annotations

import os

from benchspec.agents.base import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_PROVIDER,
    HARNESS_PROVIDERS,
    OPENROUTER_PROVIDER,
    AgentCapabilities,
    BaseAgent,
    CodingAgent,
    unqualified_openrouter_model_error,
)
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.agents.opencode import OpenCodeAgent

__all__ = [
    "DEFAULT_AGENT_TIMEOUT",
    "DEFAULT_PROVIDER",
    "HARNESS_PROVIDERS",
    "OPENROUTER_PROVIDER",
    "AgentCapabilities",
    "BaseAgent",
    "CodingAgent",
    "ClaudeCodeAgent",
    "CodexAgent",
    "OpenCodeAgent",
    "agent_class",
    "make_agent",
    "credential_preflight_error",
    "resolve_agent_name",
    "known_harnesses",
    "known_providers",
    "unqualified_openrouter_model_error",
]

_REGISTRY: dict[str, type[BaseAgent]] = {
    "claude-code": ClaudeCodeAgent,
    "codex": CodexAgent,
    "opencode": OpenCodeAgent,
}

_DEFAULT_AGENT = "claude-code"


def known_harnesses() -> frozenset[str]:
    """Registered agent names — the authoritative set of valid harness values."""
    return frozenset(_REGISTRY)


def known_providers() -> frozenset[str]:
    """The transports a harness can reach its model through — valid `provider` values."""
    return HARNESS_PROVIDERS


def resolve_agent_name(flag: str | None = None, pyproject: str | None = None) -> str:
    """The agent for this run: --benchspec-agent > BENCHSPEC_AGENT > [tool.benchspec].

    agent > claude-code. An unknown value fails naming the source it came from, so a
    typo dies at startup with a fixable message instead of mid-run.
    """
    sources = (
        ("--benchspec-agent", flag),
        ("BENCHSPEC_AGENT", os.environ.get("BENCHSPEC_AGENT")),
        ("[tool.benchspec] agent", pyproject),
    )
    for source_name, value in sources:
        if value:
            if value not in _REGISTRY:
                valid_agents = sorted(_REGISTRY)
                raise RuntimeError(
                    f"{source_name}={value!r} is not a known agent; valid: {valid_agents}"
                )
            return value
    return _DEFAULT_AGENT


def _selected_agent_class() -> type[BaseAgent]:
    """Provide the selected agent class helper."""
    return _REGISTRY[resolve_agent_name()]


def agent_class(harness: str) -> type[BaseAgent]:
    """The registered adapter class for a harness name.

    Judge-mode dispatch looks the class up, then binds an instance to the host
    environment via `for_host()` — the judge uses the host's own credentials, so no
    `from_env()` credential read is involved. An unknown `harness` raises.
    """
    if harness not in _REGISTRY:
        raise RuntimeError(
            f"harness {harness!r} is not a known agent; valid: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[harness]


def make_agent(harness: str | None = None, provider: str = DEFAULT_PROVIDER) -> CodingAgent:
    """The agent for a run or a single arm.

    With no `harness`, reads the run-level agent from `BENCHSPEC_AGENT` (the plugin
    normalizes the precedence chain at configure time). With an explicit `harness` (an
    arm's `harness`), builds THAT agent so one run's columns can span harnesses. An
    unknown `harness` raises. `provider` is the arm's transport; it shapes exec-time
    wiring (credential, guest env, CLI flags), never the snapshot.
    """
    if harness is not None:
        return agent_class(harness).from_env(provider)
    return _selected_agent_class().from_env(provider)


def credential_preflight_error(
    harness: str | None = None, provider: str = DEFAULT_PROVIDER
) -> str | None:
    """None if a usable credential is configured for a harness, else a remediation message.

    With no `harness`, checks the run-level selected agent; with one (an arm's
    `harness`), checks that adapter, so a set whose arms span harnesses preflights each
    of them rather than only the selected agent. `provider` picks which credential the
    adapter needs: its vendor's own under `default`, OpenRouter's under `openrouter`.
    """
    adapter = agent_class(harness) if harness is not None else _selected_agent_class()
    return adapter.credential_error(provider)
