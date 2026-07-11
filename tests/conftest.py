"""Tests for conftest."""

from __future__ import annotations

import json
from pathlib import Path

# Re-export so existing test imports keep working. New code (and external
# CodingAgent implementers) should import from `evalspec.testing` directly.
from evalspec.testing import FakeExecOutput, FakeSandbox

__all__ = ["FakeExecOutput", "FakeSandbox", "seed_arm"]


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
) -> Path:
    """Write a sample-sharded grading.json + timing.json.

    Returns the sample dir.
    """
    d = eval_root / f"eval-{eval_id}" / arm / f"sample-{sample}"
    d.mkdir(parents=True)
    assertions = [{"text": f"a{i}", "passed": i < passes, "evidence": ""} for i in range(total)]
    (d / "grading.json").write_text(
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
    (d / "timing.json").write_text(
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
    return d
