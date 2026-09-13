"""Tests for conftest."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

# Re-export so existing test imports keep working. New code (and external
# CodingAgent implementers) should import from `benchspec.testing` directly.
from benchspec.testing import FakeExecOutput, FakeSandbox

__all__ = ["FakeExecOutput", "FakeSandbox", "seed_arm"]


def _skipped_assertion(text: str) -> dict:
    """The grading.json entry `run_eval_arm` writes for a line whose clause did not hold."""
    return {
        "text": text,
        "passed": None,
        "skipped": True,
        "scoped": True,
        "reason": "if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}",
    }


def seed_arm(
    eval_root: Path,
    eval_id: str,
    arm: str,
    passes: int,
    total: int,
    *,
    sample: int = 0,
    duration_ms: int = 1000,
    total_tokens: int = 500,
    input_tokens: int = 300,
    output_tokens: int = 100,
    cache_read_tokens: int = 50,
    cache_creation_tokens: int = 25,
    judge_ms: int = 200,
    errored: bool = False,
    binder_degraded: int = 0,
    skipped: Collection[int] = frozenset(),
) -> Path:
    """Write a sample-sharded grading.json + timing.json.

    Assertion `index` passes when `index < passes`, except the indices in `skipped`,
    which are written as scoped-out entries (a clause that did not hold: never bound,
    never judged) instead of graded ones.

    Returns the sample dir.
    """
    sample_dir = eval_root / f"eval-{eval_id}" / arm / f"sample-{sample}"
    sample_dir.mkdir(parents=True)
    assertions = [
        _skipped_assertion(f"a{index}")
        if index in skipped
        else {"text": f"a{index}", "passed": index < passes, "evidence": ""}
        for index in range(total)
    ]
    (sample_dir / "grading.json").write_text(
        json.dumps(
            {
                "eval_id": eval_id,
                "skill": eval_root.name,
                "arm": arm,
                "sample": sample,
                "errored": errored,
                "binder_degraded": binder_degraded,
                "assertions": assertions,
            }
        )
    )
    (sample_dir / "timing.json").write_text(
        json.dumps(
            {
                "duration_ms": duration_ms,
                "judge_ms": judge_ms,
                "total_tokens": total_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
            }
        )
    )
    return sample_dir
