"""Live labeled-corpus eval for the binder: the false-positive gate.

Samples the real binder — through the provider `[tool.benchspec.binder]` selects — over
the gold-labeled corpus and enforces the one hard gate: a punt-labeled assertion must
never bind to a checker. One pytest item per draw, so the run shows live per-item
progress and a leak names the exact draw; `make evals` shards the draws across xdist
workers and the `binder_corpus` marker keeps it out of `make test` (it costs money and
needs the provider's key). Corpus-wide stats (infra-error guard,
retention/over-punt/mismatch) are aggregated in conftest.py from the per-draw records.
"""

from __future__ import annotations

import enum
import os

import pytest
from _pytest.mark import ParameterSet
from conftest import RecordDraw, _recording_call_model
from test_corpus_integrity import CORPUS, CorpusEntry

from benchspec.grading.binder import BinderReply, _bind_bare_exists, bind
from benchspec.grading.binder_config import BinderConfig

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("BENCHSPEC_BINDER_SAMPLES", "5"))


class _BindFailure(enum.Enum):
    """A bind that failed on infra (timeout/HTTP failure) even after a retry.

    Distinct from a punt (None) and a bind (dict). Excluded from every rate; counted by
    the infra-error guard.
    """

    INFRA = "infra"


_ERROR = _BindFailure.INFRA


def _samples_for(entry: CorpusEntry) -> int:
    """Choose the sample count for a binder corpus entry."""
    # Heavy floor only where a false-positive can occur: persistence (the documented leak) and
    # skill_invoked (a punt on an activation assertion is unrecoverable). A zero-tolerance gate
    # needs enough draws on these that "0 observed" is meaningful.
    heavy = entry["cohort"] in ("persistence", "skill_invoked")
    return max(SAMPLES, 20) if heavy else SAMPLES


def _bind_resilient(
    text: str, *, sink: list[BinderReply], config: BinderConfig
) -> tuple[dict | None | _BindFailure, int]:
    """Bind one assertion with one retry for a transient binder infra failure.

    Args:
        text: The assertion text to bind.
        sink: List that the recording call_model appends each BinderReply to.
        config: The resolved binder config selecting the transport and model.

    Returns:
        A (binding, attempts) tuple. `binding` is a checker spec dict, None (punt),
        or `_ERROR` (infra failure after retry). `attempts` counts every retry-loop
        iteration entered — `len(sink)` would undercount a retry that raised before
        producing a reply — and is 1 for a regex fast-path bind (no API call).
    """
    call_model = _recording_call_model(sink, config)
    attempts = 0
    for _ in range(2):
        attempts += 1
        try:
            return bind(text, call_model=call_model), attempts
        except RuntimeError:
            continue
    return _ERROR, attempts


def _draws() -> list[ParameterSet]:
    """Build parametrized binder corpus leak-check draws."""
    return [
        pytest.param(entry, id=f"{entry['cohort']}-{index}#{sample}")
        for index, entry in enumerate(CORPUS)
        for sample in range(_samples_for(entry))
    ]


def _field_expectation_draws() -> list[ParameterSet]:
    """Build binder corpus draws with expected checker fields."""
    return [
        pytest.param(entry, id=f"{entry['cohort']}-{index}#{sample}")
        for index, entry in enumerate(CORPUS)
        if entry["gold"] == "bind" and entry.get("expect")
        for sample in range(SAMPLES)
    ]


@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(
    entry: CorpusEntry, record: RecordDraw, binder_config: BinderConfig
) -> None:
    """Reject corpus examples where a punt expectation binds to a checker."""
    replies: list[BinderReply] = []
    binding, attempts = _bind_resilient(entry["text"], sink=replies, config=binder_config)
    # Derive source from the same predicate bind() itself uses to skip call_model,
    # not from whether `replies` is non-empty — an all-retries-errored model draw
    # never appends to `replies` either, and mislabeling it "regex" would inflate
    # regex_fast_path_count and deflate model_call_count.
    source = "regex" if _bind_bare_exists(entry["text"]) else binder_config.provider

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
            "model": None if source == "regex" else binder_config.model,
            "attempts": attempts,
            "latency_ms": sum(reply.latency_ms for reply in replies) if replies else None,
            "prompt_tokens": sum(reply.prompt_tokens for reply in replies) if replies else None,
            "output_tokens": sum(reply.output_tokens for reply in replies) if replies else None,
        }
    )

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    if entry["gold"] == "punt":
        leaked_checker = binding.get("checker") if isinstance(binding, dict) else None
        assert not isinstance(binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {leaked_checker!r}"
        )


@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(
    entry: CorpusEntry, record: RecordDraw, binder_config: BinderConfig
) -> None:
    """Ensure bound checker specs preserve expected fields from the corpus."""
    replies: list[BinderReply] = []
    binding, attempts = _bind_resilient(entry["text"], sink=replies, config=binder_config)
    source = "regex" if _bind_bare_exists(entry["text"]) else binder_config.provider

    record(
        {
            "test": "fields",
            "source": source,
            "model": None if source == "regex" else binder_config.model,
            "attempts": attempts,
            "latency_ms": sum(reply.latency_ms for reply in replies) if replies else None,
            "prompt_tokens": sum(reply.prompt_tokens for reply in replies) if replies else None,
            "output_tokens": sum(reply.output_tokens for reply in replies) if replies else None,
        }
    )

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    assert isinstance(binding, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(binding.get(key) == value for key, value in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, got {binding!r}"
    )
