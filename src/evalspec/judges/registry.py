"""Harness -> judge runner lookup, binary-on-PATH preflight, and the run_judge dispatcher.

`run_judge` expands config.env via evalspec.arms.expand_env (the same
function arms use, so judge env expansion is provably identical) then dispatches to
the selected harness's runner; `probe_judge_version` is a best-effort per-harness
version probe that never raises. `judges/config.py`'s structural preflight depends on
`known_judge_harnesses`; JudgeConfig is imported here only under TYPE_CHECKING, never
at runtime, to avoid a circular import with `judges/config.py`.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

from evalspec.arms import expand_env
from evalspec.judges import claude_code, codex, opencode

if TYPE_CHECKING:
    from evalspec.judges.config import JudgeConfig

_BINARIES = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
# Map to the runner *modules*, not their `.run`/`.probe_version` functions directly:
# looking up the attribute at call time (module.run, module.probe_version) means
# `monkeypatch.setattr("evalspec.judges.claude_code.run", ...)` in tests takes effect;
# binding the bare function at import time would freeze in the pre-patch reference.
_HARNESS_MODULES = {
    "claude-code": claude_code,
    "codex": codex,
    "opencode": opencode,
}


def known_judge_harnesses() -> frozenset[str]:
    """Registered judge-runner harnesses — deliberately its own registry.

    Decoupled from evalspec.agents.known_harnesses() (task harnesses), even though the
    two currently name the same three values.
    """
    return frozenset(_BINARIES)


def judge_binary(harness: str) -> str:
    """The PATH binary name for a judge harness (e.g. 'codex' -> 'codex')."""
    return _BINARIES[harness]


def preflight_judge_binary(config: JudgeConfig) -> None:
    """RuntimeError if the selected judge harness's binary is missing from PATH.

    Environment-dependent — call only when tests actually execute (from the
    `judge_config` fixture, see cases.py), never from collection-time code that also
    runs under --collect-only.
    """
    binary = judge_binary(config.harness)
    if shutil.which(binary) is None:
        raise RuntimeError(
            f"judge harness `{config.harness}` binary `{binary}` not found on PATH"
        )


def run_judge(prompt: str, *, config: JudgeConfig) -> str:
    """Expand config.env like an arm's env, then dispatch to the selected harness's runner.

    Uses evalspec.arms.expand_env — the same function, so judge env expansion is
    provably identical to arm env expansion. Raises SchemaError for an unset
    referenced $VAR (never a silent empty string); RuntimeError propagates unchanged
    from the runner for infra failures.
    """
    env = expand_env(config.env, os.environ)
    module = _HARNESS_MODULES[config.harness]  # KeyError impossible after config.py's preflight

    return module.run(
        prompt, model=config.model, effort=config.effort, timeout=config.timeout,
        harness_args=config.harness_args, env=env,
    )


def probe_judge_version(harness: str) -> str | None:
    """Best-effort version probe for meta.json.

    Never raises — any exception from the per-harness probe degrades to None, matching
    the existing agent-version pattern in plugin._write_manifest.
    """
    module = _HARNESS_MODULES.get(harness)
    if module is None:
        return None
    try:
        return module.probe_version()
    except Exception:
        return None
