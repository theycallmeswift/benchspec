"""Record a synthetic run for the watch tests: its event files and graded artifacts."""

from __future__ import annotations

import http.client
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from benchspec.events.schema import FORMAT_VERSION
from benchspec.watch.tail import EventTail
from benchspec.watch.view import RunView, read_gradings

T0_MS = 1_800_000_000_000
CELL_CAP_S = 900
PHASE_OFFSETS_S = {"bind": 0, "boot": 1, "setup": 3, "agent": 4}


def arm(name: str, harness: str = "claude-code", model: str = "sonnet") -> dict:
    """One arm as `run.start` lists it."""
    return {
        "name": name,
        "harness": harness,
        "model": model,
        "effort": "medium",
        "timeout": CELL_CAP_S,
    }


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
        folder: str | None = None,
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
            folder: The eval's group under its skill, such as `to-spec/triggers`;
                `<skill>/<skill>` when None.
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
                        "assertions": [_line_text(index) for index in range(len(names))],
                        "folder": folder or f"{skill}/{skill}",
                    }
                )

    def wait(self, cell: str, at: float, *, limit: int | None = 10, worker: str = "gw0") -> None:
        """Emit `cell.wait`: the boot found the sandbox backend at capacity."""
        self.emit("cell.wait", at, worker=worker, cell=cell, sandbox="e2b", limit=limit)

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
        """Emit `run.start` with the plan so far; n/a samples get their all-skipped grading."""
        for planned in self.planned:
            if not any(planned["applies"]):
                self._write_artifacts(planned, 0, {}, errored=False)

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

    def play(
        self,
        cell: str,
        at: float,
        *,
        through: str = "agent",
        held_s: float = 0,
        worker: str = "gw0",
    ) -> None:
        """Emit `cell.start`, then each phase up to `through` at the usual offsets.

        `held_s` is how long a snapshot build holds the sample before `bind`.
        """
        self.start(cell, at, worker=worker)

        for phase, offset in PHASE_OFFSETS_S.items():
            self.phase(cell, phase, at + held_s + offset, worker=worker)
            if phase == through:
                return

    def done(
        self,
        cell: str,
        at: float,
        *,
        passed: int | None = None,
        wall_s: float = 60,
        held_s: float = 0,
        timed_out: bool = False,
        first_output_ms: int | None = 2400,
        cost_usd: float | None = 0.06,
        output: tuple[tuple[str, str], ...] = (("Bash", "ls"),),
        worker: str = "gw0",
    ) -> None:
        """Run a sample start to `cell.done` at `at`, and write its grading and timing.

        `wall_s` includes `held_s`, the wait for a snapshot build before `bind`.
        """
        planned = next(each for each in self.planned if each["cell"] == cell)
        total = sum(planned["applies"])
        passed = total if passed is None else passed

        self.play(cell, at - wall_s, held_s=held_s, worker=worker)
        for index, (name, text) in enumerate(output):
            self.delta(cell, at - wall_s + held_s + 6 + 9 * index, text, name=name, worker=worker)

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
            text = _line_text(index)
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


def _line_text(index: int) -> str:
    """The text of a recorded eval's assertion line."""
    return f"line {index + 1} holds"


def iteration(root: Path, name: str = "iteration_12") -> Path:
    """The iteration directory `benchspec watch` looks for under a repo root."""
    return root / "tmp" / "evals" / name


def mid_run(root: Path) -> Recording:
    """The run of mockup frames A and D, caught live: 19 of 54 samples finished.

    Trigger evals grade only on `trial`, so their `baseline` samples are n/a. The run
    holds every fail step, an error, a quiet sample and samples booting and running.
    """
    elapsed_s = 401
    run = _codex_plan(root, start_ms=int(time.time() * 1000) - elapsed_s * 1000)
    skill = "delegating-to-codex"

    for sample, passed in enumerate((9, 9, 7)):
        run.done(
            f"{skill}/plan-implement-task/trial/{sample}", 100 + sample, passed=passed, wall_s=90.9
        )

    for sample, passed in enumerate((6, 5, 2)):
        run.done(
            f"{skill}/plan-review-judges-findings/baseline/{sample}", 120 + sample, passed=passed
        )

    run.done(f"{skill}/plan-implement-task/baseline/0", 130, passed=7, wall_s=68)
    run.play(f"{skill}/plan-implement-task/baseline/1", elapsed_s - 124)
    run.delta(f"{skill}/plan-implement-task/baseline/1", elapsed_s - 3, "git status")
    run.play(f"{skill}/plan-implement-task/baseline/2", elapsed_s - 60)
    run.delta(f"{skill}/plan-implement-task/baseline/2", elapsed_s - 52, "cat PLAN.md", name="Read")
    run.done(f"{skill}/codex-logged-out/baseline/0", 150, passed=9)
    run.error(f"{skill}/codex-logged-out/baseline/1", 160)
    run.done(f"{skill}/codex-logged-out/baseline/2", 170, passed=8)

    for sample in range(3):
        run.done(f"{skill}/plan-review-judges-findings/trial/{sample}", 200 + sample)

    for index, eval_id in enumerate(("codex-eperm-debug", "gemini-review")):
        for sample in range(3):
            run.done(f"not-triggers/{eval_id}/trial/{sample}", 220 + index * 3 + sample, wall_s=20)

    run.play(f"{skill}/python3-missing/trial/0", elapsed_s - 9, worker="gw1")
    run.play(f"{skill}/python3-missing/trial/1", elapsed_s - 1, through="boot", worker="gw2")
    run.play("triggers/codex-job-status/trial/0", elapsed_s - 20, worker="gw3")

    for second in range(7):
        at = elapsed_s - 14 + second
        run.delta("triggers/codex-job-status/trial/0", at, f"step {second + 1}", worker="gw3")

    return run


def finished_frame(root: Path) -> Recording:
    """The run of mockup frames B, C and D2: finished with exit 1 and one trial boot failure."""
    run = _codex_plan(root, start_ms=T0_MS, trial_only_check=True)
    at = 30.0
    output = (
        ("Bash", "codex exec --sandbox read-only -c model_reasoning_effort=high"),
        ("Read", "pager.py"),
        ("", "Codex returned two findings: an off-by-one and a missing guard."),
        ("Edit", "pager.py"),
        ("", "Fixed both. Which of the findings would you like to see first?"),
    )

    for planned in list(run.planned):
        total = sum(planned["applies"])
        if not total:
            continue

        at += 20
        cell, arm_name, sample = planned["cell"], planned["arm"], planned["sample"]
        if (planned["eval_id"], arm_name, sample) == ("plan-review-judges-findings", "trial", 2):
            run.error(cell, at, phase="boot", message="sandbox failed to boot", worker="gw1")
        elif arm_name == "baseline":
            run.done(cell, at, passed=(6, 5, 8)[sample], wall_s=70.4, cost_usd=0.087, output=output)
        else:
            run.done(cell, at, wall_s=39, first_output_ms=3900, cost_usd=0.194, worker="gw1")

    run.end(1182, exit_status=1)

    return run


def _codex_plan(root: Path, *, start_ms: int, trial_only_check: bool = False) -> Recording:
    """Plan the mockups' run: 13 evals on two arms, three samples each, trigger evals trial-only."""
    run = Recording(iteration(root), start_ms=start_ms)
    plans = (
        "codex-logged-out",
        "plan-implement-task",
        "plan-review-judges-findings",
        "python3-missing",
        "user-review-presents",
    )
    quiet = ("codex-eperm-debug", "gemini-review", "review-codex-pr", "review-my-branch")
    fires = (
        "codex-job-status",
        "continue-plan-codex-task",
        "have-codex-review",
        "send-findings-back",
    )
    trial_only = {0: ["trial"]}

    checks = ["judge"] * 9 + ["skill_invoked"] * trial_only_check
    scoped = {9: ["trial"]} if trial_only_check else None

    for eval_id in plans:
        run.plan("delegating-to-codex", eval_id, checks=checks, only=scoped, count=3)

    for eval_id in quiet:
        run.plan("not-triggers", eval_id, checks=["not_skill_invoked"], only=trial_only, count=3)

    for eval_id in fires:
        run.plan("triggers", eval_id, checks=["skill_invoked"], only=trial_only, count=3)

    run.begin()

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


def uneven_run(root: Path, *, workers: int = 6, snapshot_s: float = 150) -> Recording:
    """A finished 72-sample run on four arms, three samples a cell.

    `codex` runs several times slower than the rest, a snapshot build holds the
    `opencode` samples started in the first `snapshot_s` seconds, and the last `codex`
    sample outruns every other and ends the run alone.
    """
    work_s = {"bare": 16, "claude-code": 20, "codex": 62, "opencode": 24}
    eval_pace = {
        "greets": 1.0,
        "fixes-typo": 1.2,
        "adds-test": 0.85,
        "renames-symbol": 1.1,
        "writes-docs": 0.9,
        "reads-config": 1.0,
    }
    sample_pace = (0.9, 1.0, 1.15)
    run = Recording(iteration(root), arms=[arm(name) for name in work_s], baseline="bare")

    for eval_id in eval_pace:
        run.plan("e2e", eval_id, count=3)

    run.begin()
    free_at = {f"gw{number}": 0.0 for number in range(workers)}

    for planned in run.planned:
        worker = min(free_at, key=lambda name: free_at[name])
        started = free_at[worker]
        held_s = max(snapshot_s - started, 0) if planned["arm"] == "opencode" else 0
        pace = eval_pace[planned["eval_id"]] * sample_pace[planned["sample"]]
        wall_s = held_s + work_s[planned["arm"]] * pace

        if planned["cell"] == "e2e/reads-config/codex/2":
            wall_s = 110

        free_at[worker] = started + wall_s
        run.done(planned["cell"], free_at[worker], wall_s=wall_s, held_s=held_s, worker=worker)

    run.end(max(free_at.values()))

    return run


def long_label_live(root: Path, *, arms: int = 4) -> Recording:
    """A live run on up to four arms with long model names and eval ids.

    The first arm has a cell booting for over two minutes and finished the second and last
    evals;
    the second has a cell running; the last finished a mid-table eval.
    """
    elapsed_s = 200
    models = {
        "claude-code": "anthropic/claude-haiku-4.5",
        "codex": "openai/gpt-5.1-codex-mini",
        "opencode": "google/gemini-2.5-flash",
        "pi": "moonshotai/kimi-k2",
    }
    names = list(models)[:arms]
    evals = (
        "allows-filesystem-traversal",
        "blocks-network-egress",
        "keeps-secrets-out",
        "limits-memory",
        "mounts-read-only",
        "greets",
    )
    run = Recording(
        iteration(root),
        arms=[arm(name, name, models[name]) for name in names],
        baseline=None,
        start_ms=int(time.time() * 1000) - elapsed_s * 1000,
    )

    for eval_id in evals:
        run.plan("sandbox", eval_id, count=3)

    run.begin()

    for sample in range(3):
        run.done(f"sandbox/greets/{names[0]}/{sample}", 70 + sample, wall_s=40)
        run.done(f"sandbox/blocks-network-egress/{names[0]}/{sample}", 80 + sample, wall_s=40)
        run.done(f"sandbox/keeps-secrets-out/{names[-1]}/{sample}", 75 + sample, passed=2)

    run.start(f"sandbox/allows-filesystem-traversal/{names[0]}/0", elapsed_s - 144)
    run.play(f"sandbox/allows-filesystem-traversal/{names[1]}/0", elapsed_s - 31, worker="gw1")

    return run


def replay(run: Recording) -> Iterator[tuple[dict, RunView]]:
    """Each event of a recording in time order, with the view folded up to it."""
    view = RunView("tmp/evals/iteration_12")

    for event in sorted(EventTail(run.iteration_dir).poll(), key=lambda event: event["ts_ms"]):
        view.apply(event)

        yield event, view


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
