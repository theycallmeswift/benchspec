"""End-to-end artifact tests for a cell whose wall-clock cap fires.

Each test drives the real `run_eval_arm` and artifact writer. Only the guest (a session
factory double) and the clock behind the cell's deadline are doubles, so no test waits.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from benchspec.config.arms import Arm
from benchspec.events.sink import NULL_SINK, EventSink
from benchspec.grading.judges import JudgeConfig
from benchspec.orchestration import execution, prebind, workspace
from benchspec.orchestration.deadline import CellDeadline, CellTimeoutError
from benchspec.orchestration.execution import run_eval_arm
from benchspec.orchestration.results import RunResult
from benchspec.reporting import report
from benchspec.sandbox.sandbox import TurnRunner
from benchspec.specs.discovery import EvalCase

CAP_SECONDS = 900
FILE_LINE = "out.txt exists"
JUDGED_LINE = "the summary reads well"


class _Clock:
    """A hand-driven monotonic clock behind the cell's deadline."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """The current reading."""
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Back every cell deadline with one hand-driven clock."""
    fake = _Clock()
    monkeypatch.setattr(
        execution, "CellDeadline", lambda cap_seconds: CellDeadline(cap_seconds, clock=fake)
    )
    return fake


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


class _Session:
    """A session factory double: boots (or fails to), then runs one scripted turn."""

    def __init__(
        self,
        *,
        boot_timeout_phase: str | None = None,
        turn: Callable[[], RunResult] | None = None,
    ) -> None:
        """Script the session: a phase to time out in at boot, or a turn to run."""
        self._boot_timeout_phase = boot_timeout_phase
        self._turn = turn
        self.opened_with: dict[str, object] = {}
        self.turns_run = 0

    def __call__(self, **kwargs: object) -> _Session:
        """Act as the session factory, keeping what `run_eval_arm` opened it with."""
        self.opened_with = kwargs
        return self

    async def __aenter__(self) -> TurnRunner:
        """Boot, or raise the cell timeout the real session raises when the cap fires."""
        if self._boot_timeout_phase is not None:
            deadline = self.opened_with["deadline"]
            assert isinstance(deadline, CellDeadline)
            raise CellTimeoutError(self._boot_timeout_phase, deadline.cap_seconds)
        return self._run

    async def __aexit__(self, *exc: object) -> None:
        """Nothing to tear down."""
        return None

    async def _run(
        self, prompt: str, *, resume_session_id: str | None, detect_skill: str | None
    ) -> RunResult:
        """Run the scripted turn."""
        assert self._turn is not None
        self.turns_run += 1
        return self._turn()


class _JudgeDouble:
    """A judge that passes every line it is given and records the budget it was handed."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.timeouts: list[int] = []

    def __call__(self, assertions: list[str], *args: object, **kwargs: object) -> dict:
        """Pass every line, noting the judge timeout."""
        judge_config = kwargs["judge_config"]
        assert isinstance(judge_config, JudgeConfig)
        remaining_seconds = kwargs["remaining_seconds"]
        assert isinstance(remaining_seconds, Callable)
        self.timeouts.append(min(judge_config.timeout, int(float(remaining_seconds()))))
        return {
            "assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in assertions]
        }


def _bind_file_exists_only(text: str) -> dict | None:
    """Bind the file line to its checker and punt the rest to the judge."""
    if text == FILE_LINE:
        return {"type": "deterministic", "checker": "file_exists", "path": "out.txt"}
    return None


def _eval_case(tmp_path: Path, assertions: list[str]) -> EvalCase:
    """Build a one-prompt eval with `assertions` under `tmp_path`."""
    eval_dir = tmp_path / "skills" / "myskill" / "evals" / "alpha"
    eval_dir.mkdir(parents=True, exist_ok=True)
    return EvalCase(
        group="myskill",
        eval_dir=eval_dir,
        eval_file=eval_dir / "eval.md",
        eval={"id": "alpha", "prompt": "perform the task", "assertions": assertions},
    )


def _run_cell(
    tmp_path: Path,
    session: _Session,
    judge: _JudgeDouble,
    *,
    assertions: list[str],
    bind: Callable[[str], dict | None] = _bind_file_exists_only,
    events: EventSink = NULL_SINK,
) -> tuple[Path, dict, dict]:
    """Run sample 0 and return its run dir, grading.json, and timing.json."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)

    run_eval_arm(
        _eval_case(tmp_path, assertions),
        Arm("trial", "claude-code", "sonnet", timeout=CAP_SECONDS),
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=session,
        grade=judge,
        bind=bind,
        events=events,
    )

    run_dir = workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)
    grading = json.loads((run_dir / "grading.json").read_text())
    timing = json.loads((run_dir / "timing.json").read_text())
    return run_dir, grading, timing


def _timed_out_turn(
    tmp_path: Path, clock: _Clock, *, used_seconds: float
) -> Callable[[], RunResult]:
    """A turn that writes `out.txt`, burns `used_seconds` of the cap, and ends timed out."""

    def turn() -> RunResult:
        """Leave partial work on the host mount, then run into the cap."""
        (tmp_path / "wd" / "out.txt").write_text("half done")
        clock.now += used_seconds
        return RunResult("alpha", "trial", "partial", 600_000, 50, False, timed_out=True)

    return turn


def test_agent_timeout_grades_the_partial_workspace_and_is_not_errored(
    tmp_path: Path, clock: _Clock
) -> None:
    """A file written before the kill is checked, and the judge still grades its line."""
    session = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=600))
    judge = _JudgeDouble()

    _, grading, timing = _run_cell(
        tmp_path, session, judge, assertions=[FILE_LINE, JUDGED_LINE]
    )

    assert grading["errored"] is False
    assert grading["timed_out"] is True
    assert [entry["passed"] for entry in grading["assertions"]] == [True, True]
    assert grading["assertions"][0]["evidence"].startswith("CHECK file_exists")
    assert timing["timed_out"] is True
    assert timing["timeout_phase"] == "agent"
    assert timing["duration_ms"] == 600_000
    assert judge.timeouts == [300]


def test_judge_is_clamped_to_the_budget_the_cell_has_left(
    tmp_path: Path, clock: _Clock
) -> None:
    """The judge never gets more than what remains of the cap."""
    session = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=800))
    judge = _JudgeDouble()

    _run_cell(tmp_path, session, judge, assertions=[JUDGED_LINE])

    assert judge.timeouts == [100]


def test_agent_gets_a_judge_reserve_only_when_a_line_is_judge_bound(
    tmp_path: Path, clock: _Clock
) -> None:
    """The session is told to leave the judge its share, and only when there is a judge line."""
    with_judge = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=0))
    _run_cell(tmp_path, with_judge, _JudgeDouble(), assertions=[FILE_LINE, JUDGED_LINE])

    bound_only = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=0))
    _run_cell(tmp_path, bound_only, _JudgeDouble(), assertions=[FILE_LINE])

    assert with_judge.opened_with["judge_reserve"] == 300
    assert bound_only.opened_with["judge_reserve"] == 0


def test_boot_timeout_skips_the_agent_and_records_unjudged_lines_as_failed(
    tmp_path: Path, clock: _Clock
) -> None:
    """A cap that fires while booting never runs the agent, and the judge never runs."""
    session = _Session(boot_timeout_phase="boot")
    judge = _JudgeDouble()

    _, grading, timing = _run_cell(tmp_path, session, judge, assertions=[FILE_LINE, JUDGED_LINE])

    assert session.turns_run == 0
    assert judge.timeouts == []
    assert grading["errored"] is False
    assert grading["timed_out"] is True
    assert timing["timeout_phase"] == "boot"
    assert [entry["passed"] for entry in grading["assertions"]] == [False, False]
    assert grading["assertions"][1]["evidence"] == (
        "CELL TIMEOUT: boot hit the 900s cap before this line was judged"
    )


def test_setup_timeout_is_recorded_with_its_own_phase(tmp_path: Path, clock: _Clock) -> None:
    """A cap that fires inside setup.sh is a `setup` timeout, not a `boot` one."""
    session = _Session(boot_timeout_phase="setup")

    _, _, timing = _run_cell(tmp_path, session, _JudgeDouble(), assertions=[JUDGED_LINE])

    assert timing["timeout_phase"] == "setup"


def test_bind_that_spends_the_whole_cap_skips_the_sandbox(tmp_path: Path, clock: _Clock) -> None:
    """A binder that eats the cap leaves no budget: no sandbox opens, the cell is `bind`."""
    session = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=0))

    def slow_bind(text: str) -> dict | None:
        """Burn the whole cap, then punt the line."""
        clock.now += CAP_SECONDS
        return None

    _, grading, timing = _run_cell(
        tmp_path, session, _JudgeDouble(), assertions=[JUDGED_LINE], bind=slow_bind
    )

    assert session.opened_with == {}
    assert timing["timeout_phase"] == "bind"
    assert grading["timed_out"] is True
    assert grading["errored"] is False
    assert grading["assertions"][0]["passed"] is False
    assert "bind hit the 900s cap" in grading["assertions"][0]["evidence"]


class _RecordingSink:
    """Keeps every event the cell emits."""

    def __init__(self) -> None:
        """Start empty."""
        self.events: list[Mapping[str, object]] = []

    def emit(self, event: Mapping[str, object]) -> None:
        """Record the event."""
        self.events.append(event)

    def phases(self) -> list[object]:
        """The phases the cell entered, in order."""
        return [event["phase"] for event in self.events if event["type"] == "cell.phase"]


@pytest.mark.parametrize("phase", ["boot", "setup"])
def test_cap_that_fires_before_the_agent_closes_the_stream_with_a_timed_out_done(
    tmp_path: Path, clock: _Clock, phase: str
) -> None:
    """A boot or setup timeout skips the agent phase and ends in `cell.done`, not an error."""
    sink = _RecordingSink()

    _, _, timing = _run_cell(
        tmp_path,
        _Session(boot_timeout_phase=phase),
        _JudgeDouble(),
        assertions=[FILE_LINE],
        events=sink,
    )

    done = sink.events[-1]
    assert sink.phases() == ["bind", "boot", "grade"]
    assert done["type"] == "cell.done"
    assert (done["timed_out"], done["timeout_phase"]) == (True, phase)
    assert {key: done[key] for key in timing} == timing


def test_bind_that_spends_the_cap_goes_straight_to_grade_in_the_stream(
    tmp_path: Path, clock: _Clock
) -> None:
    """A cell whose binder ate the cap enters no sandbox phase and ends timed out in `bind`."""
    sink = _RecordingSink()

    def slow_bind(text: str) -> dict | None:
        """Burn the whole cap, then punt the line."""
        clock.now += CAP_SECONDS
        return None

    _run_cell(
        tmp_path,
        _Session(),
        _JudgeDouble(),
        assertions=[JUDGED_LINE],
        bind=slow_bind,
        events=sink,
    )

    done = sink.events[-1]
    assert sink.phases() == ["bind", "grade"]
    assert done["type"] == "cell.done"
    assert (done["timed_out"], done["timeout_phase"]) == (True, "bind")


def test_agent_that_spends_the_judge_budget_leaves_judge_lines_unjudged(
    tmp_path: Path, clock: _Clock
) -> None:
    """With no budget left after the agent, the judge-bound line fails with the evidence."""
    session = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=CAP_SECONDS))
    judge = _JudgeDouble()

    _, grading, timing = _run_cell(tmp_path, session, judge, assertions=[FILE_LINE, JUDGED_LINE])

    assert judge.timeouts == []
    assert timing["timeout_phase"] == "agent"
    assert grading["assertions"][0]["passed"] is True
    assert grading["assertions"][1]["passed"] is False
    assert grading["assertions"][1]["evidence"] == (
        "CELL TIMEOUT: agent hit the 900s cap before this line was judged"
    )


def test_run_that_finishes_inside_the_cap_carries_no_timeout_fields(
    tmp_path: Path, clock: _Clock
) -> None:
    """The timeout fields appear only on a timed-out sample."""
    session = _Session(turn=lambda: RunResult("alpha", "trial", "done", 5_000, 50, False))

    _, grading, timing = _run_cell(tmp_path, session, _JudgeDouble(), assertions=[JUDGED_LINE])

    assert "timed_out" not in grading
    assert "timed_out" not in timing
    assert "timeout_phase" not in timing


def test_session_without_a_cap_error_propagates_other_timeouts(
    tmp_path: Path, clock: _Clock
) -> None:
    """Only the cell's own timeout is a result; any other failure still aborts the cell."""

    class _BrokenSession(_Session):
        """A session whose boot fails for a reason that is not the cap."""

        async def __aenter__(self) -> TurnRunner:
            """Fail like a broken backend."""
            raise TimeoutError("docker stalled")

    with pytest.raises(TimeoutError, match="docker stalled") as raised:
        _run_cell(tmp_path, _BrokenSession(), _JudgeDouble(), assertions=[JUDGED_LINE])

    assert not isinstance(raised.value, CellTimeoutError)


def test_timed_out_samples_count_in_the_rates_and_the_report(
    tmp_path: Path, clock: _Clock
) -> None:
    """A timeout lands in index.jsonl, the benchmark, and the markdown, and stays in the rate."""
    session = _Session(turn=_timed_out_turn(tmp_path, clock, used_seconds=600))
    _run_cell(tmp_path, session, _JudgeDouble(), assertions=[FILE_LINE, JUDGED_LINE])
    skill_dir = workspace.skill_dir(tmp_path, "myskill")

    rows = report.index_rows(skill_dir, "myskill")
    benchmark = report.build_benchmark(
        report.discover_eval_dirs(skill_dir.parent),
        label="timeouts",
        arm_meta={"trial": {"harness": "claude-code", "model": "sonnet"}},
    )
    markdown = report._format_markdown(benchmark)

    assert [row["timed_out"] for row in rows] == [True]
    assert benchmark["arms"]["trial"]["timed_out_samples"] == 1
    assert benchmark["arms"]["trial"]["pass_rate"] == 1.0
    assert benchmark["arms"]["trial"]["n"] == 1
    assert (
        "- Timed out: 1 sample(s) hit the cell cap and were graded on the work finished; "
        "they count in the rates"
    ) in markdown
    assert "| alpha | 1 | 2 | 2 | 100% |  | 1 timed out |" in markdown


def _remaining_after_bind(
    tmp_path: Path, clock: _Clock, table: prebind.BoundSpecs
) -> tuple[float, list[str]]:
    """Run a cell binding through `table`, a live binder costing 100 s a call, on a miss.

    Returns the budget the session saw left after bind, and the texts bound live.
    """
    bound_live: list[str] = []

    def live(text: str) -> dict | None:
        """Bind live, spending 100 s of the cell's cap."""
        bound_live.append(text)
        clock.now += 100
        return None

    session = _Session(turn=lambda: RunResult("alpha", "trial", "done", 5_000, 50, False))
    _run_cell(
        tmp_path,
        session,
        _JudgeDouble(),
        assertions=[FILE_LINE, JUDGED_LINE],
        bind=prebind.table_or_live(table, live),
    )

    deadline = session.opened_with["deadline"]
    assert isinstance(deadline, CellDeadline)
    return deadline.remaining(), bound_live


def test_prebound_assertions_spend_none_of_the_cells_budget_on_binding(
    tmp_path: Path, clock: _Clock
) -> None:
    """A cell whose lines were all bound before the run starts with its whole cap."""
    table: prebind.BoundSpecs = {
        FILE_LINE: {"type": "deterministic", "checker": "file_exists", "path": "out.txt"},
        JUDGED_LINE: None,
    }

    remaining, bound_live = _remaining_after_bind(tmp_path, clock, table)

    assert remaining == CAP_SECONDS
    assert bound_live == []


def test_a_line_rebound_in_the_cell_draws_on_the_cells_deadline(
    tmp_path: Path, clock: _Clock
) -> None:
    """A text missing from the pre-bound table is bound live, and that time is the cell's."""
    table: prebind.BoundSpecs = {
        FILE_LINE: {"type": "deterministic", "checker": "file_exists", "path": "out.txt"}
    }

    remaining, bound_live = _remaining_after_bind(tmp_path, clock, table)

    assert bound_live == [JUDGED_LINE]
    assert remaining == CAP_SECONDS - 100
