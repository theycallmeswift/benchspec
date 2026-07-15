"""Live labeled-corpus eval for the Gemini binder: the false-positive gate.

Samples the real Gemini binder over the gold-labeled corpus and enforces the one hard
gate — a punt-labeled assertion must never bind to a checker. One pytest item per draw,
so the run shows live per-item progress and a leak names the exact draw; `make
evals` shards the draws across xdist workers and the `binder_corpus` marker keeps
it out of `make test` (it costs money and needs a `GEMINI_API_KEY`). A draw that
exhausts its Gemini retries fails its own pytest item — never a skip — and `make
evals`'s `--maxfail` stops the run after too many. Corpus-wide stats (infra-failure
count/elapsed time, retention/over-punt/mismatch) are aggregated in conftest.py from
the per-draw records.
"""

from __future__ import annotations

import os

import pytest
from conftest import _bind_resilient
from test_corpus_integrity import CORPUS

from evalspec.grading.binder import _bind_bare_exists

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("EVALSPEC_BINDER_SAMPLES", "5"))


def _samples_for(entry: object) -> object:
    """Choose the sample count for a binder corpus entry."""
    # Heavy floor only where a false-positive can occur: persistence (the documented leak) and
    # skill_invoked (a punt on an activation assertion is unrecoverable). A zero-tolerance gate
    # needs enough draws on these that "0 observed" is meaningful.
    heavy = entry["cohort"] in ("persistence", "skill_invoked")
    return max(SAMPLES, 20) if heavy else SAMPLES


def _draws() -> object:
    """Build parametrized binder corpus leak-check draws."""
    return [
        pytest.param(entry, id=f"{entry['cohort']}-{index}#{sample}")
        for index, entry in enumerate(CORPUS)
        for sample in range(_samples_for(entry))
    ]


def _field_expectation_draws() -> object:
    """Build binder corpus draws with expected checker fields."""
    return [
        pytest.param(entry, id=f"{entry['cohort']}-{index}#{sample}")
        for index, entry in enumerate(CORPUS)
        if entry["gold"] == "bind" and entry.get("expect")
        for sample in range(SAMPLES)
    ]


@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(entry: object, record: object) -> None:
    """Reject corpus examples where a punt expectation binds to a checker."""
    replies: list = []
    attempt = _bind_resilient(entry["text"], sink=replies)
    # Derive source from the same predicate bind() itself uses to skip call_model,
    # not from whether `replies` is non-empty — an all-retries-errored Gemini draw
    # never appends to `replies` either, and mislabeling it "regex" would inflate
    # regex_fast_path_count and deflate gemini_count.
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"
    result = (
        "error" if attempt.error is not None else "punt" if attempt.binding is None else "bound"
    )

    record(
        {
            "test": "leak",
            "gold": entry["gold"],
            "cohort": entry["cohort"],
            "expect_checker": entry.get("expect_checker"),
            "expect": entry.get("expect"),
            "actual": {key: attempt.binding.get(key) for key in entry.get("expect", {})}
            if isinstance(attempt.binding, dict)
            else None,
            "result": result,
            "checker": attempt.binding.get("checker")
            if isinstance(attempt.binding, dict)
            else None,
            "source": source,
            "attempts": attempt.attempts,
            "elapsed_ms": attempt.elapsed_ms,
            "error_type": type(attempt.error).__name__ if attempt.error is not None else None,
            "error_message": str(attempt.error) if attempt.error is not None else None,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if attempt.error is not None:
        pytest.fail(f"infra failure after retry: {type(attempt.error).__name__}: {attempt.error}")

    if entry["gold"] == "punt":
        assert not isinstance(attempt.binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {attempt.binding.get('checker')!r}"
        )


@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(entry: object, record: object) -> None:
    """Ensure bound checker specs preserve expected fields from the corpus."""
    replies: list = []
    attempt = _bind_resilient(entry["text"], sink=replies)
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"
    result = (
        "error" if attempt.error is not None else "punt" if attempt.binding is None else "bound"
    )

    record(
        {
            "test": "fields",
            "result": result,
            "source": source,
            "attempts": attempt.attempts,
            "elapsed_ms": attempt.elapsed_ms,
            "error_type": type(attempt.error).__name__ if attempt.error is not None else None,
            "error_message": str(attempt.error) if attempt.error is not None else None,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if attempt.error is not None:
        pytest.fail(f"infra failure after retry: {type(attempt.error).__name__}: {attempt.error}")

    assert isinstance(attempt.binding, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(attempt.binding.get(key) == value for key, value in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, "
        f"got {attempt.binding!r}"
    )
