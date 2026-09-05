"""Run-level judge abstraction, independent from task arms.

Config resolution plus dispatch onto the harness adapters' host-side judge mode —
grading uses the same class per harness as task execution (`agents.*.judge`), run as
a fresh host process instead of in the sandbox.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
WHICH harness grades and resolves its config. `binder.py`'s prose→checker classifier
is a separate, unrelated direct Gemini API call, independent from this package.
"""

from __future__ import annotations

from benchspec.grading.judges.config import JudgeConfig, resolve_judge_config
from benchspec.grading.judges.registry import (
    known_judge_harnesses,
    probe_judge_version,
    run_judge,
)

__all__ = [
    "JudgeConfig",
    "resolve_judge_config",
    "run_judge",
    "probe_judge_version",
    "known_judge_harnesses",
]
