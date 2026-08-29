"""harnessbench — a benchmark framework for coding agents.

An eval is one Markdown file: a prompt plus a checklist of prose assertions. Each eval
runs across the named arms of an eval set — harness × model × effort × environment —
as parametrized pytest tests, each cell inside its own microVM. Assertions the binder
can map to a deterministic checker are graded on the host; the rest go to an LLM judge
that sees only collected evidence. The report is a matrix of pass rates with every
arm's delta against the set's baseline.
"""

from __future__ import annotations

__version__ = "0.1.0a0"

from harnessbench.agents import CodingAgent, make_agent

__all__ = ["CodingAgent", "make_agent", "__version__"]
