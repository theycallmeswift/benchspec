"""Run-level judge abstraction, independent from task arms.

Config resolution plus dispatch onto the harness adapters' host-side judge mode —
grading uses the same class per harness as task execution (`agents.*.judge`), run as
a fresh host process instead of in the sandbox.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
WHICH harness grades and resolves its config. `binder.py`'s host-Claude call
(`agents.judge_cli.run_host_judge`) is a separate, untouched transport.
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
