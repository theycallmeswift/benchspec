"""Tests for `scripts/check_sample_accounting.py`, the post-run guard `make e2e` runs.

Each test seeds an iteration tree the way a live run lays it out, runs the real script
as a subprocess from that root, and reads its exit code and verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_sample_accounting.py"

COMPLETE_TIMING = {
    "duration_ms": 12429,
    "judge_ms": 5721,
    "total_tokens": 54186,
    "cache_read_tokens": 22016,
    "cache_creation_tokens": 0,
    "input_tokens": 31582,
    "output_tokens": 523,
}


def _seed_sample(
    root: Path, iteration: str, arm: str, *, timing: dict, errored: bool = False
) -> None:
    """Write one sample's grading.json and timing.json under `root/tmp/evals/<iteration>`."""
    sample_dir = (
        root / "tmp" / "evals" / iteration / "skills" / "hello" / "eval-greets" / arm / "sample-0"
    )
    sample_dir.mkdir(parents=True)
    (sample_dir / "grading.json").write_text(json.dumps({"errored": errored, "assertions": []}))
    (sample_dir / "timing.json").write_text(json.dumps(timing))


def _run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the script from `root`, the way `make e2e` runs it from the repo root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=root, capture_output=True, text=True, check=False
    )


def test_complete_accounting_passes(tmp_path: Path) -> None:
    """Every clean sample carrying time and a token split passes."""
    _seed_sample(tmp_path, "iteration_01", "trial", timing=COMPLETE_TIMING)
    _seed_sample(tmp_path, "iteration_01", "trial-codex", timing=COMPLETE_TIMING)

    result = _run_check(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "sample accounting ok: iteration_01, 2 clean samples" in result.stdout


def test_zero_duration_or_output_tokens_fails_naming_the_sample(tmp_path: Path) -> None:
    """A Codex sample with no duration and an OpenCode sample with no output both fail."""
    _seed_sample(tmp_path, "iteration_01", "trial", timing=COMPLETE_TIMING)
    _seed_sample(
        tmp_path, "iteration_01", "trial-codex", timing={**COMPLETE_TIMING, "duration_ms": 0}
    )
    _seed_sample(
        tmp_path, "iteration_01", "trial-opencode", timing={**COMPLETE_TIMING, "output_tokens": 0}
    )

    result = _run_check(tmp_path)

    assert result.returncode == 1
    assert "trial-codex/sample-0: duration_ms is 0" in result.stdout
    assert "trial-opencode/sample-0: output_tokens is 0" in result.stdout
    assert "hello/eval-greets/trial/sample-0" not in result.stdout


def test_only_the_newest_iteration_is_checked(tmp_path: Path) -> None:
    """A broken older run does not fail the run that just finished."""
    _seed_sample(
        tmp_path, "iteration_09", "trial-codex", timing={**COMPLETE_TIMING, "duration_ms": 0}
    )
    _seed_sample(tmp_path, "iteration_10", "trial-codex", timing=COMPLETE_TIMING)

    result = _run_check(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "iteration_10" in result.stdout


def test_errored_samples_are_skipped(tmp_path: Path) -> None:
    """A sandbox failure has nothing to time; the benchmark already counts it as errored."""
    _seed_sample(tmp_path, "iteration_01", "trial", timing=COMPLETE_TIMING)
    _seed_sample(
        tmp_path,
        "iteration_01",
        "trial-codex",
        timing={**COMPLETE_TIMING, "duration_ms": 0, "total_tokens": 0},
        errored=True,
    )

    result = _run_check(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "1 clean samples" in result.stdout


def test_a_run_with_no_clean_sample_fails(tmp_path: Path) -> None:
    """With every sample errored there is nothing proven, so the guard does not pass."""
    _seed_sample(tmp_path, "iteration_01", "trial", timing=COMPLETE_TIMING, errored=True)

    result = _run_check(tmp_path)

    assert result.returncode == 1
    assert "no clean sample to check" in result.stdout
