"""Plugin tests for the run event stream, serial and under xdist.

Each test runs the real plugin and the real `test_eval` in a subprocess. Only the paid
edges are stubbed, by a plugin the run (and each xdist worker) loads: the sandbox session,
the judge, the binder and the preflights.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from tests.runners.test_pytest import _make_project
from tests.support.events import of_type, read_events

# beta × trial is the one cell whose agent errors.
STUB_EDGES = '''
import functools

from benchspec.grading import binder
from benchspec.orchestration import cases, execution
from benchspec.orchestration.results import RunResult
from benchspec.sandbox import sandbox


class _Session:
    def __init__(self, **kwargs):
        self._errors = (kwargs["eval_id"], kwargs["arm"]) == ("beta", "trial")

    async def __aenter__(self):
        return self._run

    async def __aexit__(self, *exc):
        return None

    async def _run(self, prompt, *, resume_session_id, detect_skill):
        return RunResult(
            "eval", "arm", "agent crashed", 10, 5, is_error=self._errors, cost_usd=0.031
        )


def _grade(assertions, *args, **kwargs):
    return {"assertions": [{"text": text, "passed": True, "evidence": "ok"} for text in assertions]}


sandbox.preflight = lambda backend=None, **kwargs: None
binder.preflight_verify_binder_key = lambda config: None
binder.bind = lambda text, config=None: None
cases.preflight_verify_judge_binary = lambda config: None
cases.preflight_verify_judge_credential = lambda config: None
execution.make_agent = lambda harness=None, provider="default": None
execution.ensure_snapshot = lambda agent, **kwargs: "snap"
cases.run_eval_arm = functools.partial(
    execution.run_eval_arm, session_factory=_Session, grade=_grade
)
'''

PLANNED = [
    f"myskill/{eval_id}/{arm}/0" for eval_id in ("alpha", "beta") for arm in ("baseline", "trial")
]


def _run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *extra: str
) -> pytest.RunResult:
    """Run the project's eval cells in a subprocess with the paid edges stubbed."""
    _make_project(pytester)
    pytester.makepyfile(stub_edges=STUB_EDGES)
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(pytester.path), os.environ.get("PYTHONPATH", "")])
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    return pytester.runpytest_subprocess(
        "-p", "stub_edges",
        "-p", "benchspec.runners.pytest",
        "--benchspec-repo-root", str(pytester.path),
        *extra,
    )


def _iteration(pytester: pytest.Pytester) -> Path:
    """The run's iteration directory."""
    return pytester.path / "tmp" / "evals" / "iteration_01"


def _assert_every_planned_cell_closed_once(events: list[dict], planned: list[str]) -> None:
    """One `run.start` and `run.end`; each planned cell starts once and closes once."""
    (start,) = of_type(events, "run.start")
    assert [cell["cell"] for cell in start["cells"]] == planned
    assert start["cell_count"] == len(planned)
    assert len(of_type(events, "run.end")) == 1
    assert Counter(event["cell"] for event in of_type(events, "cell.start")) == Counter(planned)
    closed = of_type(events, "cell.done") + of_type(events, "cell.error")
    assert Counter(event["cell"] for event in closed) == Counter(planned)


def test_serial_run_writes_one_file_that_plans_and_closes_every_cell(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serial run writes one file that plans and closes every cell."""
    result = _run(pytester, monkeypatch)

    events = read_events(_iteration(pytester))
    assert [path.name for path in _iteration(pytester).glob("events-*")] == ["events-main.jsonl"]
    _assert_every_planned_cell_closed_once(events, PLANNED)
    assert events[0]["type"] == "run.start"
    assert events[0] | {"ts_ms": 0, "run_id": "", "cells": []} == {
        "format_version": 1,
        "type": "run.start",
        "ts_ms": 0,
        "worker": "main",
        "iteration": "iteration_01",
        "run_id": "",
        "set": "default",
        "baseline": "baseline",
        "arms": [
            {"name": "baseline", "harness": "claude-code", "model": "sonnet", "effort": "medium"},
            {"name": "trial", "harness": "claude-code", "model": "opus", "effort": "medium"},
        ],
        "cells": [],
        "cell_count": 4,
    }
    assert [event["cell"] for event in of_type(events, "cell.error")] == ["myskill/beta/trial/0"]
    assert events[-1] | {"ts_ms": 0, "wall_ms": 0} == {
        "format_version": 1,
        "type": "run.end",
        "ts_ms": 0,
        "worker": "main",
        "exit_status": result.ret,
        "wall_ms": 0,
        "done": 3,
        "errored": 1,
        "benchmark_md": "tmp/evals/iteration_01/benchmark.md",
        "benchmark_json": "tmp/evals/iteration_01/benchmark.json",
    }
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_run_start_and_the_manifest_share_one_run_id(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run start and the manifest share one run id."""
    _run(pytester, monkeypatch)

    (start,) = of_type(read_events(_iteration(pytester)), "run.start")
    meta = json.loads((_iteration(pytester) / "meta.json").read_text())
    assert start["run_id"] == meta["run_id"] != ""


def test_parallel_run_keeps_run_events_on_the_controller_and_cells_on_workers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel run keeps run events on the controller and cells on workers."""
    _run(pytester, monkeypatch, "-p", "xdist.plugin", "-n", "2")

    events = read_events(_iteration(pytester))
    _assert_every_planned_cell_closed_once(events, PLANNED)
    by_worker = {
        worker: {event["type"].split(".")[0] for event in events if event["worker"] == worker}
        for worker in {event["worker"] for event in events}
    }
    assert by_worker.pop("main") == {"run"}
    assert by_worker
    assert all(worker.startswith("gw") and kinds == {"cell"} for worker, kinds in by_worker.items())
    (end,) = of_type(events, "run.end")
    assert (end["done"], end["errored"]) == (3, 1)


def test_planned_cells_honor_deselection_and_count(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planned cells honor deselection and count."""
    _run(pytester, monkeypatch, "-p", "pytest_repeat", "--count", "2", "-k", "alpha-trial")

    _assert_every_planned_cell_closed_once(
        read_events(_iteration(pytester)), ["myskill/alpha/trial/0", "myskill/alpha/trial/1"]
    )


def test_collect_only_creates_no_events_file(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collect only creates no events file."""
    result = _run(pytester, monkeypatch, "--collect-only", "-q")

    assert result.ret == 0
    assert list(pytester.path.rglob("events-*.jsonl")) == []


def test_terminal_prints_sample_lines_with_the_reported_cost_and_no_event(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminal prints sample lines with the reported cost and no event."""
    result = _run(pytester, monkeypatch, "-k", "alpha")

    output = result.stdout.str()
    assert "PASS     myskill/eval-alpha  baseline  s0   1/1    0.0s  first      —  $0.031" in output
    assert "format_version" not in output
    assert "cell.start" not in output
