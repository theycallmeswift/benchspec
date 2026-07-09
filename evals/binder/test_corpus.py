"""Live labeled-corpus eval for the Gemini binder: the false-positive gate.

Samples the real Gemini binder over the gold-labeled corpus and enforces the one hard
gate — a punt-labeled assertion must never bind to a checker. One pytest item per draw,
so the run shows live per-item progress and a leak names the exact draw; `make
evals:binder` shards the draws across xdist workers and the `binder_corpus` marker keeps
it out of `make test` (it costs money and needs a `GEMINI_API_KEY`). Corpus-wide stats
(infra-error guard, retention/over-punt/mismatch) are aggregated in conftest.py from the
per-draw records.
"""

from __future__ import annotations

import os

import pytest
from conftest import _recording_call_model
from test_corpus_integrity import CORPUS

from evalspec.binder import _bind_bare_exists, bind

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("EVALSPEC_BINDER_SAMPLES", "5"))

# A bind that failed on infra (timeout/CLI crash) even after a retry — distinct from a punt
# (None) and a bind (dict). Excluded from every rate; counted by the infra-error guard.
_ERROR = object()


def _samples_for(entry: object) -> object:
    """Choose the sample count for a binder corpus entry."""
    # Heavy floor only where a false-positive can occur: persistence (the documented leak) and
    # skill_invoked (a punt on an activation assertion is unrecoverable). A zero-tolerance gate
    # needs enough draws on these that "0 observed" is meaningful.
    heavy = entry["cohort"] in ("persistence", "skill_invoked")
    return max(SAMPLES, 20) if heavy else SAMPLES


def _bind_resilient(text: object, *, sink: list) -> tuple:
    """Bind one assertion with one retry for a transient Gemini infra failure.

    Returns (binding, attempts): attempts counts each retry-loop iteration entered,
    regardless of whether it succeeded, raised, or was the final abandoned try —
    unlike `len(sink)`, which only counts replies _call_gemini actually returned and
    undercounts whenever a retry raises before producing one.

    Args:
        text: The assertion text to bind.
        sink: List that the recording call_model appends each GeminiReply to.

    Returns:
        A (binding, attempts) tuple. `binding` is a checker spec dict, None (punt),
        or `_ERROR` (infra failure after retry). `attempts` is 1 for a regex
        fast-path bind (one loop pass, no API call).
    """
    call_model = _recording_call_model(sink)
    attempts = 0
    for _ in range(2):
        attempts += 1
        try:
            return bind(text, call_model=call_model), attempts
        except RuntimeError:
            continue
    return _ERROR, attempts


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
    binding, attempts = _bind_resilient(entry["text"], sink=replies)
    # Derive source from the same predicate bind() itself uses to skip call_model,
    # not from whether `replies` is non-empty — an all-retries-errored Gemini draw
    # never appends to `replies` either, and mislabeling it "regex" would inflate
    # regex_fast_path_count and deflate gemini_count.
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"

    record(
        {
            "test": "leak",
            "gold": entry["gold"],
            "cohort": entry["cohort"],
            "expect_checker": entry.get("expect_checker"),
            "expect": entry.get("expect"),
            "actual": {key: binding.get(key) for key in entry.get("expect", {})}
            if isinstance(binding, dict)
            else None,
            "result": "error" if binding is _ERROR else "punt" if binding is None else "bound",
            "checker": binding.get("checker") if isinstance(binding, dict) else None,
            "source": source,
            "attempts": attempts,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    if entry["gold"] == "punt":
        assert not isinstance(binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {binding.get('checker')!r}"
        )


@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(entry: object, record: object) -> None:
    """Ensure bound checker specs preserve expected fields from the corpus."""
    replies: list = []
    binding, attempts = _bind_resilient(entry["text"], sink=replies)
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"

    record(
        {
            "test": "fields",
            "source": source,
            "attempts": attempts,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    assert isinstance(binding, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(binding.get(key) == value for key, value in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, got {binding!r}"
    )
