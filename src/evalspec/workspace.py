"""Locate the eval-output tree and its iteration dirs.

Artifacts land under `<repo_root>/tmp/evals/iteration_NN/skills/<skill>/eval-<id>/<arm>/sample-K/`
(sample zero-indexed; `sample-0/` even at `--count 1`). Iterations are a global
counter shared across skills; one run writes every touched skill under the same
`iteration_NN`. The name is chosen once on the pytest controller and shared with
xdist workers (see `plugin.py`) — read from the environment, not recomputed per process.

`workspace_parent` stays at `tmp/` (not `tmp/evals/`): `sandbox.py` anchors the
microsandbox snapshot lock files there, alongside (not inside) the eval outputs.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_ITERATION = "EVALSPEC_ITERATION"


def workspace_parent(repo_root: Path) -> Path:
    """Handle workspace_parent."""
    return repo_root / "tmp"


def evals_root(repo_root: Path) -> Path:
    """Handle evals_root."""
    return workspace_parent(repo_root) / "evals"


def next_iteration_name(repo_root: Path) -> str:
    """Return the next global `iteration_NN` name under `tmp/evals/`.

    One run shares one N across every skill it touches. Computed once on the controller
    — never per worker — so the increment can't race. Padded to 2 digits; widens
    naturally past 99.
    """
    existing: list[int] = []
    root = evals_root(repo_root)
    if root.is_dir():
        for it in root.glob("iteration_*"):
            if not it.is_dir():
                continue
            tail = it.name.removeprefix("iteration_")
            if tail.isdecimal():
                existing.append(int(tail))
    return f"iteration_{max(existing, default=0) + 1:02d}"


def set_current_iteration(name: str) -> None:
    """Handle set_current_iteration."""
    os.environ[_ENV_ITERATION] = name


def current_iteration() -> str:
    """Handle current_iteration."""
    return os.environ[_ENV_ITERATION]


def current_iteration_or_none() -> str | None:
    """Like `current_iteration` but returns None instead of raising when the handle.

    is unset. Use at call sites that want a no-op when the iteration lifecycle never
    started (e.g. the terminal summary on a collection-only run).
    """
    return os.environ.get(_ENV_ITERATION)


def iteration_root(repo_root: Path) -> Path:
    """The shared dir for the current iteration: `tmp/evals/iteration_NN/`."""
    return evals_root(repo_root) / current_iteration()


def skills_root(repo_root: Path) -> Path:
    """The current iteration's `skills/` dir, one subdir per touched skill."""
    return iteration_root(repo_root) / "skills"


def skill_dir(repo_root: Path, skill: str) -> Path:
    """One skill's slice of the current iteration: `…/iteration_NN/skills/<skill>/`."""
    return skills_root(repo_root) / skill


def arm_dir(repo_root: Path, skill: str, eval_id: str, arm: str, sample: int) -> Path:
    """Handle arm_dir."""
    return skill_dir(repo_root, skill) / f"eval-{eval_id}" / arm / f"sample-{sample}"


def trigger_dir(repo_root: Path, skill: str, slug: object, sample: int) -> Path:
    """Handle trigger_dir."""
    return skill_dir(repo_root, skill) / f"trigger-{slug}" / f"sample-{sample}"
