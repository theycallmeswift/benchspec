"""Pytest plugin: turn discovered eval files into parametrized `(eval × arm)` tests.

Registers options, parametrizes the single `eval_arm` fixture over `(case, arm)` pairs,
and picks one iteration-dir name on the controller that all xdist workers share. The
test bodies and fixtures live in `cases.py`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

import evalspec
from evalspec.agents import resolve_agent_name
from evalspec.config.arms import Set as EvalSet
from evalspec.config.sets import has_configured_set, resolved_judge_config, run_set_when_needed
from evalspec.grading.binder import binder_identity
from evalspec.grading.judges import JudgeConfig
from evalspec.grading.judges.registry import probe_judge_version
from evalspec.orchestration import workspace
from evalspec.reporting import report
from evalspec.sandbox.provenance import RuntimeProvenance, aggregate_observed
from evalspec.specs.discovery import (
    discover_eval_cases,
    pyproject_table,
    resolve_eval_paths,
    resolve_repo_root,
)

_CASES = Path(__file__).parent.parent / "orchestration" / "cases.py"

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


def _git_commit(repo_root: Path) -> str | None:
    """Provide the git commit helper."""
    # The repo under test isn't guaranteed to be a git repo (vaults often aren't);
    # a null commit beats a crashed run.
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def build_manifest(
    *,
    run_id: str,
    started_at: str | None,
    commit: str | None,
    iteration: str,
    cfg: dict,
    observed_arms: dict,
) -> dict:
    """Assemble the run manifest from already-resolved identity + config values.

    Pure: the uuid/clock/git/agent reads happen in the caller, so the manifest shape
    (and the order-independent config_hash) is testable by value without IO.

    `cfg` is the planned configuration (set/runner/arms/judge/binder). `config_hash`
    hashes only the planned selectors, so it stays stable across runs of identical config.
    `observed_arms` is runtime observation aggregated from persisted records; it lands
    top-level and is deliberately EXCLUDED from `config_hash` — folding what ran into a
    config identity would make two runs of one config hash differently. `judge.actual_version`
    is the same kind of host-probed observation, so it is stripped before hashing too (the
    full judge object, `actual_version` included, is still emitted). `arms[].requested_version`
    stays hashed — it is a configured install selector, not a probe.
    """
    return {
        "format_version": 2,
        "run_id": run_id,
        "commit": commit,
        "config_hash": _config_hash(cfg),
        "iteration": iteration,
        "started_at": started_at,
        "evalspec_version": evalspec.__version__,
        "observed_arms": observed_arms,
        **cfg,
    }


def _config_hash(cfg: dict) -> str:
    """Hash the planned configuration, excluding host-probed runtime observations.

    `judge.actual_version` is a probe of the host judge binary, not a configured value, so
    it varies with where the run happens rather than with the config. Stripping it keeps
    two runs of one config hash-identical even when their judge binary differs or is absent.
    """
    judge = cfg.get("judge")
    if isinstance(judge, dict) and "actual_version" in judge:
        planned_judge = {key: value for key, value in judge.items() if key != "actual_version"}
        cfg = {**cfg, "judge": planned_judge}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def _judge_meta(judge_config: JudgeConfig) -> dict:
    """The resolved judge, structurally shaped for meta.json.

    `actual_version` is the host-side probe of the judge binary (the judge runs on the
    host, not in a guest snapshot), distinct from every arm's guest-probed
    `observed_arms[arm].actual_version`. Best-effort: a missing binary probes to null.
    """
    return {
        "harness": judge_config.harness,
        "model": judge_config.model,
        "effort": judge_config.effort,
        "timeout": judge_config.timeout,
        "env": report.redact_env(judge_config.env),
        "harness_args": judge_config.harness_args,
        "actual_version": probe_judge_version(judge_config.harness),
    }


def _write_manifest(
    config: object,
    iteration_root: Path,
    iteration: str,
    repo_root: Path,
    run_set: EvalSet | None,
    judge_meta: dict,
    observed_arms: dict,
) -> None:
    """What produced this run — identity + resolved config, so artifacts self-describe.

    Identity is run_id/commit/config_hash; an aggregator can join multi-arm runs on
    metadata alone. Thin shell: read nondeterministic identity, hand off to pure
    `build_manifest`.

    meta.json v2 separates planned config from observed execution. `arms` is the COMPLETE
    configured roster (including arms that never ran, each carrying its own install
    selector + capabilities via `report.planned_arms`); `observed_arms` holds only the
    arms with a persisted runtime record, aggregated by the caller and never synthesized
    here. The v1 run-level `agent`/`agent_version`/`token_split` fields are gone with no
    aliases — the selector and capabilities live per planned arm. `run_set` is None for a
    run with no eval set — `set`/`runner` degrade to null and `arms` to empty. `judge_meta`
    is the already-resolved judge object (see `_judge_meta`); `binder` is the fixed
    run-level binder transport identity (no key material).
    """
    cfg = {
        "set": run_set.name if run_set else None,
        "runner": run_set.runner if run_set else None,
        "arms": report.planned_arms(run_set),
        "judge": judge_meta,
        "binder": binder_identity(),
    }
    manifest = build_manifest(
        run_id=uuid.uuid4().hex,
        started_at=config.stash.get(_STARTED_AT, None),
        commit=_git_commit(repo_root),
        iteration=iteration,
        cfg=cfg,
        observed_arms=observed_arms,
    )
    (iteration_root / "meta.json").write_text(json.dumps(manifest, indent=2) + "\n")


def _aggregate_observed_arms(skills_root: Path, run_set: EvalSet | None) -> dict:
    """Aggregate every persisted `provenance.json` under the skills root by arm.

    Walks for the runtime records execution wrote beside each sample (`provenance.json`
    sits next to `grading.json`), reloads them, and folds them into the `observed_arms`
    mapping. Only arms with a persisted record appear — nothing is synthesized for a
    deselected, skipped, or never-sampled arm. Identical records for one arm dedupe;
    records that disagree on snapshot/digest/version raise loudly (see `aggregate_observed`)
    rather than silently combining unlike environments into one report.

    Two defensive checks run per record before aggregation, so a corrupt or mislocated
    label can't quietly key a column: the record's `arm` must match the arm name in its
    own path (the `<arm>` component of `…/<arm>/sample-K/provenance.json`, per
    `workspace.arm_dir`), and it must belong to the configured roster. A trigger-only run
    resolves no set (`run_set is None`), so roster validation is skipped there.
    """
    roster = None if run_set is None else {arm["name"] for arm in report.planned_arms(run_set)}
    records = []
    for provenance_path in sorted(skills_root.rglob("provenance.json")):
        record = RuntimeProvenance.from_disk_dict(json.loads(provenance_path.read_text()))
        dir_arm = provenance_path.parent.parent.name
        if record.arm != dir_arm:
            raise ValueError(
                f"provenance arm mismatch in `{provenance_path}`: record arm `{record.arm}` "
                f"disagrees with its directory arm `{dir_arm}`"
            )
        if roster is not None and record.arm not in roster:
            raise ValueError(
                f"provenance arm `{record.arm}` in `{provenance_path}` is not in the "
                f"configured roster {sorted(roster)}"
            )
        records.append(record)
    return aggregate_observed(records)


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
    judge_meta = _judge_meta(judge_config)
    # Aggregate observed provenance BEFORE writing the manifest — the records live under
    # the skills root the same walk below reads, so meta.json must not be written with a
    # stale/empty observed_arms. Conflicting records raise here and abort the write, which
    # is the intended loud failure.
    observed_arms = _aggregate_observed_arms(skills_root, run_set)
    _write_manifest(
        config, skills_root.parent, iteration, repo_root, run_set, judge_meta, observed_arms
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
        lines.append(report.delta_line(iteration, benchmark, skills_root.parent / "benchmark.md"))

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
    fixtures = metafunc.fixturenames
    if "eval_arm" in fixtures:
        # One eval set per run — uniform columns across every skill (resolved once, not
        # per skill). Parametrize the single `eval_arm` fixture over `(case, arm)` pairs.
        repo_root = resolve_repo_root(metafunc.config)
        cases = discover_eval_cases(repo_root, resolve_eval_paths(metafunc.config))
        # Resolve the set only when there are eval cases to cross with — an empty
        # collection has no eval_arm pairs and must not require an eval-set pyproject.
        run_set = run_set_when_needed(metafunc.config, needs_set=bool(cases))
        arms = run_set.arms if run_set else []
        if cases:
            # Structural judge preflight — before ANY paid task arm runs. Raises
            # pytest.UsageError at collection on a bad config; binary-on-PATH is
            # checked separately, later, only when tests actually execute.
            resolved_judge_config(metafunc.config)
        pairs = []
        ids = []
        for case in cases:
            for arm in arms:
                pairs.append((case, arm))
                ids.append(f"{case.param_id}-{arm.name}")
        metafunc.parametrize("eval_arm", pairs, ids=ids)
