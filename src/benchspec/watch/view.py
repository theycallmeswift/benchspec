"""Fold a run's events into everything the watch page draws.

The page only renders: every number and label is computed here. A sample is one
event-stream `cell`; a cell is eval × arm; a check is one graded assertion line.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from benchspec.grading.checkers import ACTIVATION_CHECKERS
from benchspec.reporting import report

QUIET_AFTER_S = 30
STALE_MARGIN_S = 60
RECENT_DELTAS = 5

GRADED = ("pass", "fail", "timeout")
FINISHED = (*GRADED, "error")
IDLE = ("queued", "unfinished")
PREP_PHASES = ("bind", "boot", "setup")

_MEASURED_SECONDS_BELOW_MS = 120_000
_PROBLEM_TEXT_LIMIT = 80


@dataclass
class Sample:
    """One sample's folded events."""

    cell: str
    skill: str
    eval_id: str
    arm: str
    index: int
    applies: list[bool] | None = None
    started_ms: int | None = None
    phases: list[tuple[str, int]] = field(default_factory=list)
    last_delta_ms: int | None = None
    deltas: deque[dict] = field(default_factory=lambda: deque(maxlen=RECENT_DELTAS))
    closed_ms: int | None = None
    done: dict | None = None
    error: dict | None = None
    run_ended: bool = False

    @property
    def phase(self) -> str | None:
        """The phase the sample is in; None before `bind`."""
        return self.phases[-1][0] if self.phases else None

    def phase_started_ms(self, phase: str) -> int | None:
        """When the sample entered `phase`; None when it never did."""
        return next((ts_ms for name, ts_ms in self.phases if name == phase), None)

    def silent_ms(self, now_ms: int) -> int:
        """How long the agent has been silent, from its last delta or the phase's start."""
        since = self.last_delta_ms or self.phase_started_ms("agent") or now_ms

        return now_ms - since

    def state(self, now_ms: int) -> str:
        """The sample's state word."""
        if self.applies is not None and not any(self.applies):
            return "n/a"

        if self.error is not None:
            return "error"

        if self.done is not None:
            if self.done.get("timed_out"):
                return "timeout"

            return "pass" if self.done["passed"] == self.done["total"] else "fail"

        if self.run_ended:
            return "unfinished"

        if self.started_ms is None:
            return "queued"

        if self.phase == "agent":
            return "quiet" if self.silent_ms(now_ms) >= QUIET_AFTER_S * 1000 else "running"

        if self.phase == "grade":
            return "running"

        return "booting"

    def checks(self) -> tuple[int, int]:
        """Checks passed and graded; `(0, 0)` for a sample that was not graded."""
        if self.done is None:
            return 0, 0

        return self.done["passed"], self.done["total"]


@dataclass
class Gradings:
    """What the leaderboard reads from `grading.json`, keyed by arm."""

    pass_rate: dict[str, float | None] = field(default_factory=dict)
    delta_pp: dict[str, float | None] = field(default_factory=dict)
    activation: dict[str, tuple[int, int]] = field(default_factory=dict)


def read_gradings(iteration_dir: Path, arms: list[str], baseline: str | None) -> Gradings:
    """Pool the finished samples' `grading.json` the way `benchmark.md` does.

    Raises:
        ValueError: a `grading.json` is malformed, such as one read mid-write.
    """
    skills_root = iteration_dir / "skills"
    gradings = Gradings()
    if not skills_root.is_dir():
        return gradings

    eval_dirs = report.discover_eval_dirs(skills_root)
    benchmark = report.build_benchmark(
        eval_dirs, iteration_dir.name, baseline=baseline, arm_meta={arm: {} for arm in arms}
    )

    for arm in arms:
        stats = benchmark["arms"][arm]
        gradings.pass_rate[arm] = stats["pass_rate"]
        gradings.delta_pp[arm] = stats.get("delta_pp")
        gradings.activation[arm] = _activation(eval_dirs, arm)

    return gradings


def _activation(eval_dirs: list[Path], arm: str) -> tuple[int, int]:
    """Activation lines passed and graded across an arm's graded samples."""
    passed = graded = 0

    for eval_dir in eval_dirs:
        for path in eval_dir.glob(f"{arm}/sample-*/grading.json"):
            grading = json.loads(path.read_text(encoding="utf-8"))
            if grading.get("errored"):
                continue

            for assertion in grading.get("assertions", []):
                if assertion.get("checker") in ACTIVATION_CHECKERS:
                    graded += 1
                    passed += bool(assertion.get("passed"))

    return passed, graded


def assertions_view(grading: dict) -> dict:
    """Shape one `grading.json` for the drawer: failures first, scoped-off lines last."""
    order = ("fail", "pass", "n/a")
    entries = []

    for index, assertion in enumerate(grading.get("assertions", [])):
        if assertion.get("skipped"):
            state = "n/a"
        else:
            state = "pass" if assertion.get("passed") else "fail"

        entries.append(
            {
                "index": index,
                "state": state,
                "text": assertion.get("text", ""),
                "checker": assertion.get("checker", "judge"),
                "evidence": assertion.get("evidence", ""),
            }
        )

    entries.sort(key=lambda entry: order.index(entry["state"]))
    counts = [
        f"{count} {word}"
        for word, state in (("failed", "fail"), ("passed", "pass"), ("n/a", "n/a"))
        if (count := sum(1 for entry in entries if entry["state"] == state))
    ]

    return {"caption": _caption("assertions", *counts), "assertions": entries}


def live_duration(milliseconds: int) -> str:
    """A ticking duration: `9s`, `1m 21s`, `1h 47m`."""
    seconds = max(milliseconds, 0) // 1000
    if seconds < 60:
        return f"{seconds}s"

    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"

    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def measured_duration(milliseconds: float) -> str:
    """A measured duration: `90.9s`, then `6m 41s` from two minutes up."""
    if milliseconds < _MEASURED_SECONDS_BELOW_MS:
        return f"{milliseconds / 1000:.1f}s"

    return live_duration(int(milliseconds))


def rate_text(passed: float, total: float) -> str:
    """A whole percent that reads `100%` only when all passed and `0%` only when none did."""
    percent = math.floor(100 * passed / total + 0.5)
    if passed < total:
        percent = min(percent, 99)

    if passed > 0:
        percent = max(percent, 1)

    return f"{percent}%"


def fail_step(passed: int, total: int) -> int:
    """A failed sample's shade: 1 for most checks passed, 3 for fewest."""
    percent = math.floor(100 * passed / total + 0.5)
    if percent >= 67:
        return 1

    return 2 if percent >= 34 else 3


def _caption(*parts: str) -> str:
    """A bracketed lowercase caption: `[ phases · 90.9s ]`."""
    return f"[ {' · '.join(parts)} ]"


def _plural(count: int, word: str) -> str:
    """`1 eval`, `2 evals`."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


class RunView:
    """A pure fold of the event stream, tolerant of any order across files."""

    def __init__(self, location: str) -> None:
        """Start empty.

        Args:
            location: The iteration directory as the page names it, such as
                `tmp/evals/iteration_07`.
        """
        self._location = location
        self._start: dict | None = None
        self._end: dict | None = None
        self._samples: dict[str, Sample] = {}
        self._workers: set[str] = set()
        self._last_event_ms = 0
        self.gradings = Gradings()

    @property
    def _plan(self) -> dict:
        """The `run.start` event.

        Raises:
            RuntimeError: `run.start` has not arrived.
        """
        if self._start is None:
            raise RuntimeError("run.start has not arrived")

        return self._start

    @property
    def ended(self) -> bool:
        """Whether `run.end` arrived."""
        return self._end is not None

    @property
    def arms(self) -> list[str]:
        """The run's arm names, in `run.start` order."""
        return [arm["name"] for arm in self._plan["arms"]] if self._start else []

    @property
    def baseline(self) -> str | None:
        """The run's baseline arm."""
        return self._plan["baseline"] if self._start else None

    def finished_count(self) -> int:
        """Samples that closed with `cell.done` or `cell.error`."""
        return sum(1 for sample in self._samples.values() if sample.closed_ms is not None)

    def apply(self, event: dict) -> None:
        """Fold one event in."""
        kind = event["type"]
        self._last_event_ms = max(self._last_event_ms, event["ts_ms"])
        if event["worker"] != "main":
            self._workers.add(event["worker"])

        if kind == "run.start":
            self._start = event

            for planned in event["cells"]:
                self._sample(planned).applies = planned["applies"]

        elif kind == "run.end":
            self._end = event

            for sample in self._samples.values():
                sample.run_ended = True

        elif kind == "cell.start":
            self._apply_to_sample(self._sample(event), event)
        elif event.get("cell") in self._samples:
            self._apply_to_sample(self._samples[event["cell"]], event)

    def _sample(self, named: dict) -> Sample:
        """The sample a planned cell or a `cell.start` names, created on first sight."""
        cell = named["cell"]
        if cell not in self._samples:
            self._samples[cell] = Sample(
                cell,
                named["skill"],
                named["eval_id"],
                named["arm"],
                named["sample"],
                run_ended=self.ended,
            )

        return self._samples[cell]

    def _apply_to_sample(self, sample: Sample, event: dict) -> None:
        """Fold one sample event in."""
        kind = event["type"]
        ts_ms = event["ts_ms"]
        if kind == "cell.start":
            sample.started_ms = ts_ms
        elif kind == "cell.phase":
            sample.phases.append((event["phase"], ts_ms))
        elif kind == "cell.delta":
            sample.last_delta_ms = ts_ms
            sample.deltas.append(event)
        elif kind == "cell.done":
            sample.done = event
            sample.closed_ms = ts_ms
        elif kind == "cell.error":
            sample.error = event
            sample.closed_ms = ts_ms

    def _stale_after_ms(self) -> int:
        """The silence that marks a run stale: its longest cell cap plus a margin."""
        longest_cap = max((arm["timeout"] for arm in self._plan["arms"]), default=0)

        return (longest_cap + STALE_MARGIN_S) * 1000

    def to_json(self, now_ms: int) -> dict:
        """Everything the page draws at `now_ms`.

        The clock stops at `run.end`, and at the last event while the run is stale.
        """
        if self._start is None:
            return {"state": "loading"}

        silent_ms = now_ms - self._last_event_ms
        stale = self._end is None and silent_ms > self._stale_after_ms()

        if self._end is not None:
            now_ms = self._end["ts_ms"]
        elif stale:
            now_ms = self._last_event_ms

        planned = [self._samples[cell["cell"]] for cell in self._plan["cells"]]
        counted = [sample for sample in planned if sample.state(now_ms) != "n/a"]
        finished = [sample for sample in counted if sample.state(now_ms) in FINISHED]
        total, done = len(counted), len(finished)

        if total == 0:
            state = "empty"
        elif self._end is None:
            state = "stale" if stale else "live"
        else:
            state = "finished" if done == total else "stopped"

        return {
            "state": state,
            "top": self._top(planned, state, silent_ms),
            "counter": self._counter(counted, done, state, now_ms),
            "report": self._end["benchmark_md"] if self._end else None,
            "reopen": f"benchspec watch --iteration {self._plan['iteration']}",
            "matrix": self._matrix(planned, state, now_ms),
            "leaderboard": self._leaderboard(counted, now_ms),
        }

    def _top(self, planned: list[Sample], state: str, silent_ms: int) -> dict:
        """The top bar: iteration, suite and run state, `silent_ms` after the last event."""
        skills = list(dict.fromkeys(sample.skill for sample in planned))
        count = max((sample.index for sample in planned), default=0) + 1

        if self._end is not None:
            status, detail = f"ended · exit {self._end['exit_status']}", None
        elif state == "stale":
            status, detail = "stale", f"no events for {live_duration(silent_ms)}"
        else:
            status, detail = "live", f"last event {live_duration(silent_ms)} ago"

        return {
            "iteration": self._plan["iteration"],
            "suite": " · ".join([self._plan["set"], *(skills if len(skills) == 1 else [])]),
            "count": f"--count {count}" if count > 1 else None,
            "live": self._end is None,
            "status": status,
            "detail": detail,
        }

    def _counter(self, counted: list[Sample], done: int, state: str, now_ms: int) -> dict:
        """The counter: percent finished, the tally and the clock."""
        total = len(counted)
        states = [sample.state(now_ms) for sample in counted]
        elapsed_ms = now_ms - self._plan["ts_ms"]

        buckets = {
            "passed": states.count("pass"),
            "failed": states.count("fail") + states.count("timeout"),
            "errored": states.count("error"),
        }
        if self._end is None:
            buckets["running"] = states.count("running") + states.count("booting")
            buckets["quiet"] = states.count("quiet")
            buckets["waiting"] = states.count("queued")
        elif state == "stopped":
            buckets["unfinished"] = states.count("unfinished")

        reason = (
            _run_reason(self._end["exit_status"], buckets["errored"])
            if self._end is not None
            else None
        )
        finished_line = f"{done} of {total} samples finished"
        line = f"Run stopped · {done} of {total} finished" if state == "stopped" else finished_line
        announce = {
            "empty": "No cells in this run.",
            "finished": f"Run finished · {done} of {total} samples"
            + (f" · {reason}" if reason else ""),
            "stopped": f"Run stopped · {done} of {total} samples",
            "stale": f"Run stale · {done} of {total} samples finished",
        }.get(state, finished_line)
        left_ms = self._left_ms(counted, now_ms) if state == "live" else None

        return {
            "percent": 100 * done // total if total else 0,
            "done": done,
            "total": total,
            "line": line,
            "progress": finished_line,
            "announce": announce,
            "reason": reason,
            "clock_word": "elapsed" if self._end is None else "took",
            "clock": (
                live_duration(self._end["wall_ms"])
                if self._end is not None
                else live_duration(elapsed_ms)
            ),
            "left": f"~{_coarse_duration(left_ms)} left" if left_ms is not None else None,
            "tally": [{"bucket": bucket, "count": count} for bucket, count in buckets.items()],
            "workers": f"-n {len(self._workers)}" if self._workers and self._end is None else None,
        }

    def _left_ms(self, counted: list[Sample], now_ms: int) -> float | None:
        """The time left: each unfinished sample's expected remainder, shared by the workers.

        A sample is expected to take the mean of the finished ones. None until one has
        finished, while every running sample is past that mean, and under a second.
        """
        durations = [
            sample.closed_ms - sample.started_ms
            for sample in counted
            if sample.closed_ms is not None and sample.started_ms is not None
        ]

        if not durations:
            return None

        unfinished = [sample for sample in counted if sample.closed_ms is None]
        if not unfinished:
            return None

        expected_ms = statistics.mean(durations)
        running_ms = [
            now_ms - sample.started_ms for sample in unfinished if sample.started_ms is not None
        ]
        if running_ms and min(running_ms) >= expected_ms:
            return None

        queued = len(unfinished) - len(running_ms)
        work_ms = queued * expected_ms + sum(max(expected_ms - ran_ms, 0) for ran_ms in running_ms)
        left_ms = work_ms / min(max(len(self._workers), 1), len(unfinished))

        return left_ms if left_ms >= 1000 else None

    def _matrix(self, planned: list[Sample], state: str, now_ms: int) -> dict:
        """The matrix: one group per skill, one row per eval, one cell per arm."""
        arms = self._plan["arms"]
        evals = list(dict.fromkeys((sample.skill, sample.eval_id) for sample in planned))
        by_cell: dict[tuple[str, str, str], list[Sample]] = {}

        for sample in planned:
            by_cell.setdefault((sample.skill, sample.eval_id, sample.arm), []).append(sample)

        groups = []

        for skill in dict.fromkeys(skill for skill, _ in evals):
            rows = [
                {
                    "id": eval_id,
                    "cells": [
                        self._cell(by_cell.get((skill, eval_id, arm["name"]), []), eval_id, now_ms)
                        for arm in arms
                    ],
                }
                for group, eval_id in evals
                if group == skill
            ]
            members = [sample for sample in planned if sample.skill == skill]
            states = {sample.state(now_ms) for sample in members}
            settled = states <= {"pass", "fail", "n/a"}
            groups.append(
                {
                    "name": f"{skill}/",
                    "evals": _plural(len(rows), "eval"),
                    "note": _scope_note(members, [arm["name"] for arm in arms]),
                    "open": not (settled and self._end is None),
                    "summaries": [
                        _group_summary(
                            [sample for sample in members if sample.arm == arm["name"]],
                            now_ms,
                            live=state == "live",
                        )
                        for arm in arms
                    ],
                    "rows": rows,
                }
            )

        word = state if state in ("live", "stale", "finished") else "stopped"

        return {
            "caption": _caption(
                f"{_plural(len(evals), 'eval')} × {_plural(len(arms), 'arm')}", word
            ),
            "arms": [
                {"name": arm["name"], "detail": f"{arm['harness']} · {arm['model']}"}
                for arm in arms
            ],
            "groups": groups,
        }

    def _cell(self, samples: list[Sample], eval_id: str, now_ms: int) -> dict:
        """One eval × arm cell: its boxes, its one label, hover card and drawer data."""
        if not samples:
            return {"kind": "notrun", "label": "not run"}

        states = [sample.state(now_ms) for sample in samples]
        boxes = [_box_class(sample, now_ms) for sample in samples]
        if all(state == "n/a" for state in states):
            return {
                "kind": "na",
                "label": "n/a",
                "boxes": boxes,
                "aria": f"{eval_id} on {samples[0].arm}: n/a",
            }

        done = sum(1 for state in states if state in FINISHED)
        passed = sum(sample.checks()[0] for sample in samples)
        checks = sum(sample.checks()[1] for sample in samples)
        graded = any(state in GRADED for state in states)
        rate = rate_text(passed, checks) if graded and checks else "—"

        kind, muted, short, count, progress = "run", False, "", None, None
        if done == len(samples):
            label = short = rate
            muted = all(state == "pass" for state in states)
        elif all(state in IDLE for state in states):
            kind, label = "queued", states[0]
        else:
            count, progress = f"{done}/{len(samples)}", _progress(samples, now_ms)
            label = f"{count} · {progress}"

        words = "; ".join(_state_words(sample, now_ms) for sample in samples)

        return {
            "kind": kind,
            "label": label,
            "count": count,
            "progress": progress,
            "short": short,
            "muted": muted,
            "boxes": boxes,
            "aria": (
                f"{eval_id} on {samples[0].arm}: {label}; "
                f"{_plural(len(samples), 'sample')}: {words}"
            ),
            "hover": {
                "rate": rate,
                "checks": f"{passed} of {checks} checks passed" if checks else "no checks graded",
                "problem": _worst_problem(samples, now_ms),
                "rows": [_hover_row(sample, now_ms) for sample in samples],
            },
            "samples": [self._detail(sample, len(samples), now_ms) for sample in samples],
        }

    def _detail(self, sample: Sample, of: int, now_ms: int) -> dict:
        """One sample's drawer: status, key numbers, phases, recent output and path."""
        state = sample.state(now_ms)
        closing = sample.done or sample.error or {}
        passed, total = sample.checks()

        verdict, detail = state, None
        if state in GRADED:
            verdict, detail = state.upper(), f"{passed}/{total} assertions"
        elif sample.error is not None:
            where = sample.error["phase"] or "before bind"
            verdict, detail = "ERROR", f"{sample.error['error_kind']} · {where}"
        elif state == "quiet":
            verdict = f"quiet {live_duration(sample.silent_ms(now_ms))}"

        pending = None
        if sample.closed_ms is None and not sample.run_ended:
            pending = "Graded when the sample finishes."

        facts = []
        if sample.started_ms is not None:
            facts.append(_sample_duration(sample, now_ms))

        if closing.get("first_output_ms") is not None:
            facts.append(f"first output {measured_duration(closing['first_output_ms'])}")

        if closing:
            cost = closing.get("cost_usd")
            facts.append(f"${cost:.3f}" if cost is not None else "cost not reported")

        wide = " · ".join(facts)

        agent_started = sample.phase_started_ms("agent") or 0
        scoped_to = self._scoped_to(sample)

        return {
            "cell": sample.cell,
            "number": sample.index + 1,
            "of": of,
            "skill": sample.skill,
            "eval_id": sample.eval_id,
            "arm": sample.arm,
            "verdict": verdict,
            "detail": detail,
            "pending": pending,
            "bad": state in ("fail", "error"),
            "passes": state == "pass",
            "graded": sample.closed_ms is not None,
            "facts": wide,
            "facts_phone": wide.replace("first output", "first"),
            "message": sample.error["message"] if sample.error else None,
            "scoped": scoped_to,
            "phases": _phases(sample, now_ms),
            "output": [
                {
                    "offset": _offset(delta["ts_ms"] - agent_started),
                    "name": delta["name"] or "",
                    "text": delta["text"],
                }
                for delta in sample.deltas
            ],
            "path": (
                f"{self._location}/skills/{sample.skill}/eval-{sample.eval_id}/"
                f"{sample.arm}/sample-{sample.index}/"
            ),
        }

    def _scoped_to(self, sample: Sample) -> dict[str, str]:
        """For each line scoped off this sample's arm, the arms it runs on."""
        notes: dict[str, str] = {}

        for index, holds in enumerate(sample.applies or []):
            if holds:
                continue

            arms = [
                other.arm
                for other in self._samples.values()
                if (other.skill, other.eval_id, other.index)
                == (sample.skill, sample.eval_id, sample.index)
                and other.applies
                and other.applies[index]
            ]
            ordered = [arm for arm in self.arms if arm in arms]
            notes[str(index)] = f"runs only on {', '.join(ordered)}" if ordered else "n/a"

        return notes

    def _leaderboard(self, counted: list[Sample], now_ms: int) -> dict:
        """The leaderboard: arms as columns, one row per metric, over graded samples."""
        arms = self.arms
        graded = {
            arm: [
                sample for sample in counted if sample.arm == arm and sample.state(now_ms) in GRADED
            ]
            for arm in arms
        }
        errored = {
            arm: sum(
                1 for sample in counted if sample.arm == arm and sample.state(now_ms) == "error"
            )
            for arm in arms
        }
        planned = {arm: sum(1 for sample in counted if sample.arm == arm) for arm in arms}

        def missing(arm: str) -> dict:
            """The cell of an arm with no value: `—` with nothing graded, else `n/a`."""
            return {"value": "n/a", "na": True} if graded[arm] else {"value": "—"}

        rates = {arm: self.gradings.pass_rate.get(arm) for arm in arms}
        pass_cells = []

        for arm in arms:
            rate = rates[arm]
            if rate is None:
                pass_cells.append(missing(arm))
                continue

            cell: dict = {"value": rate_text(rate, 1), "fraction": rate}
            delta_pp = self.gradings.delta_pp.get(arm)
            if delta_pp is not None:
                cell["delta"] = f"{delta_pp:+.0f}pp"
                cell["sub"] = f"vs {self.baseline}"

            pass_cells.append(cell)

        rows = [
            {
                "label": "Pass rate",
                "tip": "Excludes checks that don't run on every arm.",
                "bars": True,
                "cells": _mark_best(pass_cells, arms, rates),
            }
        ]

        activation = {arm: self.gradings.activation.get(arm, (0, 0)) for arm in arms}
        if any(total for _, total in activation.values()):
            fractions = {
                arm: (passed / total if total else None)
                for arm, (passed, total) in activation.items()
            }
            cells = [
                {
                    "value": f"{activation[arm][0]}/{activation[arm][1]}",
                    "sub": rate_text(*activation[arm]),
                    "fraction": fractions[arm],
                }
                if activation[arm][1]
                else {"value": "n/a", "na": True}
                for arm in arms
            ]
            rows.append(
                {
                    "label": "Skill triggered",
                    "bars": True,
                    "cells": _mark_best(cells, arms, fractions, alone_wins=True),
                }
            )

        rows.append(
            {
                "label": "Samples (n)",
                "cells": [
                    {
                        "value": f"{len(graded[arm])} of {planned[arm]}",
                        **(
                            {"sub": f"+{errored[arm]} errored", "err": True} if errored[arm] else {}
                        ),
                    }
                    for arm in arms
                ],
            }
        )

        metrics = (
            ("Median time", "wall_ms", statistics.median, measured_duration),
            ("Median first output", "first_output_ms", statistics.median, measured_duration),
            ("Mean cost / sample", "cost_usd", statistics.mean, "${:.3f}".format),
        )

        for label, key, reduce, show in metrics:
            values: dict[str, float | None] = {}

            for arm in arms:
                reported = [
                    sample.done[key]
                    for sample in graded[arm]
                    if sample.done is not None and sample.done.get(key) is not None
                ]
                values[arm] = reduce(reported) if reported else None

            cells = []

            for arm in arms:
                value = values[arm]
                if value is not None:
                    cells.append({"value": show(value)})
                elif key == "cost_usd" and graded[arm]:
                    cells.append({"value": "not reported", "na": True})
                else:
                    cells.append(missing(arm))

            rows.append(
                {"label": label, "cells": _mark_best(cells, arms, values, lowest_wins=True)}
            )

        return {
            "arms": [
                {"name": arm["name"], "detail": f"{arm['harness']} · {arm['model']}"}
                for arm in self._plan["arms"]
            ],
            "rows": rows,
        }


def _mark_best(
    cells: list[dict],
    arms: list[str],
    values: dict[str, float | None],
    *,
    lowest_wins: bool = False,
    alone_wins: bool = False,
) -> list[dict]:
    """Mark each winning arm's cell `best`; where lowest wins, losers get their ratio to it."""
    known = {arm: value for arm, value in values.items() if value is not None}
    if len(known) < (1 if alone_wins else 2):
        return cells

    winning = min(known.values()) if lowest_wins else max(known.values())
    winner = next(arm for arm in arms if known.get(arm) == winning)

    for arm, cell in zip(arms, cells, strict=True):
        if arm not in known:
            continue

        if known[arm] == winning:
            cell["best"] = True
        elif lowest_wins and winning > 0:
            cell["sub"] = f"{known[arm] / winning:.2f}× {winner}"

    return cells


def _coarse_duration(milliseconds: float) -> str:
    """An estimate's duration: `40s`, `10m`, `1h 47m`."""
    seconds = int(milliseconds / 1000)
    if seconds < 60:
        return f"{seconds}s"

    if seconds < 3600:
        return f"{round(seconds / 60)}m"

    return live_duration(seconds * 1000)


def _run_reason(exit_status: int, errored: int) -> str | None:
    """Why an ended run is not clean: a non-zero exit and its errored samples, or None."""
    parts = []

    if exit_status != 0:
        parts.append(f"exit {exit_status}")

    if errored:
        parts.append(f"{errored} errored")

    return " · ".join(parts) or None


def _box_class(sample: Sample, now_ms: int) -> str:
    """The sample's StatusBox class."""
    state = sample.state(now_ms)
    if state == "fail":
        return f"bs-b-fail bs-f{fail_step(*sample.checks())}"

    if state == "n/a":
        return "bs-b-na"

    return "bs-b-queued" if state in IDLE else f"bs-b-{state}"


def _state_words(sample: Sample, now_ms: int) -> str:
    """The sample's state for an accessible name: `fail, 7 of 9 checks`, `quiet 52s`."""
    state = sample.state(now_ms)
    passed, total = sample.checks()
    if state in ("fail", "timeout"):
        return f"{state}, {passed} of {total} checks"

    if state == "quiet":
        return f"quiet {live_duration(sample.silent_ms(now_ms))}"

    return state


def _progress(samples: list[Sample], now_ms: int) -> str:
    """The unfinished cell's phase and its elapsed time, from its earliest started sample."""
    if samples[0].run_ended:
        return "unfinished"

    running = [
        sample for sample in samples if sample.started_ms is not None and sample.closed_ms is None
    ]
    if not running:
        return "queued"

    lead = min(running, key=lambda sample: sample.started_ms or 0)
    since = lead.phases[-1][1] if lead.phases else lead.started_ms or now_ms

    return f"{lead.phase or 'booting'} {live_duration(now_ms - since)}"


def _sample_duration(sample: Sample, now_ms: int) -> str:
    """A finished sample's measured wall time, or a running one's elapsed time."""
    closing = sample.done or sample.error
    if closing is not None:
        return measured_duration(closing["wall_ms"])

    return live_duration(now_ms - (sample.started_ms or now_ms))


def _hover_row(sample: Sample, now_ms: int) -> dict:
    """One hover-card row: the sample, its verdict and its duration in whole seconds."""
    state = sample.state(now_ms)
    passed, total = sample.checks()
    if state == "pass":
        verdict = f"✓ {passed}/{total}"
    elif state == "fail":
        verdict = f"✗ {passed}/{total}"
    elif state == "timeout":
        verdict = f"TIMEOUT {passed}/{total}"
    elif state == "error":
        verdict = "ERROR"
    elif state == "quiet":
        verdict = f"quiet {live_duration(sample.silent_ms(now_ms))}"
    else:
        verdict = state

    closing = sample.done or sample.error
    if closing is not None:
        duration = f"{math.floor(closing['wall_ms'] / 1000 + 0.5)}s"
    elif sample.started_ms is not None:
        duration = live_duration(now_ms - sample.started_ms)
    else:
        duration = ""

    return {
        "sample": f"Sample {sample.index + 1}",
        "verdict": verdict,
        "bad": state in ("fail", "error"),
        "duration": duration,
    }


def _worst_problem(samples: list[Sample], now_ms: int) -> dict | None:
    """The cell's worst problem in words: an error, else a timeout, else the longest silence."""
    states = {sample.index: sample.state(now_ms) for sample in samples}

    for sample in samples:
        if states[sample.index] == "error" and sample.error is not None:
            message = sample.error["message"].splitlines()[0] if sample.error["message"] else ""
            text = f"error · sample {sample.index + 1} {message}".rstrip()

            return {"text": text[:_PROBLEM_TEXT_LIMIT], "warn": False}

    for sample in samples:
        if states[sample.index] == "timeout" and sample.done is not None:
            phase = sample.done.get("timeout_phase", "agent")

            return {
                "text": f"timeout · sample {sample.index + 1} hit the cap in {phase}",
                "warn": True,
            }

    quiet = [sample for sample in samples if states[sample.index] == "quiet"]
    if not quiet:
        return None

    longest = max(quiet, key=lambda sample: sample.silent_ms(now_ms))

    return {
        "text": f"quiet · sample {longest.index + 1} silent "
        f"{live_duration(longest.silent_ms(now_ms))}",
        "warn": True,
    }


def _phases(sample: Sample, now_ms: int) -> dict | None:
    """The sample's phase bar: one segment per phase it entered, sized by its time."""
    if not sample.phases:
        return None

    end_ms = sample.closed_ms or now_ms
    total_ms = max(end_ms - sample.phases[0][1], 1)
    segments = []

    for position, (phase, started_ms) in enumerate(sample.phases):
        later = sample.phases[position + 1 :]
        phase_ms = (later[0][1] if later else end_ms) - started_ms
        segments.append(
            {
                "name": phase,
                "kind": "prep" if phase in PREP_PHASES else phase,
                "time": measured_duration(phase_ms),
                "share": phase_ms / total_ms,
            }
        )

    return {
        "caption": _caption("phases", measured_duration(total_ms)),
        "label": ", ".join(f"{segment['name']} {segment['time']}" for segment in segments),
        "segments": segments,
    }


def _offset(milliseconds: int) -> str:
    """A delta's offset from the agent phase's start: `+0:04`."""
    seconds = max(milliseconds, 0) // 1000

    return f"+{seconds // 60}:{seconds % 60:02d}"


def _group_summary(samples: list[Sample], now_ms: int, *, live: bool) -> dict:
    """One arm's summary of a group: its text, its `done/total` and its completion bar.

    The bar of a group in progress is accented only while the run is `live`.
    """
    states = [sample.state(now_ms) for sample in samples]
    counted = [state for state in states if state != "n/a"]
    if not counted:
        word = "n/a" if samples else "not run"

        return {"text": word, "count": None, "muted": True, "bar": 0, "live": False}

    done = sum(1 for state in counted if state in FINISHED)
    passed = sum(sample.checks()[0] for sample in samples)
    checks = sum(sample.checks()[1] for sample in samples)
    rate = rate_text(passed, checks) if checks else None
    count = f"{done}/{len(counted)}"

    if done == len(counted):
        return {"text": rate or "—", "count": None, "muted": False, "bar": 1, "live": False}

    if all(state in IDLE for state in counted):
        return {"text": counted[0], "count": count, "muted": True, "bar": 0, "live": False}

    return {
        "text": rate or ("unfinished" if "unfinished" in counted else "running"),
        "count": count,
        "muted": rate is None,
        "bar": done / len(counted),
        "live": live,
    }


def _scope_note(samples: list[Sample], arms: list[str]) -> str | None:
    """The group's scope note, such as `[trial-only]` or `[1 check skips bare]`."""
    lines: dict[tuple[str, int], set[str]] = {}
    present: dict[str, set[str]] = {}

    for sample in samples:
        present.setdefault(sample.eval_id, set()).add(sample.arm)

        for index, holds in enumerate(sample.applies or []):
            line = lines.setdefault((sample.eval_id, index), set())
            if holds:
                line.add(sample.arm)

    scoped: dict[str, dict[str, int]] = {}

    for (eval_id, _), runs_on in lines.items():
        skips = present[eval_id] - runs_on
        if not runs_on or not skips:
            continue

        if len(runs_on) <= len(skips):
            phrase = "-".join(arm for arm in arms if arm in runs_on) + "-only"
        else:
            phrase = "skip " + ", ".join(arm for arm in arms if arm in skips)

        per_eval = scoped.setdefault(phrase, {})
        per_eval[eval_id] = per_eval.get(eval_id, 0) + 1

    if not scoped:
        return None

    phrase, per_eval = max(scoped.items(), key=lambda item: max(item[1].values()))
    if sum(per_eval.values()) == len(lines) and phrase.endswith("-only"):
        return f"[{phrase}]"

    most = max(per_eval.values())
    if phrase.startswith("skip ") and most == 1:
        phrase = "skips " + phrase.removeprefix("skip ")

    return f"[{_plural(most, 'check')} {phrase}]"
