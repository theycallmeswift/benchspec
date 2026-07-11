"""Orchestrate one `(eval, arm)`: run the agent, grade it, write the artifacts.

The eval is single-turn: a `history:` transcript block (context) is prepended to the one
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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from evalspec import binder, checkers, workspace
from evalspec.agents import make_agent
from evalspec.arms import Arm, expand_env
from evalspec.discovery import EvalCase
from evalspec.judge import grade_run
from evalspec.judges import JudgeConfig
from evalspec.room import gather_facts, merge_facts, render_history
from evalspec.runner import substitute_assertions, substitute_prompt
from evalspec.sandbox import (
    DEFAULT_PROJECT_MARKER,
    SandboxSession,
    arm_session,
    ensure_snapshot,
)
from evalspec.trajectory import TURN_DELIM, render_process_facts, skills_dispatched

# The judge is a run-level concern, independent of the task arm's own harness/model
# (which can be a provider-qualified name like `google/gemini-3.5-flash` for OpenCode).
# The default judge is JudgeConfig() (harness=claude-code, model=sonnet); callers pass
# a resolved JudgeConfig (see evalspec.judges.config.resolve_judge_config) to override.


@dataclass
class ArmOutcome:
    """Store arm outcome data."""

    grading: dict  # {"eval_id", "skill", "arm", "sample", "assertions": [...]}
    errored: bool  # the agent run errored
    duration_ms: int
    total_tokens: int


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


def _turn_transcript(*, prompt: str, result: object, tree: str) -> dict:
    """Render seeded turns and the prompt into an agent transcript."""
    record = {
        "turn": 1,
        "prompt": prompt,
        "result": result.result_text,
        "is_error": result.is_error,
        "result_subtype": result.result_subtype,
        # count dispatches (tool_call), not the round-trip tool_result events
        "tool_call_count": sum(
            1 for event in result.trajectory if event.get("kind") == "tool_call"
        ),
        "workdir_tree": tree,
    }
    # Informational Skill-tool set for this turn. The fallback-inclusive, candidate-unioned
    # set used for grading is computed in run_eval_arm, not here — a no-arg call cannot see
    # a bare-tool-name fallback fire.
    dispatched = skills_dispatched(result.trajectory)
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
    grade: Callable[..., dict],
    judge_config: object,
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
            judge_config=judge_config,
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


def _bind_all(
    assertions: list, bind: Callable[[str], dict | None]
) -> tuple[list, int]:
    """Bind every assertion once, returning one spec-or-None per assertion plus a degrade count.

    A transient binder infra failure (RuntimeError) degrades that assertion to judge
    grading — spec None — and increments the count, never a silent absorb. A punt
    (bind returning None) is the binder's designed outcome, not a degrade. BinderAuthError
    is deliberately NOT caught: it propagates and fails the run.
    """
    cache: dict[str, dict | None] = {}
    binder_degraded = 0
    specs: list = []
    for text in assertions:
        if text not in cache:
            try:
                cache[text] = bind(text)
            except RuntimeError:
                cache[text] = None
                binder_degraded += 1
        specs.append(cache[text])
    return specs, binder_degraded


def _grade_mixed(
    *,
    assertions: object,
    specs: object,
    tree: object,
    contents: object,
    shas: object,
    result_text: object,
    grade: Callable[..., dict],
    workdir: object,
    grade_context: object,
    judge_config: object,
    eval_id: object,
    arm_name: object,
    pre_run_shas: object,
    process_facts: object = "",
) -> object:
    """Grade pre-bound assertions locally and punt the unbound rest to the judge.

    `specs[i]` is the binder's spec for `assertions[i]`, or None to punt. Returns
    (results, judge_ms, judge_errored).
    """
    results: list = [None] * len(assertions)
    judge_idx: list[int] = []
    for assertion_index, spec in enumerate(specs):
        if spec is not None:
            entry = checkers.run_assertion(spec, workdir, pre_run_shas, context=grade_context)
            entry["text"] = assertions[assertion_index]  # author's prose, not spec-derived
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
        judge_config=judge_config,
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
    session_factory: Callable[..., SandboxSession],
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
    setup_reldir: object,
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
        setup_reldir=setup_reldir,
        arm=arm_name,
        project_marker=project_marker,
        arm_env=arm_env,
        eval_set=eval_set,
        harness_args=harness_args,
    ) as run:
        # Prompts use cwd-relative `./` paths: the agent runs with cwd = the workdir
        # mount (GUEST_WORKDIR), so `./x` resolves there, and gather_facts reads the
        # host side of the same mount.
        # Ordinary execution never feeds a group-derived skill name to detection; the
        # adapter keeps the parameter for the __route__/runner paths but receives None here.
        result = await run(
            prompt,
            resume_session_id=None,
            detect_skill=None,
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
        run_acc.transcript.append(_turn_transcript(prompt=prompt, result=result, tree=tree))
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
    judge_config: JudgeConfig | None = None,
    session_factory: Callable[..., SandboxSession] = arm_session,
    grade: Callable[..., dict] = grade_run,
    bind: Callable[[str], dict | None] = binder.bind,
) -> ArmOutcome:
    """Run all cases for one eval arm and write result artifacts."""
    judge_config = judge_config or JudgeConfig()
    eval_id = eval_case.eval_id
    # Every downstream sink keys on the arm NAME string (artifact paths, grading["arm"],
    # the session config). Bind it once; never let an Arm(...) repr leak into a path.
    arm_name = arm.name
    # setup.sh lives in the eval folder, located by its path relative to the mount.
    # Assumes `project` is an ancestor of `eval_case.eval_dir` — currently guaranteed
    # because the sole caller (cases.py) passes project=repo_root. A future caller that
    # mounts a staged project distinct from repo_root must address this.
    setup_reldir = str(eval_case.eval_dir.relative_to(project)) if project is not None else None

    # Resolve the agent + snapshot BEFORE the per-arm asyncio.run: building a missing
    # snapshot itself calls asyncio.run, which can't nest inside a running loop. The agent
    # is selected per arm so a multi-harness set runs each column on its own harness.
    agent = make_agent(arm.harness)
    snapshot = ensure_snapshot(agent, repo_root=repo_root)

    # History block (context) first, then the one graded prompt — both rendered with the
    # same `today`. render_history emits a trailing blank line, so the prompt follows cleanly.
    prompt = render_history(eval_case.history, today) + substitute_prompt(eval_case.prompt, today)
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
            setup_reldir=setup_reldir,
            # Lazy $VAR expansion: an unset referenced var raises here, at the arm that
            # actually runs, never at collection (a deselected arm's secret is never read).
            arm_env=expand_env(arm.env, os.environ),
            eval_set=eval_set,
            harness_args=arm.harness_args,
        )
    )

    # Bind once, up front: the binder's own output is the source of truth for which
    # assertions are activation checks — no separate recognizer.
    specs, binder_degraded = _bind_all(graded_assertions, bind)

    # Candidate skills are those the binder bound to an activation checker — the skills the
    # eval asserts on, never the group name, so an eval whose group differs still grades each.
    candidate_skills = {
        spec["skill"]
        for spec in specs
        if spec is not None and spec["checker"] in ("skill_invoked", "not_skill_invoked")
    }

    # Fired skills = every real Skill-tool fire (target-independent) plus each candidate's
    # fallback-shape fire, deduped first-seen so the evidence list stays stable.
    dispatched = list(skills_dispatched(arm_run.trajectory))
    for skill in candidate_skills:
        dispatched.extend(skills_dispatched(arm_run.trajectory, skill))
    fired_skills = tuple(dict.fromkeys(dispatched))

    grade_context = checkers.GradeContext(fired_skills=fired_skills)

    # {TODAY} resolves in the prompt, history, and workspace; the assertions were substituted
    # pre-run (above) so a date-bearing path checker grades against the real date.
    merged, judge_ms, judge_errored = _grade_mixed(
        assertions=graded_assertions,
        specs=specs,
        tree=arm_run.tree,
        contents=arm_run.contents,
        shas=arm_run.shas,
        result_text=arm_run.result_text,
        grade=grade,
        workdir=workdir,
        grade_context=grade_context,
        judge_config=judge_config,
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
        "binder_degraded": binder_degraded,
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
    )
