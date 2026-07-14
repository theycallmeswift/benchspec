"""Contract tests for the frozen E2E artifact verifier."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

from evalspec.binder import binder_identity
from evalspec.provenance import ImageIdentity, RuntimeProvenance, SandboxProvenance

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/verify_e2e_artifacts.py"
SPEC = importlib.util.spec_from_file_location("verify_e2e_artifacts", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

EXPECTED_ARMS = [
    {
        "name": "baseline",
        "harness": "claude-code",
        "model": "sonnet",
        "effort": "medium",
        "env": {"GREETING_STYLE": "formal", "GREETING_LOCALE": "en-US"},
        "harness_args": [],
        "requested_version": "latest",
        "capabilities": {"token_split": True},
    },
    {
        "name": "trial",
        "harness": "claude-code",
        "model": "sonnet",
        "effort": "medium",
        "env": {"GREETING_STYLE": "formal", "GREETING_LOCALE": "en-US"},
        "harness_args": [],
        "requested_version": "latest",
        "capabilities": {"token_split": True},
    },
    {
        "name": "trial-overrides",
        "harness": "claude-code",
        "model": "opus",
        "effort": "high",
        "env": {"GREETING_STYLE": "formal", "GREETING_LOCALE": "en-GB"},
        "harness_args": [],
        "requested_version": "latest",
        "capabilities": {"token_split": True},
    },
]
EXPECTED_EVALS = ("greets-by-name", "writes-greeting-file")


def _write_valid_iteration(root: Path, name: str = "iteration_02") -> Path:
    """Write a minimal valid six-cell E2E iteration."""
    iteration = root / name
    iteration.mkdir(parents=True)
    provenance_record = RuntimeProvenance(
        arm="placeholder",
        actual_version="1.0",
        actual_version_status="available",
        actual_version_error=None,
        sandbox=SandboxProvenance.from_image_identity(
            backend="microsandbox", snapshot="test", fingerprint="fingerprint",
            base_image_ref="ubuntu:24.04", install_fingerprint="install",
            env_script_sha256="env-hash", image_identity=ImageIdentity.available("sha256:image"),
        ),
    )
    provenance = provenance_record.to_observed_dict()
    observed = {arm["name"]: provenance for arm in EXPECTED_ARMS}
    binder = binder_identity()
    meta = {
        "format_version": 2,
        "run_id": "a" * 32,
        "commit": "abc123",
        "config_hash": "abc123def456",
        "iteration": name,
        "started_at": "2026-07-13T00:00:00Z",
        "evalspec_version": "0.1.0",
        "set": "e2e",
        "runner": "pytest",
        "arms": EXPECTED_ARMS,
        "observed_arms": observed,
        "judge": {
            "harness": "codex", "model": "gpt-5.5", "effort": "medium",
            "timeout": 300, "env": {}, "harness_args": [], "actual_version": "1.0",
        },
        "binder": binder,
    }
    benchmark = {
        "format_version": 3,
        "label": name,
        "roster": [{"group": "hello", "eval_id": eval_id} for eval_id in EXPECTED_EVALS],
        "arms": {
            arm["name"]: {
                "pass_rate": 1.0, "pass_rate_stdev": 0.0, "duration_ms_mean": 1,
                "duration_ms_stdev": 0.0, "judge_ms_mean": 1, "tokens_mean": 1,
                "tokens_stdev": 0.0, "errored_samples": 0, "binder_degraded": 0,
                "n": 2,
                "per_eval": [
                    {"group": "hello", "eval_id": eval_id, "samples": 1,
                     "errored_samples": 0, "passed_total": 1, "total_total": 1,
                     "pass_rate_mean": 1.0, "pass_rate_stdev": None}
                    for eval_id in EXPECTED_EVALS
                ],
                "harness": arm["harness"], "model": arm["model"],
                "effort": arm["effort"], "env": arm["env"], "harness_args": [],
            }
            for arm in EXPECTED_ARMS
        },
        "planned_arms": EXPECTED_ARMS,
        "observed_arms": observed,
        "runner": "pytest",
        "binder": binder,
        "baseline": "baseline",
        "max_samples": 1,
    }
    (iteration / "meta.json").write_text(json.dumps(meta))
    (iteration / "benchmark.json").write_text(json.dumps(benchmark))
    (iteration / "benchmark.md").write_text(
        "# Benchmark — iteration_02\n## Matrix\nAll evals\n"
        "greets-by-name\nwrites-greeting-file\nbaseline\ntrial\ntrial-overrides\n## Provenance\n"
    )
    rows = []
    for arm in EXPECTED_ARMS:
        for eval_id in EXPECTED_EVALS:
            sample = iteration / "skills" / "hello" / f"eval-{eval_id}" / arm["name"] / "sample-0"
            sample.mkdir(parents=True)
            disk_provenance = RuntimeProvenance(
                arm=arm["name"], actual_version="1.0", actual_version_status="available",
                actual_version_error=None, sandbox=provenance_record.sandbox,
            ).to_disk_dict()
            (sample / "provenance.json").write_text(json.dumps(disk_provenance))
            rows.append(
                {
                    "eval_id": eval_id,
                    "arm": arm["name"],
                    "harness": arm["harness"],
                    "model": arm["model"],
                    "effort": arm["effort"],
                    "sample": 0,
                    "errored": False,
                    "skill": "hello", "kind": "eval", "passed": 1, "total": 1,
                    "duration_ms": 1, "judge_ms": 1, "total_tokens": 1,
                    "input_tokens": 1, "output_tokens": 0,
                }
            )
    (iteration / "index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    return iteration


def test_validates_exact_new_e2e_iteration(tmp_path: Path) -> None:
    """Accept the exact three-arm by two-eval artifact contract."""
    (tmp_path / "iteration_01").mkdir()
    _write_valid_iteration(tmp_path)

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path), "--existing", "iteration_01"])

    assert result == 0


def test_rejects_stale_prior_iteration(tmp_path: Path, capsys: object) -> None:
    """Reject a run that creates no iteration after the pre-run snapshot."""
    _write_valid_iteration(tmp_path, "iteration_01")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path), "--existing", "iteration_01"])

    assert result == 1
    assert "expected exactly one new iteration, found 0" in capsys.readouterr().err


def test_empty_snapshot_selects_exactly_one_new_iteration(tmp_path: Path) -> None:
    """Treat an explicit empty snapshot as snapshot mode."""
    _write_valid_iteration(tmp_path, "iteration_01")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path), "--existing"])

    assert result == 0


def test_snapshot_rejects_multiple_new_iterations(tmp_path: Path, capsys: object) -> None:
    """Reject ambiguous runs that create more than one iteration."""
    _write_valid_iteration(tmp_path, "iteration_01")
    _write_valid_iteration(tmp_path, "iteration_02")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path), "--existing"])

    assert result == 1
    assert "expected exactly one new iteration, found 2" in capsys.readouterr().err


def test_rejects_duplicate_planned_arm(tmp_path: Path, capsys: object) -> None:
    """Reject duplicate names instead of collapsing them into an apparently valid roster."""
    iteration = _write_valid_iteration(tmp_path)
    meta = json.loads((iteration / "meta.json").read_text())
    meta["arms"].append(meta["arms"][0])
    (iteration / "meta.json").write_text(json.dumps(meta))

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    assert result == 1
    assert "duplicate arm" in capsys.readouterr().out


def test_rejects_incorrect_frozen_content_and_status_invariants(
    tmp_path: Path, capsys: object
) -> None:
    """Reject complete-shaped artifacts whose frozen values or invariants are wrong."""
    iteration = _write_valid_iteration(tmp_path)
    meta = json.loads((iteration / "meta.json").read_text())
    benchmark = json.loads((iteration / "benchmark.json").read_text())
    for artifact in (meta["arms"], benchmark["planned_arms"]):
        artifact[0]["harness_args"] = ["--unsafe"]
        artifact[0]["requested_version"] = "old"
        artifact[0]["capabilities"]["token_split"] = "yes"
        artifact[0]["env"]["EXTRA"] = "value"
    meta["binder"]["api_path"] = benchmark["binder"]["api_path"] = "wrong"
    meta["observed_arms"]["baseline"]["actual_version_status"] = "available"
    meta["observed_arms"]["baseline"]["actual_version"] = None
    benchmark["observed_arms"] = meta["observed_arms"]
    benchmark["label"] = "wrong"
    benchmark["baseline"] = None
    benchmark["max_samples"] = 2
    benchmark["arms"]["baseline"]["model"] = "opus"
    benchmark["arms"]["baseline"]["per_eval"][0]["eval_id"] = "wrong"
    (iteration / "meta.json").write_text(json.dumps(meta))
    (iteration / "benchmark.json").write_text(json.dumps(benchmark))
    rows = [json.loads(line) for line in (iteration / "index.jsonl").read_text().splitlines()]
    rows[0]["skill"] = "wrong"
    rows[0]["kind"] = "other"
    rows[0]["passed"] = "1"
    rows[0]["duration_ms"] = "fast"
    (iteration / "index.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    provenance_path = next(iteration.rglob("provenance.json"))
    disk = json.loads(provenance_path.read_text())
    disk["actual_version"] = None
    provenance_path.write_text(json.dumps(disk))

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    output = capsys.readouterr().out
    assert result == 1
    for marker in (
        "configured fields", "binder identity", "status/error", "label/baseline/max_samples",
        "arm 'baseline' axes", "per_eval roster", "result fields", "provenance.json",
    ):
        assert marker in output


def test_rejects_false_token_split_and_wrong_judge_defaults(
    tmp_path: Path, capsys: object
) -> None:
    """Require Claude token splitting and the resolved E2E judge defaults."""
    iteration = _write_valid_iteration(tmp_path)
    meta = json.loads((iteration / "meta.json").read_text())
    benchmark = json.loads((iteration / "benchmark.json").read_text())
    meta["arms"][0]["capabilities"]["token_split"] = False
    benchmark["planned_arms"][0]["capabilities"]["token_split"] = False
    meta["judge"].update({"effort": "high", "timeout": 30, "env": {"X": "1"}})
    meta["judge"]["harness_args"] = ["--unsafe"]
    (iteration / "meta.json").write_text(json.dumps(meta))
    (iteration / "benchmark.json").write_text(json.dumps(benchmark))

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    output = capsys.readouterr().out
    assert result == 1
    assert "configured fields" in output
    assert "judge defaults" in output


def test_invalid_utf8_fails_cleanly(tmp_path: Path, capsys: object) -> None:
    """Report unreadable UTF-8 artifacts without leaking UnicodeError."""
    iteration = _write_valid_iteration(tmp_path)
    (iteration / "index.jsonl").write_bytes(b"\xff")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    assert result == 1
    assert "index.jsonl: unreadable" in capsys.readouterr().out


def test_make_e2e_passes_explicit_empty_snapshot_to_verifier(tmp_path: Path) -> None:
    """Exercise Make's run-to-verifier handoff when no prior iteration exists."""
    fixture_root = tmp_path / "fixture"
    fixture = _write_valid_iteration(fixture_root, "iteration_01")
    shutil.copytree(SCRIPT_PATH.parent, tmp_path / "scripts")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "run evalspec" ]; then\n'
        '  mkdir -p tmp/evals\n  cp -R "$EVALSPEC_FIXTURE" tmp/evals/iteration_01\n  exit 0\n'
        "fi\nshift\nexec \"$@\"\n"
    )
    uv.chmod(0o755)

    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(SCRIPT_PATH.parents[1] / "Makefile"), "e2e"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "EVALSPEC_FIXTURE": str(fixture),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "iteration_01 OK" in result.stdout


def test_rejects_wrong_roster_axes_and_cross_artifact_values(
    tmp_path: Path, capsys: object
) -> None:
    """Reject plausible artifacts that do not describe the frozen E2E matrix."""
    iteration = _write_valid_iteration(tmp_path)
    meta = json.loads((iteration / "meta.json").read_text())
    meta["arms"][0]["model"] = "opus"
    (iteration / "meta.json").write_text(json.dumps(meta))
    rows = [json.loads(line) for line in (iteration / "index.jsonl").read_text().splitlines()]
    rows.pop()
    rows[0]["model"] = "opus"
    (iteration / "index.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    output = capsys.readouterr().out
    assert result == 1
    assert "planned arm roster" in output
    assert "index.jsonl: cell roster" in output
    assert "index.jsonl:1: axes" in output


def test_rejects_missing_per_sample_provenance(tmp_path: Path, capsys: object) -> None:
    """Require provenance beside every indexed graded sample, not merely a total count."""
    iteration = _write_valid_iteration(tmp_path)
    missing = next(iteration.rglob("provenance.json"))
    misplaced = iteration / "unrelated" / "provenance.json"
    misplaced.parent.mkdir()
    missing.rename(misplaced)

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    assert result == 1
    assert "provenance.json: missing for index.jsonl:" in capsys.readouterr().out


def test_errored_index_row_still_requires_valid_provenance(tmp_path: Path, capsys: object) -> None:
    """Validate provenance captured before a later sample error."""
    iteration = _write_valid_iteration(tmp_path)
    rows = [json.loads(line) for line in (iteration / "index.jsonl").read_text().splitlines()]
    rows[0]["errored"] = True
    (iteration / "index.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    provenance = (
        iteration / "skills" / "hello" / f"eval-{rows[0]['eval_id']}"
        / rows[0]["arm"] / "sample-0" / "provenance.json"
    )
    provenance.unlink()

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    assert result == 1
    assert "provenance.json: missing for index.jsonl:1" in capsys.readouterr().out


def test_malformed_nested_types_fail_cleanly(tmp_path: Path, capsys: object) -> None:
    """Report malformed collection members without leaking TypeError tracebacks."""
    iteration = _write_valid_iteration(tmp_path)
    meta = json.loads((iteration / "meta.json").read_text())
    meta["arms"] = [None]
    (iteration / "meta.json").write_text(json.dumps(meta))
    malformed_row = {
        "eval_id": {},
        "arm": [],
        "harness": None,
        "model": None,
        "effort": None,
        "sample": [],
        "errored": False,
    }
    (iteration / "index.jsonl").write_text("[]\n" + json.dumps(malformed_row) + "\n")

    result = verifier.main(["verify_e2e_artifacts.py", str(tmp_path)])

    output = capsys.readouterr().out
    assert result == 1
    assert "meta.json: arms[0] expected a JSON object" in output
    assert "index.jsonl:1: expected a JSON object" in output
