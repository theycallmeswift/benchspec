"""Tests for harnessbench.reporting.manifest.

Manifest assembly and observed-arm aggregation by value.
"""

from __future__ import annotations

import json

from harnessbench.orchestration import workspace
from harnessbench.reporting import manifest
from harnessbench.sandbox.provenance import ImageIdentity, RuntimeProvenance, SandboxProvenance
from tests.support import seed_arm


def test_build_manifest_assembles_shape_by_value() -> None:
    """Verify build manifest assembles shape by value."""
    # The pure assembler is testable by value (no uuid/clock/git IO) — the shell
    # injects identity. Pins the spread of cfg and the hash, which the IO-bound
    # sessionfinish test below can only presence-check.
    cfg = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline", "requested_version": "latest"}],
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
            "actual_version": "1.2.3",
        },
        "binder": {"provider": "gemini", "model": "x", "api_path": "y"},
    }
    observed = {"baseline": {"actual_version": "1.2.3", "actual_version_status": "available"}}

    result = manifest.build_manifest(
        run_id="r" * 32,
        started_at="2026-06-13T00:00:00+00:00",
        commit="abc123",
        iteration="iteration_07",
        cfg=cfg,
        observed_arms=observed,
    )

    assert result["format_version"] == 2
    assert result["run_id"] == "r" * 32
    assert result["commit"] == "abc123"
    assert result["started_at"] == "2026-06-13T00:00:00+00:00"
    assert result["iteration"] == "iteration_07"
    assert result["set"] == "default"  # cfg spread in
    assert result["runner"] == "pytest"
    assert result["observed_arms"] == observed
    assert len(result["config_hash"]) == 12
    assert result["judge"]["harness"] == "claude-code"  # nested judge spread in whole
    assert result["binder"]["provider"] == "gemini"
    # No v1 run-level identity fields survive.
    assert "agent" not in result
    assert "agent_version" not in result
    assert "token_split" not in result


def test_build_manifest_config_hash_excludes_observed_arms() -> None:
    """Verify config_hash hashes only cfg, never the runtime observation."""
    # config_hash must be stable across runs of one config; folding observed_arms into it
    # would make two runs of the same config hash differently. Same cfg + different
    # observed_arms ⇒ identical config_hash.
    cfg = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {"harness": "claude-code"},
        "binder": {"provider": "gemini"},
    }

    manifest_a = manifest.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        observed_arms={"baseline": {"actual_version": "1.0.0"}},
    )
    manifest_b = manifest.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=cfg,
        observed_arms={},
    )

    assert manifest_a["config_hash"] == manifest_b["config_hash"]


def test_build_manifest_config_hash_ignores_judge_actual_version() -> None:
    """Verify the host-probed judge actual_version never shifts config_hash."""
    # actual_version is a probe of the host judge binary, not a configured selector; two
    # runs of one config on machines whose judge binary differs (or is absent → null) must
    # still hash identically. The full judge object, actual_version included, is still emitted.
    base_judge = {"harness": "claude-code", "model": "sonnet", "effort": "medium"}
    cfg_probed = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {**base_judge, "actual_version": "1.2.3"},
        "binder": {"provider": "gemini"},
    }
    cfg_null = {**cfg_probed, "judge": {**base_judge, "actual_version": None}}

    probed = manifest.build_manifest(
        run_id="a" * 32, started_at="t", commit="c", iteration="i",
        cfg=cfg_probed, observed_arms={},
    )
    null = manifest.build_manifest(
        run_id="b" * 32, started_at="t", commit="c", iteration="i",
        cfg=cfg_null, observed_arms={},
    )

    assert probed["config_hash"] == null["config_hash"]
    assert probed["judge"]["actual_version"] == "1.2.3"  # still emitted in the output


def test_build_manifest_config_hash_is_order_independent() -> None:
    """Verify build manifest config hash is order independent."""
    # config_hash hashes cfg with sort_keys, so two cfgs that differ only in key
    # order (and in the non-cfg identity fields) hash identically.
    cfg = {
        "set": "default",
        "runner": "pytest",
        "arms": [{"name": "baseline"}],
        "judge": {
            "harness": "claude-code",
            "model": "sonnet",
            "effort": "medium",
            "timeout": 300,
            "env": {},
            "harness_args": [],
            "actual_version": None,
        },
        "binder": {"provider": "gemini", "model": "x", "api_path": "y"},
    }
    reordered = dict(reversed(list(cfg.items())))

    manifest_a = manifest.build_manifest(
        run_id="a" * 32,
        started_at="t1",
        commit="c1",
        iteration="iteration_01",
        cfg=cfg,
        observed_arms={},
    )
    manifest_b = manifest.build_manifest(
        run_id="b" * 32,
        started_at="t2",
        commit="c2",
        iteration="iteration_99",
        cfg=reordered,
        observed_arms={},
    )

    assert manifest_a["config_hash"] == manifest_b["config_hash"]


def _seed_provenance(
    sample_dir: object,
    arm: object,
    *,
    actual_version: object = "1.2.3",
    snapshot: object = "snap-abc",
    digest: object = "sha256:dead",
) -> None:
    """Write a `provenance.json` beside a seeded sample's grading.json.

    Mirrors what execution persists per sample, so the sessionfinish aggregation walk
    (`skills_root.rglob('provenance.json')`) picks it up into `observed_arms`.
    """
    record = RuntimeProvenance(
        arm=arm,
        actual_version=actual_version,
        actual_version_status="available",
        actual_version_error=None,
        sandbox=SandboxProvenance.from_image_identity(
            backend="microsandbox",
            snapshot=snapshot,
            fingerprint="ab12cd34",
            base_image_ref="ubuntu:latest",
            install_fingerprint="if-sha",
            env_script_sha256="env-sha",
            image_identity=ImageIdentity.available(digest),
        ),
    )
    (sample_dir / "provenance.json").write_text(json.dumps(record.to_disk_dict()))


def test_aggregate_observed_arms_trigger_only_skips_roster(tmp_path: object) -> None:
    """A trigger-only run (run_set None) aggregates without roster validation."""
    # No eval set resolves for a trigger-only run, so there is no roster to check against;
    # the walk must still aggregate the record rather than reject every arm.
    workspace.set_current_iteration("iteration_01")
    skills_root = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"
    ghost_sample = seed_arm(skills_root / "archive", "alpha", "ghost", passes=1, total=1)
    _seed_provenance(ghost_sample, "ghost")

    observed = manifest.aggregate_observed_arms(skills_root, None)

    assert set(observed) == {"ghost"}  # off-roster arm accepted when no set is configured
