"""The view model: how events fold into what the watch page draws."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from benchspec.reporting import report
from benchspec.watch.view import STALE_MARGIN_S, assertions_view, fail_step, rate_text
from tests.support.watch import (
    CELL_CAP_S,
    Recording,
    arm,
    cell_of,
    finished_frame,
    finished_run,
    fold,
    iteration,
    mid_run,
    replay,
    uneven_run,
)

CELL = "hello/greets/trial/0"


def _one_cell(tmp_path: Path, *, checks: int = 3, count: int = 1) -> Recording:
    """A run of one eval on `baseline` and `trial`."""
    run = Recording(iteration(tmp_path))
    run.plan("hello", "greets", checks=checks, count=count)
    run.begin()

    return run


def _box(view: dict, arm_name: str = "trial", sample: int = 0) -> str:
    """The box class of one sample of the one-eval run."""
    return cell_of(view, "greets", arm_name)["boxes"][sample]


def _tally(view: dict) -> dict[str, int]:
    """The counter's tally, by bucket."""
    return {item["bucket"]: item["count"] for item in view["counter"]["tally"]}


def test_a_sample_moves_from_queued_through_booting_to_running(tmp_path: Path) -> None:
    """A sample moves from queued through booting to running."""
    run = _one_cell(tmp_path)
    queued = _box(fold(run, at=1))

    run.start(CELL, 2)
    in_snapshot_build = _box(fold(run, at=3))
    run.phase(CELL, "bind", 4)
    run.phase(CELL, "boot", 5)
    run.phase(CELL, "setup", 6)
    booting = _box(fold(run, at=7))
    run.phase(CELL, "agent", 8)
    running = _box(fold(run, at=9))
    run.phase(CELL, "grade", 50)
    grading = _box(fold(run, at=90))

    assert queued == "bs-b-queued"
    assert in_snapshot_build == booting == "bs-b-booting"
    assert running == grading == "bs-b-running"


@pytest.mark.parametrize(
    ("silent_s", "box"),
    [(29.9, "bs-b-running"), (30, "bs-b-quiet"), (52, "bs-b-quiet")],
)
def test_an_agent_silent_for_thirty_seconds_is_quiet(
    tmp_path: Path, silent_s: float, box: str
) -> None:
    """An agent silent for thirty seconds is quiet."""
    run = _one_cell(tmp_path)
    run.play(CELL, 0)
    run.delta(CELL, 10)

    assert _box(fold(run, at=10 + silent_s)) == box


def test_quiet_counts_from_the_agent_phase_without_a_delta_and_a_delta_clears_it(
    tmp_path: Path,
) -> None:
    """Quiet counts from the agent phase without a delta and a delta clears it."""
    run = _one_cell(tmp_path)
    run.play(CELL, 0)
    agent_started = 4

    quiet = fold(run, at=agent_started + 30)
    run.delta(CELL, agent_started + 31)
    cleared = fold(run, at=agent_started + 32)

    assert _box(quiet) == "bs-b-quiet"
    assert _tally(quiet)["quiet"] == 1
    assert _box(cleared) == "bs-b-running"


@pytest.mark.parametrize(
    ("passed", "total", "step"),
    [(67, 100, 1), (66, 100, 2), (34, 100, 2), (33, 100, 3), (0, 1, 3), (2, 3, 1), (1, 3, 3)],
)
def test_a_fail_is_shaded_by_its_share_of_checks_passed(passed: int, total: int, step: int) -> None:
    """A fail is shaded by its share of checks passed."""
    assert fail_step(passed, total) == step


@pytest.mark.parametrize(
    ("passed", "total", "text"),
    [(9, 9, "100%"), (199, 200, "99%"), (1, 300, "1%"), (0, 9, "0%"), (5, 9, "56%")],
)
def test_a_rate_reads_100_only_when_all_passed_and_0_only_when_none_did(
    passed: int, total: int, text: str
) -> None:
    """A rate reads 100 only when all passed and 0 only when none did."""
    assert rate_text(passed, total) == text


def test_finished_states_are_error_then_timeout_then_fail_then_pass(tmp_path: Path) -> None:
    """Finished states are error then timeout then fail then pass."""
    run = _one_cell(tmp_path, count=4)
    run.error("hello/greets/trial/0", 20)
    run.done("hello/greets/trial/1", 70, passed=1, timed_out=True)
    run.done("hello/greets/trial/2", 80, passed=1)
    run.done("hello/greets/trial/3", 90)

    cell = cell_of(fold(run, at=100), "greets", "trial")

    assert cell["boxes"] == ["bs-b-error", "bs-b-timeout", "bs-b-fail bs-f3", "bs-b-pass"]
    assert cell["label"] == "56%"
    assert cell["muted"] is False
    assert cell["aria"] == (
        "greets on trial: 56%; 4 samples: error; timeout, 1 of 3 checks; fail, 1 of 3 checks; pass"
    )


def test_a_cell_error_before_bind_has_no_phase(tmp_path: Path) -> None:
    """A cell error before bind has no phase."""
    run = _one_cell(tmp_path)
    run.error(CELL, 20, phase=None, message="snapshot build failed")

    sample = cell_of(fold(run, at=30), "greets", "trial")["samples"][0]

    assert (sample["verdict"], sample["detail"]) == ("ERROR", "exception · before bind")
    assert sample["message"] == "snapshot build failed"
    assert sample["phases"] is None


def test_a_sample_that_never_starts_stays_queued_and_the_run_reads_stopped(tmp_path: Path) -> None:
    """A sample that never starts stays queued and the run reads stopped."""
    run = _one_cell(tmp_path)
    run.done(CELL, 70)
    run.end(80, exit_status=1)

    view = fold(run, at=999)

    assert view["state"] == "stopped"
    assert _box(view, "baseline") == "bs-b-queued"
    assert _tally(view) == {"passed": 1, "failed": 0, "errored": 0, "unfinished": 1}
    assert view["counter"]["line"] == "Run stopped · 1 of 2 finished"
    assert view["counter"]["progress"] == "1 of 2 samples finished"
    assert view["counter"]["announce"] == "Run stopped · 1 of 2 samples"
    assert view["matrix"]["caption"] == "[ 1 eval × 2 arms · stopped ]"


def test_no_sample_reads_as_active_once_the_run_has_ended(tmp_path: Path) -> None:
    """No sample reads as active once the run has ended."""
    run = _one_cell(tmp_path, count=2)
    run.play("hello/greets/trial/0", 0)
    run.play("hello/greets/trial/1", 0, through="boot")
    run.done("hello/greets/baseline/0", 70)
    run.end(80, exit_status=1)

    view = fold(run, at=999)
    killed = cell_of(view, "greets", "trial")
    partial = cell_of(view, "greets", "baseline")

    assert _tally(view) == {"passed": 1, "failed": 0, "errored": 0, "unfinished": 3}
    assert (killed["kind"], killed["label"]) == ("queued", "unfinished")
    assert killed["boxes"] == ["bs-b-queued", "bs-b-queued"]
    assert killed["hover"]["problem"] is None
    assert [row["verdict"] for row in killed["hover"]["rows"]] == ["unfinished", "unfinished"]
    assert killed["samples"][0]["verdict"] == "unfinished"
    assert killed["samples"][0]["pending"] is None
    assert (partial["label"], partial["count"], partial["progress"]) == (
        "1/2 · unfinished",
        "1/2",
        "unfinished",
    )
    assert view["matrix"]["groups"][0]["summaries"][1] == {
        "text": "unfinished",
        "count": "0/2",
        "muted": True,
        "bar": 0,
        "live": False,
    }


def test_a_run_that_planned_no_samples_has_no_cells(tmp_path: Path) -> None:
    """A run that planned no samples has no cells."""
    run = Recording(iteration(tmp_path))
    run.begin()

    view = fold(run, at=1)

    assert view["state"] == "empty"
    assert view["top"]["count"] is None
    assert view["matrix"]["groups"] == []


def test_events_before_run_start_are_kept(tmp_path: Path) -> None:
    """Events before run start are kept."""
    run = Recording(iteration(tmp_path))
    run.plan("hello", "greets")
    run.play(CELL, 5)
    loading = fold(run, at=6)
    run.begin()

    assert loading == {"state": "loading"}
    assert _box(fold(run, at=7)) == "bs-b-running"


def test_scoped_off_samples_are_na_from_run_start_and_count_nowhere(tmp_path: Path) -> None:
    """Scoped off samples are na from run start and count nowhere."""
    view = fold(mid_run(tmp_path), at=401)

    cell = cell_of(view, "codex-eperm-debug", "baseline")
    counter = view["counter"]

    assert cell == {
        "kind": "na",
        "label": "n/a",
        "boxes": ["bs-b-na"] * 3,
        "aria": "codex-eperm-debug on baseline: n/a",
    }
    assert (counter["done"], counter["total"], counter["percent"]) == (19, 54, 35)
    assert sum(_tally(view).values()) == 54
    assert _tally(view) == {
        "passed": 12,
        "failed": 6,
        "errored": 1,
        "running": 4,
        "quiet": 1,
        "waiting": 30,
    }
    assert counter["workers"] == "-n 4"
    assert counter["left"] == "~6m left"


def test_a_cell_counts_every_check_its_graded_samples_ran(tmp_path: Path) -> None:
    """A cell counts every check its graded samples ran."""
    view = fold(mid_run(tmp_path), at=401)

    errored = cell_of(view, "codex-logged-out", "baseline")
    live = cell_of(view, "plan-implement-task", "baseline")

    assert errored["label"] == "94%"
    assert errored["muted"] is False
    assert errored["hover"]["checks"] == "17 of 18 checks passed"
    assert errored["hover"]["problem"] == {
        "text": "error · sample 2 exited 1: provider returned 429",
        "warn": False,
    }
    assert [row["verdict"] for row in errored["hover"]["rows"]] == ["✓ 9/9", "ERROR", "✗ 8/9"]
    assert live["label"] == "1/3 · agent 2m 00s"
    assert live["hover"]["problem"] == {"text": "quiet · sample 3 silent 52s", "warn": True}
    assert live["hover"]["rows"][0] == {
        "sample": "Sample 1",
        "verdict": "✗ 7/9",
        "bad": True,
        "duration": "68s",
    }


def test_unfinished_cells_are_labelled_by_progress(tmp_path: Path) -> None:
    """Unfinished cells are labelled by progress."""
    view = fold(mid_run(tmp_path), at=401)

    assert cell_of(view, "user-review-presents", "trial")["label"] == "queued"
    assert cell_of(view, "python3-missing", "trial")["label"] == "0/3 · agent 5s"
    assert cell_of(view, "plan-review-judges-findings", "trial")["muted"] is True


def test_groups_follow_run_start_order_with_scope_notes_and_summaries(tmp_path: Path) -> None:
    """Groups follow run start order with scope notes and summaries."""
    groups = fold(mid_run(tmp_path), at=401)["matrix"]["groups"]

    plan, quiet, triggers = groups

    assert [group["name"] for group in groups] == [
        "delegating-to-codex/",
        "not-triggers/",
        "triggers/",
    ]
    assert (plan["evals"], plan["note"]) == ("5 evals", None)
    assert (triggers["evals"], triggers["note"]) == ("4 evals", "[trial-only]")
    assert plan["summaries"][0] == {
        "text": "69%",
        "count": "7/15",
        "muted": False,
        "bar": 7 / 15,
        "live": True,
    }
    assert quiet["summaries"][0]["text"] == "n/a"
    assert quiet["summaries"][1]["text"] == "100%"
    assert triggers["summaries"][1] == {
        "text": "running",
        "count": "0/12",
        "muted": True,
        "bar": 0,
        "live": True,
    }
    assert all(group["open"] for group in groups)


@pytest.mark.parametrize(
    ("only", "note"),
    [
        ({0: ["trial"]}, "[1 check trial-only]"),
        ({0: ["trial"], 1: ["trial"]}, "[2 checks trial-only]"),
        ({0: ["claude-code", "codex"]}, "[1 check skips bare]"),
        (None, None),
    ],
)
def test_a_scope_note_names_the_checks_that_do_not_run_everywhere(
    tmp_path: Path, only: dict[int, list[str]] | None, note: str | None
) -> None:
    """A scope note names the checks that do not run everywhere."""
    names = (
        ["bare", "claude-code", "codex"] if only and "codex" in only[0] else ["baseline", "trial"]
    )
    run = Recording(iteration(tmp_path), arms=[arm(name) for name in names], baseline=names[0])
    run.plan("hello", "greets", checks=3, only=only)
    run.begin()

    group = fold(run, at=1)["matrix"]["groups"][0]

    assert (group["evals"], group["note"]) == ("1 eval", note)


def test_a_group_that_finished_cleanly_collapses_while_the_run_is_live(tmp_path: Path) -> None:
    """A group that finished cleanly collapses while the run is live."""
    run = Recording(iteration(tmp_path))
    run.plan("done", "first")
    run.plan("pending", "second")
    run.begin()
    run.done("done/first/baseline/0", 70)
    run.done("done/first/trial/0", 80, passed=1)

    live = fold(run, at=90)["matrix"]["groups"]
    run.end(95)
    ended = fold(run, at=99)["matrix"]["groups"]

    assert [group["open"] for group in live] == [False, True]
    assert [group["open"] for group in ended] == [True, True]


def test_the_drawer_keeps_the_last_five_deltas_and_the_phases_in_order(tmp_path: Path) -> None:
    """The drawer keeps the last five deltas and the phases in order."""
    view = fold(mid_run(tmp_path), at=401)

    sample = cell_of(view, "codex-job-status", "trial")["samples"][0]

    assert [line["text"] for line in sample["output"]] == [f"step {n}" for n in range(3, 8)]
    assert sample["output"][0] == {"offset": "+0:04", "name": "Bash", "text": "step 3"}
    assert [segment["name"] for segment in sample["phases"]["segments"]] == [
        "bind",
        "boot",
        "setup",
        "agent",
    ]
    assert sample["phases"]["label"] == "bind 1.0s, boot 2.0s, setup 1.0s, agent 16.0s"
    assert sample["path"] == (
        "tmp/evals/iteration_12/skills/triggers/eval-codex-job-status/trial/sample-0/"
    )


def test_the_counter_stops_at_run_end(tmp_path: Path) -> None:
    """The counter stops at run end."""
    view = fold(finished_run(tmp_path), at=99_999)

    assert view["state"] == "finished"
    assert view["top"]["status"] == "ended · exit 1"
    assert view["counter"]["clock"] == "14m 29s"
    assert view["counter"]["left"] is None
    assert view["report"] == "tmp/evals/iteration_12/benchmark.md"
    assert view["reopen"] == "benchspec watch --iteration iteration_12"


def test_no_estimate_shows_before_a_sample_finishes(tmp_path: Path) -> None:
    """No estimate shows before a sample finishes."""
    run = _one_cell(tmp_path, count=2)
    run.play(CELL, 0)

    view = fold(run, at=30)

    assert view["counter"]["left"] is None


def test_the_estimate_counts_down_between_events_and_never_goes_negative(tmp_path: Path) -> None:
    """The estimate counts down between events and never goes negative."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 60, wall_s=60)
    run.done("hello/greets/baseline/0", 60, wall_s=60, worker="gw1")
    run.play("hello/greets/trial/1", 60)
    run.play("hello/greets/baseline/1", 60, worker="gw1")

    estimates = [fold(run, at=at)["counter"]["left"] for at in (80, 100, 130)]

    assert estimates == ["~40s left", "~20s left", None]


def test_the_estimate_hides_once_the_last_running_samples_outrun_the_mean(tmp_path: Path) -> None:
    """The estimate hides once the last running samples outrun the mean."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 60, wall_s=60)
    run.done("hello/greets/baseline/0", 60, wall_s=60, worker="gw1")
    run.play("hello/greets/trial/1", 60)
    run.play("hello/greets/baseline/1", 60, worker="gw1")

    estimates = [fold(run, at=at)["counter"]["left"] for at in (119, 120, 500)]

    assert estimates == ["~1s left", None, None]


def test_the_estimate_stays_while_samples_are_queued(tmp_path: Path) -> None:
    """The estimate stays while samples are queued, though the running one outran the mean."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 60, wall_s=60)
    run.play("hello/greets/trial/1", 60)

    view = fold(run, at=500)

    assert view["counter"]["left"] == "~2m left"


def test_a_sample_is_estimated_from_finished_samples_of_its_own_arm(tmp_path: Path) -> None:
    """A sample is estimated from finished samples of its own arm."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 300, wall_s=300)
    run.done("hello/greets/baseline/0", 60, wall_s=60, worker="gw1")

    view = fold(run, at=300)

    assert view["counter"]["left"] == "~5m left"


def test_a_snapshot_build_does_not_pass_for_work(tmp_path: Path) -> None:
    """A snapshot build does not pass for work."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 60, wall_s=60)
    run.start("hello/greets/trial/1", 60)
    run.start("hello/greets/baseline/0", 60, worker="gw1")

    held = fold(run, at=600)["counter"]["left"]

    assert held == "~2m left"


@pytest.mark.parametrize(
    "shape",
    [{}, {"snapshot_s": 300}, {"workers": 1}],
    ids=["as recorded", "longer snapshot build", "one worker"],
)
def test_the_estimate_tracks_an_uneven_run(tmp_path: Path, shape: dict) -> None:
    """The estimate tracks a run with a slow arm, a late-booting arm and a straggler."""
    run = uneven_run(tmp_path, **shape)
    took_ms = max(event["ts_ms"] for event, _ in replay(run))
    quarter = len(run.planned) // 4

    closes = [
        ((took_ms - event["ts_ms"]) / 1000, (view.left_ms(event["ts_ms"]) or 0) / 1000)
        for event, view in replay(run)
        if event["type"] == "cell.done"
    ][quarter - 1 : -1]

    assert all(abs(shown - left) <= max(0.3 * left, 60) for left, shown in closes)
    assert all(after - before <= 60 for (_, before), (_, after) in pairwise(closes))


def test_no_estimate_shows_after_the_run_ends(tmp_path: Path) -> None:
    """No estimate shows after the run ends."""
    run = _one_cell(tmp_path, count=2)
    run.done(CELL, 60, wall_s=60)
    run.play("hello/greets/trial/1", 60)
    run.end(70, exit_status=1)

    view = fold(run, at=75)

    assert view["counter"]["left"] is None


def test_the_estimate_shares_the_remaining_samples_between_the_workers(tmp_path: Path) -> None:
    """The estimate shares the remaining samples between the workers."""
    run = _one_cell(tmp_path, count=3)
    run.done(CELL, 60, wall_s=60)
    run.done("hello/greets/trial/1", 120, wall_s=60)
    one_worker = fold(run, at=120)["counter"]["left"]
    run.start("hello/greets/trial/2", 120, worker="gw1")

    two_workers = fold(run, at=120)["counter"]["left"]

    assert (one_worker, two_workers) == ("~4m left", "~2m left")


def test_a_run_silent_past_its_longest_cell_cap_reads_stale(tmp_path: Path) -> None:
    """A run silent past its longest cell cap reads stale."""
    run = _one_cell(tmp_path)
    run.play(CELL, 0, through="bind")
    longest_silence_s = CELL_CAP_S + STALE_MARGIN_S

    healthy = fold(run, at=longest_silence_s)
    stale = fold(run, at=longest_silence_s + 1)

    assert (healthy["state"], healthy["top"]["status"]) == ("live", "live")
    assert stale["state"] == "stale"
    assert stale["top"] | {"iteration": "", "suite": ""} == {
        "iteration": "",
        "suite": "",
        "count": None,
        "live": True,
        "status": "stale",
        "detail": "no events for 16m 01s",
    }
    assert (stale["counter"]["clock"], stale["counter"]["left"]) == ("0s", None)
    assert stale["counter"]["announce"] == "Run stale · 0 of 2 samples finished"


def test_a_stale_run_returns_to_live_when_events_resume(tmp_path: Path) -> None:
    """A stale run returns to live when events resume."""
    run = _one_cell(tmp_path)
    run.play(CELL, 0, through="bind")
    silent = fold(run, at=1999)
    run.phase(CELL, "boot", 2000)

    view = fold(run, at=2001)

    assert silent["state"] == "stale"
    assert (view["state"], view["top"]["status"]) == ("live", "live")
    assert view["counter"]["clock"] == "33m 21s"


def test_a_longer_cell_cap_lengthens_the_silence_a_live_run_may_keep(tmp_path: Path) -> None:
    """A longer cell cap lengthens the silence a live run may keep."""
    run = Recording(iteration(tmp_path), arms=[arm("baseline"), arm("trial") | {"timeout": 3600}])
    run.plan("hello", "greets")
    run.begin()

    view = fold(run, at=3600)

    assert view["state"] == "live"


def test_a_four_arm_run_shows_its_hole_its_errored_cell_and_its_timeout(tmp_path: Path) -> None:
    """A four arm run shows its hole its errored cell and its timeout."""
    view = fold(finished_run(tmp_path), at=0)

    assert cell_of(view, "skips-drafts", "bare") == {"kind": "notrun", "label": "not run"}
    assert cell_of(view, "cites-lines", "codex")["label"] == "—"
    assert cell_of(view, "finds-the-bug", "opencode")["hover"]["problem"] == {
        "text": "timeout · sample 2 hit the cap in agent",
        "warn": True,
    }
    assert view["matrix"]["groups"][0]["note"] == "[1 check skips bare]"
    assert view["counter"]["total"] == 117


def _row(view: dict, label: str) -> list[dict]:
    """One leaderboard row's cells."""
    return next(row for row in view["leaderboard"]["rows"] if row["label"] == label)["cells"]


def test_the_leaderboard_pass_rate_pools_like_benchmark_json(tmp_path: Path) -> None:
    """The leaderboard pass rate pools like benchmark json."""
    run = finished_run(tmp_path)
    eval_dirs = report.discover_eval_dirs(run.iteration_dir / "skills")
    benchmark = report.build_benchmark(
        eval_dirs, "run", baseline="bare", arm_meta={each["name"]: {} for each in run.arms}
    )

    cells = _row(fold(run, at=0), "Pass rate")

    assert [cell["value"] for cell in cells] == [
        f"{benchmark['arms'][each['name']]['pass_rate']:.0%}" for each in run.arms
    ]
    assert [cell.get("delta") for cell in cells] == [None, "+30pp", "+40pp", "+28pp"]
    assert [cell.get("best", False) for cell in cells] == [False, False, True, False]


def test_a_check_scoped_off_one_arm_counts_in_cells_but_not_in_the_pass_rate(
    tmp_path: Path,
) -> None:
    """A check scoped off one arm counts in cells but not in the pass rate."""
    run = Recording(iteration(tmp_path))
    run.plan("hello", "greets", checks=2, only={1: ["trial"]})
    run.begin()
    run.done("hello/greets/baseline/0", 70)
    run.done("hello/greets/trial/0", 80, passed=1)

    view = fold(run, at=90)

    assert cell_of(view, "greets", "trial")["label"] == "50%"
    assert [cell["value"] for cell in _row(view, "Pass rate")] == ["100%", "100%"]


def test_the_leaderboard_compares_arms_on_graded_samples(tmp_path: Path) -> None:
    """The leaderboard compares arms on graded samples."""
    view = fold(finished_run(tmp_path), at=0)

    assert [cell["value"] for cell in _row(view, "Samples (n)")] == [
        "27 of 27",
        "30 of 30",
        "27 of 30",
        "30 of 30",
    ]
    assert _row(view, "Samples (n)")[2]["sub"] == "+3 errored"
    assert [cell["value"] for cell in _row(view, "Skill triggered")] == [
        "6/18",
        "20/30",
        "27/27",
        "19/30",
    ]
    assert _row(view, "Median time") == [
        {"value": "43.0s", "best": True},
        {"value": "55.0s", "sub": "1.28× bare"},
        {"value": "67.0s", "sub": "1.56× bare"},
        {"value": "79.0s", "sub": "1.84× bare"},
    ]
    assert _row(view, "Mean cost / sample")[3] == {"value": "not reported", "na": True}
    assert all(cell.get("best") for cell in _row(view, "Median first output"))


def test_the_leaderboard_omits_skill_triggered_when_no_arm_grades_activation(
    tmp_path: Path,
) -> None:
    """The leaderboard omits skill triggered when no arm grades activation."""
    run = _one_cell(tmp_path)
    run.error("hello/greets/baseline/0", 20)
    run.done(CELL, 70)

    view = fold(run, at=90)

    assert [row["label"] for row in view["leaderboard"]["rows"]] == [
        "Pass rate",
        "Samples (n)",
        "Median time",
        "Median first output",
        "Mean cost / sample",
    ]
    assert [cell["value"] for cell in _row(view, "Pass rate")] == ["—", "100%"]
    assert [cell["value"] for cell in _row(view, "Median time")] == ["—", "60.0s"]


def test_the_drawer_lists_failures_first_and_scoped_off_lines_last(tmp_path: Path) -> None:
    """The drawer lists failures first and scoped off lines last."""
    run = Recording(iteration(tmp_path))
    run.plan(
        "hello", "greets", checks=["file_exists", "judge", "skill_invoked"], only={2: ["trial"]}
    )
    run.begin()
    run.done("hello/greets/baseline/0", 70, passed=1)
    grading = json.loads(
        (run.iteration_dir / "skills/hello/eval-greets/baseline/sample-0/grading.json").read_text()
    )

    shaped = assertions_view(grading)
    sample = cell_of(fold(run, at=80), "greets", "baseline")["samples"][0]

    assert shaped["caption"] == "[ assertions · 1 failed · 1 passed · 1 n/a ]"
    assert [
        (entry["index"], entry["state"], entry["checker"]) for entry in shaped["assertions"]
    ] == [(1, "fail", "judge"), (0, "pass", "file_exists"), (2, "n/a", "judge")]
    assert sample["scoped"] == {"2": "runs only on trial"}
    assert (sample["verdict"], sample["detail"]) == ("FAIL", "1/2 assertions")


def test_a_finished_run_tallies_only_what_happened_and_marks_a_lone_arm_best(
    tmp_path: Path,
) -> None:
    """A finished run tallies only what happened and marks a lone arm best."""
    view = fold(finished_frame(tmp_path), at=0)

    assert view["state"] == "finished"
    assert _tally(view) == {"passed": 38, "failed": 15, "errored": 1}
    assert view["counter"]["workers"] is None
    assert _row(view, "Skill triggered") == [
        {"value": "n/a", "na": True},
        {"value": "38/38", "sub": "100%", "fraction": 1.0, "best": True},
    ]
    assert [cell["value"] for cell in _row(view, "Pass rate")] == ["70%", "100%"]
    assert view["top"] == {
        "iteration": "iteration_12",
        "suite": "e2e",
        "count": "--count 3",
        "live": False,
        "status": "ended · exit 1",
        "detail": None,
    }
