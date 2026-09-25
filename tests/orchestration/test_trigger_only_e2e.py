"""End-to-end artifact tests for trigger-only evals: derived before the run, stopped early.

Each test writes a real `eval.md`, parses it with `mdformat.parse_eval_md`, and drives it
through the real `run_eval_arm` and artifact writer. Only the guest is a double: a session
factory that records the stop rule it was handed and answers the one turn with a canned
`RunResult`, as the adapter's watched `invoke` would have returned it. The binder is a
real-shaped double mapping activation lines to their checkers.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.config.arms import Arm
from benchspec.grading.trigger import TriggerWatch
from benchspec.orchestration import workspace
from benchspec.orchestration.execution import run_eval_arm
from benchspec.orchestration.results import RunResult
from benchspec.reporting import report
from benchspec.sandbox.sandbox import TurnRunner
from benchspec.specs import mdformat
from benchspec.specs.discovery import EvalCase

_EVAL_HEADER = dedent("""\
    ---
    ---

    ## Prompt

    Say hi to Dana for me.

    ## Assertions

""")

HELLO_DISPATCH = [
    {"kind": "tool_call", "id": "t1", "name": "Skill", "arguments": {"skill": "hello"}}
]

TRIAL_ARM = Arm("trial", "claude-code", "sonnet")
BASELINE_ARM = Arm("baseline", "claude-code", "sonnet")


@pytest.fixture(autouse=True)
def _no_real_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `run_eval_arm` off a real sandbox runtime; the session double holds the agent."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/benchspec-docker")
    monkeypatch.setattr(
        "benchspec.orchestration.execution.make_agent",
        lambda harness=None, provider="default": None,
    )
    monkeypatch.setattr(
        "benchspec.orchestration.execution.ensure_snapshot", lambda agent, **kwargs: "snap"
    )


class _WatchRecordingSession:
    """A session factory double that records the `watch` it was opened with."""

    def __init__(self, result: RunResult) -> None:
        """Hold the canned result the one turn returns."""
        self._result = result
        self.watches: list[object] = []

    def __call__(self, **kwargs: object) -> _WatchRecordingSession:
        """Record the session's stop rule; `run_eval_arm` enters the returned session."""
        self.watches.append(kwargs["watch"])
        return self

    async def __aenter__(self) -> TurnRunner:
        """Expose the per-turn runner."""
        return self._run

    async def __aexit__(self, *exc: object) -> None:
        """Nothing to tear down."""
        return None

    async def _run(
        self, prompt: str, *, resume_session_id: str | None, detect_skill: str | None
    ) -> RunResult:
        """Answer the turn with the canned result."""
        return self._result


def _bind(text: str) -> dict | None:
    """Bind `hello` activation lines to their checkers; punt everything else to the judge."""
    if text == "Skill `hello` invoked":
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "hello"}
    if text == "Skill `hello` not invoked":
        return {"type": "deterministic", "checker": "not_skill_invoked", "skill": "hello"}
    return None


def _grade_all_pass(assertions: list[str], *args: object, **kwargs: object) -> dict:
    """A judge double that passes every line it is handed."""
    return {"assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in assertions]}


def _eval_case(tmp_path: Path, checklist: str, eval_id: str = "alpha") -> EvalCase:
    """Write `skills/hello/evals/<eval_id>/eval.md` with `checklist` and parse it for real."""
    eval_dir = tmp_path / "skills" / "hello" / "evals" / eval_id
    eval_dir.mkdir(parents=True)
    eval_file = eval_dir / "eval.md"
    eval_file.write_text(_EVAL_HEADER + checklist)
    return EvalCase(
        group="hello",
        eval_dir=eval_dir,
        eval_file=eval_file,
        eval=mdformat.parse_eval_md(eval_file),
    )


def _run(
    tmp_path: Path, eval_case: EvalCase, arm: Arm, result: RunResult
) -> tuple[_WatchRecordingSession, dict, dict]:
    """Run sample 0 of `arm`; return the session double, grading.json, and timing.json."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)
    session = _WatchRecordingSession(result)

    run_eval_arm(
        eval_case, arm, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=session, grade=_grade_all_pass, bind=_bind, baseline="baseline",
    )

    run_dir = workspace.arm_dir(tmp_path, "hello", eval_case.eval_id, arm.name, sample=0)
    grading = json.loads((run_dir / "grading.json").read_text())
    timing = json.loads((run_dir / "timing.json").read_text())
    return session, grading, timing


def test_trigger_only_eval_is_watched_and_graded_from_the_stopped_run(tmp_path: Path) -> None:
    """Verify an all-activation eval gets a stop rule and a decided stop grades a pass."""
    eval_case = _eval_case(tmp_path, "- [ ] Skill `hello` invoked\n")
    stopped = RunResult(
        "alpha", "trial", "", 1200, 0, False, trajectory=HELLO_DISPATCH, stopped="decided"
    )

    session, grading, timing = _run(tmp_path, eval_case, TRIAL_ARM, stopped)

    assert session.watches == [TriggerWatch(frozenset({"hello"}))]
    assert grading["errored"] is False
    assert grading["assertions"] == [
        {
            "text": "Skill `hello` invoked",
            "passed": True,
            "evidence": "CHECK skill_invoked: `hello` invoked",
            "type": "deterministic",
        }
    ]
    assert timing["stopped"] == "decided"
    assert timing["duration_ms"] == 1200
    assert timing["total_tokens"] is None
    assert timing["input_tokens"] is None


def test_stopped_run_keeps_the_usage_it_did_report(tmp_path: Path) -> None:
    """Verify partial usage from a stopped run is recorded as numbers, not nulled."""
    eval_case = _eval_case(tmp_path, "- [ ] Skill `hello` not invoked\n")
    stopped = RunResult(
        "alpha", "trial", "", 900, 210, False, input_tokens=20, output_tokens=10, stopped="timeout"
    )

    _, grading, timing = _run(tmp_path, eval_case, TRIAL_ARM, stopped)

    assert grading["assertions"][0]["passed"] is True
    assert timing["stopped"] == "timeout"
    assert (timing["total_tokens"], timing["input_tokens"]) == (210, 20)


def test_mixed_eval_is_never_watched(tmp_path: Path) -> None:
    """Verify one outcome line beside a trigger line keeps the run whole and unmarked."""
    eval_case = _eval_case(
        tmp_path, "- [ ] Skill `hello` invoked\n- [ ] The greeting is warm\n"
    )
    full = RunResult("alpha", "trial", "Hi Dana!", 16000, 900, False, trajectory=HELLO_DISPATCH)

    session, grading, timing = _run(tmp_path, eval_case, TRIAL_ARM, full)

    assert session.watches == [None]
    assert [entry["passed"] for entry in grading["assertions"]] == [True, True]
    assert "stopped" not in timing
    assert timing["total_tokens"] == 900


def test_baseline_with_its_only_trigger_line_scoped_off_runs_unwatched(tmp_path: Path) -> None:
    """Verify an arm whose every line is scoped off runs as before, with nothing graded."""
    eval_case = _eval_case(
        tmp_path, "- [ ] Skill `hello` invoked\n  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}\n"
    )
    full = RunResult("alpha", "baseline", "Hi Dana!", 16000, 900, False)

    session, grading, _ = _run(tmp_path, eval_case, BASELINE_ARM, full)

    assert session.watches == [None]
    assert grading["assertions"][0]["skipped"] is True


def test_a_line_the_binder_punts_makes_the_run_whole(tmp_path: Path) -> None:
    """Verify a trigger line that did not bind to an activation checker is not trigger-only."""
    eval_case = _eval_case(tmp_path, "- [ ] The hello skill was used\n")
    full = RunResult("alpha", "trial", "Hi Dana!", 16000, 900, False)

    session, _, _ = _run(tmp_path, eval_case, TRIAL_ARM, full)

    assert session.watches == [None]


def test_stopped_samples_are_counted_in_the_index_and_the_report(tmp_path: Path) -> None:
    """Verify `stopped` reaches index.jsonl rows and the per-arm benchmark section."""
    decided = _eval_case(tmp_path, "- [ ] Skill `hello` invoked\n", eval_id="fires")
    _run(
        tmp_path, decided, TRIAL_ARM,
        RunResult("fires", "trial", "", 1200, 0, False, trajectory=HELLO_DISPATCH,
                  stopped="decided"),
    )
    quiet = _eval_case(tmp_path, "- [ ] Skill `hello` not invoked\n", eval_id="quiet")
    _run(
        tmp_path, quiet, TRIAL_ARM,
        RunResult("quiet", "trial", "", 3000, 400, False, stopped="timeout"),
    )
    skill_dir = workspace.skill_dir(tmp_path, "hello")

    rows = report.index_rows(skill_dir, "hello")
    benchmark = report.build_benchmark(
        report.discover_eval_dirs(skill_dir.parent),
        label="trigger",
        arm_meta={"trial": {"harness": "claude-code", "model": "sonnet"}},
    )
    markdown = report._format_markdown(benchmark)

    assert {row["eval_id"]: row["stopped"] for row in rows} == {
        "fires": "decided",
        "quiet": "timeout",
    }
    assert benchmark["arms"]["trial"]["stopped_samples"] == {"decided": 1, "timeout": 1}
    assert benchmark["arms"]["trial"]["tokens_mean"] == 400
    assert (
        "- Stopped early: 2 trigger-only sample(s) (1 decided, 1 timeout); "
        "their time and tokens are trigger costs, not full-task costs"
    ) in markdown
    assert "| fires | 1 | 1 | 1 | 100% |  | 1 stopped early |" in markdown


def test_stopped_samples_are_counted_when_every_line_is_scoped_off_the_rates(
    tmp_path: Path,
) -> None:
    """Verify a stop is reported even when the line's baseline skip leaves no pooled rate."""
    checklist = "- [ ] Skill `hello` invoked\n  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}\n"
    eval_case = _eval_case(tmp_path, checklist)
    _run(
        tmp_path, eval_case, BASELINE_ARM,
        RunResult("alpha", "baseline", "Hi Dana!", 3000, 30000, False),
    )
    _run(
        tmp_path, eval_case, TRIAL_ARM,
        RunResult("alpha", "trial", "", 3200, 0, False, trajectory=HELLO_DISPATCH,
                  stopped="decided"),
    )

    benchmark = report.build_benchmark(
        report.discover_eval_dirs(workspace.skills_root(tmp_path)), label="trigger"
    )

    assert benchmark["arms"]["trial"]["stopped_samples"] == {"decided": 1}
    assert benchmark["arms"]["baseline"]["stopped_samples"] == {}
