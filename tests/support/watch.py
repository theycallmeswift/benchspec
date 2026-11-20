"""Record a synthetic run for the watch tests: its event files and graded artifacts."""

from __future__ import annotations

import http.client
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from benchspec.events.schema import FORMAT_VERSION
from benchspec.watch.tail import EventTail
from benchspec.watch.view import RunView, read_gradings

T0_MS = 1_800_000_000_000
PHASE_OFFSETS_S = {"bind": 0, "boot": 1, "setup": 3, "agent": 4}


def arm(name: str, harness: str = "claude-code", model: str = "sonnet") -> dict:
    """One arm as `run.start` lists it."""
    return {"name": name, "harness": harness, "model": model, "effort": "medium"}


@dataclass
class Recording:
    """Appends events to an iteration directory, at seconds from the run's start."""

    iteration_dir: Path
    arms: list[dict] = field(default_factory=lambda: [arm("baseline"), arm("trial")])
    baseline: str | None = "baseline"
    start_ms: int = T0_MS
    planned: list[dict] = field(default_factory=list)
    checkers: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def plan(
        self,
        skill: str,
        eval_id: str,
        *,
        checks: list[str] | int = 3,
        only: dict[int, list[str]] | None = None,
        arms: list[str] | None = None,
        count: int = 1,
    ) -> None:
        """Plan an eval's samples.

        Args:
            skill: The eval's group.
            eval_id: The eval's id.
            checks: One checker name per assertion line (`judge` for a judged line), or a
                count of judged lines.
            only: Line index to the arms that line is scoped to; other lines run everywhere.
            arms: The arms that collected this eval; every arm when None.
            count: Samples per arm.
        """
        names = ["judge"] * checks if isinstance(checks, int) else checks
        self.checkers[(skill, eval_id)] = names

        for name in arms or [each["name"] for each in self.arms]:
            for sample in range(count):
                self.planned.append(
                    {
                        "cell": f"{skill}/{eval_id}/{name}/{sample}",
                        "skill": skill,
                        "eval_id": eval_id,
                        "arm": name,
                        "sample": sample,
                        "applies": [
                            name in (only or {}).get(index, [name]) for index in range(len(names))
                        ],
                    }
                )

    def emit(self, event_type: str, at: float, *, worker: str = "gw0", **fields: object) -> None:
        """Append one event to its worker's file."""
        self.iteration_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "format_version": FORMAT_VERSION,
            "type": event_type,
            "ts_ms": self.start_ms + int(at * 1000),
            "worker": worker,
            **fields,
        }
        with (self.iteration_dir / f"events-{worker}.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(event) + "\n")

    def begin(self) -> None:
        """Emit `run.start` with the plan so far."""
        self.emit(
            "run.start",
            0,
            worker="main",
            iteration=self.iteration_dir.name,
            run_id="run",
            set="e2e",
            baseline=self.baseline,
            arms=self.arms,
            cells=self.planned,
            cell_count=len(self.planned),
        )

    def end(self, at: float, *, exit_status: int = 0) -> None:
        """Emit `run.end`."""
        location = "/".join(self.iteration_dir.parts[-3:])
        self.emit(
            "run.end",
            at,
            worker="main",
            exit_status=exit_status,
            wall_ms=int(at * 1000),
            done=0,
            errored=0,
            benchmark_md=f"{location}/benchmark.md",
            benchmark_json=f"{location}/benchmark.json",
        )

    def start(self, cell: str, at: float, *, worker: str = "gw0") -> None:
        """Emit `cell.start`."""
        skill, eval_id, arm_name, sample = cell.split("/")
        self.emit(
            "cell.start",
            at,
            worker=worker,
            cell=cell,
            skill=skill,
            eval_id=eval_id,
            arm=arm_name,
            sample=int(sample),
            harness="claude-code",
            model="sonnet",
        )

    def phase(self, cell: str, phase: str, at: float, *, worker: str = "gw0") -> None:
        """Emit `cell.phase`."""
        self.emit("cell.phase", at, worker=worker, cell=cell, phase=phase)

    def delta(
        self, cell: str, at: float, text: str = "ls", *, name: str = "Bash", worker: str = "gw0"
    ) -> None:
        """Emit a tool-call `cell.delta`."""
        self.emit(
            "cell.delta", at, worker=worker, cell=cell, kind="tool_call", name=name, text=text
        )

    def play(self, cell: str, at: float, *, through: str = "agent", worker: str = "gw0") -> None:
        """Emit `cell.start` and each phase up to `through`, at the usual offsets."""
        self.start(cell, at, worker=worker)

        for phase, offset in PHASE_OFFSETS_S.items():
            self.phase(cell, phase, at + offset, worker=worker)
            if phase == through:
                return

    def done(
        self,
        cell: str,
        at: float,
        *,
        passed: int | None = None,
        wall_s: float = 60,
        timed_out: bool = False,
        first_output_ms: int | None = 2400,
        cost_usd: float | None = 0.06,
        worker: str = "gw0",
    ) -> None:
        """Run a sample start to `cell.done` at `at`, and write its grading and timing."""
        planned = next(each for each in self.planned if each["cell"] == cell)
        total = sum(planned["applies"])
        passed = total if passed is None else passed

        self.play(cell, at - wall_s, worker=worker)
        self.delta(cell, at - wall_s + 6, worker=worker)
        self.phase(cell, "grade", at - 5, worker=worker)
        timing: dict[str, object] = {
            "duration_ms": int(wall_s * 1000) - 9000,
            "judge_ms": 5000,
            "total_tokens": 1200,
            "first_output_ms": first_output_ms,
            "cost_usd": cost_usd,
        }
        if timed_out:
            timing |= {"timed_out": True, "timeout_phase": "agent"}

        self._write_artifacts(planned, passed, timing, errored=False)
        self.emit(
            "cell.done",
            at,
            worker=worker,
            cell=cell,
            passed=passed,
            total=total,
            pass_rate=passed / total if total else None,
            wall_ms=int(wall_s * 1000),
            stopped=False,
            **timing,
        )

    def error(
        self,
        cell: str,
        at: float,
        *,
        phase: str | None = "agent",
        message: str = "exited 1: provider returned 429",
        worker: str = "gw0",
    ) -> None:
        """Run a sample up to `phase` and end it in `cell.error` at `at`."""
        planned = next(each for each in self.planned if each["cell"] == cell)
        if phase is None:
            self.start(cell, at - 10, worker=worker)
        else:
            self.play(cell, at - 10, through=phase, worker=worker)

        self._write_artifacts(planned, 0, {}, errored=True)
        self.emit(
            "cell.error",
            at,
            worker=worker,
            cell=cell,
            phase=phase,
            error_kind="agent" if phase == "agent" else "exception",
            message=message,
            wall_ms=10_000,
        )

    def _write_artifacts(
        self, planned: dict, passed: int, timing: dict[str, object], *, errored: bool
    ) -> None:
        """Write the sample's `grading.json` and `timing.json` as a real run would."""
        names = self.checkers[(planned["skill"], planned["eval_id"])]
        assertions: list[dict] = []
        graded = 0

        for index, (name, applies) in enumerate(zip(names, planned["applies"], strict=True)):
            text = f"line {index + 1} holds"
            if not applies:
                assertions.append(
                    {"text": text, "passed": None, "skipped": True, "scoped": True, "reason": "if"}
                )
                continue

            entry: dict[str, object] = {
                "text": text,
                "passed": not errored and graded < passed,
                "evidence": "seen in the workspace",
                "type": "semantic" if name == "judge" else "deterministic",
            }
            if name != "judge":
                entry["checker"] = name

            assertions.append(entry)
            graded += 1

        sample_dir = (
            self.iteration_dir
            / "skills"
            / planned["skill"]
            / f"eval-{planned['eval_id']}"
            / planned["arm"]
            / f"sample-{planned['sample']}"
        )
        sample_dir.mkdir(parents=True, exist_ok=True)
        grading = {
            "eval_id": planned["eval_id"],
            "skill": planned["skill"],
            "arm": planned["arm"],
            "sample": planned["sample"],
            "errored": errored,
            "binder_degraded": 0,
            "assertions": assertions,
        }
        (sample_dir / "grading.json").write_text(json.dumps(grading), encoding="utf-8")
        (sample_dir / "timing.json").write_text(json.dumps(timing), encoding="utf-8")


def iteration(root: Path, name: str = "iteration_12") -> Path:
    """The iteration directory `benchspec watch` looks for under a repo root."""
    return root / "tmp" / "evals" / name


def mid_run(root: Path) -> Recording:
    """A two-arm run caught live: 19 of 54 samples finished, three samples a cell.

    Trigger evals grade only on `trial`, so their `baseline` samples are n/a. The run
    holds every fail step, an error, a quiet sample and samples booting and running.
    """
    elapsed_s = 401
    run = Recording(iteration(root), start_ms=int(time.time() * 1000) - elapsed_s * 1000)
    plans = (
        "implement-task",
        "review-judges-findings",
        "splits-work",
        "logged-out",
        "rate-limited",
    )
    triggers = ("asks-for-codex", "names-a-task", "second-opinion", "review-branch", "job-status")
    trial_only = {0: ["trial"]}

    for eval_id in plans:
        run.plan("plan", eval_id, checks=9, count=3)

    for eval_id in triggers:
        run.plan("triggers", eval_id, checks=["skill_invoked"], only=trial_only, count=3)

    for eval_id in ("own-review", "install-codex", "other-model"):
        run.plan("not-triggers", eval_id, checks=["not_skill_invoked"], only=trial_only, count=3)

    run.begin()

    for sample, passed in enumerate((9, 9, 7)):
        run.done(f"plan/implement-task/trial/{sample}", 100 + sample, passed=passed, wall_s=90.9)

    for sample, passed in enumerate((6, 5, 2)):
        run.done(f"plan/review-judges-findings/baseline/{sample}", 120 + sample, passed=passed)

    run.done("plan/implement-task/baseline/0", 130, passed=7, wall_s=68)
    run.play("plan/implement-task/baseline/1", elapsed_s - 124)
    run.delta("plan/implement-task/baseline/1", elapsed_s - 3, "git status")
    run.play("plan/implement-task/baseline/2", elapsed_s - 60)
    run.delta("plan/implement-task/baseline/2", elapsed_s - 52, "cat PLAN.md", name="Read")
    run.done("plan/logged-out/baseline/0", 150, passed=9)
    run.error("plan/logged-out/baseline/1", 160)
    run.done("plan/logged-out/baseline/2", 170, passed=8)

    for sample in range(3):
        run.done(f"plan/review-judges-findings/trial/{sample}", 200 + sample)

    for index, eval_id in enumerate(("asks-for-codex", "names-a-task")):
        for sample in range(3):
            run.done(f"triggers/{eval_id}/trial/{sample}", 220 + index * 3 + sample, wall_s=20)

    run.play("plan/splits-work/trial/0", elapsed_s - 9, worker="gw1")
    run.play("plan/splits-work/trial/1", elapsed_s - 1, through="boot", worker="gw2")
    run.play("triggers/second-opinion/trial/0", elapsed_s - 20, worker="gw3")

    for second in range(7):
        at = elapsed_s - 14 + second
        run.delta("triggers/second-opinion/trial/0", at, f"step {second + 1}", worker="gw3")

    return run


def finished_run(root: Path) -> Recording:
    """A four-arm run that ended with exit 1, three samples a cell.

    It holds a `-k` hole, a cell whose every sample errored, a timeout, and one check
    that skips `bare`.
    """
    arms = [
        arm("bare", "bare", "sonnet"),
        arm("claude-code"),
        arm("codex", "codex", "gpt-5.5"),
        arm("opencode", "opencode", "sonnet"),
    ]
    run = Recording(iteration(root), arms=arms, baseline="bare")
    names = [each["name"] for each in arms]
    harnessed = names[1:]
    evals = [
        ("commit", "writes-message", 4),
        ("commit", "splits-changes", 3),
        ("commit", "keeps-hooks", 2),
        ("review", "finds-the-bug", 5),
        ("review", "cites-lines", 3),
        ("review", "stays-in-scope", 2),
        ("release", "bumps-version", 3),
        ("release", "writes-changelog", 4),
        ("release", "tags-commit", 2),
        ("release", "skips-drafts", 1),
    ]

    for skill, eval_id, checks in evals:
        run.plan(
            skill,
            eval_id,
            checks=["judge"] * (checks - 1) + ["skill_invoked"],
            only={checks - 1: harnessed} if skill == "commit" else None,
            arms=harnessed if eval_id == "skips-drafts" else None,
            count=3,
        )

    run.begin()

    at = 30.0

    for skill, eval_id, _ in evals:
        for position, name in enumerate(names):
            for sample in range(3):
                cell = f"{skill}/{eval_id}/{name}/{sample}"
                planned = next((each for each in run.planned if each["cell"] == cell), None)
                if planned is None:
                    continue

                at += 7
                total = sum(planned["applies"])
                if (eval_id, name) == ("cites-lines", "codex"):
                    run.error(cell, at, phase="boot", message="sandbox failed to boot")
                elif (eval_id, name, sample) == ("finds-the-bug", "opencode", 1):
                    run.done(cell, at, passed=2, wall_s=600, timed_out=True, cost_usd=None)
                else:
                    misses = (sample == 2) * (position % 2)
                    if name == "bare":
                        misses = (sample + position) % 3

                    run.done(
                        cell,
                        at,
                        passed=max(total - misses, 0),
                        wall_s=40 + 12 * position + 3 * sample,
                        cost_usd=None if name == "opencode" else 0.04 + 0.02 * position,
                    )

    run.end(at + 20, exit_status=1)

    return run


def fold(run: Recording, *, at: float) -> dict:
    """The view of a recording, `at` seconds into its run."""
    view = RunView("tmp/evals/iteration_12")

    for event in EventTail(run.iteration_dir).poll():
        view.apply(event)

    view.gradings = read_gradings(run.iteration_dir, view.arms, view.baseline)

    return view.to_json(run.start_ms + int(at * 1000))


def cell_of(view: dict, eval_id: str, arm: str) -> dict:
    """One eval × arm cell of a view."""
    column = [each["name"] for each in view["matrix"]["arms"]].index(arm)
    rows = [row for group in view["matrix"]["groups"] for row in group["rows"]]

    return next(row for row in rows if row["id"] == eval_id)["cells"][column]


def open_stream(url: str) -> http.client.HTTPResponse:
    """Open the event stream of the page at `url`."""
    host = url.removeprefix("http://").rstrip("/")
    connection = http.client.HTTPConnection(host, timeout=10)
    connection.request("GET", "/events")

    return connection.getresponse()


def next_event(stream: http.client.HTTPResponse) -> tuple[str, dict]:
    """Read the stream's next named event."""
    name = ""

    for raw in stream:
        line = raw.decode().rstrip("\n")
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: ") and name:
            return name, json.loads(line.removeprefix("data: "))

    raise AssertionError("the stream closed without another event")
