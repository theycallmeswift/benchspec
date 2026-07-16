"""Pytest plugin: turn discovered eval files into parametrized `(eval × arm)` tests.

Registers options, parametrizes the single `eval_arm` fixture over `(case, arm)` pairs,
and picks one iteration-dir name on the controller that all xdist workers share. The
test bodies and fixtures live in `cases.py`.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from evalspec.agents import resolve_agent_name
from evalspec.config.sets import (
    has_configured_set,
    resolved_judge_config,
    run_set_when_needed,
)
from evalspec.grading.binder import binder_identity
from evalspec.grading.judges import JudgeConfig
from evalspec.orchestration import cases, workspace
from evalspec.reporting import manifest, report
from evalspec.specs.discovery import pyproject_table, resolve_repo_root

_CASES = Path(cases.__file__)

_STARTED_AT = pytest.StashKey[str]()
_SUMMARY_LINES = pytest.StashKey[list]()


def _help(*parts: str) -> str:
    """Join help text fragments into one argparse string."""
    return " ".join(parts)


def pytest_addoption(parser: object) -> None:
    """Register evalspec command-line options with pytest."""
    group = parser.getgroup("evalspec", "skill-eval runner")
    group.addoption(
        "--evalspec-model",
        default=None,
        help="scalar override of the selected set's `model` default (every inheriting "
        "arm picks it up). Default None = don't override; the set's own model "
        "stands. Agent-specific — the agent fails fast if its CLI rejects the value.",
    )
    group.addoption(
        "--evalspec-repo-root",
        default=None,
        help="repository root that output-eval search paths resolve against, plus "
        "project-relative paths (default: $PROJECT_ROOT, else rootdir)",
    )
    group.addoption(
        "--evalspec-models",
        default=None,
        metavar="M[,M...]",
        help="comma-separated model sweep — expand the selected set into one arm per "
        "value (baseline = first). File-free single-axis comparison.",
    )
    group.addoption(
        "--evalspec-set",
        default=None,
        help="name of the eval set to run (default: the pyproject `default-set`)",
    )
    group.addoption(
        "--evalspec-config",
        default=None,
        metavar="FILE",
        help="path to an untracked TOML layering extra [tool.evalspec.sets.*] over "
        "pyproject (scratch set for a one-off comparison)",
    )
    group.addoption(
        "--evalspec-harness",
        default=None,
        help="scalar override of the selected set's `harness` default",
    )
    group.addoption(
        "--evalspec-effort",
        default=None,
        help="scalar override of the selected set's `effort` default",
    )
    group.addoption(
        "--evalspec-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="add/override an env entry on the selected set's defaults (repeatable); "
        "a $VAR value expands from the host environment",
    )
    group.addoption(
        "--evalspec-eval-paths",
        default=None,
        help=_help(
            "comma-separated paths (relative to repo root) to walk for",
            "eval.md / *.eval.md (default: skills, tests, evals, benchmarks;",
            "also overridable via [tool.evalspec] eval_paths in pyproject.toml).",
        ),
    )
    group.addoption(
        "--evalspec-project-marker",
        default=".claude-plugin/plugin.json",
        help=_help(
            "path (relative to repo root) whose presence marks the repo as a host plugin",
            "worth mounting in the sandbox as --plugin-dir",
            "(default: .claude-plugin/plugin.json — the Claude Code plugin manifest)",
        ),
    )
    group.addoption(
        "--evalspec-agent",
        default=None,
        help=_help(
            "coding agent for the `__route__` sandbox path (default: claude-code).",
            "Output-eval task arms select their harness per arm, so this flag no longer",
            "governs them. Precedence: this flag > EVALSPEC_AGENT > [tool.evalspec]",
            "agent in pyproject.toml. Unknown values fail at startup naming the source.",
        ),
    )
    group.addoption(
        "--evalspec-judge-harness",
        default=None,
        help="scalar override of the judge harness (default: claude-code, or "
             "[tool.evalspec.judge] harness). One of claude-code, codex, opencode.",
    )
    group.addoption(
        "--evalspec-judge-model",
        default=None,
        help="model for the LLM judge (default: sonnet, or [tool.evalspec.judge] "
             "model). Precedence: this flag > --evalspec-config > "
             "[tool.evalspec.judge] > built-in default. Recorded in meta.json under "
             "`judge.model`. Must default to None (not a hardcoded model) so an "
             "unset flag never shadows a project's [tool.evalspec.judge] model.",
    )
    group.addoption(
        "--evalspec-judge-effort",
        default=None,
        help="scalar override of the judge reasoning effort (default: medium, or "
             "[tool.evalspec.judge] effort).",
    )
    group.addoption(
        "--evalspec-judge-timeout",
        type=int,
        default=None,
        help="scalar override of the judge subprocess timeout in seconds (default: "
             "300, or [tool.evalspec.judge] timeout).",
    )
    group.addoption(
        "--evalspec-judge-harness-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="raw CLI token appended to the judge harness invocation (repeatable). "
             "When given at all, fully REPLACES [tool.evalspec.judge] harness_args "
             "(not merged/appended) — unlike set/arm harness_args, which append. "
             "A value starting with '-' needs the '--evalspec-judge-harness-arg=VALUE' "
             "form (argparse otherwise reads it as a new flag).",
    )
    group.addoption(
        "--evalspec-judge-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="add/override a judge env entry (repeatable); shallow-merges over "
             "[tool.evalspec.judge] env, CLI keys winning. A $VAR value expands "
             "from the host environment at judge EXECUTION time (never at "
             "collection); an unset referenced var raises.",
    )
    group.addoption(
        "--evalspec-fail-under",
        type=float,
        default=None,
        metavar="PP",
        help="minimum acceptable with/without delta in percentage points; any "
        "skill below it fails the run with exit 1 (e.g. 0 = the skill must "
        "at least match baseline). Uses the raw delta — read the within-noise "
        "label in benchmark.md before trusting small numbers. Skills without "
        "both arms are exempt.",
    )


@pytest.fixture
def sample_index(request: object) -> int:
    """Return the current repeated-sample index for pytest-xdist."""
    # pytest-repeat parametrizes each test with a hidden `__pytest_repeat_step_number`
    # param (0..N-1). Without --count, the param is absent — default to 0 so single-sample
    # runs still shard cleanly under sample-0/. Lives on the plugin (not cases.py) so user
    # test bodies under pytester can resolve it the same way real eval cases do.
    callspec = getattr(request.node, "callspec", None)
    if callspec is None:
        return 0
    return callspec.params.get("__pytest_repeat_step_number", 0)


def pytest_configure(config: object) -> None:
    """Configure pytest state for evalspec collection."""
    load_dotenv()
    config.addinivalue_line(
        "markers", "evalspec: skill-eval cases run via `evalspec run` (not `make test`)"
    )
    repo_root = resolve_repo_root(config)
    try:
        agent_name = resolve_agent_name(
            config.getoption("evalspec_agent"),
            pyproject_table(repo_root).get("agent"),
        )
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from None
    # Normalize into the env var make_agent() reads everywhere downstream — the
    # EVALSPEC_ITERATION handoff pattern; xdist workers inherit the controller env.
    os.environ["EVALSPEC_AGENT"] = agent_name
    # Self-register the eval cases so `make evals` is just `pytest -p evalspec.runners.pytest`
    # (+ `-k`/flags) — no `evalspec/cases.py` positional to fat-finger or to union with
    # a `-k`-style nodeid (which silently re-collected test_eval). Respect a user-given
    # target. `make test` never loads this plugin, so cases.py stays out of the unit run.
    if getattr(config.args_source, "name", "") != "ARGS":
        config.args = [str(_CASES)]
    if hasattr(config, "workerinput"):
        # xdist worker: adopt the iteration name the controller chose.
        workspace.set_current_iteration(config.workerinput["evalspec_iteration"])
    else:
        # Controller / serial run: choose once.
        workspace.set_current_iteration(workspace.next_iteration_name(repo_root))
        config.stash[_STARTED_AT] = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        )


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: object) -> None:
    """Pass evalspec configuration into xdist worker nodes."""
    # xdist controller → worker: hand the chosen iteration name through workerinput,
    # which execnet serializes to the worker before its own pytest_configure runs.
    node.workerinput["evalspec_iteration"] = workspace.current_iteration()


def pytest_collection_modifyitems(config: object, items: list) -> None:
    """Attribute each eval item's displayed path to its authored `.eval.md` file.

    Rewrites only the PATH portion of the nodeid (everything before the first `::`) to the
    eval's authored Markdown file, repo-relative, preserving the
    `test_eval[group-evalid-arm]` domain verbatim. pytest derives its non-verbose progress
    lines, `--collect-only` ids, and FAILED headers from `nodeid.split("::")[0]`, so the
    nodeid path — not `item.location` — is the field that regroups them; `item.location`
    is set to the same path so `-vv` and reportinfo stay coherent. The domain carries the
    per-cell `(eval × arm)` uniqueness downstream tooling and tests match on. Nothing in
    evalspec keys off pytest nodeids (aggregation uses arm names and on-disk `eval-*`
    dirs), so persisted artifacts are unaffected. Non-eval items (no `eval_arm` callspec
    param) are left alone. Because the rewritten path comes from the eval file rather than
    the test module, explicitly passing multiple modules that each request `eval_arm` can
    alias their rewritten nodeids together.
    """
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None or "eval_arm" not in callspec.params:
            continue
        eval_case, _arm = callspec.params["eval_arm"]
        relpath = item.session._node_location_to_relpath(eval_case.eval_file)
        # `nodeid` is a read-only property; assign the backing `_nodeid`. Keep everything
        # after the first `::` (the parametrized test-function domain) untouched.
        domain = item.nodeid.split("::", 1)[1]
        item._nodeid = f"{relpath}::{domain}"
        _, lineno, testname = item.location
        item.location = (relpath, lineno, testname)


def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    """Aggregate binder corpus records after the pytest session."""
    # Controller-only, and only when a run actually produced artifacts (a
    # --collect-only run never creates skills_root). Runs before
    # pytest_terminal_summary (plain impls fire inside TerminalReporter's
    # wrapper), so the summary can read what this wrote and a CI gate can still
    # change session.exitstatus.
    config = session.config
    if hasattr(config, "workerinput"):
        return
    iteration = workspace.current_iteration_or_none()
    if iteration is None:
        return
    repo_root = resolve_repo_root(config)
    skills_root = workspace.skills_root(repo_root)
    if not skills_root.is_dir():
        return
    # Resolve the eval set whenever one is configured — the planned roster is a
    # collection-time fact, independent of whether output artifacts landed. Inferring this
    # from produced `eval-*` dirs would erase the roster for a run whose every arm errored
    # before writing its dir. A trigger-only project declares no sets table and degrades to
    # None here; resolving unconditionally would raise UsageError for a config we tolerate.
    needs_set = has_configured_set(config)
    run_set = run_set_when_needed(config, needs_set=needs_set)
    judge_config = resolved_judge_config(config) if needs_set else JudgeConfig()
    judge_meta = manifest.judge_meta(judge_config)
    # Aggregate observed provenance BEFORE writing the manifest — the records live under
    # the skills root the same walk below reads, so meta.json must not be written with a
    # stale/empty observed_arms. Conflicting records raise here and abort the write, which
    # is the intended loud failure.
    observed_arms = manifest.aggregate_observed_arms(skills_root, run_set)
    manifest.write_manifest(
        skills_root.parent,
        iteration,
        repo_root,
        run_set,
        judge_meta,
        observed_arms,
        started_at=config.stash.get(_STARTED_AT, None),
    )
    lines = []
    index_lines = []
    fail_under = config.getoption("evalspec_fail_under")
    # The baseline arm names the Δ reference; build_benchmark coerces it to None when the
    # baseline didn't land on disk, so the report scores arms absolutely (no Δ to gate on).
    # Per-arm metadata is joined onto the report by arm name so the matrix columns and
    # per-arm sections describe what ran.
    baseline = run_set.baseline if run_set else None
    arm_meta = (
        {
            arm.name: {
                "harness": arm.harness,
                "model": arm.model,
                "effort": arm.effort,
                "env": report.redact_env(arm.env),
                "harness_args": arm.harness_args,
            }
            for arm in run_set.arms
        }
        if run_set
        else None
    )
    # Planned roster (same builder meta.json uses) drives the benchmark's planned/observed
    # split and the three core axes denormalized onto each index.jsonl row.
    planned = report.planned_arms(run_set)
    arm_axes = {
        arm["name"]: {"harness": arm["harness"], "model": arm["model"], "effort": arm["effort"]}
        for arm in planned
    }
    runner = run_set.runner if run_set else None

    # One run-level benchmark at the iteration root (beside meta.json / index.jsonl).
    # Skipped when no eval-* dirs were discovered, so an eval-less run writes no artifact.
    binder_degraded_total = 0
    all_eval_dirs = report.discover_eval_dirs(skills_root)
    if all_eval_dirs:
        benchmark = report.write_benchmark(
            skills_root.parent,
            all_eval_dirs,
            label=iteration,
            baseline=baseline,
            arm_meta=arm_meta,
            planned=planned,
            observed_arms=observed_arms,
            runner=runner,
            binder=binder_identity(),
        )
        binder_degraded_total = sum(
            stats.get("binder_degraded", 0) for stats in benchmark["arms"].values()
        )
        lines += report.terminal_matrix(benchmark, skills_root.parent / "benchmark.md")

    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill = skill_dir.name
        group_eval_dirs = sorted(
            child
            for child in skill_dir.iterdir()
            if child.is_dir() and child.name.startswith("eval-")
        )
        if not group_eval_dirs:
            continue
        # The report is pooled across the whole run, but the fail-under gate stays
        # per-group: rebuild each group's benchmark in memory (never written to disk)
        # and gate its per-arm Δ, so a regression in any single group still trips exit 1.
        if fail_under is not None and baseline is not None:
            group_benchmark = report.build_benchmark(
                group_eval_dirs,
                label=f"{iteration} · {skill}",
                baseline=baseline,
                arm_meta=arm_meta,
            )
            if group_benchmark["baseline"] is not None:
                # Gate every non-baseline arm's Δ against the threshold; arms with no Δ
                # (a missing rate on either side) are skipped, not failed.
                for arm_name, stats in group_benchmark["arms"].items():
                    if arm_name == group_benchmark["baseline"]:
                        continue
                    delta_pp = stats.get("delta_pp")
                    if delta_pp is not None and delta_pp < fail_under:
                        lines.append(
                            f"FAIL fail-under: {skill}/{arm_name} delta {delta_pp:+.0f}pp "
                            f"< {fail_under:+.0f}pp"
                        )
                        if session.exitstatus == 0:
                            session.exitstatus = 1
        index_lines += [
            json.dumps(row) for row in report.index_rows(skill_dir, skill, arm_axes)
        ]
    if index_lines:
        (skills_root.parent / "index.jsonl").write_text("\n".join(index_lines) + "\n")
    if binder_degraded_total:
        # Visibility, not a CI gate — session.exitstatus is deliberately untouched, unlike
        # the fail_under gate above. See execution.py's degradation-visibility contract.
        lines.append(
            f"WARN binder: {binder_degraded_total} assertion(s) degraded to judge "
            "grading after a binder infra RuntimeError — see grading.json's "
            "binder_degraded field per arm"
        )
    config.stash[_SUMMARY_LINES] = lines


def pytest_terminal_summary(terminalreporter: object, exitstatus: object, config: object) -> None:
    """Print binder corpus summary lines in pytest output."""
    lines = config.stash.get(_SUMMARY_LINES, [])
    if not lines:
        return
    terminalreporter.write_sep("=", "evalspec benchmark")
    for line in lines:
        terminalreporter.line(line)


def pytest_generate_tests(metafunc: object) -> None:
    """Parametrize pytest items from discovered evalspec cases."""
    if "eval_arm" in metafunc.fixturenames:
        pairs, ids = cases.eval_arm_params(metafunc.config)
        metafunc.parametrize("eval_arm", pairs, ids=ids)
