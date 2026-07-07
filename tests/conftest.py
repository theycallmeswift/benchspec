"""Tests and helpers for evalspec."""

from __future__ import annotations

import json
from pathlib import Path

# Re-export so existing test imports keep working. New code (and external
# CodingAgent implementers) should import from `evalspec.testing` directly.
from evalspec.testing import FakeExecOutput, FakeSandbox
from evalspec.trigger import trigger_record

__all__ = ["FakeExecOutput", "FakeSandbox", "seed_arm", "seed_trigger"]


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


def seed_trigger(
    eval_root: Path,
    query_id: int,
    *,
    should_trigger: bool,
    fires: int,
    threshold: int = 1,
    sample: int = 0,
    query: str = "some query",
    xfail: dict | None = None,
    model: str = "sonnet",
    slug: str | None = None,
) -> Path:
    """Write a trigger sample's timing.json in the persisted-verdict shape.

    Delegates the fired/passed verdict to the production `trigger_record`, so the
    fixture can never drift from the rule the report consumes.
    """
    resolved_slug = slug if slug is not None else f"q{query_id}"
    d = eval_root / f"trigger-{resolved_slug}" / f"sample-{sample}"
    d.mkdir(parents=True)
    query_obj = {
        "slug": resolved_slug,
        "query": query,
        "should_trigger": should_trigger,
    }
    if xfail is not None:
        query_obj["xfail"] = xfail
    per_pass = [{"ms": 0, "fired": i < fires} for i in range(3)]
    record = trigger_record(
        query_obj,
        mode="asymmetric",
        threshold=threshold,
        fires=fires,
        per_pass=per_pass,
        model=model,
    )
    (d / "timing.json").write_text(json.dumps(record))
    return d
