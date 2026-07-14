"""Tests for execution."""

import contextlib
import json
from dataclasses import dataclass, field
from typing import NoReturn

import pytest

from evalspec.config.arms import Arm
from evalspec.orchestration import workspace
from evalspec.orchestration.execution import run_eval_arm
from evalspec.orchestration.results import RunResult
from evalspec.sandbox.backend import FingerprintInputs
from evalspec.sandbox.provenance import ImageIdentity
from evalspec.sandbox.sandbox import ensure_snapshot
from evalspec.specs.discovery import EnvConfig, EvalCase
from evalspec.specs.schema import SchemaError

TRIAL = Arm("trial", "claude-code", "opus")
BASELINE = Arm("baseline", "claude-code", "opus")


@pytest.fixture(autouse=True)
def _no_real_vm(monkeypatch: object) -> None:
    """Build the no real vm test fixture."""
    # run_eval_arm resolves a real agent + snapshot (which would build a microVM). Stub both
    # so unit tests never touch microsandbox; session_factory is faked separately per test.
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: None)
    monkeypatch.setattr(
        "evalspec.orchestration.execution.ensure_snapshot", lambda agent, **kwargs: "snap"
    )


def _grade_all_pass(
    assertions: object,
    tree: object,
    contents: object,
    shas: object,
    final: object,
    eval_id: object,
    config: object,
    *,
    judge_config: object = None,
    original_shas: object = None,
    process_facts: object = "",
) -> object:
    """Build the grade all pass test fixture."""
    return {
        "eval_id": eval_id,
        "arm": config,
        "assertions": [
            {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
        ],
    }


def _punt_all(text: object) -> None:
    """Build the punt all test fixture."""
    return None  # never bind — send every assertion to the judge


def _case(tmp_path: object, eval_obj: object, skill: object = "myskill") -> object:
    """Build the case test fixture — eval_obj is {id, prompt, assertions, history?}."""
    eval_dir = tmp_path / "skills" / skill / "evals" / eval_obj["id"]
    eval_dir.mkdir(parents=True, exist_ok=True)
    return EvalCase(
        group=skill, eval_dir=eval_dir, eval_file=eval_dir / "eval.md", eval=eval_obj
    )


def fake_session_factory(result: object) -> object:
    """Single-turn: one RunResult returned for the arm's one run."""
    calls = []

    @contextlib.asynccontextmanager
    async def factory(**kwargs: object) -> object:
        """Factory."""
        calls.append(kwargs)

        async def run(prompt: object, *, resume_session_id: object, detect_skill: object) -> object:
            """Run."""
            factory.prompts.append(prompt)
            factory.detects.append(detect_skill)
            return result

        yield run

    factory.calls = calls
    factory.prompts = []
    factory.detects = []
    return factory


def test_single_turn_writes_artifacts_and_substitutes_prompt(tmp_path: object) -> None:
    """Verify single turn writes artifacts and substitutes prompt."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    (workdir / "out.md").write_text("result")
    eval_case = _case(
        tmp_path,
        {"id": "alpha", "prompt": "work in ./vault", "assertions": ["a1", "a2"]},
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert outcome.errored is False
    assert not hasattr(outcome, "fired")  # ArmOutcome carries no fired flag
    assert [assertion["passed"] for assertion in outcome.grading["assertions"]] == [True, True]
    # session_factory called with the eval_arm NAME (not the Arm repr) and eval_arm.model
    factory_kwargs = session_factory.calls[0]
    assert factory_kwargs["host_workdir"] == workdir
    assert factory_kwargs["config"] == "trial"
    assert factory_kwargs["model"] == "opus"  # eval_arm.model, not a --evalspec-model fixture

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    assert (run_dir / "grading.json").is_file()
    assert (run_dir / "timing.json").is_file()
    assert (run_dir / "transcript.json").is_file()
    transcript = json.loads((run_dir / "transcript.json").read_text())
    assert transcript[0]["prompt"] == "work in ./vault"


def test_today_substituted_in_assertions_before_grading(tmp_path: object) -> None:
    """Verify today substituted in assertions before grading."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {
            "id": "alpha",
            "prompt": "work",
            "assertions": ["file at ./9. Archive/Sources/{TODAY}/x.md"],
        },
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    graded_text = outcome.grading["assertions"][0]["text"]
    assert "{TODAY}" not in graded_text
    assert "2099-01-01" in graded_text


def test_bound_checker_keeps_original_assertion_prose(tmp_path: object) -> None:
    """Verify bound checker keeps original assertion prose."""
    # A bound checker's grading entry must carry the author's prose, not the spec-derived
    # text() (e.g. "the file ./gone.md does not exist"), so grading.json stays faithful.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    prose = "the inbox capture is gone"
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": [prose]})

    def bind(text: object) -> object:
        """Bind."""
        return {
            "checker": "not_file_exists",
            "path": "./gone.md",
            "type": "deterministic",
        }

    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=bind,
    )

    entry = outcome.grading["assertions"][0]
    assert entry["passed"] is True  # the checker ran (no judge punt)
    assert entry["text"] == prose


def test_binder_runtime_error_punts_to_judge_not_errors(tmp_path: object) -> None:
    """Verify a binder infra RuntimeError punts to judge not errors."""
    # A transient binder infra failure must degrade to a judge punt, not crash the cell.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a1"]})

    def bind(text: object) -> NoReturn:
        """Bind."""
        raise RuntimeError("gemini transient failure")

    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )
    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=bind,
    )
    assert outcome.errored is False
    assert outcome.grading["assertions"][0]["passed"] is True  # judge graded it, no crash
    assert outcome.grading["binder_degraded"] == 1


def test_run_eval_arm_trial_grades_activation_true_and_judges_semantic(
    tmp_path: object,
) -> None:
    """Verify run eval arm trial grades activation true and judges semantic."""
    workspace.set_current_iteration("iteration_01")

    def bind(text: object) -> object:
        """Bind."""
        if text == "Skill `ingest` invoked":
            return {
                "type": "deterministic",
                "checker": "skill_invoked",
                "skill": "ingest",
            }
        return None

    assertions = ["Skill `ingest` invoked", "the summary reflects the facts"]
    judged = []

    def grade(texts: object, *args: object, **kwargs: object) -> object:
        """Grade."""
        judged.append(list(texts))
        return {"assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in texts]}

    wd_trial = tmp_path / "trial"
    wd_trial.mkdir()
    eval_case = _case(
        tmp_path,
        {"id": "single", "prompt": "perform the task", "assertions": assertions},
        skill="ingest",
    )
    traj = [
        {
            "kind": "tool_call",
            "id": "1",
            "name": "Skill",
            "arguments": {"skill": "ingest"},
        }
    ]

    trial = run_eval_arm(
        eval_case,
        TRIAL,
        wd_trial,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "single",
                "trial",
                "out",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=True,
                trajectory=traj,
            )
        ),
        grade=grade,
        bind=bind,
    )

    text_results = {
        assertion["text"]: assertion["passed"] for assertion in trial.grading["assertions"]
    }
    assert text_results["Skill `ingest` invoked"] is True
    assert judged == [["the summary reflects the facts"]]  # the semantic one reached the judge
    assert "gated" not in trial.grading


def test_run_eval_arm_baseline_grades_activation_false_and_judges_semantic(
    tmp_path: object,
) -> None:
    """Verify run eval arm baseline grades activation false and judges semantic."""
    workspace.set_current_iteration("iteration_01")

    def bind(text: object) -> object:
        """Bind."""
        if text == "Skill `ingest` invoked":
            return {
                "type": "deterministic",
                "checker": "skill_invoked",
                "skill": "ingest",
            }
        return None

    assertions = ["Skill `ingest` invoked", "the summary reflects the facts"]
    judged = []

    def grade(texts: object, *args: object, **kwargs: object) -> object:
        """Grade."""
        judged.append(list(texts))
        return {"assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in texts]}

    wd_base = tmp_path / "base"
    wd_base.mkdir()
    eval_case = _case(
        tmp_path,
        {"id": "single", "prompt": "perform the task", "assertions": assertions},
        skill="ingest",
    )

    baseline = run_eval_arm(
        eval_case,
        BASELINE,
        wd_base,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "single",
                "baseline",
                "out",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=False,
                trajectory=[],
            )
        ),
        grade=grade,
        bind=bind,
    )

    baseline_results = {
        assertion["text"]: assertion["passed"] for assertion in baseline.grading["assertions"]
    }
    assert baseline_results["Skill `ingest` invoked"] is False
    assert judged == [["the summary reflects the facts"]]
    assert "gated" not in baseline.grading


def test_run_eval_arm_no_fired_gate(tmp_path: object) -> None:
    """Verify run eval arm no fired gate."""
    # Missing skill activation grades false.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {"id": "miss", "prompt": "perform the task", "assertions": ["Skill `ingest` invoked"]},
        skill="ingest",
    )

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "miss",
                "trial",
                "hand-rolled",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=False,
                trajectory=[],
            )
        ),
        grade=_grade_all_pass,
        bind=bind,
    )

    assert outcome.errored is False
    entry = outcome.grading["assertions"][0]
    assert entry["passed"] is False
    assert entry["type"] == "deterministic"


def test_baseline_arm_fired_skills_empty_not_errored(tmp_path: object) -> None:
    """Verify baseline arm fired skills empty not errored."""
    # Empty dispatched-skill context grades the activation assertion false.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {"id": "base", "prompt": "perform the task", "assertions": ["Skill `ingest` invoked"]},
        skill="ingest",
    )

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}

    outcome = run_eval_arm(
        eval_case,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "base",
                "baseline",
                "baseline output",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=False,
                trajectory=[],
            )
        ),
        grade=_grade_all_pass,
        bind=bind,
    )

    assert outcome.errored is False
    entry = outcome.grading["assertions"][0]
    assert entry["passed"] is False
    assert "not among fired []" in entry["evidence"]


def _bind_ingest_activation(text: object) -> object:
    """Bind the `ingest` activation line to a skill_invoked spec; punt everything else."""
    if text == "Skill `ingest` invoked":
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}
    return None


def test_activation_grades_fallback_fire_when_group_differs_from_asserted_skill(
    tmp_path: object,
) -> None:
    """Verify a fallback-shape fire grades True even when the group differs from the skill."""
    # The hard decoupling case: group ("writing") differs from the asserted skill ("ingest"),
    # and the skill fires ONLY as a bare tool_call named `ingest` — no Skill tool call. The
    # no-arg Skill-tool pass misses that fallback shape, so it lands in fired_skills only via
    # per-candidate capture keyed off the *asserted* skill. Were detection to key off the group
    # name again, "ingest" would never be a candidate and this would grade False.
    workspace.set_current_iteration("iteration_01")

    eval_case = _case(
        tmp_path,
        {"id": "single", "prompt": "do it", "assertions": ["Skill `ingest` invoked"]},
        skill="writing",
    )
    trajectory = [{"kind": "tool_call", "id": "1", "name": "ingest", "arguments": {"arg": "x"}}]
    workdir = tmp_path / "wd"
    workdir.mkdir()

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "single", "trial", "out", 1, 1, False,
                session_id="s1", fired=True, trajectory=trajectory,
            )
        ),
        grade=_grade_all_pass,
        bind=_bind_ingest_activation,
    )

    assert outcome.grading["assertions"][0]["passed"] is True


def test_activation_grades_true_on_namespaced_skill_tool_value(tmp_path: object) -> None:
    """Verify a Skill-tool fire with a namespaced value grades the plain-name assertion True."""
    workspace.set_current_iteration("iteration_01")

    eval_case = _case(
        tmp_path,
        {"id": "single", "prompt": "do it", "assertions": ["Skill `ingest` invoked"]},
        skill="ingest",
    )
    trajectory = [
        {"kind": "tool_call", "id": "1", "name": "Skill", "arguments": {"skill": "plugin:ingest"}}
    ]
    workdir = tmp_path / "wd"
    workdir.mkdir()

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "single", "trial", "out", 1, 1, False,
                session_id="s1", fired=True, trajectory=trajectory,
            )
        ),
        grade=_grade_all_pass,
        bind=_bind_ingest_activation,
    )

    assert outcome.grading["assertions"][0]["passed"] is True


def _record_session_model(seen: object, name: object) -> object:
    """Build the record session model test fixture."""

    @contextlib.asynccontextmanager
    async def factory(**kwargs: object) -> object:
        """Factory."""
        seen[name] = kwargs["model"]

        async def run(prompt: object, *, resume_session_id: object, detect_skill: object) -> object:
            """Run."""
            return RunResult("m", name, "out", 1, 1, False, session_id="session-alpha", fired=True)

        yield run

    return factory


def test_run_eval_arm_routes_opus_arm_model(tmp_path: object) -> None:
    """Verify run eval arm routes opus arm model."""
    # The session model comes from the eval_arm configuration.
    workspace.set_current_iteration("iteration_01")

    eval_case = _case(tmp_path, {"id": "m", "prompt": "perform the task", "assertions": ["a"]})
    seen = {}
    wd_o = tmp_path / "o"
    wd_o.mkdir()

    run_eval_arm(
        eval_case,
        Arm("trial-opus", "claude-code", "opus"),
        wd_o,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=_record_session_model(seen, "trial-opus"),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert seen == {"trial-opus": "opus"}


def test_run_eval_arm_routes_sonnet_arm_model(tmp_path: object) -> None:
    """Verify run eval arm routes sonnet arm model."""
    workspace.set_current_iteration("iteration_01")

    eval_case = _case(tmp_path, {"id": "m", "prompt": "perform the task", "assertions": ["a"]})
    seen = {}
    wd_s = tmp_path / "s"
    wd_s.mkdir()

    run_eval_arm(
        eval_case,
        Arm("trial-sonnet", "claude-code", "sonnet"),
        wd_s,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=_record_session_model(seen, "trial-sonnet"),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert seen == {"trial-sonnet": "sonnet"}


def test_run_eval_arm_selects_agent_by_arm_harness(tmp_path: object, monkeypatch: object) -> None:
    """Verify run eval arm selects agent by arm harness."""
    # The agent is built from eval_arm.harness.
    workspace.set_current_iteration("iteration_01")

    captured = {}

    def fake_make_agent(harness: object = None) -> None:
        """Fake make agent."""
        captured["harness"] = harness
        return None

    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", fake_make_agent)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    eval_arm = Arm("trial", "opencode", "anthropic/claude-sonnet-4-6", "high", {})

    run_eval_arm(
        eval_case,
        eval_arm,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert captured["harness"] == "opencode"


def test_run_eval_arm_threads_arm_effort_and_expanded_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify run eval arm threads arm effort and expanded env."""
    # Effort, env, and eval-set name reach the session from the eval_arm context.
    workspace.set_current_iteration("iteration_01")

    monkeypatch.setenv("SECRET", "s3cr3t")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    eval_arm = Arm(
        "trial",
        "opencode",
        "anthropic/claude-sonnet-4-6",
        "high",
        {"TOK": "$SECRET", "LIT": "plain"},
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
    )

    run_eval_arm(
        eval_case,
        eval_arm,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        eval_set="trial-set",
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    call_kwargs = session_factory.calls[0]
    assert call_kwargs["effort"] == "high"
    assert call_kwargs["arm_env"] == {
        "TOK": "s3cr3t",
        "LIT": "plain",
    }
    assert call_kwargs["eval_set"] == "trial-set"


def test_run_eval_arm_threads_setup_reldir(tmp_path: object) -> None:
    """Verify run_eval_arm passes the eval-dir-relative path as setup_reldir."""
    # setup.sh lives in the eval folder; the sandbox locates it by its path relative to
    # the mounted project root (here, repo_root == project == tmp_path).
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True),
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert session_factory.calls[0]["setup_reldir"] == "skills/myskill/evals/alpha"


def test_run_eval_arm_threads_harness_args(tmp_path: object) -> None:
    """Verify run eval arm threads harness args."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    eval_arm = Arm("plugin", "claude-code", "opus", harness_args=["--plugin-dir", "/project"])
    session_factory = fake_session_factory(
        RunResult("alpha", "plugin", "done", 1, 1, False, session_id="session-alpha", fired=True),
    )

    run_eval_arm(
        eval_case,
        eval_arm,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert session_factory.calls[0]["harness_args"] == ["--plugin-dir", "/project"]


def test_run_eval_arm_threads_sandbox_name_to_resolve_sandbox(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify the caller's sandbox_name (not a hardcoded default) drives resolve_sandbox."""
    # cases.test_eval threads the resolved set's `.sandbox` here; execution must resolve
    # exactly that name, never silently fall back to DEFAULT_SANDBOX.
    workspace.set_current_iteration("iteration_01")
    captured = {}

    def fake_resolve(name: object) -> object:
        """Fake resolve."""
        captured["name"] = name
        return object()

    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", fake_resolve)

    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        sandbox_name="custombackend",
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert captured["name"] == "custombackend"


def test_artifact_dir_uses_arm_name_string(tmp_path: object) -> None:
    """Verify artifact dir uses eval_arm name string."""
    # Artifact paths use the eval_arm name string.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    assert (run_dir / "grading.json").is_file()
    assert "Arm(" not in str(run_dir)
    persisted = json.loads((run_dir / "grading.json").read_text())
    assert persisted["arm"] == "trial"


def test_seed_block_prepended_to_graded_prompt(tmp_path: object) -> None:
    """Verify seed block prepended to graded prompt."""
    # History turns are prepended to the graded prompt.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {
            "id": "seeded",
            "prompt": "the deeper question",
            "history": [
                {"role": "user", "content": "scope my plan"},
                {"role": "assistant", "content": "which part?"},
            ],
            "assertions": ["a"],
        },
    )
    session_factory = fake_session_factory(
        RunResult("seeded", "trial", "out", 1, 1, False, session_id="session-alpha", fired=True),
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    sent = session_factory.prompts[0]
    assert sent.startswith(
        "<transcript>\nuser: scope my plan\nassistant: which part?\n</transcript>"
    )
    assert sent.index("<transcript>") < sent.index("the deeper question")


def test_empty_seed_leaves_prompt_unchanged(tmp_path: object) -> None:
    """Verify empty seed leaves prompt unchanged."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "noseed", "prompt": "just the prompt", "assertions": ["a"]}
    )
    session_factory = fake_session_factory(
        RunResult("noseed", "trial", "out", 1, 1, False, session_id="session-alpha", fired=True),
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert session_factory.prompts[0] == "just the prompt"


def test_ordinary_execution_passes_no_group_skill_to_detection(tmp_path: object) -> None:
    """Verify ordinary execution never feeds the group name to skill detection."""
    # The adapter keeps the detect_skill parameter for the __route__ path, but ordinary
    # eval execution passes None so detection stays decoupled from the group name.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]},
        skill="ingest",
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "baseline", "out", 1, 1, False, session_id="session-alpha", fired=False),
    )

    run_eval_arm(
        eval_case,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert session_factory.detects == [None]


def test_errored_run_flags_outcome(tmp_path: object) -> None:
    """Verify errored run flags outcome."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "gamma", "prompt": "perform the task", "assertions": ["a"]}
    )
    session_factory = fake_session_factory(
        RunResult("gamma", "baseline", "<timeout>", 0, 0, True),
    )

    def grade_fail(assertions: object, *args: object, **kwargs: object) -> object:
        """Grade fail."""
        return {
            "assertions": [
                {"text": assertion, "passed": False, "evidence": ""} for assertion in assertions
            ]
        }

    outcome = run_eval_arm(
        eval_case,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session_factory,
        grade=grade_fail,
        bind=_punt_all,
    )

    assert outcome.errored is True


def test_failed_assertions_do_not_error_the_arm(tmp_path: object) -> None:
    """Verify failed assertions do not error the eval_arm."""
    # Failed assertions do not mark the eval_arm as an infrastructure error.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "fail", "prompt": "perform the task", "assertions": ["a1", "a2"]}
    )

    def grade_fail(assertions: object, *args: object, **kwargs: object) -> object:
        """Grade fail."""
        return {
            "assertions": [
                {"text": assertion, "passed": False, "evidence": "no"} for assertion in assertions
            ]
        }

    outcome = run_eval_arm(
        eval_case,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "fail", "baseline", "out", 1, 1, False, session_id="session-alpha", fired=False
            )
        ),
        grade=grade_fail,
        bind=_punt_all,
    )

    assert outcome.errored is False
    assert [assertion["passed"] for assertion in outcome.grading["assertions"]] == [False, False]


def test_default_judge_config_used_by_default(tmp_path: object) -> None:
    """Verify the default JudgeConfig grades when no judge config is passed."""
    # The judge is independent of the task eval_arm: the eval_arm can run any harness/model
    # (here opencode/gemini) while the judge still grades with the default JudgeConfig
    # (claude-code/sonnet) — grading never calls the task arm as judge.
    from evalspec.grading.judges import JudgeConfig

    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(tmp_path, {"id": "mu", "prompt": "perform the task", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions: object, *args: object, judge_config: object, **kwargs: object) -> object:
        """Grade."""
        seen_configs.append(judge_config)
        return {
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ]
        }

    run_eval_arm(
        eval_case,
        Arm("trial", "opencode", "google/gemini-3.5-flash"),
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("mu", "trial", "did it", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=grade,
        bind=_punt_all,
    )

    assert seen_configs == [JudgeConfig()]


def test_judge_config_param_overrides_default(tmp_path: object) -> None:
    """Verify a passed judge_config overrides the default."""
    from evalspec.grading.judges import JudgeConfig

    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(tmp_path, {"id": "nu", "prompt": "perform the task", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions: object, *args: object, judge_config: object, **kwargs: object) -> object:
        """Grade."""
        seen_configs.append(judge_config)
        return {
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ]
        }

    custom = JudgeConfig(harness="codex", model="gpt-5.5")
    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        judge_config=custom,
        session_factory=fake_session_factory(
            RunResult("nu", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=grade,
        bind=_punt_all,
    )

    assert seen_configs == [custom]


def test_judge_runtimeerror_marks_arm_errored(tmp_path: object) -> None:
    """Verify judge runtimeerror marks arm errored."""
    # Judge infrastructure failures mark the arm errored.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "kappa", "prompt": "perform the task", "assertions": ["a1", "a2"]}
    )

    def grade_raises(*args: object, **kwargs: object) -> NoReturn:
        """Grade raises."""
        raise RuntimeError("host claude CLI returned is_error=true: Not logged in")

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "kappa",
                "trial",
                "did the thing",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=True,
            )
        ),
        grade=grade_raises,
        bind=_punt_all,
    )

    assert outcome.errored is True
    assertions = outcome.grading["assertions"]
    assert [assertion["text"] for assertion in assertions] == ["a1", "a2"]
    assert all("JUDGE INFRA ERROR" in assertion["evidence"] for assertion in assertions)
    assert all("Not logged in" in assertion["evidence"] for assertion in assertions)
    run_dir = workspace.arm_dir(tmp_path, "myskill", "kappa", "trial", sample=0)
    persisted = json.loads((run_dir / "grading.json").read_text())
    assert persisted["errored"] is True


def test_missing_judge_binary_marks_arm_errored(tmp_path: object, monkeypatch: object) -> None:
    """Verify missing judge binary marks arm errored."""
    # Missing judge binary marks the arm errored.
    from evalspec.agents.claude import ClaudeCodeAgent

    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "lam", "prompt": "perform the task", "assertions": ["a1", "a2"]}
    )
    agent = ClaudeCodeAgent(auth_value="test-key", version="test-version")
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)

    def boom(*args: object, **kwargs: object) -> NoReturn:
        """Boom."""
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")

    monkeypatch.setattr("evalspec.orchestration.environments.subprocess.run", boom)

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "lam", "trial", "did the thing", 1, 1, False, session_id="session-alpha", fired=True
            )
        ),
        bind=_punt_all,  # grade defaults to the real grade_run
    )

    assert outcome.errored is True
    assertions = outcome.grading["assertions"]
    assert [assertion["passed"] for assertion in assertions] == [False, False]
    assert all("JUDGE INFRA ERROR" in assertion["evidence"] for assertion in assertions)
    run_dir = workspace.arm_dir(tmp_path, "myskill", "lam", "trial", sample=0)
    assert json.loads((run_dir / "grading.json").read_text())["errored"] is True


def test_artifacts_land_under_sample_dir(tmp_path: object) -> None:
    """Verify artifacts land under sample dir."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=2,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=2)
    assert run_dir.name == "sample-2"
    assert (run_dir / "grading.json").is_file()
    assert (run_dir / "timing.json").is_file()
    assert (run_dir / "transcript.json").is_file()


def test_session_jsonl_consolidated_with_turn_delimiter(tmp_path: object) -> None:
    """Verify session jsonl consolidated with turn delimiter."""
    # The consolidated session file uses explicit turn delimiters.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    tool_use_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Skill",
                        "input": {"skill": "writing-prompts"},
                    }
                ]
            },
        }
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "alpha",
                "trial",
                "r1",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=True,
                raw='{"type":"system"}\n' + tool_use_line + "\n",
                trajectory=[
                    {
                        "kind": "tool_call",
                        "id": "t1",
                        "name": "Skill",
                        "arguments": {"skill": "writing-prompts"},
                    }
                ],
            )
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    assert (run_dir / "session.jsonl").exists()
    text = (run_dir / "session.jsonl").read_text()
    assert '{"turn": 1}' in text
    assert '{"type":"system"}' in text
    transcript = json.loads((run_dir / "transcript.json").read_text())
    assert transcript[0]["skills_dispatched"] == ["writing-prompts"]
    assert transcript[0]["tool_call_count"] == 1
    from evalspec.grading.trajectory import trajectory_from_session

    eval_cases = trajectory_from_session(text)
    assert {
        "turn": 1,
        "kind": "tool_call",
        "id": "t1",
        "name": "Skill",
        "arguments": {"skill": "writing-prompts"},
    } in eval_cases


def test_no_session_jsonl_when_no_raw(tmp_path: object) -> None:
    """Verify no session jsonl when no raw."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(tmp_path, {"id": "beta", "prompt": "perform the task", "assertions": ["a"]})

    run_eval_arm(
        eval_case,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("beta", "baseline", "out", 1, 1, False, session_id="session-alpha")
        ),  # raw ""
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "beta", "baseline", sample=0)
    assert not (run_dir / "session.jsonl").exists()
    transcript = json.loads((run_dir / "transcript.json").read_text())
    assert transcript[0]["tool_call_count"] == 0
    assert "skills_dispatched" not in transcript[0]


def test_run_result_artifacts_merge_into_graded_facts(tmp_path: object) -> None:
    """Verify run result artifacts merge into graded facts."""
    # RunResult artifacts are merged into judge facts.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    (workdir / "note.md").write_text("workdir file")
    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )
    seen = {}

    def grade(
        assertions: object,
        tree: object,
        contents: object,
        shas: object,
        result_text: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Grade."""
        seen["tree"], seen["contents"] = tree, contents
        return {
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ]
        }

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "alpha",
                "trial",
                "done",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=True,
                artifacts={"~/.claude/skills/commit/SKILL.md": "---\nname: commit\n---\n"},
            )
        ),
        grade=grade,
        bind=_punt_all,
    )

    assert "~/.claude/skills/commit/SKILL.md" in seen["tree"]
    assert "note.md" in seen["tree"]
    assert seen["contents"]["~/.claude/skills/commit/SKILL.md"] == "---\nname: commit\n---\n"
    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    transcript = json.loads((run_dir / "transcript.json").read_text())
    assert "~/.claude/skills/commit/SKILL.md" in transcript[0]["workdir_tree"]


def test_process_facts_reach_the_judge(tmp_path: object) -> None:
    """Verify process facts reach the judge."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path,
        {"id": "alpha", "prompt": "perform the task", "assertions": ["uses writing-prompts"]},
    )
    traj = [
        {
            "kind": "tool_call",
            "id": "t1",
            "name": "Skill",
            "arguments": {"skill": "writing-prompts"},
        }
    ]
    seen = {}

    def grade(
        assertions: object,
        tree: object,
        contents: object,
        shas: object,
        result_text: object,
        eval_id: object,
        config: object,
        *,
        judge_config: object = None,
        original_shas: object = None,
        process_facts: object = "",
    ) -> object:
        """Grade."""
        seen["process_facts"] = process_facts
        return {
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ]
        }

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "alpha",
                "trial",
                "inline pass",
                1,
                1,
                False,
                session_id="session-alpha",
                fired=True,
                raw="{}",
                trajectory=traj,
            )
        ),
        grade=grade,
        bind=_punt_all,
    )
    assert "Skill(writing-prompts)" in seen["process_facts"]


def test_bind_failure_punts_to_judge_not_error(tmp_path: object) -> None:
    """Verify bind failure punts to judge not error."""
    # Bind failures degrade to semantic grading.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a1"]}
    )
    judged = []

    def bind_raises(text: object) -> NoReturn:
        """Bind raises."""
        raise RuntimeError("host claude hiccup")

    def grade(assertions: object, *args: object, **kwargs: object) -> object:
        """Grade."""
        judged.append(list(assertions))
        return {
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ]
        }

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=grade,
        bind=bind_raises,
    )

    assert outcome.errored is False  # bind failure did NOT error the eval_arm
    assert judged == [["a1"]]  # it was routed to the judge
    assert outcome.grading["assertions"][0]["type"] == "semantic"
    assert outcome.grading["binder_degraded"] == 1


def test_binder_degraded_counts_once_per_bind_cache_miss_not_per_assertion(
    tmp_path: object,
) -> None:
    """Verify binder_degraded counts distinct bind_cache misses, not assertions."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["dup", "dup"]}
    )
    calls = []

    def bind(text: object) -> NoReturn:
        calls.append(text)
        raise RuntimeError("gemini transient failure")

    outcome = run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
        ),
        grade=_grade_all_pass,
        bind=bind,
    )

    assert len(calls) == 1  # the second "dup" hit the bind_cache, never called bind again
    assert outcome.grading["binder_degraded"] == 1


def test_bind_punt_does_not_count_as_binder_degraded(tmp_path: object) -> None:
    """Verify an ordinary punt (bind returns None) is not a degradation."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a1"]})

    outcome = run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert outcome.grading["binder_degraded"] == 0


def test_run_eval_arm_propagates_binder_auth_error(tmp_path: object) -> None:
    """Verify a BinderAuthError is never caught or counted — it fails the run."""
    from evalspec.grading.binder import BinderAuthError

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a1"]})

    def bind(text: object) -> NoReturn:
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        run_eval_arm(
            eval_case, TRIAL, workdir, {}, tmp_path,
            today="2099-01-01", repo_root=tmp_path, sample=0,
            session_factory=fake_session_factory(
                RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
            ),
            grade=_grade_all_pass,
            bind=bind,
        )


def test_bind_caches_distinct_strings(tmp_path: object) -> None:
    """Verify bind caches distinct strings."""
    # Repeated assertions bind once.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    (workdir / "out.md").write_text("x")
    eval_case = _case(
        tmp_path,
        {
            "id": "alpha",
            "prompt": "perform the task",
            "assertions": ["the file out.md exists", "the file out.md exists"],
        },
    )
    bind_calls = []

    def bind(text: object) -> object:
        """Bind."""
        bind_calls.append(text)
        return {"type": "deterministic", "checker": "file_exists", "path": "out.md"}

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=_grade_all_pass,
        bind=bind,
    )

    assert bind_calls == ["the file out.md exists"]  # bound once, not twice


def test_bound_file_exists_check_runs_on_workdir(tmp_path: object) -> None:
    """Verify bound file exists check runs on workdir."""
    # A bound file_exists spec is checked on the host workdir; no judge call for it.
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    (workdir / "report.md").write_text("done")
    eval_case = _case(
        tmp_path,
        {
            "id": "alpha",
            "prompt": "perform the task",
            "assertions": ["the file report.md exists"],
        },
    )
    judged = []

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "file_exists", "path": "report.md"}

    def grade(assertions: object, *args: object, **kwargs: object) -> object:
        """Grade."""
        judged.append(list(assertions))
        return {"assertions": []}

    outcome = run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=grade,
        bind=bind,
    )

    assert judged == []  # no judge call — the spec was checked deterministically
    entry = outcome.grading["assertions"][0]
    assert entry["passed"] is True
    assert entry["type"] == "deterministic"
    assert entry["evidence"].startswith("CHECK file_exists:")


def test_timing_json_contains_token_split(tmp_path: object) -> None:
    """Verify timing json contains token split."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(
        tmp_path, {"id": "alpha", "prompt": "perform the task", "assertions": ["a"]}
    )

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult(
                "alpha",
                "trial",
                "r1",
                10,
                50,
                False,
                session_id="session-alpha",
                fired=True,
                input_tokens=120,
                output_tokens=40,
            )
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    timing = json.loads((run_dir / "timing.json").read_text())
    assert timing["input_tokens"] == 120
    assert timing["output_tokens"] == 40


def test_grading_is_self_describing(tmp_path: object) -> None:
    """Verify grading is self describing."""
    workspace.set_current_iteration("iteration_01")

    workdir = tmp_path / "wd"
    workdir.mkdir()

    eval_case = _case(tmp_path, {"id": "rho", "prompt": "perform the task", "assertions": ["a"]})

    run_eval_arm(
        eval_case,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=3,
        session_factory=fake_session_factory(
            RunResult("rho", "trial", "done", 1, 1, False, session_id="session-alpha", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "rho", "trial", sample=3)
    persisted = json.loads((run_dir / "grading.json").read_text())
    assert persisted["skill"] == "myskill"
    assert persisted["sample"] == 3
    assert persisted["arm"] == "trial"



_CANNED_FINGERPRINT = FingerprintInputs(
    backend_id="microsandbox",
    base_image_ref="ubuntu:latest",
    install_fingerprint="deadbeef1234",
    env_script_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    digest="abc12345",
)


@dataclass
class _FakeBackend:
    """A backend double exposing only the provenance seams run_eval_arm reaches.

    `fingerprint_inputs`/`image_identity` feed the static sandbox identity; `guest_shell`
    is the guest command seam the version probe uses against the live sandbox.
    """

    image: ImageIdentity
    id: str = "microsandbox"
    guest_output: str | None = "claude 1.2.3"
    guest_shell_calls: list = field(default_factory=list)

    def fingerprint_inputs(self, agent: object, env: object) -> FingerprintInputs:
        """Return the canned fingerprint inputs (ignores agent/env)."""
        return _CANNED_FINGERPRINT

    def image_identity(self, snapshot: object) -> ImageIdentity:
        """Return the canned image identity (available or unavailable)."""
        return self.image

    async def guest_shell(self, sandbox: object, agent: object, script: str) -> str | None:
        """Record the guest command and return the canned version output."""
        self.guest_shell_calls.append((sandbox, agent, script))
        return self.guest_output


class _FakeAgent:
    """A task-harness agent double carrying only what the version probe touches."""

    def __init__(self) -> None:
        """Bind the guest binary name and reset the host-binding tripwire."""
        self.agent_bin = "claude"
        self.for_host_called = False

    def for_host(self) -> object:
        """Trip a flag if the (wrong) host-binding path is ever taken by the probe."""
        self.for_host_called = True
        return self


class _FakeArmSession:
    """A live-sandbox session double: `__aenter__` returns the bound `_run` method.

    Mirrors the real `SandboxSession` seam so `run.__self__._sandbox` reaches the live
    guest — the exact path the guest version probe uses.
    """

    def __init__(self, sandbox: object, result: object) -> None:
        """Hold the live sandbox and the single canned turn result."""
        self._sandbox = sandbox
        self._result = result

    async def __aenter__(self) -> object:
        """Enter the session, exposing the bound per-turn run method."""
        return self._run

    async def __aexit__(self, *exc: object) -> None:
        """Exit the session (no teardown needed for the double)."""
        return None

    async def _run(
        self, prompt: object, *, resume_session_id: object, detect_skill: object
    ) -> object:
        """Return the canned RunResult for the arm's one turn."""
        return self._result


def _live_sandbox_session_factory(sandbox: object, result: object) -> object:
    """Build a session factory whose session exposes a live `_sandbox` for probing."""

    def factory(**kwargs: object) -> _FakeArmSession:
        """Open a live-sandbox session double for one arm."""
        return _FakeArmSession(sandbox, result)

    return factory


def test_provenance_json_written_with_arm_and_guest_version(
    tmp_path: object, monkeypatch: object
) -> None:
    """A completed sample writes provenance.json with arm, fingerprint, and guest version."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    agent = _FakeAgent()
    backend = _FakeBackend(image=ImageIdentity.available("sha256:cafef00d"))
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)

    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=_live_sandbox_session_factory(object(), result),
        grade=_grade_all_pass, bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    data = json.loads((run_dir / "provenance.json").read_text())
    assert data["arm"] == "trial"
    assert data["actual_version"] == "1.2.3"
    assert data["actual_version_status"] == "available"
    assert data["actual_version_error"] is None
    sandbox = data["sandbox"]
    assert sandbox["backend"] == "microsandbox"
    assert sandbox["fingerprint"] == "abc12345"
    assert sandbox["base_image_ref"] == "ubuntu:latest"
    assert sandbox["install_fingerprint"] == "deadbeef1234"
    assert sandbox["env_script_sha256"] == _CANNED_FINGERPRINT.env_script_sha256
    assert sandbox["image_digest"] == "sha256:cafef00d"
    assert sandbox["image_digest_status"] == "available"


def test_guest_probe_uses_live_sandbox_seam_not_host(
    tmp_path: object, monkeypatch: object
) -> None:
    """The version probe runs through the live-sandbox guest seam, never for_host()."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    agent = _FakeAgent()
    backend = _FakeBackend(image=ImageIdentity.available("sha256:cafef00d"))
    live_sandbox = object()
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)

    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=_live_sandbox_session_factory(live_sandbox, result),
        grade=_grade_all_pass, bind=_punt_all,
    )

    assert len(backend.guest_shell_calls) == 1  # probed exactly once
    probed_sandbox, probed_agent, script = backend.guest_shell_calls[0]
    assert probed_sandbox is live_sandbox  # the LIVE sandbox, not a name/snapshot
    assert probed_agent is agent
    assert script == "claude --version"
    assert agent.for_host_called is False  # never the host binding


def test_image_identity_failure_is_unavailable_but_run_completes(
    tmp_path: object, monkeypatch: object
) -> None:
    """An unavailable image digest is recorded explained, and other artifacts still write."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    agent = _FakeAgent()
    backend = _FakeBackend(image=ImageIdentity.unavailable("manifest read failed"))
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)

    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    outcome = run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=_live_sandbox_session_factory(object(), result),
        grade=_grade_all_pass, bind=_punt_all,
    )

    assert outcome.errored is False  # a digest failure never aborts the run
    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    assert (run_dir / "grading.json").is_file()
    assert (run_dir / "timing.json").is_file()
    assert (run_dir / "transcript.json").is_file()
    sandbox = json.loads((run_dir / "provenance.json").read_text())["sandbox"]
    assert sandbox["image_digest"] is None
    assert sandbox["image_digest_status"] == "unavailable"
    assert sandbox["image_digest_error"] == "manifest read failed"


def test_capture_error_on_real_arm_raises_loudly(
    tmp_path: object, monkeypatch: object
) -> None:
    """A genuine capture failure fails the arm loudly, not by silently dropping provenance."""
    # Capture runs before the agent/grading, so there is no completed result to shield: a
    # bad environment config must propagate here, like a stray prompt placeholder does.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    agent = _FakeAgent()
    backend = _FakeBackend(image=ImageIdentity.available("sha256:cafef00d"))
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)

    def _raise_config(repo_root: object) -> NoReturn:
        """Stand in for a real config defect surfaced at capture time."""
        raise SchemaError("[tool.evalspec] base_image must be a non-empty string")

    monkeypatch.setattr(
        "evalspec.orchestration.execution.resolve_environment_config", _raise_config
    )

    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    with pytest.raises(SchemaError, match="base_image"):
        run_eval_arm(
            eval_case, TRIAL, workdir, {}, tmp_path,
            today="2099-01-01", repo_root=tmp_path, sample=0,
            session_factory=_live_sandbox_session_factory(object(), result),
            grade=_grade_all_pass, bind=_punt_all,
        )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    assert not (run_dir / "provenance.json").exists()  # failed loudly, wrote nothing


def test_provenance_json_survives_binder_failure_after_sandbox_use(
    tmp_path: object, monkeypatch: object
) -> None:
    """A sandbox that ran remains observed when later binding aborts the arm."""
    from evalspec.grading.binder import BinderAuthError

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    agent = _FakeAgent()
    backend = _FakeBackend(image=ImageIdentity.available("sha256:cafef00d"))
    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    def bind(text: object) -> NoReturn:
        """Simulate an authentication failure after the sandbox task completed."""
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        run_eval_arm(
            eval_case, TRIAL, workdir, {}, tmp_path,
            today="2099-01-01", repo_root=tmp_path, sample=0,
            session_factory=_live_sandbox_session_factory(object(), result),
            grade=_grade_all_pass, bind=bind,
        )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    provenance = json.loads((run_dir / "provenance.json").read_text())
    assert provenance["actual_version"] == "1.2.3"
    assert provenance["sandbox"]["snapshot"] == "snap"


def test_provenance_fingerprint_uses_environment_that_selected_snapshot(
    tmp_path: object, monkeypatch: object
) -> None:
    """Recorded fingerprint inputs match the environment used for snapshot selection."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    class SnapshotAgent(_FakeAgent):
        """Agent double exposing snapshot naming identity."""

        id = "claude-code"

        def version(self) -> str:
            """Return a stable host version for snapshot naming."""
            return "1.2.3"

    @dataclass
    class EnvironmentBackend(_FakeBackend):
        """Backend double whose snapshot fingerprint reflects its environment input."""

        def cache_fingerprint(self, agent: object, env: EnvConfig) -> str:
            """Use the base image as an observable snapshot cache identity."""
            return str(env.base_image)

        def snapshot_exists(self, name: str) -> bool:
            """Treat every computed snapshot as already built."""
            return True

        def fingerprint_inputs(self, agent: object, env: EnvConfig) -> FingerprintInputs:
            """Expose the environment used when provenance is captured."""
            return FingerprintInputs(
                backend_id=self.id,
                base_image_ref=str(env.base_image),
                install_fingerprint="deadbeef1234",
                env_script_sha256=_CANNED_FINGERPRINT.env_script_sha256,
                digest=str(env.base_image),
            )

    agent = SnapshotAgent()
    backend = EnvironmentBackend(image=ImageIdentity.available("sha256:cafef00d"))
    environments = iter(
        [EnvConfig(base_image="snapshot-image"), EnvConfig(base_image="changed-image")]
    )

    def resolve_environment(repo_root: object) -> EnvConfig:
        """Return a changed config if production resolves the environment twice."""
        return next(environments)

    monkeypatch.setattr("evalspec.orchestration.execution.make_agent", lambda harness=None: agent)
    monkeypatch.setattr("evalspec.orchestration.execution.resolve_sandbox", lambda name: backend)
    monkeypatch.setattr("evalspec.orchestration.execution.ensure_snapshot", ensure_snapshot)
    monkeypatch.setattr("evalspec.sandbox.sandbox.resolve_environment_config", resolve_environment)
    monkeypatch.setattr(
        "evalspec.orchestration.execution.resolve_environment_config", resolve_environment
    )
    eval_case = _case(tmp_path, {"id": "alpha", "prompt": "work", "assertions": ["a"]})
    result = RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)

    run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=_live_sandbox_session_factory(object(), result),
        grade=_grade_all_pass, bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    provenance = json.loads((run_dir / "provenance.json").read_text())
    assert provenance["sandbox"]["snapshot"].endswith("-snapshot-image")
    assert provenance["sandbox"]["base_image_ref"] == "snapshot-image"
