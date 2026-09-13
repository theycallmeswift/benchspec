"""End-to-end artifact tests for scoped assertions (`- if:` / `- unless:` sub-bullets).

Each test writes a real `eval.md` to `tmp_path`, parses it with `mdformat.parse_eval_md`
(so the checklist parser runs for real, never a hand-built dict), and drives the parsed
case through the real `run_eval_arm` orchestration and artifact writer. Only the guest is
a double: a session that answers the one turn with a canned `RunResult` and counts how
often it was entered. The binder and judge are recording doubles so a test can assert
which assertion texts each was handed — a line whose clause does not hold must reach
neither. The report tests then aggregate the written artifacts with the real `report`
module, the way the plugin's `pytest_sessionfinish` does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.config.arms import Arm
from benchspec.grading.judges import JudgeConfig
from benchspec.orchestration import workspace
from benchspec.orchestration.execution import run_eval_arm
from benchspec.orchestration.results import RunResult
from benchspec.reporting import report
from benchspec.sandbox.sandbox import TurnRunner
from benchspec.specs import mdformat
from benchspec.specs.discovery import EvalCase
from benchspec.specs.schema import SchemaError

_EVAL_HEADER = dedent("""\
    ---
    ---

    ## Prompt

    Greet Bob.

    ## Assertions

""")

# One unscoped line either side of a trigger line scoped off the baseline arm.
THREE_LINE_CHECKLIST = dedent("""\
    - [ ] ./out.md exists
    - [ ] Skill `hello` invoked
      - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
    - [ ] the greeting is warm
""")

TRIGGER_LINE = "Skill `hello` invoked"

BASELINE_ARM = Arm("baseline", "claude-code", "sonnet")
TRIAL_ARM = Arm("trial", "claude-code", "sonnet")

ARM_META = {
    "baseline": {"harness": "claude-code", "model": "sonnet"},
    "trial": {"harness": "claude-code", "model": "sonnet"},
}


@pytest.fixture(autouse=True)
def _no_real_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `run_eval_arm` off a real sandbox runtime; the session double below holds the agent."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/benchspec-docker")
    monkeypatch.setattr(
        "benchspec.orchestration.execution.make_agent",
        lambda harness=None, provider="default": None,
    )
    monkeypatch.setattr(
        "benchspec.orchestration.execution.ensure_snapshot", lambda agent, **kwargs: "snap"
    )


class _RecordingSession:
    """A session double that answers the one turn with a canned result and counts its entries.

    `enters` is how many times the orchestration actually opened the session — the
    "sandbox booted" signal a pre-run failure must leave at zero.
    """

    def __init__(self) -> None:
        """Start with no entries recorded."""
        self.enters = 0

    def __call__(self, **kwargs: object) -> _RecordingSession:
        """Act as the session factory: `run_eval_arm` enters the returned session."""
        return self

    async def __aenter__(self) -> TurnRunner:
        """Enter the session, exposing the per-turn runner."""
        self.enters += 1
        return self._run

    async def __aexit__(self, *exc: object) -> None:
        """Exit the session; the fake guest needs no teardown."""
        return None

    async def _run(
        self, prompt: str, *, resume_session_id: str | None, detect_skill: str | None
    ) -> RunResult:
        """Answer the one turn with a clean, token-bearing result."""
        return RunResult(
            eval_id="alpha",
            config="arm",
            result_text="Greeted Bob.",
            duration_ms=10,
            total_tokens=5,
            is_error=False,
        )


class _RecordingBinder:
    """A binder double that punts every line to the judge and records each text it saw."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.seen: list[str] = []

    def __call__(self, text: str) -> None:
        """Record the text and punt it (no checker binds)."""
        self.seen.append(text)
        return None


class _RecordingGrader:
    """A judge double that passes every line and records each assertion text it was handed."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.seen: list[str] = []

    def __call__(
        self,
        assertions: list[str],
        tree: str,
        contents: dict[str, str],
        shas: dict[str, str],
        final: str,
        eval_id: str,
        config: str,
        *,
        judge_config: JudgeConfig | None = None,
        original_shas: dict[str, str] | None = None,
        process_facts: str = "",
    ) -> dict:
        """Record the batch and grade every assertion as passed."""
        self.seen.extend(assertions)
        return {
            "eval_id": eval_id,
            "arm": config,
            "assertions": [
                {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
            ],
        }


@dataclass
class _Doubles:
    """The three doubles one `run_eval_arm` call ran against."""

    session: _RecordingSession
    binder: _RecordingBinder
    grader: _RecordingGrader


def _eval_case(tmp_path: Path, checklist: str) -> EvalCase:
    """Write `skills/myskill/evals/alpha/eval.md` with `checklist` and parse it for real."""
    eval_dir = tmp_path / "skills" / "myskill" / "evals" / "alpha"
    eval_dir.mkdir(parents=True)
    eval_file = eval_dir / "eval.md"
    eval_file.write_text(_EVAL_HEADER + checklist)

    return EvalCase(
        group="myskill",
        eval_dir=eval_dir,
        eval_file=eval_file,
        eval=mdformat.parse_eval_md(eval_file),
    )


def _run_arm(
    tmp_path: Path, eval_case: EvalCase, arm: Arm, *, baseline: str | None
) -> tuple[dict, _Doubles]:
    """Run sample 0 of `arm` through `run_eval_arm` and return its grading.json plus the doubles."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)
    doubles = _Doubles(_RecordingSession(), _RecordingBinder(), _RecordingGrader())

    run_eval_arm(
        eval_case,
        arm,
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=doubles.session,
        grade=doubles.grader,
        bind=doubles.binder,
        baseline=baseline,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", arm.name, sample=0)
    return json.loads((run_dir / "grading.json").read_text()), doubles


def test_skipped_line_is_recorded_and_costs_nothing(tmp_path: Path) -> None:
    """Verify a line whose clause is false lands as a skipped entry that neither double saw."""
    eval_case = _eval_case(tmp_path, THREE_LINE_CHECKLIST)

    grading, doubles = _run_arm(tmp_path, eval_case, BASELINE_ARM, baseline="baseline")

    assert grading["assertions"][1] == {
        "text": TRIGGER_LINE,
        "passed": None,
        "skipped": True,
        "scoped": True,
        "reason": "if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}",
    }
    for index in (0, 2):
        entry = grading["assertions"][index]
        assert entry["passed"] is True
        assert "skipped" not in entry
        assert "scoped" not in entry
    assert doubles.binder.seen == ["./out.md exists", "the greeting is warm"]
    assert doubles.grader.seen == ["./out.md exists", "the greeting is warm"]
    assert grading["errored"] is False


def test_applicable_scoped_line_is_graded_and_marked_scoped(tmp_path: Path) -> None:
    """Verify a line whose clause holds is bound and judged like any other, tagged `scoped`."""
    eval_case = _eval_case(tmp_path, THREE_LINE_CHECKLIST)

    grading, doubles = _run_arm(tmp_path, eval_case, TRIAL_ARM, baseline="baseline")

    entry = grading["assertions"][1]
    assert entry["text"] == TRIGGER_LINE
    assert entry["scoped"] is True
    assert "skipped" not in entry
    assert entry["passed"] is True
    assert TRIGGER_LINE in doubles.binder.seen
    assert TRIGGER_LINE in doubles.grader.seen


@pytest.mark.parametrize(
    ("locale", "skipped"),
    [
        pytest.param("en-US", True, id="en-US-skips"),
        pytest.param("en-GB", False, id="en-GB-grades"),
    ],
)
def test_env_clause_resolves_from_arm_env(tmp_path: Path, locale: str, skipped: bool) -> None:
    """Verify a clause over an arm `env` key is decided by that arm's value."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./Greetings/Bob.md contains the text 'an absolute pleasure'
              - if: {GREETING_LOCALE} == "en-GB"
        """),
    )
    arm = Arm("trial", "claude-code", "sonnet", env={"GREETING_LOCALE": locale})

    grading, doubles = _run_arm(tmp_path, eval_case, arm, baseline="baseline")

    entry = grading["assertions"][0]
    assert entry["scoped"] is True
    assert entry.get("skipped", False) is skipped
    assert (entry["text"] in doubles.binder.seen) is not skipped
    assert (entry["text"] in doubles.grader.seen) is not skipped


@pytest.mark.parametrize(
    ("arm", "skipped"),
    [
        pytest.param(Arm("trial-codex", "codex", "openai/gpt-5.5"), True, id="codex-skips"),
        pytest.param(Arm("trial", "claude-code", "sonnet"), False, id="claude-code-grades"),
    ],
)
def test_unless_clause_inverts(tmp_path: Path, arm: Arm, skipped: bool) -> None:
    """Verify `unless:` skips the line exactly when its expression holds."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] Opens the note with Read, not Bash
              - unless: {BENCHSPEC_HARNESS} == "codex"
        """),
    )

    grading, doubles = _run_arm(tmp_path, eval_case, arm, baseline="baseline")

    entry = grading["assertions"][0]
    assert entry["scoped"] is True
    assert entry.get("skipped", False) is skipped
    assert (entry["text"] in doubles.binder.seen) is not skipped
    assert (entry["text"] in doubles.grader.seen) is not skipped


def test_unknown_variable_fails_before_the_sandbox_boots(tmp_path: Path) -> None:
    """Verify an unknown `{VAR}` raises before the session opens and before any artifact lands."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
              - if: {NOPE} == 1
        """),
    )
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    session = _RecordingSession()

    with pytest.raises(SchemaError) as exc_info:
        run_eval_arm(
            eval_case,
            TRIAL_ARM,
            workdir,
            {},
            tmp_path,
            today="2099-01-01",
            repo_root=tmp_path,
            sample=0,
            session_factory=session,
            grade=_RecordingGrader(),
            bind=_RecordingBinder(),
            baseline="baseline",
        )

    assert "NOPE" in str(exc_info.value)
    assert "if: {NOPE} == 1" in str(exc_info.value)
    assert session.enters == 0
    assert not workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0).exists()
    assert not workspace.evals_root(tmp_path).exists()


def test_baseline_is_exported_to_the_cell_env() -> None:
    """Verify `cell_env` exports the set's baseline as `BENCHSPEC_BASELINE`, empty when unset."""
    agent = ClaudeCodeAgent()

    cell_env = agent.cell_env(arm="trial", model="sonnet", eval_set="", baseline="baseline")

    assert cell_env["BENCHSPEC_BASELINE"] == "baseline"
    assert agent.cell_env(arm="trial", model="sonnet")["BENCHSPEC_BASELINE"] == ""


def test_benchmark_pools_only_lines_graded_in_every_arm(tmp_path: Path) -> None:
    """Verify a line skipped in one arm leaves the rates and lands in `scoped` instead."""
    eval_case = _eval_case(tmp_path, THREE_LINE_CHECKLIST)
    _run_arm(tmp_path, eval_case, BASELINE_ARM, baseline="baseline")
    _run_arm(tmp_path, eval_case, TRIAL_ARM, baseline="baseline")
    iteration_root = workspace.iteration_root(tmp_path)
    eval_dirs = report.discover_eval_dirs(workspace.skills_root(tmp_path))

    benchmark = report.write_benchmark(
        iteration_root, eval_dirs, "iteration_01", baseline="baseline", arm_meta=ARM_META
    )

    assert benchmark["arms"]["baseline"]["pass_rate"] == 1.0
    assert benchmark["arms"]["trial"]["pass_rate"] == 1.0
    assert benchmark["arms"]["trial"]["delta_pp"] == 0
    assert benchmark["scoped"] == [
        {
            "group": "myskill",
            "eval_id": "alpha",
            "index": 1,
            "text": TRIGGER_LINE,
            "arms": {"baseline": "skipped", "trial": {"passed": 1, "total": 1}},
        }
    ]
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "## Scoped assertions" in markdown
    assert any(
        f"{TRIGGER_LINE} | skipped | pass" in line for line in markdown.splitlines()
    ), markdown


def test_index_rows_count_scoped_and_skipped_lines(tmp_path: Path) -> None:
    """Verify index rows count only graded lines in `total` and report scoped/skipped beside it."""
    eval_case = _eval_case(tmp_path, THREE_LINE_CHECKLIST)
    _run_arm(tmp_path, eval_case, BASELINE_ARM, baseline="baseline")
    _run_arm(tmp_path, eval_case, TRIAL_ARM, baseline="baseline")

    rows = report.index_rows(workspace.skill_dir(tmp_path, "myskill"), "myskill", None)

    by_arm = {row["arm"]: row for row in rows}
    baseline_row = by_arm["baseline"]
    trial_row = by_arm["trial"]
    assert (baseline_row["passed"], baseline_row["total"]) == (2, 2)
    assert (baseline_row["scoped"], baseline_row["skipped"]) == (1, 1)
    assert (trial_row["passed"], trial_row["total"]) == (3, 3)
    assert (trial_row["scoped"], trial_row["skipped"]) == (1, 0)


def test_all_lines_scoped_out_renders_dash_cell(tmp_path: Path) -> None:
    """Verify an eval whose every line is scoped out has no rate but keeps its matrix row."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
              - if: false
            - [ ] the greeting is warm
              - if: false
        """),
    )
    _run_arm(tmp_path, eval_case, BASELINE_ARM, baseline="baseline")
    _run_arm(tmp_path, eval_case, TRIAL_ARM, baseline="baseline")
    iteration_root = workspace.iteration_root(tmp_path)
    eval_dirs = report.discover_eval_dirs(workspace.skills_root(tmp_path))

    benchmark = report.write_benchmark(
        iteration_root, eval_dirs, "iteration_01", baseline="baseline", arm_meta=ARM_META
    )

    assert benchmark["arms"]["baseline"]["pass_rate"] is None
    assert benchmark["arms"]["trial"]["pass_rate"] is None
    assert {"group": "myskill", "eval_id": "alpha"} in benchmark["roster"]
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "| myskill/alpha | — | — |" in markdown
