"""Eval test bodies.

Run via `make evals` (loads `-p evalspec.plugin`), never by `make test`. — this module
lives outside `tests/`, so the unit run never spawns `claude -p`.

The plugin parametrizes the single `eval_arm` fixture over `(case, arm)` pairs; fixtures
build the clean room and seed the fixture. The project is mounted for both arms (per-
cell setup.sh installs the skill), so there is no arm asymmetry at this layer.
Orchestration lives in `execution.run_eval_arm`; the test body wires fixtures and
asserts only that the arm ran without an infra error — per-assertion pass/fail is the
recorded measurement, not a gate.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from evalspec import binder, runner, sandbox, workspace
from evalspec.agents import make_agent
from evalspec.discovery import resolve_repo_root
from evalspec.execution import run_eval_arm
from evalspec.judges import JudgeConfig
from evalspec.judges.registry import preflight_judge_binary
from evalspec.plugin import resolved_judge_config
from evalspec.room import seed_room
from evalspec.trigger import count_fires, fire_threshold, trigger_record


@pytest.fixture
def repo_root(request: object) -> Path:
    """Return the repository root selected for this pytest run."""
    return resolve_repo_root(request.config)


@pytest.fixture
def model(request: object) -> str:
    """Return the default model selected for eval arms."""
    # `--evalspec-model` defaults to None; `or "sonnet"` keeps trigger routing on a
    # concrete model when the flag is unset, never `model=None`.
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
    # exactly when a run will grade — never for a trigger-only session, which doesn't
    # request judge_config. Fixture setup is skipped under --collect-only, so this stays
    # collection-safe without an autouse gate.
    binder.preflight_gemini_key()
    config = resolved_judge_config(request.config)
    preflight_judge_binary(config)
    return config


@pytest.fixture
def trigger_mode(request: object) -> str:
    """Return the trigger-routing threshold mode."""
    return request.config.getoption("evalspec_trigger_mode")


@pytest.fixture
def trigger_effort(request: object) -> str:
    """Return the agent reasoning effort used for trigger probes."""
    # Effort is per-harness now (trust + record): an invalid value surfaces from the agent
    # CLI, not a pre-validation gate against one agent's capability set.
    return request.config.getoption("evalspec_trigger_effort")


@pytest.fixture
def trigger_timeout(request: object) -> int:
    """Return the timeout for one trigger-routing probe."""
    return request.config.getoption("evalspec_trigger_timeout")


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
    """Return the workdir prepared from eval fixtures."""
    eval_case, _arm = eval_arm
    workdir = clean_room / "workdir"
    pre_run_shas = seed_room(eval_case.fixtures_dir, workdir, today)
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


@pytest.mark.evalspec
def test_trigger(
    trigger_query: object,
    model: object,
    trigger_mode: object,
    trigger_effort: object,
    trigger_timeout: object,
    project_marker: object,
    sample_index: object,
) -> None:
    """Run one trigger-routing case and record its verdict."""
    # Real routing: the skill runs in the full environment (plugin + local skills,
    # staged from repo_root) against its peers.
    # `--evalspec-trigger-mode` picks the fire threshold (majority / best-of /
    # asymmetric); count_fires short-circuits the 3 passes once locked.
    trigger_case = trigger_query
    passes = 3
    threshold = fire_threshold(trigger_mode, trigger_case.query["should_trigger"], passes)
    per_pass: list[dict] = []

    def route(
        query: object,
        repo_root: object,
        model: object,
        timeout: object,
        *,
        effort: object,
        skill_name: object,
    ) -> object:
        """Run the trigger-routing callable for a case."""
        return sandbox.route_in_sandbox(
            query,
            repo_root,
            model,
            timeout,
            effort=effort,
            skill_name=skill_name,
            project_marker=project_marker,
        )

    agent = make_agent()
    fires = count_fires(
        trigger_case.query["query"],
        trigger_case.skill_name,
        trigger_case.repo_root,
        model,
        passes=passes,
        threshold=threshold,
        timeout=trigger_timeout,
        effort=trigger_effort,
        route=route,
        detect_fired=agent.detect_fired,
        on_pass=lambda ms, fired, routed: per_pass.append(
            {"ms": ms, "fired": fired, "fired_skill": routed}
        ),
    )
    fired = fires >= threshold
    # Record per-query timing/outcome (one file per query, xdist-safe) so a run can
    # decompose wall-clock (trigger vs task vs judge) and surface which pass fired.
    run_dir = workspace.trigger_dir(
        trigger_case.repo_root, trigger_case.skill, trigger_case.query["slug"], sample=sample_index
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "timing.json").write_text(
        json.dumps(
            trigger_record(
                trigger_case.query,
                mode=trigger_mode,
                threshold=threshold,
                fires=fires,
                per_pass=per_pass,
                model=model,
            )
        )
        + "\n"
    )
    assert fired == trigger_case.query["should_trigger"], (
        f"{trigger_case.query['slug']} ({trigger_case.query['query']!r}): fired={fired} "
        f"({fires}/{passes} fired, mode={trigger_mode}, threshold={threshold}), "
        f"expected should_trigger={trigger_case.query['should_trigger']}"
    )
