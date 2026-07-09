"""Orchestrate one `(eval, arm)`: run the agent, grade it, write the artifacts.

The eval is single-turn: a `seed:` transcript block (context) is prepended to the one
graded prompt. Both arms grade identically — there is no with-skill invocation gate.
Activation is an ordinary prose assertion (`` - Skill `X` invoked ``) the binder maps to
the `skill_invoked` checker, graded True on a firing arm and False on a non-firing one off
the arm's dispatched-skills set, not a harness assert. `session_factory`, `grade`, and `bind`
are injectable so the loop is unit-testable without spawning a microVM or calling the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from evalspec import binder, checkers, workspace
from evalspec.agents import make_agent
from evalspec.arms import Arm, expand_env
from evalspec.discovery import EvalCase
from evalspec.judge import grade_run
from evalspec.room import gather_facts, merge_facts, render_seed
from evalspec.runner import substitute_assertions, substitute_prompt
from evalspec.sandbox import DEFAULT_PROJECT_MARKER, arm_session, ensure_snapshot
from evalspec.trajectory import TURN_DELIM, render_process_facts, skills_dispatched

# The judge always shells out to the host `claude` CLI (see agents/judge_cli.py), which
# knows only Claude-family aliases. The arm's `model` selects the TASK model and can be a
# provider-qualified name like `google/gemini-3.5-flash` for OpenCode, which would 404 the
# host claude CLI. Pin the judge to a Claude alias so cross-agent matrices grade correctly.
# Overridable via `--evalspec-judge-model`, which must also be a Claude alias.
JUDGE_MODEL = "sonnet"


@dataclass
class ArmOutcome:
    """Store arm outcome data."""

    grading: dict  # {"eval_id", "skill", "arm", "sample", "assertions": [...]}
    errored: bool  # the agent run errored
    duration_ms: int
    total_tokens: int
    fired: bool = False  # informational: did the named skill get invoked this run?


@dataclass
class _ArmRun:
    """Everything accumulated from running an arm in one VM session."""

    tree: str = ""
    contents: dict = field(default_factory=dict)
    shas: dict = field(default_factory=dict)
    result_text: str = ""
    trajectory: list = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    raw: str = ""
    errored: bool = False
    total_duration_ms: int = 0
    total_tokens: int = 0
    total_cache_read: int = 0
    total_cache_creation: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


def _turn_transcript(*, prompt: str, result: object, tree: str, skill: str) -> dict:
    """Render seeded turns and the prompt into an agent transcript."""
    record = {
        "turn": 1,
        "prompt": prompt,
        "result": result.result_text,
        "is_error": result.is_error,
        "fired": result.fired,
        "result_subtype": result.result_subtype,
        # count dispatches (tool_call), not the round-trip tool_result events
        "tool_call_count": sum(
            1 for event in result.trajectory if event.get("kind") == "tool_call"
        ),
        "workdir_tree": tree,
    }
    # Pass the skill so a fire via the namespaced-tool fallback still lands here, keeping
    # this field consistent with `fired` instead of empty on that shape.
    dispatched = skills_dispatched(result.trajectory, skill)
    if dispatched:
        record["skills_dispatched"] = dispatched
    return record


def _grade_via_judge(
    *,
    assertions: object,
    tree: object,
    contents: object,
    shas: object,
    result_text: object,
    grade: object,
    agent: object,
    judge_model: object,
    eval_id: object,
    arm_name: object,
    pre_run_shas: object,
    process_facts: object = "",
) -> object:
    """Judge-grade assertions; return (graded, judge_ms, judge_errored)."""
    t0 = time.perf_counter()
    judge_errored = False
    try:
        graded = grade(
            assertions,
            tree,
            contents,
            shas,
            result_text,
            eval_id,
            arm_name,
            agent=agent,
            model=judge_model,
            original_shas=pre_run_shas,
            process_facts=process_facts,
        )
    except RuntimeError as error:
        # The judge CLI failed at the infra level (auth, rate limit, missing binary);
        # mark the arm errored so the benchmark drops it instead of scoring fake fails.
        judge_errored = True
        graded = {
            "assertions": [
                {
                    "text": assertion,
                    "passed": False,
                    "evidence": f"JUDGE INFRA ERROR: {error}",
                }
                for assertion in assertions
            ]
        }
    return graded, int((time.perf_counter() - t0) * 1000), judge_errored


def _grade_mixed(
    *,
    assertions: object,
    tree: object,
    contents: object,
    shas: object,
    result_text: object,
    bind: object,
    grade: object,
    workdir: object,
    grade_context: object,
    agent: object,
    judge_model: object,
    eval_id: object,
    arm_name: object,
    pre_run_shas: object,
    process_facts: object = "",
) -> object:
    """Grade bound assertions locally and punt the rest to the judge."""
    results: list = [None] * len(assertions)
    judge_idx: list[int] = []
    bind_cache: dict[str, dict | None] = {}
    for assertion_index, text in enumerate(assertions):
        if text not in bind_cache:
            try:
                bind_cache[text] = bind(text)
            except (RuntimeError, subprocess.TimeoutExpired):
                # A transient binder hiccup OR a host-subprocess timeout degrades to judge
                # grading (the binder's designed punt), not a cell error; a logic bug raises
                # a different type and propagates loudly, by design.
                bind_cache[text] = None
        spec = bind_cache[text]
        if spec is not None:
            entry = checkers.run_assertion(spec, workdir, pre_run_shas, context=grade_context)
            entry["text"] = text  # the author's prose, not the spec-derived rendering
            results[assertion_index] = entry
        else:
            judge_idx.append(assertion_index)
    if not judge_idx:
        return results, 0, False

    texts = [assertions[assertion_index] for assertion_index in judge_idx]
    graded, judge_ms, judge_errored = _grade_via_judge(
        assertions=texts,
        tree=tree,
        contents=contents,
        shas=shas,
        result_text=result_text,
        grade=grade,
        agent=agent,
        judge_model=judge_model,
        eval_id=eval_id,
        arm_name=arm_name,
        pre_run_shas=pre_run_shas,
        process_facts=process_facts,
    )
    for assertion_index, entry in zip(judge_idx, graded["assertions"], strict=False):
        entry["type"] = "semantic"
        results[assertion_index] = entry
    return results, judge_ms, judge_errored


async def _run_arm_turns(
    session_factory: object,
    *,
    agent: object,
    snapshot: object,
    eval_id: object,
    arm_name: object,
    workdir: object,
    project: object,
    model: object,
    effort: object,
    prompt: object,
    project_marker: object,
    skill: object,
    detect_skill: object,
    arm_env: object = None,
    eval_set: object = "",
    harness_args: object = None,
) -> _ArmRun:
    """Execute every prompt turn for one eval arm."""
    # The whole VM lifecycle (boot → run → teardown) runs in ONE asyncio.run: the microVM
    # is bound to the loop it was created in. Grading runs afterward, on the host.
    run_acc = _ArmRun()

    async with session_factory(
        agent=agent,
        snapshot=snapshot,
        eval_id=eval_id,
        config=arm_name,
        host_workdir=workdir,
        host_repo_root=project,
        model=model,
        effort=effort,
        skill=skill,
        arm=arm_name,
        project_marker=project_marker,
        arm_env=arm_env,
        eval_set=eval_set,
        harness_args=harness_args,
    ) as run:
        # Prompts use cwd-relative `./` paths: the agent runs with cwd = the workdir
        # mount (GUEST_WORKDIR), so `./x` resolves there, and gather_facts reads the
        # host side of the same mount.
        result = await run(
            prompt,
            resume_session_id=None,
            detect_skill=detect_skill,
        )

        run_acc.total_duration_ms += result.duration_ms
        run_acc.total_tokens += result.total_tokens
        run_acc.total_cache_read += result.cache_read_tokens
        run_acc.total_cache_creation += result.cache_creation_tokens
        run_acc.total_input_tokens += result.input_tokens
        run_acc.total_output_tokens += result.output_tokens
        run_acc.errored = result.is_error

        tree, contents, shas = gather_facts(workdir)
        # Fold in skill artifacts the agent wrote outside the workdir mount (its
        # ~/.claude/skills dir, etc.) so the judge grades them alongside the workdir.
        if result.artifacts:
            tree, contents, shas = merge_facts(tree, contents, shas, result.artifacts)
        run_acc.tree, run_acc.contents, run_acc.shas = tree, contents, shas
        run_acc.result_text = result.result_text
        run_acc.trajectory = result.trajectory
        run_acc.transcript.append(
            _turn_transcript(prompt=prompt, result=result, tree=tree, skill=skill)
        )
        run_acc.raw = result.raw

    return run_acc


def run_eval_arm(
    eval_case: EvalCase,
    arm: Arm,
    workdir: Path,
    pre_run_shas: dict,
    project: Path | None,
    *,
    today: str,
    repo_root: Path,
    sample: int,
    eval_set: str = "",
    project_marker: str = DEFAULT_PROJECT_MARKER,
    judge_model: str = JUDGE_MODEL,
    session_factory: object = arm_session,
    grade: object = grade_run,
    bind: object = binder.bind,
) -> ArmOutcome:
    """Run all cases for one eval arm and write result artifacts."""
    eval_id = eval_case.eval_id
    # Every downstream sink keys on the arm NAME string (artifact paths, grading["arm"],
    # the session config). Bind it once; never let an Arm(...) repr leak into a path.
    arm_name = arm.name
    # Stream the skill name on both arms so fired-detection runs unconditionally.
    detect_skill = eval_case.skill

    # Resolve the agent + snapshot BEFORE the per-arm asyncio.run: building a missing
    # snapshot itself calls asyncio.run, which can't nest inside a running loop. The agent
    # is selected per arm so a multi-harness set runs each column on its own harness.
    agent = make_agent(arm.harness)
    snapshot = ensure_snapshot(agent, repo_root=repo_root)

    # Seed block (context) first, then the one graded prompt — both rendered with the
    # same `today`. render_seed emits a trailing blank line, so the prompt follows cleanly.
    prompt = render_seed(eval_case.seed, today) + substitute_prompt(eval_case.prompt, today)
    # Substitute the assertions here too, before the run — a stray-placeholder typo must fail
    # pre-run like the prompt does, not at grade time after the agent already spent tokens
    # (a raise there would unwind before the artifact writes and discard the completed run).
    graded_assertions = substitute_assertions(eval_case.assertions, today)

    arm_run = asyncio.run(
        _run_arm_turns(
            session_factory,
            agent=agent,
            snapshot=snapshot,
            eval_id=eval_id,
            arm_name=arm_name,
            workdir=workdir,
            project=project,
            model=arm.model,
            effort=arm.effort,
            prompt=prompt,
            project_marker=project_marker,
            skill=eval_case.skill,
            detect_skill=detect_skill,
            # Lazy $VAR expansion: an unset referenced var raises here, at the arm that
            # actually runs, never at collection (a deselected arm's secret is never read).
            arm_env=expand_env(arm.env, os.environ),
            eval_set=eval_set,
            harness_args=arm.harness_args,
        )
    )

    # Activation is graded off the arm's actually-dispatched skills — empty on a non-firing
    # arm (skill_invoked then grades False, never an error). Pass the eval's skill so a fire
    # via the namespaced-tool fallback (detect_skill_fired's second shape) lands in the set
    # too, sparing a run where the skill demonstrably fired a false-negative activation.
    fired_skills = tuple(skills_dispatched(arm_run.trajectory, eval_case.skill))
    grade_context = checkers.GradeContext(fired_skills=fired_skills)

    # {TODAY} resolves in the prompt, seed, and fixtures; the assertions were substituted
    # pre-run (above) so a date-bearing path checker grades against the real date.
    merged, judge_ms, judge_errored = _grade_mixed(
        assertions=graded_assertions,
        tree=arm_run.tree,
        contents=arm_run.contents,
        shas=arm_run.shas,
        result_text=arm_run.result_text,
        bind=bind,
        grade=grade,
        workdir=workdir,
        grade_context=grade_context,
        agent=agent,
        judge_model=judge_model,
        eval_id=eval_id,
        arm_name=arm_name,
        pre_run_shas=pre_run_shas,
        process_facts=render_process_facts([arm_run.trajectory]),
    )

    # `errored` lets the report tell an infra failure (excluded from the benchmark) apart
    # from an honest measurement; a failed assertion is an honest result, not an error.
    errored = arm_run.errored or judge_errored
    grading = {
        "eval_id": eval_id,
        "skill": eval_case.skill,
        "arm": arm_name,
        "sample": sample,
        "errored": errored,
        "assertions": merged,
    }
    run_dir = workspace.arm_dir(repo_root, eval_case.skill, eval_id, arm_name, sample=sample)
    run_dir.mkdir(parents=True, exist_ok=True)
    # duration_ms = task time; judge_ms = grading time — separate so the benchmark can
    # decompose where the wall-clock goes (task vs judge).
    (run_dir / "timing.json").write_text(
        json.dumps(
            {
                "duration_ms": arm_run.total_duration_ms,
                "judge_ms": judge_ms,
                "total_tokens": arm_run.total_tokens,
                "cache_read_tokens": arm_run.total_cache_read,
                "cache_creation_tokens": arm_run.total_cache_creation,
                "input_tokens": arm_run.total_input_tokens,
                "output_tokens": arm_run.total_output_tokens,
            }
        )
        + "\n"
    )
    (run_dir / "grading.json").write_text(json.dumps(grading, indent=2) + "\n")
    (run_dir / "transcript.json").write_text(json.dumps(arm_run.transcript, indent=2) + "\n")
    # One raw stream for the arm, preceded by a {TURN_DELIM: 1} delimiter line so the
    # structured trajectory regenerates from it deterministically (trajectory_from_session)
    # and is not persisted.
    if arm_run.raw:
        block = arm_run.raw if arm_run.raw.endswith("\n") else arm_run.raw + "\n"
        (run_dir / "session.jsonl").write_text(json.dumps({TURN_DELIM: 1}) + "\n" + block)
    return ArmOutcome(
        grading,
        errored,
        arm_run.total_duration_ms,
        arm_run.total_tokens,
        # `fired` is read back from the transcript, the one persisted home for the
        # per-turn `result.fired` — no redundant copy threaded through `_ArmRun`. An
        # errored arm can return before the single turn appends; treat an empty
        # transcript as no fire rather than an IndexError.
        fired=arm_run.transcript[0]["fired"] if arm_run.transcript else False,
    )
