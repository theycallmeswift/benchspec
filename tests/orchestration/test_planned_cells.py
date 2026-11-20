"""Tests for the planned cell list `run.start` carries."""

from __future__ import annotations

from pathlib import Path

from benchspec.config.arms import Arm
from benchspec.orchestration.cases import planned_cells
from benchspec.specs.discovery import EvalCase

BASELINE = Arm("baseline", "claude-code", "sonnet")
TRIAL = Arm("trial", "claude-code", "sonnet")
TRIAL_OVERRIDES = Arm("trial-overrides", "claude-code", "opus")


TRIAL_ONLY = {"key": "unless", "expr": '{BENCHSPEC_ARM} == "baseline"'}


def _case(
    tmp_path: Path, eval_id: str, assertions: list[str], clauses: list[dict | None] | None = None
) -> EvalCase:
    """An eval `hello/<eval_id>` with the given assertion lines and their scope clauses."""
    eval_dir = tmp_path / "skills" / "hello" / "evals" / eval_id
    return EvalCase(
        group="hello",
        eval_dir=eval_dir,
        eval_file=eval_dir / "eval.md",
        eval={"id": eval_id, "prompt": "Say hi.", "assertions": assertions, "clauses": clauses},
    )


def _nodeid(case: EvalCase, arm: Arm, repeat: str = "") -> str:
    """The node id pytest gives one cell."""
    param = f"{case.param_id}-{arm.name}{repeat}"

    return f"skills/hello/evals/{case.eval_id}/eval.md::test_eval[{param}]"


def _plan(nodeids: list[str], pairs: list[tuple[EvalCase, Arm]], count: int = 1) -> list[str]:
    """The planned cell keys."""
    cells = planned_cells(nodeids, pairs, count=count, baseline="baseline", eval_set="")

    return [cell["cell"] for cell in cells]


def test_every_collected_pair_is_one_cell_at_count_one(tmp_path: Path) -> None:
    """Every collected pair is one cell at count one."""
    case = _case(tmp_path, "greets", ["greeting written"])
    pairs = [(case, BASELINE), (case, TRIAL)]

    cells = planned_cells(
        [_nodeid(case, BASELINE), _nodeid(case, TRIAL), "tests/test_other.py::test_unrelated"],
        pairs,
        count=1,
        baseline="baseline",
        eval_set="",
    )

    assert cells == [
        {
            "cell": f"hello/greets/{arm}/0",
            "skill": "hello",
            "eval_id": "greets",
            "arm": arm,
            "sample": 0,
            "applies": [True],
        }
        for arm in ("baseline", "trial")
    ]


def test_count_three_plans_three_zero_based_samples_per_pair(tmp_path: Path) -> None:
    """Count three plans three zero based samples per pair."""
    case = _case(tmp_path, "greets", ["greeting written"])
    nodeids = [_nodeid(case, TRIAL, f"-{step}-3") for step in (1, 2, 3)]

    assert _plan(nodeids, [(case, TRIAL)], count=3) == [
        "hello/greets/trial/0",
        "hello/greets/trial/1",
        "hello/greets/trial/2",
    ]


def test_a_deselected_cell_stays_out(tmp_path: Path) -> None:
    """A deselected cell stays out."""
    greets = _case(tmp_path, "greets", ["greeting written"])
    waves = _case(tmp_path, "waves", ["wave written"])
    pairs = [(greets, BASELINE), (greets, TRIAL), (waves, BASELINE), (waves, TRIAL)]

    assert _plan([_nodeid(waves, TRIAL)], pairs) == ["hello/waves/trial/0"]


def test_an_arm_name_that_prefixes_another_matches_only_itself(tmp_path: Path) -> None:
    """An arm name that prefixes another matches only itself."""
    case = _case(tmp_path, "greets", ["greeting written"])
    pairs = [(case, TRIAL), (case, TRIAL_OVERRIDES)]

    assert _plan([_nodeid(case, TRIAL_OVERRIDES)], pairs) == ["hello/greets/trial-overrides/0"]
    assert _plan([_nodeid(case, TRIAL), _nodeid(case, TRIAL_OVERRIDES)], pairs) == [
        "hello/greets/trial/0",
        "hello/greets/trial-overrides/0",
    ]


def test_applies_is_false_for_a_line_scoped_off_the_arm(tmp_path: Path) -> None:
    """Applies is false for a line scoped off the arm."""
    mixed = _case(tmp_path, "mixed", ["greeting written", "skill fired"], [None, TRIAL_ONLY])
    scoped = _case(tmp_path, "scoped", ["skill fired"], [TRIAL_ONLY])
    pairs = [(mixed, BASELINE), (mixed, TRIAL), (scoped, BASELINE)]

    cells = planned_cells(
        [_nodeid(case, arm) for case, arm in pairs],
        pairs,
        count=1,
        baseline="baseline",
        eval_set="",
    )

    assert [cell["applies"] for cell in cells] == [[True, False], [True, True], [False]]
