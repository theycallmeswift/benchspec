"""evalspec — a pytest-native runner for coding-agent skill evals.

Discovers each skill's self-contained output evals (`evals/<slug>/prompt.md`) and
`evals/trigger-evals.md`, runs each eval's `(eval × arm)` units as parametrized tests
against a coding agent CLI (`claude-code` or `opencode`) inside a microsandbox microVM,
grades with an LLM judge (or host-side deterministic checkers), and reports each arm's
delta against the reference arm when a `reference` arm ran.
"""

from __future__ import annotations

__version__ = "0.1.0a0"

from evalspec.agents import CodingAgent, make_agent

__all__ = ["CodingAgent", "make_agent", "__version__"]
