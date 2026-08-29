"""Assemble the run manifest (meta.json): identity, planned config, observed arms."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import harnessbench
from harnessbench.config.arms import Set as EvalSet
from harnessbench.grading.binder import binder_identity
from harnessbench.grading.judges import JudgeConfig
from harnessbench.grading.judges.registry import probe_judge_version
from harnessbench.reporting import report
from harnessbench.sandbox.provenance import RuntimeProvenance, aggregate_observed


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
        "harnessbench_version": harnessbench.__version__,
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


def judge_meta(judge_config: JudgeConfig) -> dict:
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


def write_manifest(
    iteration_root: Path,
    iteration: str,
    repo_root: Path,
    run_set: EvalSet | None,
    judge_meta: dict,
    observed_arms: dict,
    *,
    started_at: str | None,
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
    is the already-resolved judge object (see `judge_meta`); `binder` is the fixed
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
        started_at=started_at,
        commit=_git_commit(repo_root),
        iteration=iteration,
        cfg=cfg,
        observed_arms=observed_arms,
    )
    (iteration_root / "meta.json").write_text(json.dumps(manifest, indent=2) + "\n")


def aggregate_observed_arms(skills_root: Path, run_set: EvalSet | None) -> dict:
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
