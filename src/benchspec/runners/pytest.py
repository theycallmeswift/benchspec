"""Pytest plugin: turn discovered eval files into parametrized `(eval × arm)` tests.

Registers options, parametrizes the single `eval_arm` fixture over `(case, arm)` pairs,
and picks one iteration-dir name on the controller that all xdist workers share. The
test bodies and fixtures live in `cases.py`.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
from collections.abc import Generator, MutableMapping, Sequence
from pathlib import Path
from typing import Protocol

import pytest
from dotenv import find_dotenv, load_dotenv

from benchspec.agents import resolve_agent_name
from benchspec.config.arms import Arm
from benchspec.config.options import option_float, option_str
from benchspec.config.progress import PROGRESS_STYLE, resolve_progress_style
from benchspec.config.sets import (
    has_configured_set,
    resolved_binder_config,
    resolved_judge_config,
    resolved_run_set,
    run_set_when_needed,
    session_run_set,
    validate_cell_caps,
)
from benchspec.grading import binder
from benchspec.grading.binder import binder_identity
from benchspec.grading.binder_config import BinderConfig
from benchspec.grading.judges import JudgeConfig
from benchspec.orchestration import cases, prebind, results, workspace
from benchspec.reporting import manifest, report
from benchspec.reporting.cells import (
    CELL_PROPERTY,
    RESULT_PROPERTY,
    CellIdentity,
    CellResult,
    format_cell_line,
)
from benchspec.specs.discovery import EvalCase, pyproject_table, resolve_repo_root

_CASES = Path(cases.__file__)

_STARTED_AT = pytest.StashKey[str]()
# Set once any eval cell reports, so session end knows the iteration is this run's.
_RAN_CELLS = pytest.StashKey[bool]()
_SUMMARY_LINES = pytest.StashKey[list[str]]()
_BIND_SUMMARY = pytest.StashKey[str]()


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
        help="scalar override of the selected set's `timeout` default: the cap on one whole "
        "cell (bind, boot, agent, judge), seconds (default: 900, or the set/arm `timeout`)",
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
        "--benchspec-progress",
        default=None,
        metavar="{cells,dots}",
        help="live progress format: `cells` prints one line per finished sample, `dots` "
        "keeps pytest's own progress line (default: cells, or [tool.benchspec] "
        "progress). Must default to None so an unset flag never shadows the config key.",
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
    return _repeat_step(_callspec_params(request.node))


def _repeat_step(params: dict[str, object]) -> int:
    """The pytest-repeat step behind a collected item; 0 without `--count`."""
    step = params.get("__pytest_repeat_step_number", 0)

    return step if isinstance(step, int) else 0


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Record an eval sample's identity on its reports before any fixture runs.

    Ahead of setup, so a sample whose fixture fails or whose arm raises still names itself
    in the progress line. A JSON string, because xdist ships properties through execnet.
    Only under the `cells` format: `dots` reports carry nothing extra.
    """
    params = _callspec_params(item)
    if "eval_arm" not in params or item.get_closest_marker("benchspec") is None:
        return
    if item.config.stash[PROGRESS_STYLE] != "cells":
        return

    eval_case, arm = _eval_arm_param(params["eval_arm"])
    identity = CellIdentity(
        skill=eval_case.skill,
        eval_id=eval_case.eval_id,
        arm=arm.name,
        sample=_repeat_step(params),
    )
    item.user_properties.append((CELL_PROPERTY, identity.to_json()))


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
    project_table = pyproject_table(repo_root)
    try:
        agent_name = resolve_agent_name(
            option_str(config, "benchspec_agent"),
            project_table.get("agent"),
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

    config.stash[PROGRESS_STYLE] = resolve_progress_style(
        option_str(config, "benchspec_progress"), project_table.get("progress")
    )

    workerinput = prebind.workerinput(config)
    if workerinput is not None:
        # xdist worker: adopt the iteration name the controller chose.
        workspace.set_current_iteration(str(workerinput["benchspec_iteration"]))
    else:
        # Controller / serial run: choose once.
        workspace.set_current_iteration(workspace.next_iteration_name(repo_root))
        config.stash[_STARTED_AT] = datetime.datetime.now(datetime.UTC).isoformat(
            timespec="seconds"
        )
        config.pluginmanager.register(_CellTracker(config), "benchspec-cell-tracker")
        if config.stash[PROGRESS_STYLE] == "cells":
            config.pluginmanager.register(_CellProgress(config), "benchspec-cell-progress")
        _validate_cell_caps(config)
        _prebind_assertions(config)


def _validate_cell_caps(config: pytest.Config) -> None:
    """Refuse the run when an arm's cell cap does not exceed the judge timeout.

    Runs on the controller for every run with eval cases, so each invocation path reports
    one usage error before any worker or cell starts.

    Raises:
        pytest.UsageError: an arm's `timeout` is at or below the judge's.
    """
    run_set = session_run_set(config)
    if run_set is not None:
        validate_cell_caps(run_set, resolved_judge_config(config))


def _targets_cases(config: pytest.Config) -> bool:
    """Whether this run's targets include the eval cases module, the only home of `test_eval`."""
    return any(Path(arg.split("::")[0]).resolve() == _CASES for arg in config.args)


def _prebind_assertions(config: pytest.Config) -> None:
    """Bind each unique active assertion once, on the controller, before any cell starts.

    Skipped when no `test_eval` cell follows: `--collect-only`, a run that targets something
    other than the cases module (the binder corpus), no eval cases, or no set.

    Raises:
        pytest.UsageError: the grading environment (a missing or empty binder key) is
            unusable, or the binder rejected its key.
    """
    if config.option.collectonly or not _targets_cases(config):
        return

    pairs, _ = cases.eval_arm_params(config)
    if not pairs:
        return

    errors = cases.grading_preflight_errors(config)
    if errors:
        raise pytest.UsageError("\n".join(errors))

    texts = prebind.unique_active_texts(
        pairs,
        baseline=resolved_run_set(config).baseline,
        eval_set=option_str(config, "benchspec_set") or "",
        today=results.utc_today(),
    )
    started = time.perf_counter()
    try:
        table = prebind.bind_unique(texts, binder.binder_for(resolved_binder_config(config)))
    except binder.BinderAuthError as error:
        raise pytest.UsageError(str(error)) from None

    config.stash[cases.BOUND_SPECS] = table
    config.stash[_BIND_SUMMARY] = (
        f"benchspec: bound {len(table)} of {len(texts)} unique assertions "
        f"in {time.perf_counter() - started:.1f} s"
    )


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Report the pre-bind pass in the session header, where the terminal reporter exists."""
    summary = config.stash.get(_BIND_SUMMARY, None)

    return [summary] if summary else []


class _CellTracker:
    """Flags the config once an eval cell reports, on the process that finishes the session.

    Under xdist the controller collects nothing itself; it learns that cells ran only from
    the reports the workers send, which carry the `benchspec` marker as a keyword.
    """

    def __init__(self, config: pytest.Config) -> None:
        """Bind to the config whose stash carries the flag."""
        self._config = config

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Flag the session once any eval cell reports."""
        if "benchspec" in report.keywords:
            self._config.stash[_RAN_CELLS] = True


# pytest-repeat appends `<step>-<count>` to a repeated item's parameter id.
_REPEAT_ID_SUFFIX = re.compile(r"-(\d+)-\d+$")

_ITEM_PHASES = frozenset({"setup", "call", "teardown"})


def _param_id(nodeid: str) -> str:
    """The bracketed parameter id at the end of a node id; empty for an unparametrized one."""
    _path, bracket, tail = nodeid.rpartition("[")

    return tail.removesuffix("]") if bracket and tail.endswith("]") else ""


class _CellProgress:
    """Prints one line per finished sample on the process that owns the terminal.

    Registered on the controller (or the serial process) when the progress format is
    `cells`. A worker's report carries the sample's identity and result as properties,
    so a serial run and an xdist run share this one path.
    """

    def __init__(self, config: pytest.Config) -> None:
        """Bind to the config whose terminal reporter the lines go through."""
        self._config = config
        self._finished = 0
        self._collected_ids: list[str] | None = None
        self._total: int | None = None
        self._planned: dict[str, tuple[EvalCase, Arm]] | None = None
        self._label_width = 0
        self._arm_width = 0
        self._being_logged: pytest.TestReport | None = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Remember a serial run's collected eval cells, the counter's denominator."""
        if self._collected_ids is not None:
            return

        self._collected_ids = [
            item.nodeid for item in session.items if item.get_closest_marker("benchspec")
        ]
        reporter = self._reporter()
        if self._collected_ids and reporter is not None:
            # The cell lines name each sample, so pytest's per-file path would only
            # split them up. xdist turns it off the same way.
            reporter.showfspath = False

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: object, ids: Sequence[str]) -> None:
        """Remember the node ids every xdist worker collected, the counter's denominator."""
        self._collected_ids = list(ids)

    @pytest.hookimpl(tryfirst=True)
    def pytest_report_teststatus(self, report: pytest.TestReport) -> tuple[str, str, str] | None:
        """Drop pytest's progress letter for an eval sample, keeping its count in the footer.

        Every phase, so a failing teardown leaves no stray letter either. Only while the
        report is being logged: the summaries pytest prints at session end ask for the
        status again and still get `FAILED` and `ERROR`.
        """
        if report is not self._being_logged or report.skipped:
            return None
        if self._identity(report) is None:
            return None
        if report.passed and report.when != "call":
            return None

        category = "error" if report.when in ("setup", "teardown") else report.outcome

        return category, "", ""

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> Generator[None, None, None]:
        """Print the line for a sample that just finished, once pytest has logged its report."""
        self._being_logged = report
        try:
            yield
        finally:
            self._being_logged = None

        self._print_line(report)

    def _print_line(self, report: pytest.TestReport) -> None:
        """Print one line for a report that finishes an applicable sample."""
        reporter = self._reporter()
        cell = self._identity(report)
        if reporter is None or cell is None or not _ends_sample(report):
            return

        recorded_result = dict(report.user_properties).get(RESULT_PROPERTY)
        result = CellResult.from_json(str(recorded_result)) if recorded_result else None
        if result is not None and result.not_applicable:
            return

        self._plan()
        self._finished += 1
        line = format_cell_line(
            index=self._finished,
            total=self._total,
            cell=cell,
            result=result,
            wall_seconds=report.duration,
            failed_report=report.failed,
            message=_failure_message(report),
            label_width=self._label_width,
            arm_width=self._arm_width,
            color=self._config.get_terminal_writer().hasmarkup,
        )

        # Under -v pytest has already written the item's own line and left it open.
        if self._config.get_terminal_writer().width_of_current_line:
            reporter.line("")
        reporter.line(line)

    def _reporter(self) -> pytest.TerminalReporter | None:
        """The terminal reporter the lines print through; None when it is disabled."""
        reporter = self._config.pluginmanager.get_plugin("terminalreporter")

        return reporter if isinstance(reporter, pytest.TerminalReporter) else None

    def _identity(self, report: pytest.TestReport) -> CellIdentity | None:
        """Which eval sample a report belongs to; None when it is not one."""
        recorded = dict(report.user_properties).get(CELL_PROPERTY)
        if recorded:
            return CellIdentity.from_json(str(recorded))
        if report.when in _ITEM_PHASES:
            return None

        # xdist reports a sample whose worker died with no properties, only its node id.
        return self._planned_identity(report.nodeid)

    def _planned_identity(self, nodeid: str) -> CellIdentity | None:
        """Name the planned sample a node id stands for; None when it is not an eval sample."""
        if self._collected_ids is None or nodeid not in self._collected_ids:
            return None

        param_id = _param_id(nodeid)
        planned = self._plan()
        repeat = _REPEAT_ID_SUFFIX.search(param_id)
        sample = 0
        if param_id not in planned and repeat:
            param_id = param_id[: repeat.start()]
            sample = int(repeat.group(1)) - 1
        if param_id not in planned:
            return None

        case, arm = planned[param_id]

        return CellIdentity(skill=case.skill, eval_id=case.eval_id, arm=arm.name, sample=sample)

    def _plan(self) -> dict[str, tuple[EvalCase, Arm]]:
        """Work out the planned cells, the counter's total and the column widths, once.

        Deferred to the first finished sample so a session that runs no eval cells never
        resolves an eval set.
        """
        if self._planned is not None:
            return self._planned

        pairs, ids = cases.eval_arm_params(self._config)
        self._planned = dict(zip(ids, pairs, strict=True))
        self._label_width = max(
            (len(f"{case.skill}/eval-{case.eval_id}") for case, _arm in pairs), default=0
        )
        self._arm_width = max((len(arm.name) for _case, arm in pairs), default=0)

        if self._collected_ids is not None:
            not_applicable = cases.not_applicable_param_ids(self._config, pairs, ids)
            self._total = sum(
                1
                for nodeid in self._collected_ids
                if _param_id(nodeid) not in not_applicable
                and _REPEAT_ID_SUFFIX.sub("", _param_id(nodeid)) not in not_applicable
            )

        return self._planned


def _ends_sample(report: pytest.TestReport) -> bool:
    """Whether a report is the one that finishes an eval sample.

    A sample ends on its call report, on a failed setup report (a fixture error ends it
    with no call phase), or on the report xdist writes for a worker that died. A skipped
    report is left to pytest, and a failing teardown follows a call that already ended it.
    """
    if report.skipped or report.when == "teardown":
        return False

    return report.when == "call" or report.failed


def _failure_message(report: pytest.TestReport) -> str:
    """The exception message behind a failed report; empty for a passing one."""
    if not report.failed:
        return ""

    if isinstance(report.longrepr, str):
        return report.longrepr

    crash = getattr(report.longrepr, "reprcrash", None)
    message = getattr(crash, "message", None)

    return message if isinstance(message, str) and message else report.longreprtext


class _WorkerNode(Protocol):
    """The xdist controller-side node whose `workerinput` is serialized to its worker."""

    @property
    def config(self) -> pytest.Config:
        """The controller's config."""
        ...

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
    node.workerinput[prebind.WORKERINPUT_KEY] = node.config.stash.get(cases.BOUND_SPECS, {})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Aggregate binder corpus records after the pytest session."""
    # Controller-only, and only when this session ran eval cells: a session that ran none
    # (`--collect-only`, or an unrelated pytest run with the plugin autoloaded) picked an
    # iteration name it never used, and another process may have filled that name in the
    # meantime. Runs before pytest_terminal_summary (plain impls fire inside
    # TerminalReporter's wrapper), so the summary can read what this wrote and a CI gate
    # can still change session.exitstatus.
    config = session.config
    if prebind.workerinput(config) is not None or not config.stash.get(_RAN_CELLS, False):
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
