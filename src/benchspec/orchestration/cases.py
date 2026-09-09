"""Eval test bodies.

Run via `make evals` (loads `-p benchspec.runners.pytest`), never by `make test`. — this module
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
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchspec.config.arms import Arm
from benchspec.config.options import RunOptions, option_str
from benchspec.config.sets import (
    resolved_judge_config,
    resolved_run_set,
    run_set_when_needed,
    session_run_set,
)
from benchspec.grading import binder
from benchspec.grading.judges import JudgeConfig
from benchspec.grading.judges.registry import (
    preflight_verify_judge_binary,
    preflight_verify_judge_credential,
)
from benchspec.orchestration import results
from benchspec.orchestration.execution import ArmOutcome, run_eval_arm
from benchspec.orchestration.room import seed_room
from benchspec.sandbox import sandbox
from benchspec.sandbox.backend import SandboxBackend
from benchspec.sandbox.registry import resolve_sandbox
from benchspec.specs.discovery import (
    EvalCase,
    discover_eval_cases,
    resolve_eval_paths,
    resolve_repo_root,
)


def eval_arm_params(config: RunOptions) -> tuple[list[tuple[EvalCase, Arm]], list[str]]:
    """The (case × arm) pairs and ids that parametrize the eval_arm fixture."""
    # One eval set per run — uniform columns across every skill (resolved once, not
    # per skill). Parametrize the single `eval_arm` fixture over `(case, arm)` pairs.
    repo_root = resolve_repo_root(config)
    cases = discover_eval_cases(repo_root, resolve_eval_paths(config))
    # Resolve the set only when there are eval cases to cross with — an empty
    # collection has no eval_arm pairs and must not require an eval-set pyproject.
    run_set = run_set_when_needed(config, needs_set=bool(cases))
    arms = run_set.arms if run_set else []
    if cases:
        # Structural judge preflight — before ANY paid task arm runs. Raises
        # pytest.UsageError at collection on a bad config; binary-on-PATH is
        # checked separately, later, only when tests actually execute.
        resolved_judge_config(config)
    pairs: list[tuple[EvalCase, Arm]] = []
    ids: list[str] = []
    for case in cases:
        for arm in arms:
            pairs.append((case, arm))
            ids.append(f"{case.param_id}-{arm.name}")
    return pairs, ids


@pytest.fixture
def repo_root(request: pytest.FixtureRequest) -> Path:
    """Return the repository root selected for this pytest run."""
    return resolve_repo_root(request.config)


@pytest.fixture
def model(request: pytest.FixtureRequest) -> str:
    """Return the default model selected for eval arms."""
    # `--benchspec-model` defaults to None; `or "sonnet"` keeps arms on a concrete model
    # when the flag is unset, never `model=None`.
    return option_str(request.config, "benchspec_model") or "sonnet"


@pytest.fixture
def eval_set_name(request: pytest.FixtureRequest) -> str:
    """Return the configured eval set name for this run."""
    # The raw `--benchspec-set` / `make evals SET=` value (empty on a default-set run, which
    # never sets the flag). Stamped into BENCHSPEC_SET for setup.sh branching — it does NOT
    # carry the resolved default-set name.
    return option_str(request.config, "benchspec_set") or ""


def _grading_environment_errors(judge: JudgeConfig) -> list[str]:
    """Return every environment failure grading would hit with `judge`, empty when none.

    Two independent checks, each reported so one run surfaces both: the binder's Gemini
    credential (an env read), then the judge binary on PATH followed by the judge's host
    credential. The credential probe runs only when the binary exists, so a missing
    binary is reported as such rather than as a failed login.
    """
    errors: list[str] = []

    try:
        binder.preflight_verify_gemini_key()
    except RuntimeError as error:
        errors.append(str(error))

    try:
        preflight_verify_judge_binary(judge)
        preflight_verify_judge_credential(judge)
    except RuntimeError as error:
        errors.append(str(error))

    return errors


def grading_preflight_errors(config: RunOptions) -> list[str]:
    """Return every environment failure grading would hit, resolving the judge from `config`.

    Args:
        config: A pytest config, or anything exposing the plugin's `getoption`/`rootpath`.

    Returns:
        The failures, empty when grading can proceed.

    Raises:
        pytest.UsageError: a structural defect in the judge config.
    """
    return _grading_environment_errors(resolved_judge_config(config))


def preflight_grading(config: RunOptions) -> JudgeConfig:
    """Preflight everything grading needs and return the run's judge config.

    The judge config is resolved first (structural), then the environment checks run and
    every failure is reported together. Shared by the `judge_config` fixture and the
    `run` CLI, so what the CLI refuses before spawning pytest is exactly what pytest
    would refuse at grading time.

    Args:
        config: A pytest config, or anything exposing the plugin's `getoption`/`rootpath`.

    Returns:
        The resolved `JudgeConfig`.

    Raises:
        RuntimeError: `GEMINI_API_KEY` missing or empty, the judge binary absent, or the
            judge harness unable to authenticate on the host; every failure listed.
        pytest.UsageError: a structural defect in the judge config.
    """
    judge = resolved_judge_config(config)

    errors = _grading_environment_errors(judge)
    if errors:
        raise RuntimeError(
            sandbox.format_preflight_failure("benchspec grading preflight failed", errors)
        )

    return judge


@pytest.fixture
def judge_config(request: pytest.FixtureRequest) -> JudgeConfig:
    """Resolve the run's judge; preflight its binary and credential and the binder's key."""
    # Only test_eval requests this fixture, so the environment-dependent preflights fire
    # exactly when a run will grade. Fixture setup is skipped under --collect-only, so
    # this stays collection-safe without an autouse gate.
    return preflight_grading(request.config)


@pytest.fixture
def project_marker(request: pytest.FixtureRequest) -> str:
    """Return the repo-relative path marking a host plugin worth mounting."""
    return option_str(request.config, "benchspec_project_marker") or sandbox.DEFAULT_PROJECT_MARKER


@pytest.fixture
def today() -> str:
    """Return the date string used for placeholder substitution."""
    return results.utc_today()


@pytest.fixture
def clean_room() -> Iterator[Path]:
    """Return the seeded clean-room workdir for a test case."""
    # tempfile defaults under the OS temp root — outside the project, so the baseline arm
    # cannot reach the project's docs/, skills/, or plugin.
    with tempfile.TemporaryDirectory(prefix="benchspec-") as room:
        yield Path(room)


@pytest.fixture
def seeded_workdir(
    clean_room: Path, eval_arm: tuple[EvalCase, Arm], today: str
) -> tuple[Path, dict[str, str]]:
    """Return the workdir prepared from the eval's workspace."""
    eval_case, _arm = eval_arm
    workdir = clean_room / "workdir"
    pre_run_shas = seed_room(eval_case.workspace_dir, workdir, today)
    return workdir, pre_run_shas


def _session_sandbox_target(config: RunOptions) -> tuple[SandboxBackend | None, list[str]]:
    """Return the resolved set's sandbox backend and arm harnesses, for its preflight.

    Resolves the selected set from `config` (guarded so a trigger-only project with no
    sets table degrades instead of raising). No eval set ⇒ a None backend, so preflight
    resolves the default one, and no arm harnesses.
    """
    run_set = session_run_set(config)
    backend = resolve_sandbox(run_set.sandbox) if run_set else None
    harnesses = [arm.harness for arm in run_set.arms] if run_set else []
    return backend, harnesses


def sandbox_preflight_errors(config: RunOptions) -> list[str]:
    """Return every reason the resolved set's sandbox can't run, empty when it can."""
    backend, harnesses = _session_sandbox_target(config)
    return sandbox.preflight_errors(backend, harnesses=harnesses)


def preflight_session_sandbox(config: RunOptions) -> None:
    """Preflight the resolved eval set's sandbox backend before any arm runs.

    Drives `sandbox.preflight` with the set's backend and arm harnesses, so every harness
    the arms will run under has its credential checked.
    """
    backend, harnesses = _session_sandbox_target(config)
    sandbox.preflight(backend, harnesses=harnesses)


@pytest.fixture(scope="session", autouse=True)
def _sandbox_preflight(request: pytest.FixtureRequest) -> None:
    """Preflight the resolved set's sandbox once per session before eval arms run.

    Session-scoped, so it resolves the set from `request.config` directly rather than
    depending on the function-scoped `eval_set_name`/`eval_arm` fixtures (scope mismatch).
    """
    preflight_session_sandbox(request.config)


@pytest.fixture
def eval_sandbox(request: pytest.FixtureRequest) -> str:
    """Return the sandbox backend name resolved for this run's eval set.

    test_eval only runs when the set resolved (its `eval_arm` params came from that set),
    so resolving here never hits the trigger-only no-set path.
    """
    return resolved_run_set(request.config).sandbox


def _errored_message(arm_name: str, outcome: ArmOutcome) -> str:
    """Build an assertion message that names the infra failure instead of just pointing away.

    The failure lives in the arm's `grading.json` (the judge/agent evidence), not the
    transcript. Surface the first infra-error evidence inline so the reason is visible in
    the pytest summary, and still name the file for the full record.
    """
    evidence = next(
        (
            assertion.get("evidence", "")
            for assertion in outcome.grading.get("assertions", [])
            if not assertion.get("passed") and "INFRA ERROR" in assertion.get("evidence", "")
        ),
        "",
    )
    detail = f": {evidence}" if evidence else ""
    return f"{arm_name} run errored (see the arm's grading.json){detail}"


@pytest.mark.benchspec
def test_eval(
    eval_arm: tuple[EvalCase, Arm],
    seeded_workdir: tuple[Path, dict[str, str]],
    repo_root: Path,
    today: str,
    eval_set_name: str,
    project_marker: str,
    judge_config: JudgeConfig,
    sample_index: int,
    eval_sandbox: str,
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
        sandbox_name=eval_sandbox,
    )

    # Both arms grade identically and symmetrically. A failed assertion (including a
    # baseline that legitimately doesn't fire the skill) is the recorded measurement, not a
    # gate — the report computes Δ from the persisted grading.json. The only test-body
    # failure is an infra error (excluded from the benchmark).
    assert not outcome.errored, _errored_message(arm.name, outcome)
