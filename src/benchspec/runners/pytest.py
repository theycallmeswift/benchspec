"""Pytest plugin: turn discovered eval files into parametrized `(eval × arm)` tests.

Registers options, parametrizes the single `eval_arm` fixture over `(case, arm)` pairs,
and picks one iteration-dir name on the controller that all xdist workers share. The
test bodies and fixtures live in `cases.py`.
"""

from __future__ import annotations

import datetime
import json
import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Protocol

import pytest
from dotenv import find_dotenv, load_dotenv

from benchspec.agents import resolve_agent_name
from benchspec.config.arms import Arm
from benchspec.config.options import option_float, option_str
from benchspec.config.sets import (
    has_configured_set,
    resolved_binder_config,
    resolved_judge_config,
    run_set_when_needed,
)
from benchspec.grading.binder import binder_identity
from benchspec.grading.binder_config import BinderConfig
from benchspec.grading.judges import JudgeConfig
from benchspec.orchestration import cases, workspace
from benchspec.reporting import manifest, report
from benchspec.specs.discovery import EvalCase, pyproject_table, resolve_repo_root

_CASES = Path(cases.__file__)

_STARTED_AT = pytest.StashKey[str]()
_SUMMARY_LINES = pytest.StashKey[list[str]]()


def _help(*parts: str) -> str:
    """Join help text fragments into one argparse string."""
    return " ".join(parts)


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(
    early_config: pytest.Config, parser: pytest.Parser, args: list[str]
) -> None:
    """Load the repo-root `.env` before any conftest is imported or configured.

    Not `pytest_configure`: conftest plugins register after entry-point plugins, and
    pluggy calls hooks LIFO, so every conftest's `pytest_configure` runs first — the
    binder corpus conftest's `preflight_verify_binder_key()` was aborting the run before this
    plugin ever loaded `.env`. This hook fires before initial conftest collection, so
    credentials are in `os.environ` for anything a conftest does.

    `usecwd=True` so the search walks up from where pytest was invoked. Bare
    `find_dotenv()` walks up from this file instead, which only finds the repo `.env`
    while benchspec is installed editable — from site-packages it finds nothing.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register benchspec command-line options with pytest."""
    group = parser.getgroup("benchspec", "skill-eval runner")
    group.addoption(
        "--benchspec-model",
        default=None,
        help="scalar override of the selected set's `model` default (every inheriting "
        "arm picks it up). Default None = don't override; the set's own model "
        "stands. Agent-specific — the agent fails fast if its CLI rejects the value.",
    )
    group.addoption(
        "--benchspec-repo-root",
        default=None,
        help="repository root that output-eval search paths resolve against, plus "
        "project-relative paths (default: $PROJECT_ROOT, else rootdir)",
    )
    group.addoption(
        "--benchspec-models",
        default=None,
        metavar="M[,M...]",
        help="comma-separated model sweep — expand the selected set into one arm per "
        "value (baseline = first). File-free single-axis comparison.",
    )
    group.addoption(
        "--benchspec-set",
        default=None,
        help="name of the eval set to run (default: the pyproject `default-set`)",
    )
    group.addoption(
        "--benchspec-config",
        default=None,
        metavar="FILE",
        help="path to an untracked TOML layering extra [tool.benchspec.sets.*] over "
        "pyproject (scratch set for a one-off comparison)",
    )
    group.addoption(
        "--benchspec-harness",
        default=None,
        help="scalar override of the selected set's `harness` default",
    )
    group.addoption(
        "--benchspec-effort",
        default=None,
        help="scalar override of the selected set's `effort` default",
    )
    group.addoption(
        "--benchspec-timeout",
        type=int,
        default=None,
        help="scalar override of the selected set's `timeout` default: the wall-clock cap "
        "on one agent turn, seconds (default: 600, or the set/arm `timeout`)",
    )
    group.addoption(
        "--benchspec-trigger-budget",
        type=int,
        default=None,
        help="scalar override of the selected set's `trigger_budget` default: the tool calls "
        "a trigger-only eval may make with verdicts still open before it is stopped "
        "(default: 5, or the set/arm `trigger_budget`)",
    )
    group.addoption(
        "--benchspec-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="add/override an env entry on the selected set's defaults (repeatable); "
        "a $VAR value expands from the host environment",
    )
    group.addoption(
        "--benchspec-eval-paths",
        default=None,
        help=_help(
            "comma-separated paths (relative to repo root) to walk for",
            "eval.md / *.eval.md (default: skills, tests, evals, benchmarks;",
            "also overridable via [tool.benchspec] eval_paths in pyproject.toml).",
        ),
    )
    group.addoption(
        "--benchspec-project-marker",
        default=".claude-plugin/plugin.json",
        help=_help(
            "path (relative to repo root) whose presence marks the repo as a host plugin",
            "worth mounting in the sandbox as --plugin-dir",
            "(default: .claude-plugin/plugin.json — the Claude Code plugin manifest)",
        ),
    )
    group.addoption(
        "--benchspec-agent",
        default=None,
        help=_help(
            "agent for the `__route__` sandbox path (default: claude-code).",
            "Output-eval task arms select their harness per arm, so this flag no longer",
            "governs them. Precedence: this flag > BENCHSPEC_AGENT > [tool.benchspec]",
            "agent in pyproject.toml. Unknown values fail at startup naming the source.",
        ),
    )
    group.addoption(
        "--benchspec-judge-harness",
        default=None,
        help="scalar override of the judge harness (default: claude-code, or "
             "[tool.benchspec.judge] harness). One of claude-code, codex, opencode.",
    )
    group.addoption(
        "--benchspec-judge-provider",
        default=None,
        help="scalar override of the judge provider (default: default, or "
             "[tool.benchspec.judge] provider). `default` is the harness vendor's own "
             "API or CLI login; `openrouter` routes the judge through OpenRouter on "
             "OPENROUTER_API_KEY and needs a vendor-qualified model.",
    )
    group.addoption(
        "--benchspec-judge-model",
        default=None,
        help="model for the LLM judge (default: sonnet, or [tool.benchspec.judge] "
             "model). Precedence: this flag > --benchspec-config > "
             "[tool.benchspec.judge] > built-in default. Recorded in meta.json under "
             "`judge.model`. Must default to None (not a hardcoded model) so an "
             "unset flag never shadows a project's [tool.benchspec.judge] model.",
    )
    group.addoption(
        "--benchspec-judge-effort",
        default=None,
        help="scalar override of the judge reasoning effort (default: medium, or "
             "[tool.benchspec.judge] effort).",
    )
    group.addoption(
        "--benchspec-judge-timeout",
        type=int,
        default=None,
        help="scalar override of the judge subprocess timeout in seconds (default: "
             "300, or [tool.benchspec.judge] timeout).",
    )
    group.addoption(
        "--benchspec-judge-harness-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="raw CLI token appended to the judge harness invocation (repeatable). "
             "When given at all, fully REPLACES [tool.benchspec.judge] harness_args "
             "(not merged/appended) — unlike set/arm harness_args, which append. "
             "A value starting with '-' needs the '--benchspec-judge-harness-arg=VALUE' "
             "form (argparse otherwise reads it as a new flag).",
    )
    group.addoption(
        "--benchspec-judge-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="add/override a judge env entry (repeatable); shallow-merges over "
             "[tool.benchspec.judge] env, CLI keys winning. A $VAR value expands "
             "from the host environment at judge EXECUTION time (never at "
             "collection); an unset referenced var raises.",
    )
    group.addoption(
        "--benchspec-binder-provider",
        default=None,
        help="scalar override of the binder provider (default: gemini, or "
             "[tool.benchspec.binder] provider). `gemini` posts to the Gemini API on "
             "GEMINI_API_KEY; `openrouter` posts to OpenRouter on OPENROUTER_API_KEY.",
    )
    group.addoption(
        "--benchspec-binder-model",
        default=None,
        help="scalar override of the binder model (default: the resolved provider's "
             "own Gemini Flash-Lite slug, or [tool.benchspec.binder] model). Recorded "
             "in meta.json under `binder.model`.",
    )
    group.addoption(
        "--benchspec-fail-under",
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
def sample_index(request: pytest.FixtureRequest) -> int:
    """Return the current repeated-sample index for pytest-xdist."""
    # pytest-repeat parametrizes each test with a hidden `__pytest_repeat_step_number`
    # param (0..N-1). Without --count, the param is absent — default to 0 so single-sample
    # runs still shard cleanly under sample-0/. Lives on the plugin (not cases.py) so user
    # test bodies under pytester can resolve it the same way real eval cases do.
    params = _callspec_params(request.node)
    step = params.get("__pytest_repeat_step_number", 0)

    return step if isinstance(step, int) else 0


def _callspec_params(node: object) -> dict[str, object]:
    """The parametrize values behind a collected item, or `{}` for an unparametrized one."""
    if not isinstance(node, pytest.Function) or not hasattr(node, "callspec"):
        return {}

    return node.callspec.params


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest state for benchspec collection."""
    config.addinivalue_line(
        "markers", "benchspec: skill-eval cases run via `benchspec run` (not `make test`)"
    )
    repo_root = resolve_repo_root(config)
    try:
        agent_name = resolve_agent_name(
            option_str(config, "benchspec_agent"),
            pyproject_table(repo_root).get("agent"),
        )
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from None
    # Normalize into the env var make_agent() reads everywhere downstream — the
    # BENCHSPEC_ITERATION handoff pattern; xdist workers inherit the controller env.
    os.environ["BENCHSPEC_AGENT"] = agent_name
    # Self-register the eval cases so `make evals` is just `pytest -p benchspec.runners.pytest`
    # (+ `-k`/flags) — no `benchspec/cases.py` positional to fat-finger or to union with
    # a `-k`-style nodeid (which silently re-collected test_eval). Respect a user-given
    # target. `make test` never loads this plugin, so cases.py stays out of the unit run.
    if config.args_source != pytest.Config.ArgsSource.ARGS:
        config.args = [str(_CASES)]

    workerinput = _workerinput(config)
    if workerinput is not None:
        # xdist worker: adopt the iteration name the controller chose.
        workspace.set_current_iteration(str(workerinput["benchspec_iteration"]))
    else:
        # Controller / serial run: choose once.
        workspace.set_current_iteration(workspace.next_iteration_name(repo_root))
        config.stash[_STARTED_AT] = datetime.datetime.now(datetime.UTC).isoformat(
            timespec="seconds"
        )


def _workerinput(config: pytest.Config) -> MutableMapping[str, object] | None:
    """The xdist-attached `workerinput` on a worker's config; None on the controller."""
    workerinput = getattr(config, "workerinput", None)

    return workerinput if isinstance(workerinput, MutableMapping) else None


class _WorkerNode(Protocol):
    """The xdist controller-side node whose `workerinput` is serialized to its worker."""

    @property
    def workerinput(self) -> MutableMapping[str, object]:
        """The mapping execnet ships to the worker before its `pytest_configure`."""
        ...


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: _WorkerNode) -> None:
    """Pass benchspec configuration into xdist worker nodes."""
    # xdist controller → worker: hand the chosen iteration name through workerinput,
    # which execnet serializes to the worker before its own pytest_configure runs.
    node.workerinput["benchspec_iteration"] = workspace.current_iteration()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Aggregate binder corpus records after the pytest session."""
    # Controller-only, and only when a run actually produced artifacts (a
    # --collect-only run never creates skills_root). Runs before
    # pytest_terminal_summary (plain impls fire inside TerminalReporter's
    # wrapper), so the summary can read what this wrote and a CI gate can still
    # change session.exitstatus.
    config = session.config
    if _workerinput(config) is not None:
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
    binder_config = resolved_binder_config(config) if needs_set else BinderConfig()
    binder_meta = binder_identity(binder_config)
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
        binder_meta,
        observed_arms,
        started_at=config.stash.get(_STARTED_AT, None),
    )
    lines: list[str] = []
    index_lines: list[str] = []
    fail_under = option_float(config, "benchspec_fail_under")
    # The baseline arm names the Δ reference; build_benchmark coerces it to None when the
    # baseline didn't land on disk, so the report scores arms absolutely (no Δ to gate on).
    # Per-arm metadata is joined onto the report by arm name so the matrix columns and
    # per-arm sections describe what ran.
    baseline = run_set.baseline if run_set else None
    arm_meta = (
        {
            arm.name: {
                "harness": arm.harness,
                "provider": arm.provider,
                "model": arm.model,
                "effort": arm.effort,
                "timeout": arm.timeout,
                "trigger_budget": arm.trigger_budget,
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
            binder=binder_meta,
        )
        binder_degraded_total = sum(
            stats.get("binder_degraded", 0) for stats in benchmark["arms"].values()
        )
        lines += report.terminal_matrix(
            benchmark,
            _display_path(skills_root.parent / "benchmark.md"),
            # pytest's writer already folds in TTY detection, CI, and NO_COLOR.
            color=config.get_terminal_writer().hasmarkup,
        )

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


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    """Print binder corpus summary lines in pytest output."""
    lines = config.stash.get(_SUMMARY_LINES, [])
    if not lines:
        return
    terminalreporter.write_sep("=", "benchspec benchmark")
    for line in lines:
        terminalreporter.line(line)


def _display_path(path: Path) -> Path:
    """Show a path relative to the invocation directory when it sits underneath it."""
    invocation_dir = Path.cwd()
    return path.relative_to(invocation_dir) if path.is_relative_to(invocation_dir) else path


_EVAL_FILE = pytest.StashKey[Path]()


class _AuthoredCell(pytest.Function):
    """A parametrized eval cell whose reported location is its authored eval file.

    pytest mints every cell as a plain `Function` from the one `cases.test_eval`, and
    `reportinfo` is the only seam it offers for an item's location, so a collected cell
    is re-classed to this subclass with its eval file stashed.
    """

    def reportinfo(self) -> tuple[Path, None, str]:
        """Return `(path, lineno, domain)` pointing at the eval file, not the wrapper."""
        return self.stash[_EVAL_FILE], None, self.name


def _eval_arm_param(value: object) -> tuple[EvalCase, Arm]:
    """The `(case, arm)` pair behind an `eval_arm` param; any other shape is a plugin bug."""
    if isinstance(value, tuple) and len(value) == 2:
        eval_case, arm = value
        if isinstance(eval_case, EvalCase) and isinstance(arm, Arm):
            return eval_case, arm

    raise TypeError(f"eval_arm param must be an (EvalCase, Arm) pair, got {value!r}")


def pytest_itemcollected(item: pytest.Item) -> None:
    """Attribute each eval cell to its authored `.eval.md` instead of the wrapper module.

    Every cell is the one parametrized `cases.test_eval`, so pytest would otherwise file
    all progress, nodeids, and report locations under `cases.py`. The nodeid keeps its
    `test_eval[<group>-<eval_id>-<arm>]` tail, so `-k`, `--collect-only`, and verbose
    lines still identify each eval × arm cell; only the path part moves.
    """
    params = _callspec_params(item)
    if not isinstance(item, pytest.Function) or "eval_arm" not in params:
        return

    eval_case, _arm = _eval_arm_param(params["eval_arm"])
    eval_file = eval_case.eval_file

    # WARNING: must run before anything reads item.location — it is cached on first
    # access. pytest derives the progress path from the nodeid (not reportinfo), and
    # nodeid has no public setter; _nodeid is the same slot Node.__init__ fills.
    relative = os.path.relpath(eval_file, item.config.rootpath).replace(os.sep, "/")
    item._nodeid = f"{relative}::{item.name}"

    item.stash[_EVAL_FILE] = eval_file
    item.__class__ = _AuthoredCell


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize pytest items from discovered benchspec cases."""
    if "eval_arm" in metafunc.fixturenames:
        pairs, ids = cases.eval_arm_params(metafunc.config)
        metafunc.parametrize("eval_arm", pairs, ids=ids)
