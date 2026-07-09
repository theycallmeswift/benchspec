"""Run-level judge abstraction, independent from task arms.

Config resolution and host-side judge runners for claude-code/codex/opencode.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
everything about WHICH harness grades and HOW it's invoked. `binder.py`'s host-Claude
call (`agents.judge_cli.run_host_judge`) is a separate, untouched transport.
"""

from __future__ import annotations

from evalspec.judges.config import JudgeConfig, resolve_judge_config
from evalspec.judges.registry import known_judge_harnesses, probe_judge_version, run_judge

__all__ = [
    "JudgeConfig",
    "resolve_judge_config",
    "run_judge",
    "probe_judge_version",
    "known_judge_harnesses",
]
