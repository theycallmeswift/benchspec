"""Tests for execution."""

import contextlib
import json
from typing import NoReturn

import pytest

from evalspec import workspace
from evalspec.arms import Arm
from evalspec.discovery import EvalCase
from evalspec.execution import run_eval_arm
from evalspec.runner import RunResult

TRIAL = Arm("trial", "claude-code", "opus")
BASELINE = Arm("baseline", "claude-code", "opus")


@pytest.fixture(autouse=True)
def _no_real_vm(monkeypatch: object) -> None:
    """Build the no real vm test fixture."""
    # run_eval_arm resolves a real agent + snapshot (which would build a microVM). Stub both
    # so unit tests never touch microsandbox; session_factory is faked separately per test.
    monkeypatch.setattr("evalspec.execution.make_agent", lambda harness=None: None)
    monkeypatch.setattr("evalspec.execution.ensure_snapshot", lambda agent, **k: "snap")


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
        "assertions": [{"text": a, "passed": True, "evidence": "ok"} for a in assertions],
    }


def _punt_all(text: object) -> None:
    """Build the punt all test fixture."""
    return None  # never bind — send every assertion to the judge


def _case(tmp_path: object, eval_obj: object, skill: object = "myskill") -> object:
    """Build the case test fixture."""
    return EvalCase(skill_dir=tmp_path / "skills" / skill, eval=eval_obj)


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
    ec = _case(
        tmp_path,
        {"slug": "alpha", "prompt": "work in ./vault", "assertions": ["a1", "a2"]},
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        ec,
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
    assert outcome.fired is True
    assert [a["passed"] for a in outcome.grading["assertions"]] == [True, True]
    # session_factory called with the arm NAME (not the Arm repr) and arm.model
    factory_kwargs = session_factory.calls[0]
    assert factory_kwargs["host_workdir"] == workdir
    assert factory_kwargs["config"] == "trial"
    assert factory_kwargs["model"] == "opus"  # arm.model, not a --evalspec-model fixture

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
    ec = _case(
        tmp_path,
        {
            "slug": "alpha",
            "prompt": "work",
            "assertions": ["file at ./9. Archive/Sources/{TODAY}/x.md"],
        },
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        ec,
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
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "work", "assertions": [prose]})

    def bind(text: object) -> object:
        """Bind."""
        return {
            "checker": "file_exists",
            "path": "./gone.md",
            "should_exist": False,
            "type": "deterministic",
        }

    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )

    outcome = run_eval_arm(
        ec,
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


def test_binder_timeout_punts_to_judge_not_errors(tmp_path: object) -> None:
    """Verify binder timeout punts to judge not errors."""
    # A binder host-subprocess timeout must degrade to a judge punt, not crash the cell.
    import subprocess

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "work", "assertions": ["a1"]})

    def bind(text: object) -> NoReturn:
        """Bind."""
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)

    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 100, 50, False, session_id="s1", fired=True),
    )
    outcome = run_eval_arm(
        ec,
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

    def grade(texts: object, *a: object, **k: object) -> object:
        """Grade."""
        judged.append(list(texts))
        return {"assertions": [{"text": t, "passed": True, "evidence": "ok"} for t in texts]}

    wd_trial = tmp_path / "trial"
    wd_trial.mkdir()
    ec = _case(
        tmp_path,
        {"slug": "single", "prompt": "p", "assertions": assertions},
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
        ec,
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
                session_id="s",
                fired=True,
                trajectory=traj,
            )
        ),
        grade=grade,
        bind=bind,
    )

    t = {a["text"]: a["passed"] for a in trial.grading["assertions"]}
    assert t["Skill `ingest` invoked"] is True
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

    def grade(texts: object, *a: object, **k: object) -> object:
        """Grade."""
        judged.append(list(texts))
        return {"assertions": [{"text": t, "passed": True, "evidence": "ok"} for t in texts]}

    wd_base = tmp_path / "base"
    wd_base.mkdir()
    ec = _case(
        tmp_path,
        {"slug": "single", "prompt": "p", "assertions": assertions},
        skill="ingest",
    )

    baseline = run_eval_arm(
        ec,
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
                session_id="s",
                fired=False,
                trajectory=[],
            )
        ),
        grade=grade,
        bind=bind,
    )

    b = {a["text"]: a["passed"] for a in baseline.grading["assertions"]}
    assert b["Skill `ingest` invoked"] is False  # graded False, not a crash or skip
    assert judged == [["the summary reflects the facts"]]  # the semantic one reached the judge
    assert "gated" not in baseline.grading


def test_run_eval_arm_no_fired_gate(tmp_path: object) -> None:
    """Verify run eval arm no fired gate."""
    # A trial arm where the skill never fired must grade the activation assertion False,
    # not raise — there is no fired-gate that crashes the arm.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(
        tmp_path,
        {"slug": "miss", "prompt": "p", "assertions": ["Skill `ingest` invoked"]},
        skill="ingest",
    )

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}

    outcome = run_eval_arm(
        ec,
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
                session_id="s",
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
    # Baseline arm, skill absent: the trajectory reports nothing dispatched. The
    # skill_invoked assertion grades False off an EMPTY fired set, not off an error.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(
        tmp_path,
        {"slug": "base", "prompt": "p", "assertions": ["Skill `ingest` invoked"]},
        skill="ingest",
    )

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}

    outcome = run_eval_arm(
        ec,
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
                session_id="s",
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


def _record_session_model(seen: object, name: object) -> object:
    """Build the record session model test fixture."""

    @contextlib.asynccontextmanager
    async def factory(**kwargs: object) -> object:
        """Factory."""
        seen[name] = kwargs["model"]

        async def run(prompt: object, *, resume_session_id: object, detect_skill: object) -> object:
            """Run."""
            return RunResult("m", name, "out", 1, 1, False, session_id="s", fired=True)

        yield run

    return factory


def test_run_eval_arm_routes_opus_arm_model(tmp_path: object) -> None:
    """Verify run eval arm routes opus arm model."""
    # The recorded session model == arm.model, NOT a shared --evalspec-model fixture value.
    workspace.set_current_iteration("iteration_01")
    ec = _case(tmp_path, {"slug": "m", "prompt": "p", "assertions": ["a"]})
    seen = {}
    wd_o = tmp_path / "o"
    wd_o.mkdir()

    run_eval_arm(
        ec,
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
    ec = _case(tmp_path, {"slug": "m", "prompt": "p", "assertions": ["a"]})
    seen = {}
    wd_s = tmp_path / "s"
    wd_s.mkdir()

    run_eval_arm(
        ec,
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
    # The agent is built from arm.harness, so a multi-harness set runs each column on its
    # own harness, not the single global EVALSPEC_AGENT.
    workspace.set_current_iteration("iteration_01")
    captured = {}

    def fake_make_agent(harness: object = None) -> None:
        """Fake make agent."""
        captured["harness"] = harness
        return None

    monkeypatch.setattr("evalspec.execution.make_agent", fake_make_agent)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})
    arm = Arm("trial", "opencode", "anthropic/claude-sonnet-4-6", "high", {})

    run_eval_arm(
        ec,
        arm,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert captured["harness"] == "opencode"


def test_run_eval_arm_threads_arm_effort_and_expanded_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify run eval arm threads arm effort and expanded env."""
    # Effort and env reach the session from `arm`, not a shared fixture; env `$VAR` is
    # expanded against os.environ at exec time, and the resolved eval-set name threads through.
    workspace.set_current_iteration("iteration_01")
    monkeypatch.setenv("SECRET", "s3cr3t")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})
    arm = Arm(
        "trial",
        "opencode",
        "anthropic/claude-sonnet-4-6",
        "high",
        {"TOK": "$SECRET", "LIT": "plain"},
    )
    session_factory = fake_session_factory(
        RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
    )

    run_eval_arm(
        ec,
        arm,
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

    kw = session_factory.calls[0]
    assert kw["effort"] == "high"  # effort comes from arm.effort
    assert kw["arm_env"] == {
        "TOK": "s3cr3t",
        "LIT": "plain",
    }  # $VAR expanded at exec time
    assert kw["eval_set"] == "trial-set"


def test_run_eval_arm_threads_harness_args(tmp_path: object) -> None:
    """Verify run eval arm threads harness args."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})
    arm = Arm("plugin", "claude-code", "opus", harness_args=["--plugin-dir", "/project"])
    session_factory = fake_session_factory(
        RunResult("alpha", "plugin", "done", 1, 1, False, session_id="s", fired=True),
    )

    run_eval_arm(
        ec,
        arm,
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


def test_artifact_dir_uses_arm_name_string(tmp_path: object) -> None:
    """Verify artifact dir uses arm name string."""
    # The written workspace dir is eval-<id>/trial/sample-0 — the arm NAME, not an Arm(...)
    # repr. Guards the writer/reader desync with the report reader.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})

    run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
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
    # The one behavioral promise of seed:, verified end-to-end.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(
        tmp_path,
        {
            "slug": "seeded",
            "prompt": "the deeper question",
            "seed": [
                {"role": "user", "text": "scope my plan"},
                {"role": "assistant", "text": "which part?"},
            ],
            "assertions": ["a"],
        },
    )
    session_factory = fake_session_factory(
        RunResult("seeded", "trial", "out", 1, 1, False, session_id="s", fired=True),
    )

    run_eval_arm(
        ec,
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
    ec = _case(tmp_path, {"slug": "noseed", "prompt": "just the prompt", "assertions": ["a"]})
    session_factory = fake_session_factory(
        RunResult("noseed", "trial", "out", 1, 1, False, session_id="s", fired=True),
    )

    run_eval_arm(
        ec,
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


def test_detect_skill_passed_unconditionally(tmp_path: object) -> None:
    """Verify detect skill passed unconditionally."""
    # `fired` bookkeeping streams the skill name on every arm — detect_skill is the
    # suite's skill, not gated on a with_skill literal.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]}, skill="ingest")
    session_factory = fake_session_factory(
        RunResult("alpha", "baseline", "out", 1, 1, False, session_id="s", fired=False),
    )

    run_eval_arm(
        ec,
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

    assert session_factory.detects == ["ingest"]


def test_errored_run_flags_outcome(tmp_path: object) -> None:
    """Verify errored run flags outcome."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "gamma", "prompt": "p", "assertions": ["a"]})
    session_factory = fake_session_factory(
        RunResult("gamma", "baseline", "<timeout>", 0, 0, True),
    )

    def grade_fail(assertions: object, *a: object, **k: object) -> object:
        """Grade fail."""
        return {"assertions": [{"text": x, "passed": False, "evidence": ""} for x in assertions]}

    outcome = run_eval_arm(
        ec,
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
    """Verify failed assertions do not error the arm."""
    # A failed assertion is the recorded measurement, not a gate — outcome.errored stays
    # False so the report counts it as an honest 0%, not an excluded infra failure.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "fail", "prompt": "p", "assertions": ["a1", "a2"]})

    def grade_fail(assertions: object, *a: object, **k: object) -> object:
        """Grade fail."""
        return {"assertions": [{"text": x, "passed": False, "evidence": "no"} for x in assertions]}

    outcome = run_eval_arm(
        ec,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("fail", "baseline", "out", 1, 1, False, session_id="s", fired=False)
        ),
        grade=grade_fail,
        bind=_punt_all,
    )

    assert outcome.errored is False
    assert [a["passed"] for a in outcome.grading["assertions"]] == [False, False]


def test_default_judge_config_used_by_default(tmp_path: object) -> None:
    """Verify the default JudgeConfig grades when no judge config is passed."""
    # The judge is independent of the task arm: the arm can run any harness/model
    # (here opencode/gemini) while the judge still grades with the default JudgeConfig
    # (claude-code/sonnet) — grading never calls the task arm as judge.
    from evalspec.judges import JudgeConfig

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "mu", "prompt": "p", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions: object, *a: object, judge_config: object, **k: object) -> object:
        """Grade."""
        seen_configs.append(judge_config)
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    run_eval_arm(
        ec,
        Arm("trial", "opencode", "google/gemini-3.5-flash"),
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("mu", "trial", "did it", 1, 1, False, session_id="s", fired=True)
        ),
        grade=grade,
        bind=_punt_all,
    )

    assert seen_configs == [JudgeConfig()]


def test_judge_config_param_overrides_default(tmp_path: object) -> None:
    """Verify a passed judge_config overrides the default."""
    from evalspec.judges import JudgeConfig

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "nu", "prompt": "p", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions: object, *a: object, judge_config: object, **k: object) -> object:
        """Grade."""
        seen_configs.append(judge_config)
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    custom = JudgeConfig(harness="codex", model="gpt-5.5")
    run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        judge_config=custom,
        session_factory=fake_session_factory(
            RunResult("nu", "trial", "done", 1, 1, False, session_id="s", fired=True)
        ),
        grade=grade,
        bind=_punt_all,
    )

    assert seen_configs == [custom]


def test_judge_runtimeerror_marks_arm_errored(tmp_path: object) -> None:
    """Verify judge runtimeerror marks arm errored."""
    # An infra-level judge failure raises RuntimeError; run_eval_arm surfaces it as
    # arm-level errored=True with the real cause in each assertion's evidence.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "kappa", "prompt": "p", "assertions": ["a1", "a2"]})

    def grade_raises(*a: object, **k: object) -> NoReturn:
        """Grade raises."""
        raise RuntimeError("host claude CLI returned is_error=true: Not logged in")

    outcome = run_eval_arm(
        ec,
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
                session_id="s",
                fired=True,
            )
        ),
        grade=grade_raises,
        bind=_punt_all,
    )

    assert outcome.errored is True
    assertions = outcome.grading["assertions"]
    assert [a["text"] for a in assertions] == ["a1", "a2"]
    assert all("JUDGE INFRA ERROR" in a["evidence"] for a in assertions)
    assert all("Not logged in" in a["evidence"] for a in assertions)
    run_dir = workspace.arm_dir(tmp_path, "myskill", "kappa", "trial", sample=0)
    persisted = json.loads((run_dir / "grading.json").read_text())
    assert persisted["errored"] is True


def test_missing_judge_binary_marks_arm_errored(tmp_path: object, monkeypatch: object) -> None:
    """Verify missing judge binary marks arm errored."""
    # The host `claude` CLI missing must surface as arm-level errored=True. Drives the REAL
    # grade_run + a real agent so the whole chain runs.
    from evalspec.agents.claude import ClaudeCodeAgent

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "lam", "prompt": "p", "assertions": ["a1", "a2"]})
    agent = ClaudeCodeAgent(auth_value="k", version="v")
    monkeypatch.setattr("evalspec.execution.make_agent", lambda harness=None: agent)

    def boom(*a: object, **k: object) -> NoReturn:
        """Boom."""
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")

    monkeypatch.setattr("evalspec.agents.judge_cli.subprocess.run", boom)

    outcome = run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("lam", "trial", "did the thing", 1, 1, False, session_id="s", fired=True)
        ),
        bind=_punt_all,  # grade defaults to the real grade_run
    )

    assert outcome.errored is True
    assertions = outcome.grading["assertions"]
    assert [a["passed"] for a in assertions] == [False, False]
    assert all("JUDGE INFRA ERROR" in a["evidence"] for a in assertions)
    run_dir = workspace.arm_dir(tmp_path, "myskill", "lam", "trial", sample=0)
    assert json.loads((run_dir / "grading.json").read_text())["errored"] is True


def test_artifacts_land_under_sample_dir(tmp_path: object) -> None:
    """Verify artifacts land under sample dir."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})

    run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=2,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
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
    # One session.jsonl for the arm, delimited by {"turn": 1}; the structured trajectory
    # regenerates from it.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})
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
        ec,
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
                session_id="s",
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
    from evalspec.trajectory import trajectory_from_session

    evs = trajectory_from_session(text)
    assert {
        "turn": 1,
        "kind": "tool_call",
        "id": "t1",
        "name": "Skill",
        "arguments": {"skill": "writing-prompts"},
    } in evs


def test_no_session_jsonl_when_no_raw(tmp_path: object) -> None:
    """Verify no session jsonl when no raw."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "beta", "prompt": "p", "assertions": ["a"]})

    run_eval_arm(
        ec,
        BASELINE,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("beta", "baseline", "out", 1, 1, False, session_id="s")
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
    # Files the agent wrote outside the workdir mount ride on RunResult.artifacts and must
    # reach the judge's tree/contents.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "note.md").write_text("workdir file")
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})
    seen = {}

    def grade(
        assertions: object,
        tree: object,
        contents: object,
        shas: object,
        result_text: object,
        *a: object,
        **k: object,
    ) -> object:
        """Grade."""
        seen["tree"], seen["contents"] = tree, contents
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    run_eval_arm(
        ec,
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
                session_id="s",
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
    ec = _case(
        tmp_path,
        {"slug": "alpha", "prompt": "p", "assertions": ["uses writing-prompts"]},
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
        return {"assertions": [{"text": a, "passed": True, "evidence": "ok"} for a in assertions]}

    run_eval_arm(
        ec,
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
                session_id="s",
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
    # A transient bind() RuntimeError must degrade to semantic grading, never error the cell.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a1"]})
    judged = []

    def bind_raises(text: object) -> NoReturn:
        """Bind raises."""
        raise RuntimeError("host claude hiccup")

    def grade(assertions: object, *a: object, **k: object) -> object:
        """Grade."""
        judged.append(list(assertions))
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    outcome = run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
        ),
        grade=grade,
        bind=bind_raises,
    )

    assert outcome.errored is False  # bind failure did NOT error the arm
    assert judged == [["a1"]]  # it was routed to the judge
    assert outcome.grading["assertions"][0]["type"] == "semantic"


def test_bind_caches_distinct_strings(tmp_path: object) -> None:
    """Verify bind caches distinct strings."""
    # A repeated assertion binds once — the binder is a per-assertion host call, so caching
    # bounds the new latency.
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "out.md").write_text("x")
    ec = _case(
        tmp_path,
        {
            "slug": "alpha",
            "prompt": "p",
            "assertions": ["the file out.md exists", "the file out.md exists"],
        },
    )
    bind_calls = []

    def bind(text: object) -> object:
        """Bind."""
        bind_calls.append(text)
        return {"type": "deterministic", "checker": "file_exists", "path": "out.md"}

    run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
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
    ec = _case(
        tmp_path,
        {"slug": "alpha", "prompt": "p", "assertions": ["the file report.md exists"]},
    )
    judged = []

    def bind(text: object) -> object:
        """Bind."""
        return {"type": "deterministic", "checker": "file_exists", "path": "report.md"}

    def grade(assertions: object, *a: object, **k: object) -> object:
        """Grade."""
        judged.append(list(assertions))
        return {"assertions": []}

    outcome = run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s", fired=True)
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
    ec = _case(tmp_path, {"slug": "alpha", "prompt": "p", "assertions": ["a"]})

    run_eval_arm(
        ec,
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
                session_id="s",
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
    ec = _case(tmp_path, {"slug": "rho", "prompt": "p", "assertions": ["a"]})

    run_eval_arm(
        ec,
        TRIAL,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=3,
        session_factory=fake_session_factory(
            RunResult("rho", "trial", "done", 1, 1, False, session_id="s", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "rho", "trial", sample=3)
    persisted = json.loads((run_dir / "grading.json").read_text())
    assert persisted["skill"] == "myskill"
    assert persisted["sample"] == 3
    assert persisted["arm"] == "trial"
