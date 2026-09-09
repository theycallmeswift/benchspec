"""Judge dispatch onto the harness adapters.

The same adapter that runs a harness inside the sandbox (`invoke`) also grades with
it (`judge`) — one class per harness, with the execution environment
(`benchspec.orchestration.environments`) deciding where the process runs. `run_judge` is the sync
boundary the grading path calls: it expands config.env via benchspec.config.arms.expand_env
(the same function arms use, so judge env expansion is provably identical) and runs
the adapter's async `judge` to completion in a Host environment. JudgeConfig is
imported only under TYPE_CHECKING to avoid a circular import with `judges/config.py`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import replace
from typing import TYPE_CHECKING

from benchspec.agents import agent_class, known_harnesses
from benchspec.config.arms import expand_env

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig


def known_judge_harnesses() -> frozenset[str]:
    """Judge-capable harnesses — every registered adapter can judge."""
    return known_harnesses()


def judge_binary(harness: str) -> str:
    """The PATH binary name for a judge harness (e.g. 'claude-code' -> 'claude')."""
    return agent_class(harness).for_host().agent_bin


def preflight_verify_judge_binary(config: JudgeConfig) -> None:
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


def preflight_verify_judge_credential(config: JudgeConfig) -> None:
    """RuntimeError if the selected judge harness cannot authenticate on the host.

    The judge runs on the host through `for_host()`, so the adapter's host check decides:
    an env credential, or the CLI's own login where it can report one. Environment-
    dependent like `preflight_verify_judge_binary`, and meant to run after it, so a missing
    binary is reported as such rather than as a failed login probe.
    """
    error = agent_class(config.harness).for_host().host_credential_error()
    if error:
        raise RuntimeError(f"judge harness `{config.harness}`: {error}")


def run_judge(prompt: str, *, config: JudgeConfig) -> str:
    """Expand config.env like an arm's env, then judge with the harness's own adapter.

    Uses benchspec.config.arms.expand_env — the same function, so judge env expansion is
    provably identical to arm env expansion — and hands the adapter a config whose
    env is already resolved. Raises SchemaError for an unset referenced $VAR (never a
    silent empty string); RuntimeError propagates unchanged from the adapter for
    infra failures.
    """
    expanded = replace(config, env=expand_env(config.env, os.environ))
    agent = agent_class(config.harness).for_host()

    return asyncio.run(agent.judge(prompt, expanded))


def probe_judge_version(harness: str) -> str | None:
    """Best-effort version probe for meta.json.

    Never raises — an unknown harness or any probe exception degrades to None,
    matching the existing agent-version pattern in reporting.manifest.write_manifest.
    """
    try:
        adapter = agent_class(harness)
    except RuntimeError:
        return None
    try:
        return adapter.for_host().binary_version()
    except Exception:
        return None
