"""Eval test bodies.

Run via `make evals` (loads `-p evalspec.plugin`), never by `make test`. — this module
lives outside `tests/`, so the unit run never spawns `claude -p`.

The plugin parametrizes the single `eval_arm` fixture over `(case, arm)` pairs; fixtures
build the clean room and seed the eval's workspace. The project is mounted for both arms
(the eval's own per-cell setup.sh installs the skill), so there is no arm asymmetry at
this layer.
Orchestration lives in `execution.run_eval_arm`; the test body wires fixtures and
asserts only that the arm ran without an infra error — per-assertion pass/fail is the
recorded measurement, not a gate.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from evalspec import binder, runner, sandbox
from evalspec.discovery import resolve_repo_root
from evalspec.execution import run_eval_arm
from evalspec.judges import JudgeConfig
from evalspec.judges.registry import preflight_judge_binary
from evalspec.plugin import resolved_judge_config
from evalspec.room import seed_room


@pytest.fixture
def repo_root(request: object) -> Path:
    """Return the repository root selected for this pytest run."""
    return resolve_repo_root(request.config)


@pytest.fixture
def model(request: object) -> str:
    """Return the default model selected for eval arms."""
    # `--evalspec-model` defaults to None; `or "sonnet"` keeps arms on a concrete model
    # when the flag is unset, never `model=None`.
    return request.config.getoption("evalspec_model") or "sonnet"


@pytest.fixture
def eval_set_name(request: object) -> str:
    """Return the configured eval set name for this run."""
    # The raw `--evalspec-set` / `make evals SET=` value (empty on a default-set run, which
    # never sets the flag). Stamped into EVALSPEC_SET for setup.sh branching — it does NOT
    # carry the resolved default-set name.
    return request.config.getoption("evalspec_set") or ""


@pytest.fixture
def judge_config(request: object) -> JudgeConfig:
    """Resolve the run's judge and preflight its binary and the binder's Gemini credential."""
    # Only test_eval requests this fixture, so both preflights (environment-dependent,
    # unlike resolved_judge_config's structural checks already run at collection) fire
    # exactly when a run will grade. Fixture setup is skipped under --collect-only, so
    # this stays collection-safe without an autouse gate.
    binder.preflight_gemini_key()
    config = resolved_judge_config(request.config)
    preflight_judge_binary(config)
    return config


@pytest.fixture
def project_marker(request: object) -> str:
    """Return the pytest marker assigned to project eval cases."""
    return request.config.getoption("evalspec_project_marker")


@pytest.fixture
def today() -> str:
    """Return the date string used for placeholder substitution."""
    return runner.utc_today()


@pytest.fixture
def clean_room() -> object:
    """Return the seeded clean-room workdir for a test case."""
    # tempfile defaults under the OS temp root — outside the project, so the baseline arm
    # cannot reach the project's docs/, skills/, or plugin.
    with tempfile.TemporaryDirectory(prefix="evalspec-") as room:
        yield Path(room)


@pytest.fixture
def seeded_workdir(clean_room: object, eval_arm: object, today: object) -> object:
    """Return the workdir prepared from the eval's workspace."""
    eval_case, _arm = eval_arm
    workdir = clean_room / "workdir"
    pre_run_shas = seed_room(eval_case.workspace_dir, workdir, today)
    return workdir, pre_run_shas


@pytest.fixture(scope="session", autouse=True)
def _sandbox_preflight() -> None:
    """Return a sandbox preflight error message when sandboxing is unavailable."""
    sandbox.preflight()


@pytest.mark.evalspec
def test_eval(
    eval_arm: object,
    seeded_workdir: object,
    repo_root: object,
    today: object,
    eval_set_name: object,
    project_marker: object,
    judge_config: object,
    sample_index: object,
) -> None:
    """Run one output eval case through its selected arm."""
    # The project mounts for both arms (per-cell setup.sh needs the suite under either
    # eval root — /project/skills/<skill> or /project/.claude/skills/<skill>), so the
    # same repo_root is passed regardless of arm — no asymmetry here.
    eval_case, arm = eval_arm
    workdir, pre_run_shas = seeded_workdir

    # Effort + env come from `arm` now (set/CLI defaults), not a shared fixture.
    outcome = run_eval_arm(
        eval_case,
        arm,
        workdir,
        pre_run_shas,
        project=repo_root,
        today=today,
        repo_root=repo_root,
        sample=sample_index,
        eval_set=eval_set_name,
        project_marker=project_marker,
        judge_config=judge_config,
    )

    # Both arms grade identically and symmetrically. A failed assertion (including a
    # baseline that legitimately doesn't fire the skill) is the recorded measurement, not a
    # gate — the report computes Δ from the persisted grading.json. The only test-body
    # failure is an infra error (excluded from the benchmark).
    assert not outcome.errored, f"{arm.name} run errored — see transcript.json"
